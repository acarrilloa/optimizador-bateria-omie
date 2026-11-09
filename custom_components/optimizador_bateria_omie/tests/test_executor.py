"""Tests del EjecutorControl (F4, U3): traducción, dry-run y límites.

Sin hardware ni red: el escritor es un mock inyectado y las direcciones
salen del registers.yaml empaquetado (nunca hardcodeadas).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.optimizador_bateria_omie.executor import (
    EjecutorControl,
    ErrorEscrituraSolarman,
    EscritorSolarman,
    _extraer_registros,
)
from custom_components.optimizador_bateria_omie.mapa_registros import (
    MapaRegistros,
)
from custom_components.optimizador_bateria_omie.planificador import (
    ACCION_AUTOCONSUMO,
    ACCION_CARGA_RED,
    ACCION_MANTENER,
    ACCION_VENTA_RED,
    PlanTramo,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    TelemetriaInversor,
)

HOY = datetime.now().astimezone().date()
VOLTAJE = 51.2  # default


def _tramo(
    accion: str,
    carga_red_w: float = 2450.0,
    venta_w: float = 0.0,
) -> PlanTramo:
    return PlanTramo(
        fecha=HOY,
        tramo=10,
        hora="02:30",
        accion=accion,
        potencia_bateria_w=0.0,
        potencia_carga_red_w=carga_red_w,
        potencia_venta_red_w=venta_w,
        soc_previsto_pct=50.0,
        precio_referencia_eur_kwh=0.08,
        ahorro_tramo_eur=0.0,
    )


@pytest.fixture
def hass(tmp_path):
    """Hass mock: el executor ejecuta la función directamente."""
    h = MagicMock()
    h.config.config_dir = str(tmp_path)
    h.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args: fn(*args)
    )
    return h


@pytest.fixture
def mapa(hass):
    m = MapaRegistros(hass)
    m.cargar()
    return m


@pytest.fixture(autouse=True)
def _sin_espera_verify():
    """El write-verify espera latencia_verify_s: sin esperas en tests."""
    with patch(
        "custom_components.optimizador_bateria_omie.executor.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        yield


@pytest.fixture
def escritor():
    """Escritor fake: escribir registra el valor y leer lo devuelve."""
    w = MagicMock()
    w._leidos: dict[int, int] = {}

    async def _escribir(addr, valor):
        w._leidos[addr] = valor

    w.escribir = AsyncMock(side_effect=_escribir)
    w.escribir_bloque = AsyncMock()
    w.leer = AsyncMock(
        side_effect=lambda addr, cantidad=1: [w._leidos.get(addr, 0)]
    )
    return w


def _telemetria(
    soc: float = 50.0,
    edad_s: float = 0.0,
    consumo: float = 300.0,
    fv: float = 0.0,
    pbat: float = 0.0,
) -> TelemetriaInversor:
    """Telemetría válida con ts_actualizacion reciente (watchdog OK)."""
    return TelemetriaInversor(
        soc=soc,
        potencia_bateria=pbat,
        corriente_bateria=0.0,
        consumo_casa=consumo,
        produccion_fv=fv,
        temperatura_inversor=30.0,
        temperatura_bateria=25.0,
        ts_actualizacion=datetime.now(tz=UTC) - timedelta(seconds=edad_s),
    )


def _coordinador_con_tramo(tramo, telemetria=None, periodo="valle"):
    c = MagicMock()
    c.tramo_actual = MagicMock(return_value=tramo)
    c.async_registrar_callback = MagicMock(return_value=MagicMock())
    c.data = telemetria if telemetria is not None else _telemetria()
    # Período 2.0TD del instante actual (para el modo gradual, D11)
    c.coordinador_precio.modelo_tarifa.obtener_periodo.return_value = periodo
    return c


def _executor(
    hass, mapa, escritor, tramo, entry_data=None, telemetria=None,
    periodo="valle",
) -> EjecutorControl:
    entry = MagicMock()
    entry.data = dict(entry_data or {})
    coordinador = _coordinador_con_tramo(tramo, telemetria, periodo)
    return EjecutorControl(
        hass=hass,
        entry=entry,
        mapa=mapa,
        coordinador_plan=coordinador,
        coordinador_telemetria=coordinador,
        escritor=escritor,
    )


def _escrituras(escritor) -> dict[int, int]:
    """Llamadas a escribir como dict addr → valor (última por addr)."""
    resultado = {}
    for llamada in escritor.escribir.call_args_list:
        addr, valor = llamada.args
        resultado[addr] = valor
    return resultado


class TestTraduccion:
    """D3: acción → registros con direcciones del YAML."""

    @pytest.mark.asyncio
    async def test_carga_red(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escrituras = _escrituras(escritor)
        # Direcciones del registers.yaml empaquetado
        assert escrituras[210] == round(5000.0 / VOLTAJE)  # 210 constante
        assert escrituras[230] == round(2450.0 / VOLTAJE)
        assert escrituras[232] == 1

    @pytest.mark.asyncio
    async def test_autoconsumo_valores_seguros(self, hass, mapa, escritor):
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_AUTOCONSUMO),
            entry_data={"modo_activacion": "completo"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escrituras = _escrituras(escritor)
        assert escrituras[210] == round(5000.0 / VOLTAJE)
        assert escrituras[230] == 0
        assert escrituras[232] == 0
        # AUTOCONSUMO no escribe la corriente de descarga
        assert 211 not in escrituras

    @pytest.mark.asyncio
    async def test_venta_red(self, hass, mapa, escritor):
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_VENTA_RED, venta_w=2048.0),
            entry_data={"modo_activacion": "completo"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escrituras = _escrituras(escritor)
        assert escrituras[211] == round(2048.0 / VOLTAJE)
        assert escrituras[230] == 0
        assert escrituras[232] == 0
        assert escrituras[210] == round(5000.0 / VOLTAJE)

    @pytest.mark.asyncio
    async def test_mantener_no_escribe(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_MANTENER))
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escritor.escribir.assert_not_called()


class TestLimite210:
    """Corrección D3: 210 constante = potencia_max_carga en A."""

    @pytest.mark.asyncio
    async def test_210_nunca_por_debajo_del_limite(self, hass, mapa, escritor):
        """210 vale la corriente de potencia_max_carga aunque el tramo
        pida menos (y nunca la corriente del tramo)."""
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED, carga_red_w=512.0),
            entry_data={"potencia_max_carga": 3000.0},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escrituras = _escrituras(escritor)
        equivalente = round(3000.0 / VOLTAJE)  # 59
        assert escrituras[210] == equivalente
        assert escrituras[210] >= equivalente  # nunca por debajo
        assert escrituras[230] == round(512.0 / VOLTAJE)  # 10

    @pytest.mark.asyncio
    async def test_210_sigue_al_parametro(self, hass, mapa, escritor):
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_AUTOCONSUMO),
            entry_data={
                "potencia_max_carga": 4000.0,
                "modo_activacion": "completo",
            },
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert _escrituras(escritor)[210] == round(4000.0 / VOLTAJE)

    @pytest.mark.asyncio
    async def test_corrientes_fueras_de_limites_recortadas(
        self, hass, mapa, escritor
    ):
        """Nunca una corriente fuera de [min, max] del YAML (0-135)."""
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED, carga_red_w=90000.0),
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escrituras = _escrituras(escritor)
        assert escrituras[230] == 135  # clamped al max del YAML
        assert escrituras[210] == round(5000.0 / VOLTAJE)  # constante

    @pytest.mark.asyncio
    async def test_descarga_recortada_al_limite(self, hass, mapa, escritor):
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_VENTA_RED, venta_w=80000.0),
            entry_data={"modo_activacion": "completo"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert _escrituras(escritor)[211] == 135


class TestDryRun:
    """control_activo OFF → ninguna escritura real."""

    @pytest.mark.asyncio
    async def test_control_off_ninguna_escritura(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))

        await executor.async_aplicar_tramo("test")

        escritor.escribir.assert_not_called()
        assert executor.control_activo is False
        assert executor.estado == "inactivo"

    @pytest.mark.asyncio
    async def test_sin_tramo_de_plan_no_hace_nada(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, None)

        await executor.async_aplicar_tramo("test")

        escritor.escribir.assert_not_called()


class TestIdempotencia:
    @pytest.mark.asyncio
    async def test_mismos_objetivos_no_reescribe(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")
        assert escritor.escribir.call_count == 3  # 210, 230, 232

        await executor.async_aplicar_tramo("test")
        assert escritor.escribir.call_count == 3  # sin nuevas escrituras

    @pytest.mark.asyncio
    async def test_cambio_de_accion_escribe_solo_lo_que_cambia(
        self, hass, mapa, escritor
    ):
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_CARGA_RED, carga_red_w=2450.0)
        )
        executor._control_activo = True
        await executor.async_aplicar_tramo("test")

        # Tramo con otra corriente de red: solo cambia 230
        executor.coordinador_plan.tramo_actual.return_value = _tramo(
            ACCION_CARGA_RED, carga_red_w=1024.0
        )
        await executor.async_aplicar_tramo("test")

        escrituras = _escrituras(escritor)
        assert escrituras[230] == round(1024.0 / VOLTAJE)
        assert escritor.escribir.call_count == 4  # 3 + solo el 230


class TestActivacion:
    @pytest.mark.asyncio
    async def test_activar_escribe_limites_soc(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_MANTENER))

        await executor.async_set_control_activo(True)

        escrituras = _escrituras(escritor)
        # 217 = soc_min (15), 218 = soc_recuperacion (20) del YAML
        assert escrituras[217] == 15
        assert escrituras[218] == 20
        assert escrituras[218] > escrituras[217]
        assert executor.control_activo is True
        assert executor.estado == "activo"

    @pytest.mark.asyncio
    async def test_soc_recuperacion_clamp_sobre_minimo(
        self, hass, mapa, escritor
    ):
        """Validación 2: 218 siempre > 217 (clamp si viene mal)."""
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_MANTENER),
            entry_data={"soc_min": 25.0, "soc_recuperacion": 12.0},
        )

        await executor.async_set_control_activo(True)

        escrituras = _escrituras(escritor)
        assert escrituras[217] == 25
        assert escrituras[218] == 26  # max(12, 25+1)
        assert escrituras[218] > escrituras[217]

    @pytest.mark.asyncio
    async def test_desactivar_no_escribe(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_MANTENER))
        executor._control_activo = True

        await executor.async_set_control_activo(False)

        escritor.escribir.assert_not_called()
        assert executor.estado == "inactivo"

    @pytest.mark.asyncio
    async def test_fallo_limites_soc_aborta_activacion(
        self, hass, mapa, escritor
    ):
        """0.11.3: si la escritura de límites falla, la activación se
        aborta quedando OFF con motivo explícito — nunca switch ON
        con estado congelado en ``inactivo``."""
        escritor.escribir = AsyncMock(
            side_effect=ErrorEscrituraSolarman("dispositivo no resuelto")
        )
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_MANTENER))

        with pytest.raises(ErrorEscrituraSolarman):
            await executor.async_set_control_activo(True)

        assert executor.control_activo is False
        assert executor.estado == "inactivo"
        assert "abortada" in executor.estado_detalle["motivo"]


class TestEscritorSolarman:
    def _hass(self, devices=None):
        class DevicesFake(dict):
            """Imita DeviceRegistryItems: iterar da los DeviceEntry."""

            def __iter__(self):
                return iter(self.values())

        h = MagicMock()
        h.services.async_call = AsyncMock()
        registro = MagicMock()
        registro.devices = DevicesFake(devices or {})
        h._dr_devices = registro
        return h

    @pytest.mark.asyncio
    async def test_payload_con_device_configurado(self):
        hass = self._hass()
        escritor = EscritorSolarman(hass, "device-x")

        await escritor.escribir(210, 50)

        llamada = hass.services.async_call.call_args
        assert llamada.args == (
            "solarman",
            "write_single_register",
            {"device": "device-x", "register": 210, "value": 50},
        )
        assert llamada.kwargs == {"blocking": True}

    @pytest.mark.asyncio
    async def test_resolucion_automatica_primer_device(self):
        dispositivo = MagicMock()
        dispositivo.id = "auto-device"
        dispositivo.identifiers = {("solarman", "12345"), ("otro", "x")}
        hass = self._hass({"d1": dispositivo})

        # inyectar el registry: el helper importa dr.async_get(hass)
        import homeassistant.helpers.device_registry as dr_registry

        original = dr_registry.async_get
        dr_registry.async_get = lambda h: hass._dr_devices
        try:
            escritor = EscritorSolarman(hass, "")
            await escritor.escribir(210, 50)
        finally:
            dr_registry.async_get = original

        carga = hass.services.async_call.call_args.args[2]
        assert carga["device"] == "auto-device"

    @pytest.mark.asyncio
    async def test_sin_device_error_explicito(self):
        hass = self._hass({})

        import homeassistant.helpers.device_registry as dr_registry

        original = dr_registry.async_get
        dr_registry.async_get = lambda h: hass._dr_devices
        try:
            escritor = EscritorSolarman(hass, "")
            with pytest.raises(
                ErrorEscrituraSolarman, match="device_id_solarman"
            ):
                await escritor.escribir(210, 50)
        finally:
            dr_registry.async_get = original

        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_leer_normaliza_respuesta(self):
        hass = self._hass()
        hass.services.async_call = AsyncMock(
            return_value={"data": {"registers": [42]}}
        )
        escritor = EscritorSolarman(hass, "device-x")

        assert await escritor.leer(210, 1) == [42]

    def test_extraer_registros_formatos(self):
        assert _extraer_registros([1, 2], 2) == [1, 2]
        assert _extraer_registros({"data": [7]}, 1) == [7]
        assert _extraer_registros({"registers": [9]}, 1) == [9]
        with pytest.raises(ErrorEscrituraSolarman):
            _extraer_registros(None, 1)


class TestRegistroDeCallbacks:
    def test_notifica_al_cambiar_estado(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_MANTENER))
        callback = MagicMock()
        executor.async_registrar_callback(callback)

        executor._notificar()

        callback.assert_called_once()


class TestVerify:
    """D5: write-verify OK / fallo (cuenta) / parcial (no cuenta)."""

    @pytest.mark.asyncio
    async def test_verify_ok(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert executor._fallos_consecutivos == 0
        # una relectura por escritura
        assert escritor.leer.call_count == 3

    @pytest.mark.asyncio
    async def test_verify_fallo_cuenta_y_resetea(self, hass, mapa, escritor):
        """Valor leído distinto → fallo; una verify OK lo resetea."""
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True
        await executor.async_aplicar_tramo("test")
        assert executor._fallos_consecutivos == 0

        # Nueva corriente objetivo: 230 pendiente; la relectura no coincide
        executor.coordinador_plan.tramo_actual.return_value = _tramo(
            ACCION_CARGA_RED, carga_red_w=1024.0
        )
        escritor.leer = AsyncMock(side_effect=lambda addr, cantidad=1: [-1])
        await executor.async_aplicar_tramo("test")
        assert executor._fallos_consecutivos == 1

        # Verify OK en la siguiente pasada → contador a 0
        escritor.leer = AsyncMock(
            side_effect=lambda addr, cantidad=1: [
                escritor._leidos.get(addr, 0)
            ]
        )
        executor.coordinador_plan.tramo_actual.return_value = _tramo(
            ACCION_CARGA_RED, carga_red_w=2048.0
        )
        await executor.async_aplicar_tramo("test")
        assert executor._fallos_consecutivos == 0

    @pytest.mark.asyncio
    async def test_tres_fallos_consecutivos_fallback(
        self, hass, mapa, escritor
    ):
        """3 verifies fallidos → fallback + valores seguros + OFF.

        Una pasada de CARGA_RED escribe 3 registros: con las tres
        relecturas devolviendo un valor distinto se alcanzan los 3
        fallos consecutivos y se dispara el fallback.
        """
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True
        escritor.leer = AsyncMock(side_effect=lambda addr, cantidad=1: [-1])

        await executor.async_aplicar_tramo("test")

        assert executor.estado == "fallback_bloqueado"
        assert executor.control_activo is False
        # Valores seguros escritos (232=0, 230=0)
        assert escritor._leidos[232] == 0
        assert escritor._leidos[230] == 0
        # Notificación persistente emitida
        llamada = hass.services.async_call.call_args
        assert llamada.args[0] == "persistent_notification"

    @pytest.mark.asyncio
    async def test_verify_parcial_no_cuenta_fallo(self, hass, mapa, escritor):
        """Relectura con error de servicio → parcial, sin contar fallo."""
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True
        escritor.leer = AsyncMock(
            side_effect=ErrorEscrituraSolarman("timeout de relectura")
        )

        await executor.async_aplicar_tramo("test")

        assert executor._fallos_consecutivos == 0
        assert executor.estado == "activo"


class TestRateLimiter:
    """D6: máximo de escrituras reales por hora."""

    @pytest.mark.asyncio
    async def test_limite_superado_aplaza(self, hass, mapa, escritor):
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED),
            entry_data={"max_escrituras_hora": 4},
        )
        executor._control_activo = True

        # Primera pasada: 3 escrituras (210, 230, 232)
        await executor.async_aplicar_tramo("test")
        assert escritor.escribir.call_count == 3

        # Nueva corriente de red: 1 escritura más → 4/4
        executor.coordinador_plan.tramo_actual.return_value = _tramo(
            ACCION_CARGA_RED, carga_red_w=1024.0
        )
        await executor.async_aplicar_tramo("test")
        assert escritor.escribir.call_count == 4

        # La quinta supera el límite: aplazado, sin escritura
        executor.coordinador_plan.tramo_actual.return_value = _tramo(
            ACCION_CARGA_RED, carga_red_w=2048.0
        )
        await executor.async_aplicar_tramo("test")
        assert escritor.escribir.call_count == 4
        assert executor.estado_detalle["escrituras_ultima_hora"] == 4


class TestWatchdog:
    """D7: telemetría obsoleta / plan ausente → no escribir."""

    @pytest.mark.asyncio
    async def test_telemetria_obsoleta_no_escribe(self, hass, mapa, escritor):
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED),
            telemetria=_telemetria(edad_s=120.0),  # > 2 × 30 s
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escritor.escribir.assert_not_called()

    @pytest.mark.asyncio
    async def test_telemetria_obsoleta_con_carga_activa_fallback(
        self, hass, mapa, escritor
    ):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True
        # Aplicamos carga desde red...
        await executor.async_aplicar_tramo("test")
        assert escritor._leidos[232] == 1

        # ...y luego la telemetría queda obsoleta
        executor.coordinador_telemetria.data = _telemetria(edad_s=120.0)
        await executor.async_aplicar_tramo("test")

        assert executor.estado == "fallback_bloqueado"
        assert escritor._leidos[232] == 0  # valores seguros
        assert escritor._leidos[230] == 0

    @pytest.mark.asyncio
    async def test_plan_ausente_con_carga_activa_fallback(
        self, hass, mapa, escritor
    ):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True
        await executor.async_aplicar_tramo("test")

        executor.coordinador_plan.tramo_actual.return_value = None
        await executor.async_aplicar_tramo("test")

        assert executor.estado == "fallback_bloqueado"
        assert escritor._leidos[232] == 0


class TestGuardPotencia:
    """D9: guard dinámico al 95 % y al 100 % de la contratada."""

    def _estado_red(self, hass, valor: str):
        estado = MagicMock()
        estado.state = valor
        hass.states.get = MagicMock(return_value=estado)
        return hass

    @pytest.mark.asyncio
    async def test_guard_95_reduce_corriente(self, hass, mapa, escritor):
        # 5700 W de 5750 W → 99,1 % > 95 %: margen 0,95×5750−5700 = −225 W
        self._estado_red(hass, "5700")
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED, carga_red_w=4096.0),
            entry_data={"entity_id_potencia_red": "sensor.red"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        # margen negativo → corriente reducida a 0
        assert escritor._leidos[230] == 0

    @pytest.mark.asyncio
    async def test_guard_100_corte_inmediato(self, hass, mapa, escritor):
        self._estado_red(hass, "6000")  # ≥ 100 % de 5750
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED, carga_red_w=4096.0),
            entry_data={"entity_id_potencia_red": "sensor.red"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert escritor._leidos[230] == 0
        assert escritor._leidos[232] == 0

    @pytest.mark.asyncio
    async def test_guard_derivado_sin_entidad(self, hass, mapa, escritor):
        """Sin entity_id: importación derivada consumo − FV − pbat."""
        hass.states.get = MagicMock(return_value=None)
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED, carga_red_w=4096.0),
            telemetria=_telemetria(consumo=5700.0, fv=0.0, pbat=0.0),
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert escritor._leidos[230] == 0

    @pytest.mark.asyncio
    async def test_guard_normal_no_interfiere(self, hass, mapa, escritor):
        self._estado_red(hass, "2000")  # 35 % de la contratada
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED, carga_red_w=4096.0),
            entry_data={"entity_id_potencia_red": "sensor.red"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert escritor._leidos[230] == round(4096.0 / VOLTAJE)


class TestBloqueoFallo:
    """D10: registros Deye 103-106 ≠ 0 → sin escrituras."""

    @pytest.mark.asyncio
    async def test_fallo_activo_bloquea(self, hass, mapa, escritor):
        estado = MagicMock()
        estado.state = "1"
        hass.states.get = MagicMock(return_value=estado)
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED),
            entry_data={"entity_ids_fallo": "sensor.fault1, sensor.fault2"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escritor.escribir.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallo_con_carga_activa_fallback(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True
        await executor.async_aplicar_tramo("test")
        assert escritor._leidos[232] == 1

        estado = MagicMock()
        estado.state = "7"
        hass.states.get = MagicMock(return_value=estado)
        executor.entry.data["entity_ids_fallo"] = "sensor.fault1"
        await executor.async_aplicar_tramo("test")

        assert executor.estado == "fallback_bloqueado"
        assert escritor._leidos[232] == 0

    def test_lista_vacia_aviso_una_vez(self, hass, mapa, escritor, caplog):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_MANTENER))

        with caplog.at_level("WARNING"):
            assert executor._fallo_activo() is False
            assert executor._fallo_activo() is False

        assert caplog.text.count("INACTIVO") == 1  # aviso una sola vez



class TestModoGradual:
    """D11: gradual → solo CARGA_RED en valle 2.0TD se ejecuta."""

    @pytest.mark.asyncio
    async def test_carga_red_en_valle_se_ejecuta(self, hass, mapa, escritor):
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_CARGA_RED), periodo="valle"
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert escritor.escribir.call_count == 3
        assert executor.estado == "activo"

    @pytest.mark.asyncio
    async def test_carga_red_fuera_de_valle_simulada(
        self, hass, mapa, escritor
    ):
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_CARGA_RED), periodo="punta"
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escritor.escribir.assert_not_called()
        assert executor.estado == "dry_run"

    @pytest.mark.asyncio
    async def test_venta_red_simulada_en_gradual(self, hass, mapa, escritor):
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_VENTA_RED, venta_w=2048.0),
            periodo="punta",
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escritor.escribir.assert_not_called()
        assert executor.estado == "dry_run"

    @pytest.mark.asyncio
    async def test_modo_completo_ejecuta_todo(self, hass, mapa, escritor):
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_VENTA_RED, venta_w=2048.0),
            periodo="punta",
            entry_data={"modo_activacion": "completo"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert escritor.escribir.call_count == 4  # 210, 211, 230, 232
        assert executor.estado == "activo"


class TestAuditoria:
    """D14: JSONL append-only en config/optimizador_bateria_omie/."""

    def _leer_lineas(self, hass) -> list[dict]:
        import json
        from pathlib import Path

        ruta = (
            Path(hass.config.config_dir)
            / "optimizador_bateria_omie"
            / "auditoria_escrituras.jsonl"
        )
        assert ruta.exists()
        return [
            json.loads(linea)
            for linea in ruta.read_text(encoding="utf-8").splitlines()
        ]

    @pytest.mark.asyncio
    async def test_escritura_real_auditada(self, hass, mapa, escritor):
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_CARGA_RED), periodo="valle"
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        lineas = self._leer_lineas(hass)
        assert len(lineas) == 3  # una por registro escrito
        for linea in lineas:
            assert linea["ejecutada"] is True
            assert linea["verificado"] == "ok"
            assert linea["accion"] == ACCION_CARGA_RED
            assert linea["tramo"] == "02:30"
            assert "motivo" in linea
            assert linea["latencia"] is not None

    @pytest.mark.asyncio
    async def test_simulada_auditada_con_motivo(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        await executor.async_aplicar_tramo("arranque")  # control OFF

        lineas = self._leer_lineas(hass)
        assert len(lineas) == 1
        assert lineas[0]["ejecutada"] is False
        assert "control_activo OFF" in lineas[0]["motivo"]

    @pytest.mark.asyncio
    async def test_gradual_simulada_auditada(self, hass, mapa, escritor):
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_VENTA_RED), periodo="punta"
        )
        executor._control_activo = True
        await executor.async_aplicar_tramo("test")

        lineas = self._leer_lineas(hass)
        assert lineas[0]["ejecutada"] is False
        assert "gradual" in lineas[0]["motivo"]

    @pytest.mark.asyncio
    async def test_guard_registrado_en_auditoria(self, hass, mapa, escritor):
        estado = MagicMock()
        estado.state = "6000"  # ≥ 100 % de la contratada
        hass.states.get = MagicMock(return_value=estado)
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED, carga_red_w=4096.0),
            periodo="valle",
            entry_data={"entity_id_potencia_red": "sensor.red"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        lineas = self._leer_lineas(hass)
        assert all(
            linea["guard"] and "corte" in linea["guard"]
            for linea in lineas
        )

    @pytest.mark.asyncio
    async def test_fallback_auditado(self, hass, mapa, escritor):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True
        escritor.leer = AsyncMock(side_effect=lambda addr, cantidad=1: [-1])

        await executor.async_aplicar_tramo("test")

        lineas = self._leer_lineas(hass)
        # 3 reales (fallidas) + 1 línea de valores seguros del fallback
        assert any(linea["accion"] == "FALLBACK" for linea in lineas)
        fallback = next(
            linea for linea in lineas if linea["accion"] == "FALLBACK"
        )
        assert fallback["ejecutada"] is True
        assert fallback["verificado"] == "fallback"
