"""Plataforma number: parámetros del plan editables en caliente (F4)."""
# versión 0.11.6

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.optimizador_bateria_omie.const import DOMAIN
from custom_components.optimizador_bateria_omie.entidades_control import (
    crear_numbers,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Añade las 8 entities number de los parámetros del plan."""
    datos = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        crear_numbers(
            entry,
            datos["coordinador_plan"],
            datos.get("executor"),
        )
    )
