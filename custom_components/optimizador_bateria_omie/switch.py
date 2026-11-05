"""Plataforma switch: Festivo hoy (F3) y Control activo (F4)."""
# versión 0.10.1

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.optimizador_bateria_omie.const import DOMAIN
from custom_components.optimizador_bateria_omie.entidades_control import (
    SwitchControlActivo,
)
from custom_components.optimizador_bateria_omie.entidades_plan import (
    SwitchFestivoHoy,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Añade el switch de festivo manual y el de control activo."""
    datos = hass.data[DOMAIN][entry.entry_id]
    entidades = [SwitchFestivoHoy(datos["coordinador_plan"])]
    if "executor" in datos:
        entidades.append(SwitchControlActivo(datos["executor"]))
    async_add_entities(entidades)
