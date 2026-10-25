"""Plataforma de sensores para la integración Optimizador Batería OMIE."""
# versión 0.7.6

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.optimizador_bateria_omie.cliente_omie import (
    ClienteOmie,
    ConfiguracionOmie,
)
from custom_components.optimizador_bateria_omie.const import (
    DEFAULT_DIAS_CACHE,
    DEFAULT_FUENTE_PRIORITARIA,
    DEFAULT_USAR_CACHE,
)
from custom_components.optimizador_bateria_omie.coordinador import CoordinadorDatosOmie
from custom_components.optimizador_bateria_omie.entidades import (
    SensorPrecioActual,
    SensorPrecioClasificacion,
    SensorPrecioEfectivo,
    SensorPrecioEstadisticas,
    SensorPreciosEfectivosHoy,
)
from custom_components.optimizador_bateria_omie.tarifa import ModeloTarifa

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

    async_add_entities(
        [
            SensorPrecioActual(coordinador),
            SensorPrecioEfectivo(coordinador),
            SensorPrecioClasificacion(coordinador),
            SensorPrecioEstadisticas(coordinador),
            SensorPreciosEfectivosHoy(coordinador),
        ]
    )