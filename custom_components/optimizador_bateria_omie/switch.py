"""Plataforma switch: Festivo hoy (F3, D15)."""
# versión 0.9.0

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.optimizador_bateria_omie.const import DOMAIN
from custom_components.optimizador_bateria_omie.entidades_plan import (
    SwitchFestivoHoy,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Añade el switch de festivo manual."""
    datos = hass.data[DOMAIN][entry.entry_id]
    coordinador_plan = datos["coordinador_plan"]
    async_add_entities([SwitchFestivoHoy(coordinador_plan)])
