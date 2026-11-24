"""Tests del EjecutorControl (F4, U3): traducción, dry-run y límites.

Sin hardware ni red: el escritor es un mock inyectado y las direcciones
salen del registers.yaml empaquetado (nunca hardcodeadas).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
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
    """Escritor fake: escribir registra el valor y leer lo devuelve.

    Los puntos de arranque 228/229 se siembran con los valores objetivo
    por defecto para que el rearme F5.2 sea no-op; cada test siembra
    la deriva que quiera reproducir.
    """
    from custom_components.optimizador_bateria_omie.const import (
        DEFAULT_ARRANQUE_CARGA_RED_SOC,
        DEFAULT_ARRANQUE_CARGA_RED_V,
    )

    w = MagicMock()
    w._leidos: dict[int, int] = {
        228: DEFAULT_ARRANQUE_CARGA_RED_V,
        229: DEFAULT_ARRANQUE_CARGA_RED_SOC,
    }

    async def _escribir(addr, valor):
        w._leidos[addr] = valor

    async def _escribir_bloque(addr, valores):
        for i, v in enumerate(valores):
            w._leidos[addr + i] = v

    w.escribir = AsyncMock(side_effect=_escribir)
    w.escribir_bloque = AsyncMock(side_effect=_escribir_bloque)
    w.leer = AsyncMock(
        side_effect=lambda addr, cantidad=1: [
            w._leidos.get(addr + i, 0) for i in range(cantidad)
        ]
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
    # F6/A: default de los tests v0.14.x (sin tabla ToU); los tests de
    # la tabla la activan explícitamente con entry_data
    datos = {"actuador_tou": False}
    datos.update(entry_data or {})
    entry.data = datos
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

    @pytest.mark.asyncio
    async def test_actualizar_limites_soc_off_no_escribe(
        self, hass, mapa, escritor
    ):
        """0.11.6: con control_activo OFF no se toca el inversor."""
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_MANTENER))

        await executor.async_actualizar_limites_soc()

        escritor.escribir.assert_not_called()

    @pytest.mark.asyncio
    async def test_actualizar_limites_soc_on_escribe(
        self, hass, mapa, escritor
    ):
        """0.11.6: con control_activo ON reescribe 217/218 al mover
        el number de SOC mínimo."""
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_MANTENER))
        executor._control_activo = True

        await executor.async_actualizar_limites_soc()

        escrituras = _escrituras(escritor)
        assert escrituras[217] == 15
        assert escrituras[218] == 20


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
        """0.11.5: escritura individual vía FC 16 (bloque de un valor)
        porque el Deye rechaza la FC 6 con ACKNOWLEDGE."""
        hass = self._hass()
        escritor = EscritorSolarman(hass, "device-x")

        await escritor.escribir(210, 50)

        llamada = hass.services.async_call.call_args
        assert llamada.args == (
            "solarman",
            "write_multiple_holding_registers",
            {"device": "device-x", "register": 210, "values": [50]},
        )
        assert llamada.kwargs == {"blocking": True}

    @pytest.mark.asyncio
    async def test_generacion_legada_de_servicios(self):
        """Solo expone write_multiple_registers (legado) → lo usa."""
        hass = self._hass()
        hass.services.has_service = MagicMock(
            side_effect=lambda dominio, nombre: (
                nombre == "write_multiple_registers"
            )
        )
        escritor = EscritorSolarman(hass, "device-x")

        await escritor.escribir(210, 50)

        tipo, servicio, _ = hass.services.async_call.call_args.args
        assert (tipo, servicio) == ("solarman", "write_multiple_registers")

    @pytest.mark.asyncio
    async def test_sin_servicios_de_escritura_error_explicito(self):
        hass = self._hass()
        hass.services.has_service = MagicMock(return_value=False)
        escritor = EscritorSolarman(hass, "device-x")

        with pytest.raises(ErrorEscrituraSolarman, match="no expone"):
            await escritor.escribir(210, 50)

    @pytest.mark.asyncio
    async def test_leer_actual_campo_address_count(self):
        """Generación actual: address+count; el esquema register+count
        del adversario cuelga sin excepción (incidente 20-nov-2026)."""
        hass = self._hass()
        hass.services.async_call = AsyncMock(return_value={210: 42})
        escritor = EscritorSolarman(hass, "device-x")

        assert await escritor.leer(210, 1) == [42]
        tipo, servicio, carga = hass.services.async_call.call_args.args
        assert (
            tipo, servicio
        ) == ("solarman", "read_multiple_holding_registers")
        assert carga == {"device": "device-x", "address": 210, "count": 1}

    @pytest.mark.asyncio
    async def test_leer_legada_campo_address_count(self):
        """Generación legada: read_holding_registers también con
        address+count (0.15 era register+quantity → cuelgue)."""
        hass = self._hass()
        hass.services.has_service = MagicMock(
            side_effect=lambda dominio, nombre: (
                nombre == "read_holding_registers"
            )
        )
        hass.services.async_call = AsyncMock(return_value={210: 42})
        escritor = EscritorSolarman(hass, "device-x")

        assert await escritor.leer(210, 1) == [42]
        tipo, servicio, carga = hass.services.async_call.call_args.args
        assert (tipo, servicio) == ("solarman", "read_holding_registers")
        assert carga == {"device": "device-x", "address": 210, "count": 1}

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
        # F5.2: 2 lecturas de rearme (228/229) + una relectura por
        # escritura
        assert escritor.leer.call_count == 5

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
        """Relectura con error de servicio → parcial, sin contar fallo.

        F5.2: las lecturas de rearme (228/229) deben responder; solo
        la relectura de verify de los registros de control falla.
        """

        async def _leer(addr, cantidad=1):
            if addr in (228, 229):
                return [escritor._leidos.get(addr, 0)]
            raise ErrorEscrituraSolarman("timeout de relectura")

        escritor.leer = AsyncMock(side_effect=_leer)
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True

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
            telemetria=_telemetria(edad_s=180.0),  # > 4 × 30 s
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")
        await executor.async_aplicar_tramo("test")

        escritor.escribir.assert_not_called()
        # Tras el debounce (D-F5.3.3) el estado declara la caída
        assert executor.estado == "sin_telemetria"
        detalle = executor.estado_detalle
        assert detalle["telemetria_fresca"] is False
        assert detalle["modo_activacion"] == "gradual"

    @pytest.mark.asyncio
    async def test_telemetria_jitter_menor_que_umbral_escribe(
        self, hass, mapa, escritor
    ):
        """D-F5.3.3: umbral 4 × intervalo absorbe el jitter de Solarman."""
        executor = _executor(
            hass,
            mapa,
            escritor,
            _tramo(ACCION_CARGA_RED),
            telemetria=_telemetria(edad_s=100.0),  # < 120 s (antes 60 s)
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert escritor.escribir.call_count == 3
        assert executor.estado == "activo"

    @pytest.mark.asyncio
    async def test_telemetria_obsoleta_con_carga_activa_fallback(
        self, hass, mapa, escritor
    ):
        """D-F5.3.3: la carga se mantiene hasta el timeout mayor (600 s)."""
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True
        # Aplicamos carga desde red...
        await executor.async_aplicar_tramo("test")
        assert escritor._leidos[232] == 1

        # ...y luego la telemetría queda obsoleta (primera caída NO
        # derriba la carga: escrituras cortadas, plan en espera)
        executor.coordinador_telemetria.data = _telemetria(edad_s=180.0)
        await executor.async_aplicar_tramo("test")
        assert executor.estado != "fallback_bloqueado"
        assert escritor._leidos[232] == 1  # última escritura sigue en pie

        # Superado el timeout mayor → fallback con valores seguros
        executor._ts_watchdog_caida = (
            datetime.now(tz=UTC) - timedelta(seconds=601)
        )
        await executor.async_aplicar_tramo("test")

        assert executor.estado == "fallback_bloqueado"
        assert escritor._leidos[232] == 0  # valores seguros
        assert escritor._leidos[230] == 0

    @pytest.mark.asyncio
    async def test_watchdog_recuperado_resetea_caidas(
        self, hass, mapa, escritor
    ):
        """D-F5.3.3: la recuperación resetea el contador del watchdog."""
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True

        # Una caída y una recuperación: el estado no llegó a declararse
        executor.coordinador_telemetria.data = _telemetria(edad_s=180.0)
        await executor.async_aplicar_tramo("test")
        executor.coordinador_telemetria.data = _telemetria(edad_s=0.0)
        await executor.async_aplicar_tramo("test")

        assert executor.estado == "activo"
        assert executor._watchdog_caidas == 0
        assert executor._ts_watchdog_caida is None

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


class TestLivelockVerify:
    """Incidente 20-nov-2026 (parche 0.14.3): lecturas colgadas del
    servicio Solarman mataban el bucle de escritura en el primer
    registro, sin excepción ni auditoría. Fixes A/B/C del parche."""

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
    async def test_lectura_colgada_timeout_parcial_y_executor_vivo(
        self, hass, mapa
    ):
        """Fix B: lectura que nunca retorna → verify 'parcial' audible
        en ≤ SERVICIO_TIMEOUT_S y el bucle sigue (auditado por registro).

        F5.2: se usa AUTOCONSUMO porque una CARGA_RED abortaría antes
        (el rearme 228/229 lee primero y cuelga igual); su cobertura
        está en el test siguiente.
        """
        import asyncio as aio

        async def _colgar(dominio, servicio, datos,
                          blocking=False, return_response=False):
            if return_response:  # lectura: el cuelgue del incidente
                await aio.Event().wait()

        hass.services.async_call = AsyncMock(side_effect=_colgar)
        escritor = EscritorSolarman(hass, "device-x")
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_AUTOCONSUMO),
            entry_data={"modo_activacion": "completo",
                        "latencia_verify_s": 0},
        )
        executor._control_activo = True

        with patch(
            "custom_components.optimizador_bateria_omie.executor"
            ".SERVICIO_TIMEOUT_S",
            0.1,
        ):
            await executor.async_aplicar_tramo("test")

        # Cada registro escrito queda auditado con verify 'parcial'
        lineas = self._leer_lineas(hass)
        assert len(lineas) == 3  # 210, 230, 232: el bucle NO murió en 210
        for linea in lineas:
            assert linea["ejecutada"] is True
            assert linea["verificado"] == "parcial"
        # 'parcial' no cuenta para el fallback (D5) y el executor sigue vivo
        assert executor._fallos_consecutivos == 0
        assert executor.estado == "activo"

    @pytest.mark.asyncio
    async def test_carga_red_con_lectura_colgada_aborta_audible(
        self, hass, mapa
    ):
        """F5.2: CARGA_RED con lecturas colgadas → el rearme de 228/229
        aborta el tramo (auditado) en vez de armar un 232=1 a ciegas."""
        import asyncio as aio

        async def _colgar(dominio, servicio, datos,
                          blocking=False, return_response=False):
            if return_response:
                await aio.Event().wait()

        hass.services.async_call = AsyncMock(side_effect=_colgar)
        escritor = EscritorSolarman(hass, "device-x")
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_CARGA_RED),
            entry_data={"modo_activacion": "completo",
                        "latencia_verify_s": 0},
        )
        executor._control_activo = True

        with patch(
            "custom_components.optimizador_bateria_omie.executor"
            ".SERVICIO_TIMEOUT_S",
            0.1,
        ):
            await executor.async_aplicar_tramo("test")

        # Abortado antes de escribir nada, pero audible y vivo
        lineas = self._leer_lineas(hass)
        assert len(lineas) == 1
        assert lineas[0]["ejecutada"] is False
        assert (
            "puntos de arranque no asegurables" in lineas[0]["motivo"]
        )
        assert executor.estado == "dry_run"
        assert executor._escrituras_ts == []  # cuota intacta

    @pytest.mark.asyncio
    async def test_error_escritura_auditado_y_propagado(
        self, hass, mapa, escritor
    ):
        """Fix C: un fallo de escribir() deja línea de auditoría
        (error_escritura) antes de re-lanzar la excepción."""

        async def _fallo(addr, valor):
            raise ErrorEscrituraSolarman("boom de red")

        escritor.escribir = AsyncMock(side_effect=_fallo)
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_CARGA_RED),
            entry_data={"modo_activacion": "completo"},
        )
        executor._control_activo = True

        with pytest.raises(ErrorEscrituraSolarman, match="boom de red"):
            await executor.async_aplicar_tramo("test")

        lineas = self._leer_lineas(hass)
        assert len(lineas) == 1
        assert lineas[0]["ejecutada"] is False
        assert lineas[0]["verificado"] == "error_escritura"
        assert "boom de red" in lineas[0]["motivo"]

    @pytest.mark.asyncio
    async def test_cuota_rate_limiter_intacta_tras_error(
        self, hass, mapa, escritor
    ):
        """Fix C: el append de la cuota va tras el éxito → un error de
        escritura no repe la cuota (0 consumidas tras el fallo)."""

        async def _fallo(addr, valor):
            raise ErrorEscrituraSolarman("boom de red")

        escritor.escribir = AsyncMock(side_effect=_fallo)
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_CARGA_RED),
            entry_data={"modo_activacion": "completo"},
        )
        executor._control_activo = True

        with pytest.raises(ErrorEscrituraSolarman):
            await executor.async_aplicar_tramo("test")

        assert executor._escrituras_ts == []
        assert executor.estado_detalle["escrituras_ultima_hora"] == 0


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
        assert executor.estado == "gradual"

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
        assert executor.estado == "gradual"

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


class TestRearmeArranqueF52:
    """F5.2: saneo de puntos de arranque 228/229 y cap de 245."""

    def _auditoria(self, hass) -> list[dict]:
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
    async def test_deriva_reescribe_arranque_antes_de_armar(
        self, hass, mapa, escritor
    ):
        # 228=4900 (49 V): el push del datalogger congela la carga
        escritor._leidos[228] = 4900
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        orden = [llamada.args[0] for llamada in escritor.escribir.call_args_list]
        assert orden.index(228) < orden.index(232)  # rearme antes de armar
        escrituras = _escrituras(escritor)
        assert escrituras[228] == 6100
        assert escrituras[232] == 1
        assert any(
            "rearme arranque (leído 4900)" in linea["motivo"]
            for linea in self._auditoria(hass)
        )

    @pytest.mark.asyncio
    async def test_sin_deriva_no_reescribe_arranque(
        self, hass, mapa, escritor
    ):
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        escrituras = _escrituras(escritor)
        assert 228 not in escrituras
        assert 229 not in escrituras
        assert escrituras[210] == round(5000.0 / VOLTAJE)
        assert escrituras[230] == round(2450.0 / VOLTAJE)
        assert escrituras[232] == 1

    @pytest.mark.asyncio
    async def test_arranque_no_asegurable_aborta_tramo(
        self, hass, mapa, escritor
    ):
        escritor.leer = AsyncMock(
            side_effect=ErrorEscrituraSolarman("lectura caída")
        )
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        # Abortar antes de armar el tramo: ni 232 ni ningún registro
        assert escritor.escribir.await_count == 0
        assert executor._estado_simulado == "arranque_bloqueado"
        assert executor.estado == "dry_run"
        assert any(
            "puntos de arranque no asegurables" in linea["motivo"]
            for linea in self._auditoria(hass)
        )

    @pytest.mark.asyncio
    async def test_cap_exportacion_recorta_venta(self, hass, mapa, escritor):
        escritor._leidos[245] = 5000
        executor = _executor(
            hass, mapa, escritor,
            _tramo(ACCION_VENTA_RED, venta_w=5193.0),
            entry_data={"modo_activacion": "completo"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        # cap a la baja: int(5000/51.2) = 97, no round(5193/51.2) = 101
        assert _escrituras(escritor)[211] == 97

    @pytest.mark.asyncio
    async def test_sin_cap_exportacion_venta_intacta(
        self, hass, mapa, escritor
    ):
        executor = _executor(
            hass, mapa, escritor,
            _tramo(ACCION_VENTA_RED, venta_w=5193.0),
            entry_data={"modo_activacion": "completo"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert _escrituras(escritor)[211] == round(5193.0 / VOLTAJE)

    @pytest.mark.asyncio
    async def test_cooldown_rearme_una_hora_por_registro(
        self, hass, mapa, escritor
    ):
        # 229 rebota: siempre se lee 90 aunque se escriba 100
        async def _leer(addr, cantidad=1):
            return [90 if addr == 229 else escritor._leidos.get(addr, 0)]

        escritor.leer = AsyncMock(side_effect=_leer)
        executor = _executor(hass, mapa, escritor, _tramo(ACCION_CARGA_RED))
        executor._control_activo = True
        await executor.async_aplicar_tramo("tramo 1")

        escrituras_229 = [
            llamada
            for llamada in escritor.escribir.call_args_list
            if llamada.args[0] == 229
        ]
        assert len(escrituras_229) == 1  # primera vez: una reescritura

        await executor.async_aplicar_tramo("tramo 2")

        escrituras_229 = [
            llamada
            for llamada in escritor.escribir.call_args_list
            if llamada.args[0] == 229
        ]
        # Enfriamiento: no se reescribe mientras siga rebotando
        assert len(escrituras_229) == 1
        lineas = self._auditoria(hass)
        assert any(
            linea["verificado"] == "rearme_enfriamiento" for linea in lineas
        )
        # Y el tramo NO se aborta: el 232 quedó aplicado
        assert _escrituras(escritor)[232] == 1


class TestTablaTouF6:
    """F6/A: tabla ToU 250-279 como actuador del tramo."""

    def _direcciones(self, mapa) -> list[int]:
        return [
            mapa.direccion("estandar", f"tou_{campo}_{i}", "control")
            for campo in ("hora", "potencia", "vstop", "soc_stop", "flag")
            for i in range(1, 7)
        ]

    def _plan_run_carga(self) -> list:
        """Run de 3 tramos CARGA_RED: 09:15, 09:30 y 09:45."""
        return [
            PlanTramo(
                fecha=HOY,
                tramo=37,
                hora="09:15",
                accion=ACCION_CARGA_RED,
                potencia_bateria_w=-4000.0,
                potencia_carga_red_w=2450.0,
                potencia_venta_red_w=0.0,
                soc_previsto_pct=50.0,
                precio_referencia_eur_kwh=0.08,
                ahorro_tramo_eur=0.0,
            ),
            PlanTramo(
                fecha=HOY,
                tramo=38,
                hora="09:30",
                accion=ACCION_CARGA_RED,
                potencia_bateria_w=-4000.0,
                potencia_carga_red_w=2750.0,
                potencia_venta_red_w=0.0,
                soc_previsto_pct=55.0,
                precio_referencia_eur_kwh=0.08,
                ahorro_tramo_eur=0.0,
            ),
            PlanTramo(
                fecha=HOY,
                tramo=39,
                hora="09:45",
                accion=ACCION_CARGA_RED,
                potencia_bateria_w=-4000.0,
                potencia_carga_red_w=2600.0,
                potencia_venta_red_w=0.0,
                soc_previsto_pct=60.0,
                precio_referencia_eur_kwh=0.08,
                ahorro_tramo_eur=0.0,
            ),
        ]

    def _conectar_plan(self, executor, tramos):
        """Pone el plan en el coordinador mock y el tramo actual."""
        executor.coordinador_plan.plan = SimpleNamespace(tramos=tramos)

    @pytest.mark.asyncio
    async def test_carga_red_escribe_tabla_del_run(
        self, hass, mapa, escritor
    ):
        tramos = self._plan_run_carga()
        executor = _executor(
            hass, mapa, escritor, tramos[1],
            entry_data={"actuador_tou": True, "modo_activacion": "completo"},
        )
        self._conectar_plan(executor, tramos)
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        d = self._direcciones(mapa)
        # Segmento del run completo: 09:15 → 10:00, flag 5 (carga),
        # potencia máxima del run, vstop observado, SOC-stop = soc_max
        assert escritor._leidos[d[0]] == 915  # hora_1
        assert escritor._leidos[d[1]] == 1000  # hora_2 (fin del run)
        assert escritor._leidos[d[6]] == 2750  # potencia_1: máx del run
        assert escritor._leidos[d[12]] == 4900  # vstop_1 (observado)
        assert escritor._leidos[d[18]] == 95  # soc_stop_1 = soc_max
        assert escritor._leidos[d[24]] == 5  # flag_1: carga
        # Resto de puntos desactivados (sin flags de venta residuales)
        assert escritor._leidos[d[5]] == 0  # hora_3
        assert escritor._leidos[d[24 + 1]] == 0  # flag_2
        assert escritor._leidos[d[29]] == 0  # flag_6
        # Amperajes: receta D1 (230 = corriente, 211 = 0) y llave 232
        escrituras = _escrituras(escritor)
        assert escrituras[211] == 0
        assert escrituras[232] == 1
        # Resumen visible en estado_detalle
        resumen = executor.estado_detalle["tabla_tou"]
        assert resumen["hora_1"] == 915
        assert resumen["hora_2"] == 1000
        assert resumen["flag_1"] == 5

    @pytest.mark.asyncio
    async def test_tabla_idempotente_no_reescribe(
        self, hass, mapa, escritor
    ):
        tramos = self._plan_run_carga()
        executor = _executor(
            hass, mapa, escritor, tramos[1],
            entry_data={"actuador_tou": True, "modo_activacion": "completo"},
        )
        self._conectar_plan(executor, tramos)
        executor._control_activo = True
        await executor.async_aplicar_tramo("test 1")
        antes = escritor.escribir_bloque.call_count

        await executor.async_aplicar_tramo("test 2")

        # Misma tabla y mismos amperajes → cero escrituras nuevas
        assert escritor.escribir_bloque.call_count == antes
        assert executor.estado == "activo"

    @pytest.mark.asyncio
    async def test_venta_red_flag_4_soc_stop_minimo(
        self, hass, mapa, escritor
    ):
        executor = _executor(
            hass, mapa, escritor,
            _tramo(ACCION_VENTA_RED, venta_w=4096.0),
            entry_data={"actuador_tou": True, "modo_activacion": "completo"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        d = self._direcciones(mapa)
        assert escritor._leidos[d[24]] == 4  # flag venta
        assert escritor._leidos[d[18]] == 15  # soc_stop = soc_min
        assert escritor._leidos[d[6]] == 4096  # potencia venta
        assert escritor._leidos[d[24 + 1]] == 0  # sin más flags

    @pytest.mark.asyncio
    async def test_autoconsumo_deja_tabla_vacia(self, hass, mapa, escritor):
        executor = _executor(
            hass, mapa, escritor, _tramo(ACCION_AUTOCONSUMO),
            entry_data={"actuador_tou": True, "modo_activacion": "completo"},
        )
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        d = self._direcciones(mapa)
        assert all(escritor._leidos.get(addr, 0) == 0 for addr in d)
        assert escritor._leidos[232] == 0  # amperajes seguros

    @pytest.mark.asyncio
    async def test_presupuesto_tablas_aplaza(self, hass, mapa, escritor):
        tramos = self._plan_run_carga()
        executor = _executor(
            hass, mapa, escritor, tramos[1],
            entry_data={
                "actuador_tou": True,
                "modo_activacion": "completo",
                "max_tablas_tou_hora": 1,
            },
        )
        self._conectar_plan(executor, tramos)
        executor._control_activo = True
        await executor.async_aplicar_tramo("test 1")
        assert escritor.escribir_bloque.call_count == 5  # 5 bloques

        # Cambio de acción: tabla nueva → presupuesto agotado → aplazada
        executor.coordinador_plan.tramo_actual.return_value = _tramo(
            ACCION_AUTOCONSUMO
        )
        await executor.async_aplicar_tramo("test 2")
        assert escritor.escribir.call_count == 4  # amperajes autoconsumo
        assert executor.estado == "dry_run"

    @pytest.mark.asyncio
    async def test_nunca_se_tocan_244_249(self, hass, mapa, escritor):
        d = self._direcciones(mapa)
        tramos = self._plan_run_carga()
        executor = _executor(
            hass, mapa, escritor, tramos[1],
            entry_data={"actuador_tou": True, "modo_activacion": "completo"},
        )
        executor._control_activo = True
        await executor.async_aplicar_tramo("test")

        for llamada in escritor.escribir_bloque.call_args_list:
            addr = llamada.args[0]
            assert d[0] <= addr <= d[-1]  # solo 250-279

    @pytest.mark.asyncio
    async def test_actuador_off_no_toca_tabla(self, hass, mapa, escritor):
        tramos = self._plan_run_carga()
        # actuador_tou False es el default de los tests v0.14.x
        executor = _executor(
            hass, mapa, escritor, tramos[1],
            entry_data={"modo_activacion": "completo"},
        )
        self._conectar_plan(executor, tramos)
        executor._control_activo = True

        await executor.async_aplicar_tramo("test")

        assert escritor.escribir_bloque.call_count == 0
        assert executor.estado_detalle["tabla_tou"] is None

    @pytest.mark.asyncio
    async def test_vigilante_repara_deriva(self, hass, mapa, escritor):
        tramos = self._plan_run_carga()
        executor = _executor(
            hass, mapa, escritor, tramos[1],
            entry_data={"actuador_tou": True, "modo_activacion": "completo"},
        )
        self._conectar_plan(executor, tramos)
        executor._control_activo = True
        await executor.async_aplicar_tramo("test")
        antes = escritor.escribir_bloque.call_count

        # La nube pisa la tabla: deriva del objetivo → repara
        d = self._direcciones(mapa)
        escritor._leidos[d[0]] = 9999
        await executor._async_vigilante_tou(None)
        assert escritor.escribir_bloque.call_count > antes  # reparada
        assert (
            escritor._leidos[d[0]]
            == executor._tabla_tou_objetivo[d[0]]
        )

        # Deriva inmediata: cooldown 1/h → no repara
        escritor._leidos[d[0]] = 8888
        congelado = escritor.escribir_bloque.call_count
        await executor._async_vigilante_tou(None)
        assert escritor.escribir_bloque.call_count == congelado

    @pytest.mark.asyncio
    async def test_vigilante_sin_lectura_notifica(self, hass, mapa, escritor):
        tramos = self._plan_run_carga()
        executor = _executor(
            hass, mapa, escritor, tramos[1],
            entry_data={"actuador_tou": True, "modo_activacion": "completo"},
        )
        self._conectar_plan(executor, tramos)
        executor._control_activo = True
        await executor.async_aplicar_tramo("test")
        escritor.leer = AsyncMock(
            side_effect=ErrorEscrituraSolarman("lectura caída")
        )

        await executor._async_vigilante_tou(None)

        llamada = hass.services.async_call.call_args
        assert llamada.args[0] == "persistent_notification"
