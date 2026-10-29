"""Tests para GestorValidacion, CoordinadorTelemetria y SensorFamiliaValidada (U3)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.optimizador_bateria_omie.const import (
    CONF_ENTITY_ID_CONSUMO_CASA,
    CONF_ENTITY_ID_POTENCIA_BATERIA,
    CONF_ENTITY_ID_PRODUCCION_FV,
    CONF_ENTITY_ID_SOC_BATERIA,
    CONF_TELEMETRY_SOURCE,
    SIGNAL_VALIDACION_CAMBIADA,
    TELEMETRY_SOURCE_HA_ENTITIES,
    TELEMETRY_SOURCE_MODBUS,
)
from custom_components.optimizador_bateria_omie.entidades_solarman import (
    SensorFamiliaValidada,
)
from custom_components.optimizador_bateria_omie.mapa_registros import (
    MapaRegistros,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    CoordinadorTelemetria,
    GestorValidacion,
    TelemetriaInversor,
)


class StoreFake:
    """Store de hass.storage simulado para tests."""

    def __init__(self, datos: dict | None = None) -> None:
        self.datos = dict(datos) if datos else {}
        self.guardados: list[dict] = []

    async def async_load(self) -> dict | None:
        return dict(self.datos) if self.datos else None

    async def async_save(self, datos: dict) -> None:
        self.guardados.append(dict(datos))
        self.datos = dict(datos)


@pytest.fixture
def hass():
    """Hass mock: el executor ejecuta la función directamente."""
    h = MagicMock()
    h.async_add_executor_job = MagicMock(
        side_effect=lambda fn, *args: fn(*args)
    )
    return h


@pytest.fixture
def mapa(hass):
    """Mapa de registros empaquetado cargado."""
    m = MapaRegistros(hass)
    m.cargar()
    return m


def _entry(data: dict) -> MagicMock:
    """Entry mock con entry.data dado."""
    entry = MagicMock()
    entry.data = data
    return entry


def _estado(entity_id: str, valor: str) -> MagicMock:
    """Estado HA mock."""
    estado = MagicMock()
    estado.state = valor
    return estado


class TestGestorValidacion:
    """Tests del gestor del Store de validación."""

    @pytest.mark.asyncio
    async def test_cargar_vacio(self, hass):
        store = StoreFake()
        gestor = GestorValidacion(hass, store=store)

        datos = await gestor.async_cargar()

        assert datos == {}
        assert gestor.familia is None

    @pytest.mark.asyncio
    async def test_guardar_persiste_y_notifica(self, hass):
        store = StoreFake()
        gestor = GestorValidacion(hass, store=store)

        with patch(
            "custom_components.optimizador_bateria_omie.telemetria.async_dispatcher_send"
        ) as enviar:
            await gestor.async_guardar(
                {
                    "familia": "estandar",
                    "probe_addr": 184,
                    "probe_funcion": "soc",
                    "ultima_validacion_ts": "2026-10-23T12:00:00+00:00",
                }
            )

        assert gestor.familia == "estandar"
        assert store.datos["familia"] == "estandar"
        enviar.assert_called_once_with(hass, SIGNAL_VALIDACION_CAMBIADA)

    @pytest.mark.asyncio
    async def test_borrar(self, hass):
        store = StoreFake({"familia": "estandar"})
        gestor = GestorValidacion(hass, store=store)
        await gestor.async_cargar()

        with patch(
            "custom_components.optimizador_bateria_omie.telemetria.async_dispatcher_send"
        ):
            await gestor.async_borrar()

        assert gestor.familia is None
        assert store.datos == {}

    @pytest.mark.asyncio
    async def test_revalidar_coincide(self, hass, mapa):
        store = StoreFake(
            {"familia": "estandar", "probe_addr": 184, "probe_funcion": "soc"}
        )
        gestor = GestorValidacion(hass, store=store)

        datos = await gestor.async_revalidar(mapa)

        assert datos.get("familia") == "estandar"
        assert gestor.familia == "estandar"

    @pytest.mark.asyncio
    async def test_revalidar_probe_addr_cambiado(self, hass, mapa, caplog):
        """m1: si probe_addr del Store ≠ YAML → se invalida."""
        store = StoreFake(
            {"familia": "estandar", "probe_addr": 999, "probe_funcion": "soc"}
        )
        gestor = GestorValidacion(hass, store=store)

        with caplog.at_level("WARNING"):
            datos = await gestor.async_revalidar(mapa)

        assert datos == {}
        assert gestor.familia is None
        assert "probe_addr" in caplog.text

    @pytest.mark.asyncio
    async def test_revalidar_familia_inexistente(self, hass, mapa):
        store = StoreFake(
            {"familia": "Z", "probe_addr": 1, "probe_funcion": "soc"}
        )
        gestor = GestorValidacion(hass, store=store)

        datos = await gestor.async_revalidar(mapa)

        assert datos == {}
        assert gestor.familia is None


class TestCoordinadorTelemetria:
    """Tests del coordinador de telemetría (C5 y fuentes)."""

    @pytest.mark.asyncio
    async def test_modbus_sin_familia_guard_antes_de_modbus(self, hass, mapa):
        """C5: sin familia validada → UpdateFailed sin tocar el cliente."""
        gestor = GestorValidacion(hass, store=StoreFake())
        cliente = MagicMock()
        cliente.leer_telemetria = MagicMock()
        coordinador = CoordinadorTelemetria(
            hass, cliente, gestor, _entry({CONF_TELEMETRY_SOURCE: TELEMETRY_SOURCE_MODBUS})
        )

        from homeassistant.helpers.update_coordinator import UpdateFailed

        with pytest.raises(UpdateFailed, match="probe_registers"):
            await coordinador._async_update_data()

        cliente.leer_telemetria.assert_not_called()

    @pytest.mark.asyncio
    async def test_modbus_con_familia_delega(self, hass):
        gestor = GestorValidacion(
            hass, store=StoreFake({"familia": "estandar", "probe_addr": 184})
        )
        esperado = TelemetriaInversor(
            soc=73.0, potencia_bateria=-100.0, corriente_bateria=-2.0,
            consumo_casa=350.0, produccion_fv=800.0,
            temperatura_inversor=35.0, temperatura_bateria=28.0,
            ts_actualizacion=datetime.now(tz=UTC),
        )
        cliente = MagicMock()
        cliente.leer_telemetria = AsyncMock(return_value=esperado)
        coordinador = CoordinadorTelemetria(
            hass, cliente, gestor, _entry({CONF_TELEMETRY_SOURCE: TELEMETRY_SOURCE_MODBUS})
        )

        resultado = await coordinador._async_update_data()

        cliente.leer_telemetria.assert_called_once_with("estandar")
        assert resultado is esperado

    @pytest.mark.asyncio
    async def test_ha_entities_numericos(self, hass):
        gestor = GestorValidacion(hass, store=StoreFake())
        hass.states.get = MagicMock(
            side_effect=lambda entity_id: {
                "sensor.soc": _estado("sensor.soc", "73.5"),
                "sensor.pbat": _estado("sensor.pbat", "-1200"),
                "sensor.consumo": _estado("sensor.consumo", "350"),
                "sensor.fv": _estado("sensor.fv", "800"),
            }.get(entity_id)
        )
        entry = _entry(
            {
                CONF_TELEMETRY_SOURCE: TELEMETRY_SOURCE_HA_ENTITIES,
                CONF_ENTITY_ID_SOC_BATERIA: "sensor.soc",
                CONF_ENTITY_ID_POTENCIA_BATERIA: "sensor.pbat",
                CONF_ENTITY_ID_CONSUMO_CASA: "sensor.consumo",
                CONF_ENTITY_ID_PRODUCCION_FV: "sensor.fv",
            }
        )
        coordinador = CoordinadorTelemetria(hass, None, gestor, entry)

        telemetria = await coordinador._async_update_data()

        assert telemetria.soc == pytest.approx(73.5)
        assert telemetria.potencia_bateria == pytest.approx(-1200.0)
        assert telemetria.consumo_casa == pytest.approx(350.0)
        assert telemetria.produccion_fv == pytest.approx(800.0)
        assert telemetria.corriente_bateria is None

    @pytest.mark.asyncio
    async def test_ha_entities_ausentes_o_no_numericos(self, hass):
        gestor = GestorValidacion(hass, store=StoreFake())
        hass.states.get = MagicMock(
            side_effect=lambda entity_id: {
                "sensor.soc": _estado("sensor.soc", "unavailable"),
                "sensor.consumo": _estado("sensor.consumo", "350"),
            }.get(entity_id)
        )
        entry = _entry(
            {
                CONF_TELEMETRY_SOURCE: TELEMETRY_SOURCE_HA_ENTITIES,
                CONF_ENTITY_ID_SOC_BATERIA: "sensor.soc",
                CONF_ENTITY_ID_POTENCIA_BATERIA: "sensor.pbat",   # no existe
                CONF_ENTITY_ID_CONSUMO_CASA: "sensor.consumo",
                CONF_ENTITY_ID_PRODUCCION_FV: "",                 # sin configurar
            }
        )
        coordinador = CoordinadorTelemetria(hass, None, gestor, entry)

        telemetria = await coordinador._async_update_data()

        assert telemetria.soc is None            # no numérico
        assert telemetria.potencia_bateria is None  # entidad ausente
        assert telemetria.consumo_casa == pytest.approx(350.0)
        assert telemetria.produccion_fv is None  # sin entity_id


class TestSensorFamiliaValidada:
    """Tests del sensor diagnóstico (M10)."""

    def test_estado_validada(self, hass):
        gestor = GestorValidacion(hass, store=StoreFake())
        gestor.estado = {
            "familia": "estandar",
            "probe_addr": 184,
            "probe_funcion": "soc",
            "ultima_validacion_ts": "2026-10-23T12:00:00+00:00",
        }
        sensor = SensorFamiliaValidada(gestor)

        assert sensor.native_value == "estandar"
        attrs = sensor.extra_state_attributes
        assert attrs["probe_addr"] == 184
        assert attrs["probe_funcion"] == "soc"
        assert attrs["ultima_validacion_ts"] == "2026-10-23T12:00:00+00:00"

    def test_estado_ninguna(self, hass):
        gestor = GestorValidacion(hass, store=StoreFake())
        sensor = SensorFamiliaValidada(gestor)

        assert sensor.native_value == "ninguna"
        assert sensor.extra_state_attributes == {
            "probe_addr": None,
            "probe_funcion": None,
            "ultima_validacion_ts": None,
        }

    def test_categoria_diagnostica(self, hass):
        from homeassistant.helpers.entity import EntityCategory

        gestor = GestorValidacion(hass, store=StoreFake())
        sensor = SensorFamiliaValidada(gestor)

        assert sensor.entity_category is EntityCategory.DIAGNOSTIC
        assert sensor.unique_id == (
            "optimizador_bateria_omie_familia_validada"
        )
