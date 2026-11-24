"""Tests de las entidades del plan (F3, U5)."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.optimizador_bateria_omie.entidades_plan import (
    SensorAhorroEstimadoHoy,
    SensorPlan,
    SensorPrediccionConsumo,
    SwitchFestivoHoy,
)
from custom_components.optimizador_bateria_omie.planificador import (
    Plan,
)
from custom_components.optimizador_bateria_omie.predictor_consumo import (
    PercentilesTramo,
    PrediccionDia,
)

HOY = datetime.now().astimezone().date()


def _percentiles(p50: float) -> PercentilesTramo:
    return PercentilesTramo(p10=p50 * 0.5, p50=p50, p90=p50 * 1.5)


def _prediccion(fecha, fuente_fv: str = "solcast") -> PrediccionDia:
    return PrediccionDia(
        fecha=fecha,
        consumo=[_percentiles(300.0)] * 96,
        fv=[_percentiles(500.0)] * 96,
        fuente_fv=fuente_fv,
    )


@pytest.fixture
def coordinador():
    tramo_actual = SimpleNamespace(
        tramo=40,
        hora="10:00",
        accion="AUTOCONSUMO",
        soc_previsto_pct=55.0,
        potencia_bateria_w=300.0,
        potencia_carga_red_w=0.0,
        potencia_venta_red_w=0.0,
        precio_referencia_eur_kwh=0.2,
        fecha=HOY,
    )
    c = MagicMock()
    c.plan = Plan(
        estado="ok",
        motivos=[],
        coste_sin_plan_eur=1.0,
        coste_con_plan_eur=0.4,
        ahorro_estimado_eur=0.6,
    )
    c.prediccion_hoy = _prediccion(HOY)
    c.prediccion_manana = None
    c.tramo_actual = MagicMock(return_value=tramo_actual)
    c.ahorro_estimado_hoy = MagicMock(return_value=0.6)
    c.async_set_festivo_hoy = AsyncMock()
    c.festivo_hoy = False
    c.async_registrar_callback = MagicMock(return_value=MagicMock())
    return c


class TestSensorPrediccionConsumo:
    def test_native_value_p50_tramo_actual(self, coordinador):
        sensor = SensorPrediccionConsumo(coordinador)

        assert sensor.native_value == pytest.approx(300.0)
        assert sensor.unique_id == (
            "optimizador_bateria_omie_prediccion_consumo"
        )

    def test_atributos_curvas_y_fuente_fv(self, coordinador):
        sensor = SensorPrediccionConsumo(coordinador)

        attrs = sensor.extra_state_attributes
        # La curva de hoy se compacta desde el tramo en curso (0.10.2)
        ahora = datetime.now().astimezone()
        indice = ahora.hour * 4 + ahora.minute // 15
        assert len(attrs["consumo_hoy"]) == 96 - indice
        assert attrs["consumo_hoy"][0]["hora"] == (
            f"{indice // 4:02d}:{(indice % 4) * 15:02d}"
        )
        assert attrs["consumo_hoy"][0]["p50"] == 300
        assert attrs["fuente_fv_hoy"] == "solcast"
        assert attrs["fuente"] == "perfil"

    def test_sin_prediccion_none(self, coordinador):
        coordinador.prediccion_hoy = None
        sensor = SensorPrediccionConsumo(coordinador)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes["consumo_hoy"] == []


class TestSensorPlan:
    def test_native_value_accion_actual(self, coordinador):
        sensor = SensorPlan(coordinador)

        assert sensor.native_value == "AUTOCONSUMO"
        assert sensor.unique_id == "optimizador_bateria_omie_plan"

    def test_atributos_plan_completo(self, coordinador):
        sensor = SensorPlan(coordinador)

        attrs = sensor.extra_state_attributes
        assert attrs["estado"] == "ok"
        assert attrs["ahorro_estimado"] == 0.6
        assert attrs["soc_previsto_pct"] == 55.0
        assert attrs["potencia_bateria_w"] == 300.0

    def test_sin_plan(self, coordinador):
        coordinador.plan = None
        sensor = SensorPlan(coordinador)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {"estado": "sin_plan"}

    @pytest.mark.asyncio
    async def test_callback_registrado_al_añadir(self, coordinador):
        sensor = SensorPlan(coordinador)
        await sensor.async_added_to_hass()

        coordinador.async_registrar_callback.assert_called_once()

        await sensor.async_will_remove_from_hass()
        # el cancelador devuelto se invoca al quitar la entidad
        cancelador = coordinador.async_registrar_callback.return_value
        cancelador.assert_called_once()


class TestSensorAhorroEstimado:
    def test_native_value(self, coordinador):
        sensor = SensorAhorroEstimadoHoy(coordinador)

        assert sensor.native_value == pytest.approx(0.6)
        assert sensor.unique_id == (
            "optimizador_bateria_omie_ahorro_estimado_hoy"
        )
        assert sensor.extra_state_attributes["teórico"] is True

    def test_sin_plan_none(self, coordinador):
        coordinador.plan = None
        sensor = SensorAhorroEstimadoHoy(coordinador)

        assert sensor.native_value is None


class TestSwitchFestivoHoy:
    def test_estado_inicial(self, coordinador):
        switch = SwitchFestivoHoy(coordinador)

        assert switch.is_on is False
        assert switch.unique_id == "optimizador_bateria_omie_festivo_hoy"

    @pytest.mark.asyncio
    async def test_turn_on_replanifica(self, coordinador):
        switch = SwitchFestivoHoy(coordinador)

        await switch.async_turn_on()

        coordinador.async_set_festivo_hoy.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_turn_off_replanifica(self, coordinador):
        switch = SwitchFestivoHoy(coordinador)

        await switch.async_turn_off()

        coordinador.async_set_festivo_hoy.assert_awaited_once_with(False)
class TestPlanAtributoCompacto:
    """0.15.2: el atributo ``plan`` debe caber en el recorder (16 KB).

    Con horizonte de 2 días (hasta 190 tramos pendientes a medianoche)
    el ``asdict`` completo triplicaba el límite y el recorder dejaba
    de guardar atributos (producción 24-nov)."""

    def _tramo(self, tramo: int):
        from datetime import date

        from custom_components.optimizador_bateria_omie.planificador import (
            PlanTramo as _PlanTramo,
        )

        return _PlanTramo(
            fecha=date(2026, 11, 24),
            tramo=tramo % 96,
            hora=f"{tramo % 96 // 4:02d}:{(tramo % 96) % 4 * 15:02d}",
            accion="AUTOCONSUMO",
            potencia_bateria_w=300.0,
            potencia_carga_red_w=2450.0,
            potencia_venta_red_w=4096.0,
            soc_previsto_pct=55.0,
            precio_referencia_eur_kwh=0.123456,
            ahorro_tramo_eur=0.065432,
        )

    def test_plan_grande_cabe_en_recorder(self):
        import json

        from custom_components.optimizador_bateria_omie.entidades_plan import (
            MAX_TRAMOS_ATRIBUTO,
            _plan_para_atributos,
        )

        # El peor caso (190 tramos pendientes de 2 días) se corta al
        # tope y serializado cabe en el límite del recorder
        resultado = _plan_para_atributos(
            [self._tramo(i) for i in range(190)]
        )
        assert len(resultado) == MAX_TRAMOS_ATRIBUTO
        tamano = len(
            json.dumps(resultado, ensure_ascii=False, default=str).encode()
        )
        assert tamano < 16384, f"atributo plan: {tamano} bytes"

    def test_campos_esenciales_presentes(self):
        from custom_components.optimizador_bateria_omie.entidades_plan import (
            _plan_para_atributos,
        )

        entrada = [self._tramo(4)]
        compacto = _plan_para_atributos(entrada)[0]

        assert set(compacto) == {
            "hora",
            "accion",
            "potencia_bateria_w",
            "potencia_carga_red_w",
            "potencia_venta_red_w",
            "soc_previsto_pct",
            "precio_referencia_eur_kwh",
        }
