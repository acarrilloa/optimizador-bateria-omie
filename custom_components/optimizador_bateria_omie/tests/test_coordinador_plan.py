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
    # F6-U1: los avisos persistentes se llaman en varios recálculos
    h.services.async_call = AsyncMock()
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
    c.modelo_tarifa.obtener_precio_efectivo = lambda p, f, festivo_manual=False: p
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
        ahora = datetime.now()
        callback_tramo(ahora.replace(minute=0, second=0, microsecond=0))
        await _drenar(hass)

        assert coordinador.plan is not plan_antes

    @pytest.mark.asyncio
    async def test_cambio_tramo_dentro_de_bloque_no_recalcula(
        self, coordinador, hass, tracks
    ):
        """D-F5.3.1: dentro del bloque de compromiso no se redecide."""
        await coordinador.async_setup()
        plan_antes = coordinador.plan

        callback_tramo = tracks.time.call_args_list[0].args[1]
        ahora = datetime.now()
        callback_tramo(ahora.replace(minute=15, second=0, microsecond=0))
        await _drenar(hass)

        assert coordinador.plan is plan_antes

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


def _fijar_reloj(minutos_del_dia: int):
    """Parcha ``datetime`` del módulo: HOY con los minutos indicados.

    Los límites del bloque dependen del reloj pared: con el reloj fijo
    en un punto DENTRO del bloque (p. ej. 22:30 con bloque 60) las
    pruebas de compromiso no dependen de la hora real de ejecución.
    """
    hoy = datetime.now().astimezone()
    fija = hoy.replace(
        hour=minutos_del_dia // 60,
        minute=minutos_del_dia % 60,
        second=0,
        microsecond=0,
    )

    class _Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fija
            return fija if tz is UTC or tz is None else fija.astimezone(tz)

    return patch(
        "custom_components.optimizador_bateria_omie.coordinador_plan"
        ".datetime",
        _Reloj,
    )


class TestBloqueCompromiso:
    """D-F5.3.1: el bloque en curso se congela salvo replan inmediato."""

    @pytest.mark.asyncio
    async def test_bloque_minutos_default_y_validacion(self, coordinador):
        assert coordinador.bloque_minutos() == 60  # sin configurar

        coordinador.entry.data = {"bloque_minutos": 30}
        assert coordinador.bloque_minutos() == 30

        # No múltiplo de 15 / fuera de rango → default
        for invalido in (7, 45 + 15 + 1, 121, 135, "sesenta"):
            coordinador.entry.data = {"bloque_minutos": invalido}
            assert coordinador.bloque_minutos() == 60

    @pytest.mark.asyncio
    async def test_proximo_limite_bloque(self, coordinador):
        with _fijar_reloj(22 * 60 + 30):  # 22:30 → próximo límite 23:00
            limite = coordinador.proximo_limite_bloque()
            assert (limite.hour, limite.minute) == (23, 0)

    @pytest.mark.asyncio
    async def test_precios_mid_bloque_congela_bloque_en_curso(
        self, coordinador
    ):
        """Un replan reactivo dentro del bloque exporta los tramos previos."""
        await coordinador.async_setup()
        plan_previo = coordinador.plan
        coordinador.coordinador_telemetria.data.soc = 90.0

        with _fijar_reloj(22 * 60 + 30):  # 22:30: bloque [22:00, 23:00)
            plan_nuevo = await coordinador.async_recalcular("precios")

        indice = 22 * 4  # 22:00
        assert plan_nuevo.tramos[indice] is plan_previo.tramos[indice]
        indice_fin = 23 * 4  # 23:00: fuera del bloque → tramo nuevo
        assert plan_nuevo.tramos[indice_fin] is not (
            plan_previo.tramos[indice_fin]
        )
        assert any("bloque en curso congelado" in m for m in (
            plan_nuevo.motivos
        ))

    @pytest.mark.asyncio
    async def test_desviacion_soc_replanifica_inmediato(
        self, coordinador
    ):
        """D-F5.3.1: desviación de SOC = motivo inmediato, sin congelar."""
        await coordinador.async_setup()
        plan_previo = coordinador.plan
        coordinador.coordinador_telemetria.data.soc = 90.0

        with _fijar_reloj(22 * 60 + 30):
            plan_nuevo = await coordinador.async_recalcular("desviacion_soc")

        indice = 22 * 4
        assert plan_nuevo.tramos[indice] is not (
            plan_previo.tramos[indice]
        )

    @pytest.mark.asyncio
    async def test_replan_en_limite_exacto_no_congela(self, coordinador):
        """En el límite de bloque (X:00 con bloque 60) manda el plan nuevo."""
        await coordinador.async_setup()
        plan_previo = coordinador.plan
        coordinador.coordinador_telemetria.data.soc = 90.0

        with _fijar_reloj(23 * 60):  # 23:00: límite exacto
            plan_nuevo = await coordinador.async_recalcular("precios")

        indice = 23 * 4
        assert plan_nuevo.tramos[indice] is not (
            plan_previo.tramos[indice]
        )

    @pytest.mark.asyncio
    async def test_sin_soc_con_plan_previo_lo_congela(
        self, coordinador, coordinador_telemetria
    ):
        """D-F5.3.3: el gap transitorio de SOC no derriba el plan."""
        await coordinador.async_setup()
        plan_previo = coordinador.plan
        assert plan_previo.tramos  # hay plan real

        coordinador_telemetria.data = None
        plan_resultado = await coordinador.async_recalcular("tramo")

        assert plan_resultado is plan_previo  # congelado, no derribado


class TestAvisosDatos:
    """F6-U1: avisos persistentes de datos (D+1 y forecast ML)."""

    def _creaciones(self, hass):
        """Llamadas async_call a persistent_notification.create."""
        return [
            c
            for c in hass.services.async_call.call_args_list
            if c.args[:2] == ("persistent_notification", "create")
        ]

    @pytest.mark.asyncio
    async def test_aviso_sin_precios_manana_pasadas_las_1600(
        self, coordinador, hass, coordinador_precio
    ):
        assert coordinador_precio.datos.precios_manana is None

        with _fijar_reloj(16 * 60 + 30):
            await coordinador.async_recalcular("tramo")
            await coordinador.async_recalcular("tramo")

        # Se avisa UNA sola vez al día (idempotente)
        creaciones = self._creaciones(hass)
        assert len(creaciones) == 1
        assert creaciones[0].args[2]["notification_id"] == (
            "optimizador_bateria_omie_sin_precios_manana"
        )

    @pytest.mark.asyncio
    async def test_sin_aviso_antes_de_las_1600(
        self, coordinador, hass, coordinador_precio
    ):
        with _fijar_reloj(14 * 60):
            await coordinador.async_recalcular("tramo")

        assert self._creaciones(hass) == []

    @pytest.mark.asyncio
    async def test_llegada_de_precios_retira_el_aviso(
        self, coordinador, hass, coordinador_precio
    ):
        with _fijar_reloj(16 * 60 + 30):
            await coordinador.async_recalcular("tramo")
        assert len(self._creaciones(hass)) == 1

        # Llegan los precios de mañana → dismiss del aviso
        coordinador_precio.datos.precios_manana = [
            MagicMock(
                precio_eur_kwh=0.12,
                fecha_hora_utc=datetime.now(tz=UTC)
                + timedelta(days=1, minutes=15 * i),
            )
            for i in range(96)
        ]
        await coordinador.async_recalcular("tramo")
        despidos = [
            c
            for c in hass.services.async_call.call_args_list
            if c.args[:2] == ("persistent_notification", "dismiss")
        ]
        assert len(despidos) == 1
        assert despidos[0].args[2]["notification_id"] == (
            "optimizador_bateria_omie_sin_precios_manana"
        )

    @pytest.mark.asyncio
    async def test_flip_de_adopcion_ml_notifica(
        self, coordinador, hass, predictor
    ):
        coordinador._adoptado_previo = True
        predictor.metricas_ml = {"adoptado": True}
        await coordinador._async_aviso_ml()
        assert self._creaciones(hass) == []

        predictor.metricas_ml = {"adoptado": False}
        await coordinador._async_aviso_ml()
        creaciones = [
            c
            for c in self._creaciones(hass)
            if c.args[2]["notification_id"]
            == "optimizador_bateria_omie_ml_adopcion"
        ]
        assert len(creaciones) == 1
        assert "fuente de forecast" in creaciones[0].args[2]["title"]

    @pytest.mark.asyncio
    async def test_reentrenamiento_fallido_notifica_una_vez(
        self, coordinador, hass, predictor
    ):
        predictor.async_reentrenar_ml = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        await coordinador._async_perfil_y_recalcular("perfil_diario")
        await coordinador._async_perfil_y_recalcular("perfil_diario")

        creaciones = [
            c
            for c in self._creaciones(hass)
            if c.args[2]["notification_id"]
            == "optimizador_bateria_omie_ml_reentrenamiento"
        ]
        assert len(creaciones) == 1
        assert "boom" in creaciones[0].args[2]["message"]
