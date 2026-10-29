"""Planificador greedy por tramos de 15 min (F3, dry-run).

Función **pura y determinista** (D17): ``planificar()`` no toca HA, no
consulta nada y **nunca escribe en el inversor**. Recorre los tramos de
hoy (+ mañana si hay precios) en orden cronológico con balance SOC y
prioriza: (1) autoconsumo, (2) arbitraje de compra si el tramo es
barato en contexto, (3) arbitraje de venta solo si el ciclo compensa
tras la eficiencia round-trip, (4) mantener. Precio ≤ 0 → carga desde
red hasta ``soc_max`` (D9).

La restricción dura de potencia contratada (D10) limita la carga desde
red: ``min(potencia_max_carga, potencia_contratada − consumo − margen)``,
nunca negativa. Sin precios → ``sin_precios``; perfil insuficiente →
plan trivial de solo-autoconsumo (D18). Nada se inventa.
"""
# versión 0.9.0

import logging
from dataclasses import dataclass, field
from datetime import date
from statistics import median

from custom_components.optimizador_bateria_omie.const import (
    CONF_CAPACIDAD_BATERIA_KWH,
    CONF_EFICIENCIA_ROUND_TRIP,
    CONF_MARGEN_ARBITRAJE,
    CONF_MARGEN_POTENCIA,
    CONF_PERMITIR_CARGA_RED,
    CONF_PERMITIR_VENTA_RED,
    CONF_POTENCIA_CONTRATADA,
    CONF_POTENCIA_MAX_CARGA,
    CONF_POTENCIA_MAX_DESCARGA,
    CONF_PRECIO_MAX_COMPRA,
    CONF_PRECIO_MIN_VENTA,
    CONF_SOC_MAX,
    CONF_SOC_MIN,
    DEFAULT_CAPACIDAD_BATERIA_KWH,
    DEFAULT_EFICIENCIA_ROUND_TRIP,
    DEFAULT_MARGEN_ARBITRAJE,
    DEFAULT_MARGEN_POTENCIA,
    DEFAULT_PERMITIR_CARGA_RED,
    DEFAULT_PERMITIR_VENTA_RED,
    DEFAULT_POTENCIA_CONTRATADA,
    DEFAULT_POTENCIA_MAX_CARGA,
    DEFAULT_POTENCIA_MAX_DESCARGA,
    DEFAULT_PRECIO_MAX_COMPRA,
    DEFAULT_PRECIO_MIN_VENTA,
    DEFAULT_SOC_MAX,
    DEFAULT_SOC_MIN,
)
from custom_components.optimizador_bateria_omie.predictor_consumo import (
    TRAMOS_POR_DIA,
    PrediccionDia,
)

_LOGGER = logging.getLogger(__name__)

ACCION_AUTOCONSUMO = "AUTOCONSUMO"
ACCION_CARGA_RED = "CARGA_RED"
ACCION_VENTA_RED = "VENTA_RED"
ACCION_MANTENER = "MANTENER"

ESTADO_OK = "ok"
ESTADO_SIN_PRECIOS = "sin_precios"
ESTADO_PERFIL_INSUFICIENTE = "perfil_insuficiente"
ESTADO_SIN_TELEMETRIA = "sin_telemetria"

DELTA_T_H = 0.25  # tramo de 15 min
FRACCION_TRAMOS_INSUFICIENTES = 0.5


@dataclass
class ParametrosPlanner:
    """Parámetros editables del planificador (todos por reconfigure)."""

    permitir_carga_red: bool = DEFAULT_PERMITIR_CARGA_RED
    permitir_venta_red: bool = DEFAULT_PERMITIR_VENTA_RED
    precio_max_compra: float = DEFAULT_PRECIO_MAX_COMPRA
    precio_min_venta: float = DEFAULT_PRECIO_MIN_VENTA
    margen_arbitraje: float = DEFAULT_MARGEN_ARBITRAJE
    soc_min: float = DEFAULT_SOC_MIN
    soc_max: float = DEFAULT_SOC_MAX
    potencia_max_carga: float = DEFAULT_POTENCIA_MAX_CARGA
    potencia_max_descarga: float = DEFAULT_POTENCIA_MAX_DESCARGA
    potencia_contratada: float = DEFAULT_POTENCIA_CONTRATADA
    margen_potencia: float = DEFAULT_MARGEN_POTENCIA
    eficiencia_round_trip: float = DEFAULT_EFICIENCIA_ROUND_TRIP
    capacidad_bateria_kwh: float = DEFAULT_CAPACIDAD_BATERIA_KWH

    @classmethod
    def desde_entry(cls, entry) -> "ParametrosPlanner":
        """Parámetros desde ``entry.data`` con defaults (M3)."""
        data = entry.data
        return cls(
            permitir_carga_red=data.get(
                CONF_PERMITIR_CARGA_RED, DEFAULT_PERMITIR_CARGA_RED
            ),
            permitir_venta_red=data.get(
                CONF_PERMITIR_VENTA_RED, DEFAULT_PERMITIR_VENTA_RED
            ),
            precio_max_compra=data.get(
                CONF_PRECIO_MAX_COMPRA, DEFAULT_PRECIO_MAX_COMPRA
            ),
            precio_min_venta=data.get(
                CONF_PRECIO_MIN_VENTA, DEFAULT_PRECIO_MIN_VENTA
            ),
            margen_arbitraje=data.get(
                CONF_MARGEN_ARBITRAJE, DEFAULT_MARGEN_ARBITRAJE
            ),
            soc_min=data.get(CONF_SOC_MIN, DEFAULT_SOC_MIN),
            soc_max=data.get(CONF_SOC_MAX, DEFAULT_SOC_MAX),
            potencia_max_carga=data.get(
                CONF_POTENCIA_MAX_CARGA, DEFAULT_POTENCIA_MAX_CARGA
            ),
            potencia_max_descarga=data.get(
                CONF_POTENCIA_MAX_DESCARGA, DEFAULT_POTENCIA_MAX_DESCARGA
            ),
            potencia_contratada=data.get(
                CONF_POTENCIA_CONTRATADA, DEFAULT_POTENCIA_CONTRATADA
            ),
            margen_potencia=data.get(
                CONF_MARGEN_POTENCIA, DEFAULT_MARGEN_POTENCIA
            ),
            eficiencia_round_trip=data.get(
                CONF_EFICIENCIA_ROUND_TRIP, DEFAULT_EFICIENCIA_ROUND_TRIP
            ),
            capacidad_bateria_kwh=data.get(
                CONF_CAPACIDAD_BATERIA_KWH, DEFAULT_CAPACIDAD_BATERIA_KWH
            ),
        )


@dataclass
class PlanTramo:
    """Decisión del plan para un tramo de 15 min."""

    fecha: date
    tramo: int  # 0–95 (reloj pared local)
    hora: str  # "HH:MM"
    accion: str
    # Convención Deye: +descarga / −carga
    potencia_bateria_w: float
    potencia_carga_red_w: float
    potencia_venta_red_w: float
    soc_previsto_pct: float  # SOC al final del tramo
    precio_referencia_eur_kwh: float
    ahorro_tramo_eur: float  # coste sin plan − coste con plan
    insuficiente: bool = False  # consumo previsto sin datos (D6)


@dataclass
class Plan:
    """Plan completo del horizonte (hoy + mañana si hay precios)."""

    tramos: list[PlanTramo] = field(default_factory=list)
    estado: str = ESTADO_OK
    motivos: list[str] = field(default_factory=list)
    coste_sin_plan_eur: float = 0.0
    coste_con_plan_eur: float = 0.0
    ahorro_estimado_eur: float = 0.0


def _mediana(precios: list[float]) -> float:
    """Mediana de los precios de un día (contexto de barato/caro)."""
    return median(precios) if precios else 0.0


class PlanificadorGreedy:
    """Planificador greedy determinista (RF-06, capa F3)."""

    def __init__(self, parametros: ParametrosPlanner) -> None:
        """Inicializa con los parámetros editables del reconfigure."""
        self.p = parametros

    # ─── Utilidades de balance ───

    def _delta_soc_pct(self, potencia_w: float) -> float:
        """Cambio de SOC (%) de una potencia constante durante un tramo."""
        capacidad_wh = self.p.capacidad_bateria_kwh * 1000
        return potencia_w * DELTA_T_H / capacidad_wh * 100

    def _potencia_carga_red_max(self, consumo_previsto_w: float) -> float:
        """Restricción dura D10: nunca negativa."""
        return max(
            0.0,
            min(
                self.p.potencia_max_carga,
                self.p.potencia_contratada
                - consumo_previsto_w
                - self.p.margen_potencia,
            ),
        )

    def _room_carga_w(self, soc: float) -> float:
        """Potencia que llena la batería hasta soc_max en un tramo."""
        return (
            (self.p.soc_max - soc)
            * self.p.capacidad_bateria_kwh
            * 1000
            / DELTA_T_H
        )

    def _disponible_descarga_w(self, soc: float) -> float:
        """Potencia que vacía hasta soc_min en un tramo."""
        return (
            (soc - self.p.soc_min)
            * self.p.capacidad_bateria_kwh
            * 1000
            / DELTA_T_H
        )

    # ─── Núcleo ───

    def planificar(
        self,
        fecha: date,
        precios_hoy: list[float],
        precios_manana: list[float] | None,
        prediccion_hoy: PrediccionDia,
        prediccion_manana: PrediccionDia | None,
        soc_actual: float | None,
    ) -> Plan:
        """Calcula el plan del horizonte (hoy + mañana si hay precios).

        ``precios_*`` son los precios efectivos €/kWh por tramo (0–95).
        ``soc_actual`` es el SOC real de telemetría (``None`` → estado
        ``sin_telemetria``, no se inventa un SOC inicial).
        """
        if soc_actual is None:
            _LOGGER.warning("Plan sin SOC real de telemetría")
            return Plan(estado=ESTADO_SIN_TELEMETRIA)
        if not precios_hoy:
            _LOGGER.warning("Plan sin precios de hoy")
            return Plan(estado=ESTADO_SIN_PRECIOS)

        dias = [(fecha, precios_hoy, prediccion_hoy, True)]
        if precios_manana and prediccion_manana is not None:
            dias.append(
                (
                    prediccion_manana.fecha,
                    precios_manana,
                    prediccion_manana,
                    False,
                )
            )

        # Perfil insuficiente (D18): mayoría de tramos de consumo sin datos
        insuficientes_hoy = sum(
            1 for c in prediccion_hoy.consumo if c is None
        )
        solo_autoconsumo = (
            insuficientes_hoy
            >= FRACCION_TRAMOS_INSUFICIENTES * TRAMOS_POR_DIA
        )

        eta = max(self.p.eficiencia_round_trip, 1.0) / 100.0
        plan = Plan()
        if solo_autoconsumo:
            plan.estado = ESTADO_PERFIL_INSUFICIENTE
            plan.motivos.append(
                f"{insuficientes_hoy} tramos sin predicción de consumo: "
                "plan trivial de solo-autoconsumo"
            )

        soc = float(soc_actual)
        precio_compra_ciclo: float | None = None  # mínimo de CARGA_RED
        for fecha_dia, precios, prediccion, es_hoy in dias:
            mediana_dia = _mediana(precios)
            for tramo in range(len(precios)):
                precio = precios[tramo]
                consumo_p = prediccion.consumo[tramo]
                fv_p = prediccion.fv[tramo] if prediccion.fv else None
                consumo = consumo_p.p50 if consumo_p else 0.0
                fv = fv_p.p50 if fv_p else 0.0
                deficit = consumo - fv

                carga_auto = 0.0
                descarga_auto = 0.0
                carga_red = 0.0
                descarga_red = 0.0
                accion = ACCION_MANTENER

                # (1) Autoconsumo siempre (D9)
                if deficit > 0:
                    descarga_auto = min(
                        deficit,
                        self.p.potencia_max_descarga,
                        self._disponible_descarga_w(soc),
                    )
                    accion = (
                        ACCION_AUTOCONSUMO if descarga_auto > 0 else accion
                    )
                elif deficit < 0:
                    excedente = -deficit
                    carga_auto = min(
                        excedente,
                        self.p.potencia_max_carga,
                        self._room_carga_w(soc),
                    )
                    accion = (
                        ACCION_AUTOCONSUMO if carga_auto > 0 else accion
                    )

                # (2/3) Arbitraje (no en plan trivial de autoconsumo)
                if not solo_autoconsumo:
                    if (
                        precio <= 0
                        and self.p.permitir_carga_red
                        and carga_red == 0
                    ):
                        # Precio ≤ 0: cargar siempre, sin umbral (D9.4)
                        carga_red = min(
                            self._potencia_carga_red_max(consumo),
                            self._room_carga_w(soc) - carga_auto
                            if carga_auto > 0
                            else self._room_carga_w(soc),
                        )
                        carga_red = max(0.0, carga_red)
                        accion = (
                            ACCION_CARGA_RED if carga_red > 0 else accion
                        )
                    elif (
                        self.p.permitir_carga_red
                        and precio > 0
                        and precio <= self.p.precio_max_compra
                        and precio < mediana_dia
                        and carga_red == 0
                    ):
                        # Barato en contexto del día
                        carga_red = max(
                            0.0,
                            min(
                                self._potencia_carga_red_max(consumo),
                                self._room_carga_w(soc),
                            ),
                        )
                        accion = (
                            ACCION_CARGA_RED if carga_red > 0 else accion
                        )

                    # Venta: excedente FV directo (sin pérdida de ciclo)
                    # o batería comprada si el ciclo compensa (D9.3)
                    excedente_sin_cargar = max(
                        0.0, -deficit - carga_auto
                    )
                    venta_disponible = self._disponible_descarga_w(
                        soc
                    ) - descarga_auto
                    puede_vender = (
                        self.p.permitir_venta_red
                        and precio >= self.p.precio_min_venta
                    )
                    ciclo_compensa = (
                        precio_compra_ciclo is not None
                        and precio
                        >= precio_compra_ciclo / eta
                        + self.p.margen_arbitraje
                    )
                    venta_potencia = 0.0
                    if puede_vender and excedente_sin_cargar > 0:
                        venta_potencia += excedente_sin_cargar
                    if (
                        puede_vender
                        and ciclo_compensa
                        and venta_disponible > 0
                        and excedente_sin_cargar == 0
                    ):
                        venta_potencia += min(
                            venta_disponible,
                            self.p.potencia_max_descarga,
                        )
                    if venta_potencia > 0:
                        descarga_red = min(
                            venta_potencia,
                            self.p.potencia_max_descarga,
                        )
                        accion = ACCION_VENTA_RED

                if carga_red > 0 and precio_compra_ciclo is None:
                    precio_compra_ciclo = precio
                elif carga_red > 0:
                    precio_compra_ciclo = min(
                        precio_compra_ciclo, precio
                    )

                carga_total = carga_auto + carga_red
                descarga_total = descarga_auto + descarga_red
                soc = min(
                    self.p.soc_max,
                    max(
                        self.p.soc_min,
                        soc
                        + self._delta_soc_pct(
                            carga_total - descarga_total
                        ),
                    ),
                )

                # Coste del tramo: flujo de red = consumo − FV + carga −
                # descarga (positivo = compra; venta al mismo precio)
                kwh = DELTA_T_H / 1000
                flujo_red = consumo - fv + carga_total - descarga_total
                coste_sin = (consumo - fv) * kwh * precio
                coste_con = flujo_red * kwh * precio

                plan.tramos.append(
                    PlanTramo(
                        fecha=fecha_dia,
                        tramo=tramo,
                        hora=f"{tramo // 4:02d}:{(tramo % 4) * 15:02d}",
                        accion=accion or ACCION_MANTENER,
                        potencia_bateria_w=descarga_total - carga_total,
                        potencia_carga_red_w=carga_red,
                        potencia_venta_red_w=descarga_red,
                        soc_previsto_pct=round(soc, 2),
                        precio_referencia_eur_kwh=precio,
                        ahorro_tramo_eur=round(coste_sin - coste_con, 6),
                        insuficiente=consumo_p is None,
                    )
                )
                plan.coste_sin_plan_eur += coste_sin
                plan.coste_con_plan_eur += coste_con

            if not es_hoy:
                plan.motivos.append("horizonte incluye mañana")

        plan.coste_sin_plan_eur = round(plan.coste_sin_plan_eur, 4)
        plan.coste_con_plan_eur = round(plan.coste_con_plan_eur, 4)
        plan.ahorro_estimado_eur = round(
            plan.coste_sin_plan_eur - plan.coste_con_plan_eur, 4
        )
        if not self.p.permitir_venta_red:
            plan.motivos.append("venta a red deshabilitada")
        return plan

    def tramo_actual(self, plan: Plan, fecha: date, indice: int) -> (
        PlanTramo | None
    ):
        """Tramo del plan correspondiente a una fecha e índice."""
        for tramo in plan.tramos:
            if tramo.fecha == fecha and tramo.tramo == indice:
                return tramo
        return None
