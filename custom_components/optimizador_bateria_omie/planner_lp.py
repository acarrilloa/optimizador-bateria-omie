"""Planificador por programación lineal (F5-U3, RF-06).

Optimiza **globalmente** el horizonte hoy+mañana con PuLP/CBC. Produce
el mismo ``Plan``/``PlanTramo`` que ``PlanificadorGreedy`` — el
``EjecutorControl`` de F4 y sus salvaguardas no cambian (D-F5.4).

Modelo por tramo:
- ``imp`` (importación total: casa + carga de batería; la casa SIEMPRE
  puede abastecerse de red — física, no decisión), ``exp`` (exportación),
  ``cargabat``/``descargabat`` (batería, excluyentes vía binaria),
  ``cargar`` (parte de la carga de batería alimentada desde red, a la
  que solos aplican los guardarraíles de precio) y ``curtail``
  (excedente FV recortado: sumidero sin coste).
- Guardarraíles absolutos (0.11.0) sobre ``cargar`` (tope de compra) y
  ``exp`` (piso de venta); restricción dura D10 sobre ``imp``.
- Objetivo: coste neto con compra = venta (§5.6).

Selección en caliente (D-F5.2): ``entry.data["algoritmo_planificador"]``
= ``greedy`` | ``lp``; si el LP falla, el ``CoordinadorPlan`` recurre
al greedy — nunca se queda sin plan.
"""
# versión 0.12.0

import logging

import pulp

from custom_components.optimizador_bateria_omie.planificador import (
    ACCION_AUTOCONSUMO,
    ACCION_CARGA_RED,
    ACCION_MANTENER,
    ACCION_VENTA_RED,
    DELTA_T_H,
    ESTADO_OK,
    ESTADO_SIN_PRECIOS,
    ESTADO_SIN_TELEMETRIA,
    ParametrosPlanner,
    Plan,
    PlanTramo,
)

_LOGGER = logging.getLogger(__name__)

# Potencias por debajo de este umbral se consideran 0 W al traducir
# la solución continua a acciones (evita "tramos fantasma")
UMBRAL_W = 25.0


class ErrorPlannerLP(Exception):
    """El LP no llegó a solución (fallback al greedy)."""


class PlanificadorLP:
    """Planificador LP con el contrato de ``PlanificadorGreedy``."""

    def __init__(self, p: ParametrosPlanner) -> None:
        """Mismos parámetros editables que el greedy."""
        self.p = p

    def planificar(
        self,
        fecha,
        precios_hoy: list[float],
        precios_manana: list[float] | None,
        prediccion_hoy,
        prediccion_manana,
        soc_actual: float | None,
    ) -> Plan:
        """Plan óptimo del horizonte (contrato del greedy, sin escrituras).

        Sin SOC o sin precios devuelve el mismo estado que el greedy;
        si el solver no converge, lanza ``ErrorPlannerLP``.
        """
        if soc_actual is None:
            _LOGGER.warning("Plan LP sin SOC real de telemetría")
            return Plan(estado=ESTADO_SIN_TELEMETRIA)
        if not precios_hoy:
            _LOGGER.warning("Plan LP sin precios de hoy")
            return Plan(estado=ESTADO_SIN_PRECIOS)

        dias = [(fecha, list(precios_hoy), prediccion_hoy)]
        if precios_manana and prediccion_manana is not None:
            dias.append(
                (
                    prediccion_manana.fecha,
                    list(precios_manana),
                    prediccion_manana,
                )
            )

        # Series del horizonte (consumo/FV p50; None → 0 W como greedy)
        precios: list[float] = []
        consumos: list[float] = []
        fvs: list[float] = []
        for _, precios_dia, prediccion in dias:
            for tramo, precio in enumerate(precios_dia):
                precios.append(float(precio))
                c = prediccion.consumo[tramo]
                f = (
                    prediccion.fv[tramo]
                    if prediccion.fv is not None
                    else None
                )
                consumos.append(c.p50 if c is not None else 0.0)
                fvs.append(f.p50 if f is not None else 0.0)

        n = len(precios)
        energia_h = DELTA_T_H / 1000.0
        cap_wh = self.p.capacidad_bateria_kwh * 1000.0
        k_soc = DELTA_T_H * 100.0 / cap_wh
        fv_max = max(fvs) if fvs else 0.0
        grande = (
            self.p.potencia_contratada
            + self.p.potencia_max_carga
            + self.p.potencia_max_descarga
            + fv_max
            + 1000.0
        )

        problema = pulp.LpProblem("optimizador_bateria", pulp.LpMinimize)
        imp = [pulp.LpVariable(f"imp_{g}", lowBound=0) for g in range(n)]
        exp = [pulp.LpVariable(f"exp_{g}", lowBound=0) for g in range(n)]
        cargabat = [
            pulp.LpVariable(
                f"cargabat_{g}",
                lowBound=0,
                upBound=self.p.potencia_max_carga,
            )
            for g in range(n)
        ]
        descargabat = [
            pulp.LpVariable(
                f"descargabat_{g}",
                lowBound=0,
                upBound=self.p.potencia_max_descarga,
            )
            for g in range(n)
        ]
        curtail = [
            pulp.LpVariable(f"curtail_{g}", lowBound=0) for g in range(n)
        ]
        soc = [pulp.LpVariable(f"soc_{g}") for g in range(n + 1)]
        # y=0 → lado importación; y=1 → lado exportación
        y = [pulp.LpVariable(f"y_{g}", cat=pulp.LpBinary) for g in range(n)]
        # z=1 → batería cargando; z=0 → descargando
        z = [pulp.LpVariable(f"z_{g}", cat=pulp.LpBinary) for g in range(n)]

        problema += soc[0] == float(soc_actual)

        # Restricciones duras de importación (D10): la casa siempre
        # puede abastecerse de red; solo limita la potencia contratada
        for g in range(n):
            problema += imp[g] <= (
                self.p.potencia_contratada - self.p.margen_potencia
            ), f"imp_contratada_{g}"
            # Balance del nodo: entradas = salidas
            # imp + fv + descargabat = consumo + exp + cargabat + curtail
            problema += (
                imp[g] + fvs[g] + descargabat[g]
                == consumos[g] + exp[g] + cargabat[g] + curtail[g]
            ), f"balance_{g}"
            # Flujo unidireccional por tramo (regla 0.11.1)
            problema += imp[g] <= grande * (1 - y[g]), f"imp_dir_{g}"
            problema += exp[g] <= grande * y[g], f"exp_dir_{g}"
            # Carga/descarga de batería excluyentes (binaria z)
            problema += cargabat[g] <= self.p.potencia_max_carga * z[g]
            problema += descargabat[g] <= self.p.potencia_max_descarga * (
                1 - z[g]
            )
            # Excedente FV recortado: solo puede descansar en producción
            # (no puede "curtailar" energía importada)
            problema += curtail[g] <= fvs[g], f"curtail_{g}"
            # Guardarraíl de compra (0.11.0): con precio por encima del
            # tope, red solo para la casa — la batería solo puede
            # cargar del excedente FV (carga auto, como el greedy)
            if precios[g] > self.p.precio_max_compra:
                problema += imp[g] <= consumos[g], f"imp_cap_{g}"
            # Guardarraíl de venta (0.11.0): ni a 0 ni negativos abajo
            if precios[g] < self.p.precio_min_venta:
                problema += exp[g] == 0, f"exp_piso_{g}"
            # Cadena de SOC (misma convención que el greedy, sin η:
            # la eficiencia se aplica en las decisiones económicas)
            problema += soc[g + 1] >= self.p.soc_min
            problema += soc[g + 1] <= self.p.soc_max
            problema += (
                soc[g + 1] == soc[g] + (cargabat[g] - descargabat[g]) * k_soc
            ), f"soc_{g}"

        # Objetivo: coste neto del horizonte (§5.6: compra = venta)
        problema += pulp.lpSum(
            precios[g] * (imp[g] - exp[g]) * energia_h for g in range(n)
        )

        estado = problema.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=30))
        if estado != pulp.LpStatusOptimal:
            raise ErrorPlannerLP(
                f"Solver LP sin solución óptima ({pulp.LpStatus.get(estado, str(estado))})"
            )

        plan = Plan(estado=ESTADO_OK)
        for fecha_dia, _, _ in dias:
            if fecha_dia != fecha:
                plan.motivos.append("horizonte incluye mañana")

        g = 0
        for fecha_dia, precios_dia, prediccion in dias:
            for tramo, precio in enumerate(precios_dia):
                plan.tramos.append(
                    self._tramo_desde_solucion(
                        fecha_dia,
                        tramo,
                        precio,
                        consumos[g],
                        fvs[g],
                        imp[g],
                        exp[g],
                        cargabat[g],
                        descargabat[g],
                        soc[g + 1].value() or 0.0,
                        energia_h,
                    )
                )
                g += 1

        plan.coste_sin_plan_eur = round(
            sum(
                max(0.0, consumos[g] - fvs[g]) * precios[g] * energia_h
                for g in range(n)
            ),
            4,
        )
        plan.coste_con_plan_eur = round(
            sum(
                ((imp[g].value() or 0.0) - (exp[g].value() or 0.0))
                * precios[g] * energia_h
                for g in range(n)
            ),
            4,
        )
        plan.ahorro_estimado_eur = round(
            plan.coste_sin_plan_eur - plan.coste_con_plan_eur, 4
        )
        return plan

    def _tramo_desde_solucion(
        self,
        fecha_dia,
        tramo: int,
        precio: float,
        consumo: float,
        fv: float,
        imp,
        exp,
        cargabat,
        descargabat,
        soc_post: float,
        energia_h: float,
    ) -> PlanTramo:
        """Traduce la solución continua a ``PlanTramo`` (formato greedy).

        El reparto batería/FV usa la misma convención del greedy: el
        excedente FV (tras casa y exportación) carga primero, y el resto
        de la carga es "desde red" — que solo puede existir a precios
        bajo el tope (garantizado por las restricciones del modelo).
        """
        imp_w = imp.value() or 0.0
        exp_w = exp.value() or 0.0
        carga_w = cargabat.value() or 0.0
        descarga_w = descargabat.value() or 0.0

        excedente = max(0.0, fv - consumo)
        venta_fv = min(exp_w, excedente)
        excedente_para_bateria = max(0.0, excedente - venta_fv)
        carga_red = max(0.0, carga_w - excedente_para_bateria)

        accion = ACCION_MANTENER
        if carga_red > UMBRAL_W:
            accion = ACCION_CARGA_RED
        elif exp_w > UMBRAL_W:
            accion = ACCION_VENTA_RED
        elif descarga_w > UMBRAL_W or (consumo - fv) > UMBRAL_W:
            accion = ACCION_AUTOCONSUMO

        coste_con = (imp_w - exp_w) * energia_h * precio
        coste_sin = max(0.0, consumo - fv) * energia_h * precio

        return PlanTramo(
            fecha=fecha_dia,
            tramo=tramo,
            hora=f"{tramo // 4:02d}:{(tramo % 4) * 15:02d}",
            accion=accion,
            potencia_bateria_w=descarga_w - carga_w,
            potencia_carga_red_w=(
                round(carga_red, 1) if accion == ACCION_CARGA_RED else 0.0
            ),
            potencia_venta_red_w=(
                round(exp_w, 1) if accion == ACCION_VENTA_RED else 0.0
            ),
            soc_previsto_pct=round(soc_post, 2),
            precio_referencia_eur_kwh=precio,
            ahorro_tramo_eur=round(coste_sin - coste_con, 6),
        )
