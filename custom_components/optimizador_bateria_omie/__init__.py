"""Custom component para optimizar el uso de baterías con precios OMIE."""
# versión 0.8.0

import importlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.optimizador_bateria_omie.const import DOMAIN
from custom_components.optimizador_bateria_omie.mapa_registros import (
    MapaRegistros,
)
from custom_components.optimizador_bateria_omie.services import (
    async_setup_services,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    GestorValidacion,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configuración de la integración."""
    hass.data.setdefault(DOMAIN, {})

    # F2: mapa de registros y Store de validación de familia
    mapa = MapaRegistros(hass)
    await mapa.async_cargar()
    gestor = GestorValidacion(hass)
    await gestor.async_revalidar(mapa)

    hass.data[DOMAIN][entry.entry_id] = {
        "mapa": mapa,
        "gestor": gestor,
    }

    async_setup_services(hass)

    await hass.async_add_executor_job(_cargar_plataformas)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Desconfiguración de la integración."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
