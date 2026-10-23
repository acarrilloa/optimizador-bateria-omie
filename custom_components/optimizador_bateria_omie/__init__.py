"""Custom component para optimizar el uso de baterías con precios OMIE."""
# versión 0.7.0

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configuración de la integración."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Desconfiguración de la integración."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
