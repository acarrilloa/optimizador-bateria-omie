"""Plataforma de sensores para la integración Optimizador Batería OMIE."""
# versión 0.8.5

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.optimizador_bateria_omie.cliente_omie import (
    ClienteOmie,
    ConfiguracionOmie,
)
from custom_components.optimizador_bateria_omie.const import (
    CONF_IP,
    CONF_PUERTO,
    CONF_SERIE,
    CONF_TELEMETRY_SOURCE,
    DEFAULT_DIAS_CACHE,
    DEFAULT_FUENTE_PRIORITARIA,
    DEFAULT_IP,
    DEFAULT_PUERTO,
    DEFAULT_SERIE,
    DEFAULT_USAR_CACHE,
    DOMAIN,
    TELEMETRY_SOURCE_MODBUS,
)
from custom_components.optimizador_bateria_omie.coordinador import CoordinadorDatosOmie
from custom_components.optimizador_bateria_omie.entidades import (
    SensorPrecioActual,
    SensorPrecioClasificacion,
    SensorPrecioEfectivo,
    SensorPrecioEstadisticas,
    SensorPreciosEfectivosHoy,
)
from custom_components.optimizador_bateria_omie.entidades_plan import (
    SensorAhorroEstimadoHoy,
    SensorPlan,
    SensorPrediccionConsumo,
)
from custom_components.optimizador_bateria_omie.entidades_solarman import (
    SensorConsumoCasa,
    SensorCorrienteBateria,
    SensorFamiliaValidada,
    SensorPotenciaBateria,
    SensorProduccionFv,
    SensorSocBateria,
    SensorTemperaturaBateria,
    SensorTemperaturaInversor,
)
from custom_components.optimizador_bateria_omie.solarman_client import (
    ClienteSolarman,
)
from custom_components.optimizador_bateria_omie.tarifa import ModeloTarifa
from custom_components.optimizador_bateria_omie.telemetria import (
    CoordinadorTelemetria,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Configuración de los sensores."""
    config = ConfiguracionOmie(
        fuente_prioritaria=entry.data.get(
            "fuente_prioritaria", DEFAULT_FUENTE_PRIORITARIA
        ),
        usar_cache=entry.data.get("usar_cache", DEFAULT_USAR_CACHE),
        dias_cache=entry.data.get("dias_cache", DEFAULT_DIAS_CACHE),
    )
    cliente = ClienteOmie(hass, config)
    coordinador = CoordinadorDatosOmie(hass, cliente, ModeloTarifa(), entry)

    try:
        await coordinador.async_config_entry_first_refresh()
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning(
            "Sin datos iniciales: %s. Se reintentará en el próximo ciclo.", e
        )

    entidades = [
        SensorPrecioActual(coordinador),
        SensorPrecioEfectivo(coordinador),
        SensorPrecioClasificacion(coordinador),
        SensorPrecioEstadisticas(coordinador),
        SensorPreciosEfectivosHoy(coordinador)
    ]

    # Telemetría Solarman (F2): mapa y gestor ya cargados en el setup
    datos_dominio = hass.data[DOMAIN][entry.entry_id]
    gestor = datos_dominio["gestor"]

    cliente_solarman = None
    if entry.data.get(CONF_TELEMETRY_SOURCE, TELEMETRY_SOURCE_MODBUS) == (
        TELEMETRY_SOURCE_MODBUS
    ):
        cliente_solarman = ClienteSolarman(
            hass,
            ip=entry.data.get(CONF_IP, DEFAULT_IP),
            puerto=entry.data.get(CONF_PUERTO, DEFAULT_PUERTO),
            serie=entry.data.get(CONF_SERIE, DEFAULT_SERIE),
            mapa=datos_dominio["mapa"],
        )

    coordinador_telemetria = CoordinadorTelemetria(
        hass, cliente_solarman, gestor, entry
    )
    datos_dominio["coordinador_telemetria"] = coordinador_telemetria

    entidades.extend([
        SensorFamiliaValidada(gestor),
        SensorSocBateria(coordinador_telemetria),
        SensorPotenciaBateria(coordinador_telemetria),
        SensorCorrienteBateria(coordinador_telemetria),
        SensorConsumoCasa(coordinador_telemetria),
        SensorProduccionFv(coordinador_telemetria),
        SensorTemperaturaInversor(coordinador_telemetria),
        SensorTemperaturaBateria(coordinador_telemetria),
    ])

    # Entidades del plan (F3): reusan el CoordinadorPlan del setup
    if "coordinador_plan" in datos_dominio:
        coordinador_plan = datos_dominio["coordinador_plan"]
        entidades.extend([
            SensorPrediccionConsumo(coordinador_plan),
            SensorPlan(coordinador_plan),
            SensorAhorroEstimadoHoy(coordinador_plan),
        ])

    async_add_entities(entidades)
