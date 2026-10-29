"""Tests del CoordinadorPlan (F3, U4): replanificación y servicio."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.optimizador_bateria_omie.coordinador_plan import (
    CoordinadorPlan,
    _tramo_actual_index,
)
from custom_components.optimizador_bateria_omie.planificador import (
    ParametrosPlanner,
    PlanificadorGreedy,
)
from custom_components.optimizador_bateria_omie.predictor_consumo import (
    PercentilesTramo,
    PrediccionDia,
)


def _percentiles(p50: float) -> PercentilesTramo:
    return PercentilesTramo(p10=p50 * 0.5, p50=p50, p90=p50 * 1.5)


def _prediccion(fecha: date) -> PrediccionDia:
    return PrediccionDia(
        fecha=fecha,
        consumo=[_percentiles(300.0)] * 96,
        fv=[_percentiles(0.0)] * 96,
        fuente_fv="perfil",
    )


@pytest.fixture
def hass():
    """Hass mock: async_create_task crea tareas reales del loop."""
    h = MagicMock()

    def _ejecutar(coro):
        return asyncio.get_running_loop().create_task(coro)

    h.async_create_task = MagicMock(side_effect=_ejecutar)
    return h


async def _drenar(hass, ticks: int = 10):
    """Deja que las tareas creadas terminen (todo resuelve al momento)."""
    for _ in range(ticks):
        await asyncio.sleep(0)


@pytest.fixture
def tracks():
    """Parchea los helpers de eventos de HA a nivel de módulo."""
    with (
        patch(
            "custom_components.optimizador_bateria_omie.coordinador_plan"
            ".async_track_time_change",
            side_effect=lambda *a, **k: MagicMock(),
        ) as t_time,
        patch(
            "custom_components.optimizador_bateria_omie.coordinador_plan"
            ".async_track_state_change_event",
            side_effect=lambda *a, **k: MagicMock(),
        ) as t_state,
    ):
        yield SimpleNamespace(time=t_time, state=t_state)


@pytest.fixture
def coordinador_precio():
    c = MagicMock()
    c.datos.precios_hoy = [
        MagicMock(
            precio_eur_kwh=0.1,
            fecha_hora_utc=datetime.now(tz=UTC)
            + timedelta(minutes=15 * i),
        )
        for i in range(96)
    ]
    c.datos.precios_manana = None
    c.modelo_tarifa.obtener_precio_efectivo = lambda p, f: p
    c.async_add_listener = MagicMock(return_value=MagicMock())
    return c


@pytest.fixture
def coordinador_telemetria():
    c = MagicMock()
    c.data.soc = 50.0
    return c


@pytest.fixture
def predictor(hass):
    p = MagicMock()
    p.predecir = AsyncMock(
        side_effect=lambda fecha, festivo_hoy=False: _prediccion(fecha)
    )
    p.recalcular_perfil = AsyncMock()
    return p


@pytest.fixture
def coordinador(
    hass, coordinador_precio, coordinador_telemetria, predictor, tracks
):
    entry = MagicMock()
    entry.data = {}
    return CoordinadorPlan(
        hass=hass,
        entry=entry,
        coordinador_precio=coordinador_precio,
        coordinador_telemetria=coordinador_telemetria,
        predictor=predictor,
        planificador=PlanificadorGreedy(ParametrosPlanner()),
    )





class TestSetup:
    @pytest.mark.asyncio
    async def test_setup_calcula_plan_y_registra_listeners(
        self, coordinador, hass, coordinador_precio, tracks
    ):
        await coordinador.async_setup()

        assert coordinador.plan is not None
        assert len(coordinador.plan.tramos) == 96
        assert coordinador.plan.estado == "ok"
        coordinador_precio.async_add_listener.assert_called_once()
        assert tracks.time.call_count == 2  # cambio de tramo + 04:35
        # Sin entity_id de SOC no se suscribe a desviaciones
        tracks.state.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_con_entity_id_soc(
        self, coordinador, hass, coordinador_telemetria, tracks
    ):
        coordinador.entry.data = {"entity_id_soc_bateria": "sensor.soc"}
        await coordinador.async_setup()

        tracks.state.assert_called_once()

    @pytest.mark.asyncio
    async def test_precios_nuevos_recalculan(self, coordinador, hass):
        await coordinador.async_setup()
        plan_antes = coordinador.plan

        listener = (
            coordinador.coordinador_precio.async_add_listener.call_args[0][0]
        )
        listener()
        await _drenar(hass)

        assert coordinador.plan is not plan_antes


class TestReplanificacion:
    @pytest.mark.asyncio
    async def test_cambio_tramo_recalcula(self, coordinador, hass, tracks):
        await coordinador.async_setup()
        plan_antes = coordinador.plan

        callback_tramo = tracks.time.call_args_list[0].args[1]
        callback_tramo(datetime.now())
        await _drenar(hass)

        assert coordinador.plan is not plan_antes

    @pytest.mark.asyncio
    async def test_desviacion_soc_replanifica(
        self, coordinador, hass, coordinador_telemetria, tracks
    ):
        """SOC real ±10 % fuera del previsto → recálculo."""
        coordinador.entry.data = {"entity_id_soc_bateria": "sensor.soc"}
        await coordinador.async_setup()
        await _drenar(hass)
        plan_antes = coordinador.plan

        callback_soc = tracks.state.call_args[0][2]
        evento = MagicMock()
        evento.data = {"new_state": MagicMock()}
        evento.data["new_state"].state = (
            str(coordinador.tramo_actual().soc_previsto_pct + 20)
        )
        callback_soc(evento)
        await _drenar(hass)

        assert coordinador.plan is not plan_antes

    @pytest.mark.asyncio
    async def test_soc_dentro_de_umbral_no_replanifica(
        self, coordinador, hass, tracks
    ):
        coordinador.entry.data = {"entity_id_soc_bateria": "sensor.soc"}
        await coordinador.async_setup()
        await _drenar(hass)
        plan_antes = coordinador.plan

        callback_soc = tracks.state.call_args[0][2]
        evento = MagicMock()
        evento.data = {"new_state": MagicMock()}
        evento.data["new_state"].state = (
            str(coordinador.tramo_actual().soc_previsto_pct + 3)
        )
        callback_soc(evento)
        await _drenar(hass)

        assert coordinador.plan is plan_antes

    @pytest.mark.asyncio
    async def test_perfil_diario_0435(
        self, coordinador, hass, predictor, tracks
    ):
        """El job de las 04:35 recalcula el perfil y luego el plan."""
        await coordinador.async_setup()
        await _drenar(hass)

        callback_perfil = tracks.time.call_args_list[1].args[1]
        callback_perfil(datetime.now())
        await _drenar(hass)

        predictor.recalcular_perfil.assert_awaited()

    @pytest.mark.asyncio
    async def test_festivo_hoy_recalcula_con_tipo_festivo(
        self, coordinador, hass, predictor
    ):
        await coordinador.async_setup()
        await _drenar(hass)

        await coordinador.async_set_festivo_hoy(True)

        assert coordinador.festivo_hoy is True
        # predecir recibió festivo_hoy=True
        assert any(
            kwargs.get("festivo_hoy") is True
            for args, kwargs in predictor.predecir.call_args_list
        )

    @pytest.mark.asyncio
    async def test_festivo_hoy_idempotente(self, coordinador, predictor):
        await coordinador.async_set_festivo_hoy(False)
        assert coordinador.festivo_hoy is False
        # No replanifica si el valor no cambia
        assert predictor.predecir.await_count == 0


class TestAccesores:
    @pytest.mark.asyncio
    async def test_tramo_actual_y_plan_actual(self, coordinador):
        await coordinador.async_setup()

        tramo = coordinador.tramo_actual()
        hoy = datetime.now().astimezone().date()
        assert tramo is not None
        assert tramo.fecha == hoy
        assert tramo.tramo == _tramo_actual_index()

    @pytest.mark.asyncio
    async def test_ahorro_estimado_hoy(self, coordinador):
        await coordinador.async_setup()

        ahorro = coordinador.ahorro_estimado_hoy()
        hoy = datetime.now().astimezone().date()
        esperado = sum(
            t.ahorro_tramo_eur
            for t in coordinador.plan.tramos
            if t.fecha == hoy
        )
        assert ahorro == pytest.approx(esperado, abs=1e-4)

    @pytest.mark.asyncio
    async def test_callback_de_entidades(self, coordinador):
        llamada = MagicMock()
        coordinador.async_registrar_callback(llamada)

        await coordinador.async_recalcular("test")

        llamada.assert_called_once()

    @pytest.mark.asyncio
    async def test_descargar_cancela_listeners(self, coordinador, hass, tracks):
        await coordinador.async_setup()
        canceladores = list(coordinador._canceladores)
        assert canceladores

        await coordinador.async_descargar()

        for cancelar in canceladores:
            cancelar.assert_called_once()
        assert coordinador._canceladores == []


class TestRegenerar:
    @pytest.mark.asyncio
    async def test_regenerar_devuelve_resumen(self, coordinador):
        resultado = await coordinador.async_regenerar()

        assert resultado["recalculado"] is True
        assert resultado["ahorro_estimado"] == pytest.approx(
            coordinador.plan.ahorro_estimado_eur
        )
        assert resultado["accion_actual"] in (
            "AUTOCONSUMO",
            "CARGA_RED",
            "VENTA_RED",
            "MANTENER",
        )

    @pytest.mark.asyncio
    async def test_regenerar_en_rafaga_idempotente(self, coordinador):
        """El plan es barato de calcular: dos llamadas seguidas OK."""
        await coordinador.async_regenerar()
        plan_1 = coordinador.plan
        await coordinador.async_regenerar()
        plan_2 = coordinador.plan

        assert len(plan_1.tramos) == len(plan_2.tramos)
        assert [t.accion for t in plan_1.tramos] == [
            t.accion for t in plan_2.tramos
        ]


class TestSinManana:
    @pytest.mark.asyncio
    async def test_con_precios_manana_192_tramos(
        self, coordinador, coordinador_precio
    ):
        coordinador_precio.datos.precios_manana = [
            MagicMock(
                precio_eur_kwh=0.12,
                fecha_hora_utc=datetime.now(tz=UTC)
                + timedelta(days=1, minutes=15 * i),
            )
            for i in range(96)
        ]
        await coordinador.async_setup()

        assert len(coordinador.plan.tramos) == 192

    @pytest.mark.asyncio
    async def test_sin_soc_telemetria_estado_sin_telemetria(
        self, coordinador, coordinador_telemetria
    ):
        coordinador_telemetria.data = None
        await coordinador.async_setup()

        assert coordinador.plan.estado == "sin_telemetria"
        assert coordinador.tramo_actual() is None
