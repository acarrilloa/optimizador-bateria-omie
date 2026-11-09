"""Tests del planificador LP (F5-U3)."""

from datetime import date
from types import SimpleNamespace

import pytest

from custom_components.optimizador_bateria_omie.planificador import (
    ACCION_CARGA_RED,
    ACCION_VENTA_RED,
    ESTADO_OK,
    ESTADO_SIN_PRECIOS,
    ESTADO_SIN_TELEMETRIA,
    ParametrosPlanner,
    PlanificadorGreedy,
)
from custom_components.optimizador_bateria_omie.planner_lp import (
    ErrorPlannerLP,
    PlanificadorLP,
)
from custom_components.optimizador_bateria_omie.predictor_consumo import (
    PercentilesTramo,
)

HOY = date(2026, 11, 10)
MANANA = HOY + date.resolution  # +1 día


def _percentiles(p50: float) -> PercentilesTramo:
    return PercentilesTramo(p10=p50 * 0.5, p50=p50, p90=p50 * 1.5)


def _prediccion(consumo_w=300.0, fv_w=0.0, fecha=HOY):
    return SimpleNamespace(
        fecha=fecha,
        consumo=[_percentiles(consumo_w)] * 96,
        fv=[_percentiles(fv_w)] * 96,
    )


def _precios_valle_punta(valle=0.08, punta=0.25):
    return [valle if t // 4 < 8 else punta for t in range(96)]


def _lp(**overrides) -> PlanificadorLP:
    return PlanificadorLP(ParametrosPlanner(**overrides))


class TestEstados:
    def test_sin_telemetria(self):
        plan = _lp().planificar(
            HOY, _precios_valle_punta(), None, _prediccion(), None, None
        )
        assert plan.estado == ESTADO_SIN_TELEMETRIA

    def test_sin_precios(self):
        plan = _lp().planificar(
            HOY, [], None, _prediccion(), None, 50.0
        )
        assert plan.estado == ESTADO_SIN_PRECIOS


class TestPlanLP:
    def test_valle_punta_clasico(self):
        """Carga barato de noche, sirve la casa de día: el LP debe
        costar lo mismo o menos que el greedy."""
        precios = _precios_valle_punta()
        prediccion = _prediccion()
        lp = _lp().planificar(
            HOY, precios, None, prediccion, None, 20.0
        )
        greedy = PlanificadorGreedy(ParametrosPlanner()).planificar(
            HOY, precios, None, prediccion, None, 20.0
        )

        assert lp.estado == ESTADO_OK
        assert len(lp.tramos) == 96
        # Óptimo global: nunca peor que el greedy
        assert lp.coste_con_plan_eur <= greedy.coste_con_plan_eur + 1e-6
        # Se carga en valle (SOC sube de noche)
        nocturnos = [t for t in lp.tramos if t.hora < "08:00"]
        assert max(t.soc_previsto_pct for t in nocturnos) > 20.0
        # SOC dentro de límites en todo el horizonte
        assert all(15.0 <= t.soc_previsto_pct <= 95.0 for t in lp.tramos)

    def test_horizonte_con_manana(self):
        lp = _lp().planificar(
            HOY, _precios_valle_punta(),
            _precios_valle_punta(), _prediccion(),
            _prediccion(fecha=MANANA), 20.0,
        )
        assert len(lp.tramos) == 192
        assert "horizonte incluye mañana" in lp.motivos

    def test_importacion_limitada_por_potencia_contratada(self):
        precios = _precios_valle_punta(valle=0.02)
        plan = _lp().planificar(
            HOY, precios, None, _prediccion(), None, 10.0
        )
        # La carga por tramo no puede exceder potencia_max_carga ni
        # la restricción dura de importación
        for tramo in plan.tramos:
            assert tramo.potencia_carga_red_w <= 5000.0 + 1e-6

    def test_nunca_importa_y_verte_en_el_mismo_tramo(self):
        precios = [0.05 if t // 4 < 8 else 0.40 for t in range(96)]
        fv_dia = [0.0 if t // 4 < 8 else 1200.0 for t in range(96)]
        prediccion = SimpleNamespace(
            fecha=HOY,
            consumo=[_percentiles(300.0) for _ in range(96)],
            fv=[
                PercentilesTramo(p10=f, p50=f, p90=f) for f in fv_dia
            ],
        )
        plan = _lp().planificar(
            HOY, precios, None, prediccion, None, 90.0
        )
        for tramo in plan.tramos:
            carga = tramo.potencia_carga_red_w
            venta = tramo.potencia_venta_red_w
            assert not (carga > 25.0 and venta > 25.0)

    def test_guardarail_precio_max_compra(self):
        """Todo el día por encima del tope de seguridad → sin carga."""
        precios = [0.55] * 96
        plan = _lp().planificar(
            HOY, precios, None, _prediccion(), None, 20.0
        )
        assert not any(
            t.accion == ACCION_CARGA_RED for t in plan.tramos
        )

    def test_piso_minimo_venta_bloquea_barato(self):
        """Piso 0.05: no se vierte con precio 0.04."""
        precios = [0.04] * 96
        fv = [1000.0] * 96
        prediccion = SimpleNamespace(
            fecha=HOY,
            consumo=[_percentiles(100.0)] * 96,
            fv=[PercentilesTramo(p10=f, p50=f, p90=f) for f in fv],
        )
        plan = _lp(precio_min_venta=0.05).planificar(
            HOY, precios, None, prediccion, None, 95.0
        )
        assert not any(
            t.accion == ACCION_VENTA_RED for t in plan.tramos
        )

    def test_consume_solo_scn_negativo(self):
        """Precio ≤ 0: el LP debe aprovechar para cargar gratis."""
        precios = [-0.02] * 96
        plan = _lp(precio_max_compra=0.0).planificar(
            HOY, precios, None, _prediccion(), None, 20.0
        )
        assert any(t.accion == ACCION_CARGA_RED for t in plan.tramos)
        assert max(t.soc_previsto_pct for t in plan.tramos) >= 94.0

    def test_soc_minimo_respetado(self):
        precios = [0.30] * 96
        plan = _lp().planificar(
            HOY, precios, None, _prediccion(consumo_w=600.0), None, 30.0
        )
        assert all(
            t.soc_previsto_pct >= 15.0 - 1e-6 for t in plan.tramos
        )


class TestFalloLP:
    def test_infactible_lanza_error(self, monkeypatch):
        """Solver roto → ErrorPlannerLP (el coordinador caerá al greedy)."""
        import pulp

        def _solve_roto(self, solver):
            return None

        monkeypatch.setattr(pulp.LpProblem, "solve", _solve_roto)
        with pytest.raises(ErrorPlannerLP):
            _lp().planificar(
                HOY, _precios_valle_punta(), None, _prediccion(), None, 20.0
            )
