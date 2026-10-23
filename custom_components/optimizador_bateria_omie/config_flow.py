"""Configuración flow para la integración Optimizador Batería OMIE."""
# versión 0.7.3

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from custom_components.optimizador_bateria_omie.const import (
    DEFAULT_DIAS_CACHE,
    DEFAULT_FUENTE_PRIORITARIA,
    DEFAULT_USAR_CACHE,
    DOMAIN,
)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("fuente_prioritaria", default=DEFAULT_FUENTE_PRIORITARIA): vol.In(
            ["omie", "redata"]
        ),
        vol.Required("usar_cache", default=DEFAULT_USAR_CACHE): bool,
        vol.Required("dias_cache", default=DEFAULT_DIAS_CACHE): vol.All(
            int, vol.Range(min=1, max=30)
        ),
    }
)


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Maneja el flujo de configuración."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Formulario inicial. HA valida user_input contra CONFIG_SCHEMA."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="Optimizador Batería OMIE", data=user_input
            )

        return self.async_show_form(
            step_id="user",
            data_schema=CONFIG_SCHEMA,
        )