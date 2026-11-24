"""Tests del CoordinadorPlan (F3, U4): replanificación y servicio."""

import asyncio
import logging
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
    # 0.15.2: el LP resuelve vía executor job (PuLP bloquea el loop)
    h.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args: fn(*args)
    )
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
        # cambio de tramo + 04:35 + check de cobertura (F6/B, cada 30 min)
        assert tracks.time.call_count == 3
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
import pytest

from custom_components.optimizador_bateria_omie.const import (
    CONF_ENTITY_ID_SOC_BATERIA,
)
from custom_components.optimizador_bateria_omie.planificador import (
    ACCION_CARGA_RED,
    ACCION_MANTENER,
    PlanTramo,
)
from custom_components.optimizador_bateria_omie.predictor_consumo import (
    PercentilesTramo,
)


def _tramo_plan(idx: int, accion: str) -> PlanTramo:
    return PlanTramo(
        fecha=date.today(),
        tramo=idx,
        hora=f"{idx * 15 // 60:02d}:{idx * 15 % 60:02d}",
        accion=accion,
        potencia_bateria_w=0.0,
        potencia_carga_red_w=2450.0 if accion == ACCION_CARGA_RED else 0.0,
        potencia_venta_red_w=0.0,
        soc_previsto_pct=50.0,
        precio_referencia_eur_kwh=0.08,
        ahorro_tramo_eur=0.0,
    )


def _plan_con_valle_a_las_14() -> SimpleNamespace:
    """Plan: MANTENER hasta las 14:00 y CARGA_RED después."""
    return SimpleNamespace(
        tramos=[
            _tramo_plan(i, ACCION_CARGA_RED if i >= 56 else ACCION_MANTENER)
            for i in range(96)
        ]
    )


def _telemetria_simple(soc: float, consumo: float = 300.0) -> SimpleNamespace:
    return SimpleNamespace(
        soc=soc, consumo_casa=consumo, produccion_fv=0.0
    )


class TestCoberturaU1:
    """F6/B: check de cobertura hasta el próximo tramo barato (U1)."""

    @pytest.mark.asyncio
    async def test_deficit_replan_inmediato(
        self, coordinador, coordinador_telemetria, hass
    ):
        # Caso U1 emulado: SOC 15 % a las 09:00, valle a las 14:00.
        # Consumo previsto: 300 W → 19 tramos netos ≈ 1425 Wh, SOC =
        # 2304 Wh < margen default (20 % de 15,36 kWh = 3072 Wh)
        hoy = datetime.now().astimezone().date()
        coordinador._plan = _plan_con_valle_a_las_14()
        coordinador._prediccion_hoy = _prediccion(hoy)
        coordinador_telemetria.data = _telemetria_simple(15.0)
        # Sin desviación: el respaldo 0.15.1 no debe disparar antes
        for t in coordinador._plan.tramos:
            t.soc_previsto_pct = 15.0

        with patch.object(
            coordinador, "async_recalcular", AsyncMock()
        ) as recalc, _fijar_reloj(9 * 60):
            await coordinador._async_check_cobertura()

        recalc.assert_awaited_once_with("cobertura_insuficiente")
        # Notificación persistente del episodio
        llamada = hass.services.async_call.call_args
        assert llamada.args[0] == "persistent_notification"

    @pytest.mark.asyncio
    async def test_sin_deficit_no_replan(
        self, coordinador, coordinador_telemetria
    ):
        hoy = datetime.now().astimezone().date()
        coordinador._plan = _plan_con_valle_a_las_14()
        coordinador._prediccion_hoy = _prediccion(hoy)
        # SOC 80 %: 12288 Wh − 1425 Wh ≫ margen (y sin desviación)
        coordinador_telemetria.data = _telemetria_simple(80.0)
        for t in coordinador._plan.tramos:
            t.soc_previsto_pct = 80.0

        with patch.object(
            coordinador, "async_recalcular", AsyncMock()
        ) as recalc, _fijar_reloj(9 * 60):
            await coordinador._async_check_cobertura()

        recalc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ritmo_excesivo_replan_tras_dos_muestras(
        self, coordinador, coordinador_telemetria
    ):
        hoy = datetime.now().astimezone().date()
        coordinador._plan = _plan_con_valle_a_las_14()
        coordinador._prediccion_hoy = _prediccion(hoy)
        # SOC holgado pero consumo real 2000 W ≫ 1,3 × 300 W previstos
        coordinador_telemetria.data = _telemetria_simple(80.0, consumo=2000.0)
        for t in coordinador._plan.tramos:
            t.soc_previsto_pct = 80.0

        with patch.object(
            coordinador, "async_recalcular", AsyncMock()
        ) as recalc, _fijar_reloj(9 * 60):
            await coordinador._async_check_cobertura()
            recalc.assert_not_awaited()  # primera muestra: solo cuenta
            await coordinador._async_check_cobertura()
            recalc.assert_awaited_once_with("cobertura_insuficiente")

    @pytest.mark.asyncio
    async def test_cooldown_no_replica_replan(
        self, coordinador, coordinador_telemetria
    ):
        hoy = datetime.now().astimezone().date()
        coordinador._plan = _plan_con_valle_a_las_14()
        coordinador._prediccion_hoy = _prediccion(hoy)
        coordinador_telemetria.data = _telemetria_simple(15.0)
        # Sin desviación: el respaldo 0.15.1 no debe disparar antes
        for t in coordinador._plan.tramos:
            t.soc_previsto_pct = 15.0

        with patch.object(
            coordinador, "async_recalcular", AsyncMock()
        ) as recalc, _fijar_reloj(9 * 60):
            await coordinador._async_check_cobertura()
            await coordinador._async_check_cobertura()  # < 45 min

        assert recalc.await_count == 1

    def _dataclass_guard(self):
        # Las importaciones de PercentilesTramo evitan lint de no-uso
        assert PercentilesTramo is not None


class TestBug2EntidadSoc:
    """F6/B (bug 2) + 0.15.2: la escucha se registra SIEMPRE (la
    carrera del arranque la mataba si Solarman aún no había creado la
    entidad); `replan_soc_activo` refleja existencia y se reevalúa en
    el check periódico."""

    @pytest.mark.asyncio
    async def test_entidad_ausente_al_arranque_escucha_registrada(
        self, coordinador, hass, tracks
    ):
        hass.states.get = MagicMock(return_value=None)
        coordinador.entry.data = {
            CONF_ENTITY_ID_SOC_BATERIA: "sensor.soc_tarde"
        }

        await coordinador.async_setup()

        # Escucha registrada igualmente (la entidad aparecerá luego)
        tracks.state.assert_called_once()
        assert coordinador.replan_soc_activo is False

    @pytest.mark.asyncio
    async def test_entidad_aparece_habilita_replan_perezoso(
        self, coordinador, hass
    ):
        hass.states.get = MagicMock(return_value=None)
        coordinador.entry.data = {
            CONF_ENTITY_ID_SOC_BATERIA: "sensor.soc_tarde"
        }
        await coordinador.async_setup()

        # La entidad aparece tras el arranque → el check la activa
        hass.states.get = MagicMock(return_value=MagicMock())
        coordinador.coordinador_telemetria.data = SimpleNamespace(
            soc=50.0, consumo_casa=300.0, produccion_fv=0.0, soc_ts=None
        )
        await coordinador._async_check_cobertura()

        assert coordinador.replan_soc_activo is True

    @pytest.mark.asyncio
    async def test_entidad_existente_activa_replan(
        self, coordinador, hass, tracks
    ):
        hass.states.get = MagicMock(return_value=MagicMock())
        coordinador.entry.data = {
            CONF_ENTITY_ID_SOC_BATERIA: "sensor.soc_vivo"
        }

        await coordinador.async_setup()

        assert coordinador.replan_soc_activo is True
        tracks.state.assert_called_once()
class TestRespaldoDesviacion0151:
    """0.15.1: respaldo periódico de la desviación de SOC (entidad
    congelada por caída del datalogger: sin cambios de estado no hay
    eventos y el listener de D11 enmudece)."""

    def _coordinador_con_previsto(self, coordinador, coordinador_telemetria,
                                  previsto: float, soc: float,
                                  soc_ts: datetime | None):
        hoy = datetime.now().astimezone().date()
        plan = SimpleNamespace(
            tramos=[_tramo_plan(i, ACCION_MANTENER) for i in range(96)]
        )
        for t in plan.tramos:
            t.soc_previsto_pct = previsto
        coordinador._plan = plan
        coordinador._prediccion_hoy = _prediccion(hoy)
        coordinador_telemetria.data = SimpleNamespace(
            soc=soc, consumo_casa=300.0, produccion_fv=0.0,
            soc_ts=soc_ts,
        )

    @pytest.mark.asyncio
    async def test_entidad_congelada_dispara_respaldo(
        self, coordinador, coordinador_telemetria, caplog
    ):
        # Sin déficit: el respaldo dispara solo por desviación. El
        # congelado se calcula DENTRO del reloj fijo (edad real 2 h)
        with patch.object(
            coordinador, "async_recalcular", AsyncMock()
        ) as recalc, _fijar_reloj(9 * 60), caplog.at_level(logging.WARNING):
            # Reloj del coordinador: hoy 09:00 (fijar_reloj solo parchea
            # el módulo del coordinador). Congelado = 2 h antes.
            base = datetime.now().astimezone().replace(
                hour=9, minute=0, second=0, microsecond=0
            )
            congelado = base - timedelta(hours=2)
            self._coordinador_con_previsto(
                coordinador, coordinador_telemetria,
                previsto=95.0, soc=60.0, soc_ts=congelado,
            )
            await coordinador._async_check_cobertura()

        recalc.assert_awaited_once_with("desviacion_soc")
        assert "SOC congelado" in caplog.text
        assert "respaldo periódico" in caplog.text

    @pytest.mark.asyncio
    async def test_respaldo_respeta_cooldown(
        self, coordinador, coordinador_telemetria
    ):
        from datetime import UTC as _UTC

        congelado = datetime.now(tz=_UTC)
        self._coordinador_con_previsto(
            coordinador, coordinador_telemetria,
            previsto=95.0, soc=60.0, soc_ts=congelado,
        )
        with patch.object(
            coordinador, "async_recalcular", AsyncMock()
        ) as recalc, _fijar_reloj(9 * 60):
            await coordinador._async_check_cobertura()
            # Segundo check inmediato (< 5 min): cooldown → no repite
            await coordinador._async_check_cobertura()

        assert recalc.await_count == 1

    @pytest.mark.asyncio
    async def test_soc_fresco_sin_desviacion_no_replan(
        self, coordinador, coordinador_telemetria
    ):
        self._coordinador_con_previsto(
            coordinador, coordinador_telemetria,
            previsto=60.0, soc=60.5,
            soc_ts=datetime.now(tz=UTC),
        )
        with patch.object(
            coordinador, "async_recalcular", AsyncMock()
        ) as recalc, _fijar_reloj(9 * 60):
            await coordinador._async_check_cobertura()

        recalc.assert_not_awaited()


class TestEntityIdAvisos:
    """D-F6: los avisos citan el entity_id real, sin prefijos fixes."""

    @staticmethod
    def _registro(entity_id):
        registro = MagicMock()
        registro.async_get_entity_id_from_unique_id.return_value = entity_id
        return registro

    @staticmethod
    def _patch(registro):
        return patch(
            "custom_components.optimizador_bateria_omie.coordinador_plan"
            ".er.async_get",
            return_value=registro,
        )

    def test_resuelve_desde_el_registry(self, coordinador):
        registro = self._registro("sensor.mi_entry_estado_de_control")
        with self._patch(registro):
            resultado = coordinador._entity_id(
                "optimizador_bateria_omie_estado_control"
            )

        assert resultado == "sensor.mi_entry_estado_de_control"
        registro.async_get_entity_id_from_unique_id.assert_called_once_with(
            "optimizador_bateria_omie_estado_control"
        )

    def test_fallback_si_no_registrada(self, coordinador):
        with self._patch(self._registro(None)):
            resultado = coordinador._entity_id(
                "optimizador_bateria_omie_estado_control"
            )

        assert resultado == (
            "sensor.optimizador_bateria_omie_estado_control"
        )
