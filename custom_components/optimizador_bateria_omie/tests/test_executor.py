"""Tests del EjecutorControl (F4, U3): traducción, dry-run y límites.

Sin hardware ni red: el escritor es un mock inyectado y las direcciones
salen del registers.yaml empaquetado (nunca hardcodeadas).
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

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
def hass():
    h = MagicMock()
    h.config.config_dir = ""
    return h


@pytest.fixture
def mapa(hass):
    m = MapaRegistros(hass)
    m.cargar()
    return m


@pytest.fixture
def escritor():
    w = MagicMock()
    w.escribir = AsyncMock()
    w.escribir_bloque = AsyncMock()
    w.leer = AsyncMock()
    return w


def _coordinador_con_tramo(tramo):
    c = MagicMock()
    c.tramo_actual = MagicMock(return_value=tramo)
    c.async_registrar_callback = MagicMock(return_value=MagicMock())
    return c


def _executor(
    hass, mapa, escritor, tramo, entry_data=None
) -> EjecutorControl:
    entry = MagicMock()
    entry.data = dict(entry_data or {})
    return EjecutorControl(
        hass=hass,
        entry=entry,
        mapa=mapa,
        coordinador_plan=_coordinador_con_tramo(tramo),
        coordinador_telemetria=MagicMock(),
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
            hass, mapa, escritor, _tramo(ACCION_AUTOCONSUMO)
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
            hass, mapa, escritor, _tramo(ACCION_VENTA_RED, venta_w=2048.0)
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
            entry_data={"potencia_max_carga": 4000.0},
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


class TestEscritorSolarman:
    def _hass(self, devices=None):
        h = MagicMock()
        h.services.async_call = AsyncMock()
        registro = MagicMock()
        registro.devices = dict(devices or {})
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
