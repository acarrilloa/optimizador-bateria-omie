"""Flujo de configuración para la integración Optimizador Batería OMIE."""
# versión 0.8.8

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from custom_components.optimizador_bateria_omie.const import (
    CONF_ENTITY_ID_CONSUMO_CASA,
    CONF_ENTITY_ID_POTENCIA_BATERIA,
    CONF_ENTITY_ID_PRODUCCION_FV,
    CONF_ENTITY_ID_SOC_BATERIA,
    CONF_ENTITY_ID_TEMP_BATERIA,
    CONF_ENTITY_ID_TEMP_INVERSOR,
    CONF_INTERVALO_TELEMETRIA,
    CONF_IP,
    CONF_PUERTO,
    CONF_SERIE,
    CONF_TELEMETRY_SOURCE,
    DEFAULT_DIAS_CACHE,
    DEFAULT_FUENTE_PRIORITARIA,
    DEFAULT_INTERVALO_TELEMETRIA,
    DEFAULT_IP,
    DEFAULT_PUERTO,
    DEFAULT_SERIE,
    DEFAULT_USAR_CACHE,
    DOMAIN,
    TELEMETRY_SOURCE_HA_ENTITIES,
    TELEMETRY_SOURCE_MODBUS,
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
        vol.Required(
            CONF_TELEMETRY_SOURCE, default=TELEMETRY_SOURCE_MODBUS
        ): vol.In([TELEMETRY_SOURCE_MODBUS, TELEMETRY_SOURCE_HA_ENTITIES]),
        vol.Optional(CONF_IP, default=DEFAULT_IP): str,
        vol.Optional(CONF_PUERTO, default=DEFAULT_PUERTO): int,
        vol.Optional(CONF_SERIE, default=DEFAULT_SERIE): str,
        vol.Optional(
            CONF_INTERVALO_TELEMETRIA, default=DEFAULT_INTERVALO_TELEMETRIA
        ): vol.All(int, vol.Range(min=10, max=300)),
        vol.Optional(CONF_ENTITY_ID_SOC_BATERIA, default=""): str,
        vol.Optional(CONF_ENTITY_ID_POTENCIA_BATERIA, default=""): str,
        vol.Optional(CONF_ENTITY_ID_CONSUMO_CASA, default=""): str,
        vol.Optional(CONF_ENTITY_ID_PRODUCCION_FV, default=""): str,
        vol.Optional(CONF_ENTITY_ID_TEMP_INVERSOR, default=""): str,
        vol.Optional(CONF_ENTITY_ID_TEMP_BATERIA, default=""): str,
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Permite editar la configuración existente sin borrar la entrada."""
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry, data_updates=user_input
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                CONFIG_SCHEMA, entry.data
            ),
        )