"""Flujo de configuración para la integración Optimizador Batería OMIE."""
# versión 0.8.8

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from custom_components.optimizador_bateria_omie.const import (
    CONF_CAPACIDAD_BATERIA_KWH,
    CONF_DESVIACION_SOC_REPLAN,
    CONF_EFICIENCIA_ROUND_TRIP,
    CONF_ENTITY_ID_CONSUMO_CASA,
    CONF_ENTITY_ID_POTENCIA_BATERIA,
    CONF_ENTITY_ID_PREVISION_FV_HOY,
    CONF_ENTITY_ID_PREVISION_FV_MANANA,
    CONF_ENTITY_ID_PRODUCCION_FV,
    CONF_ENTITY_ID_SOC_BATERIA,
    CONF_ENTITY_ID_TEMP_BATERIA,
    CONF_ENTITY_ID_TEMP_INVERSOR,
    CONF_FESTIVOS_LOCALES,
    CONF_INTERVALO_TELEMETRIA,
    CONF_IP,
    CONF_MARGEN_ARBITRAJE,
    CONF_MARGEN_POTENCIA,
    CONF_PERMITIR_CARGA_RED,
    CONF_PERMITIR_VENTA_RED,
    CONF_POTENCIA_CONTRATADA,
    CONF_POTENCIA_MAX_CARGA,
    CONF_POTENCIA_MAX_DESCARGA,
    CONF_PRECIO_MAX_COMPRA,
    CONF_PRECIO_MIN_VENTA,
    CONF_PUERTO,
    CONF_SERIE,
    CONF_SOC_MAX,
    CONF_SOC_MIN,
    CONF_TELEMETRY_SOURCE,
    CONF_VENTANA_HISTORICO,
    DEFAULT_CAPACIDAD_BATERIA_KWH,
    DEFAULT_DESVIACION_SOC_REPLAN,
    DEFAULT_DIAS_CACHE,
    DEFAULT_EFICIENCIA_ROUND_TRIP,
    DEFAULT_ENTITY_ID_PREVISION_FV_HOY,
    DEFAULT_ENTITY_ID_PREVISION_FV_MANANA,
    DEFAULT_FESTIVOS_LOCALES,
    DEFAULT_FUENTE_PRIORITARIA,
    DEFAULT_INTERVALO_TELEMETRIA,
    DEFAULT_IP,
    DEFAULT_MARGEN_ARBITRAJE,
    DEFAULT_MARGEN_POTENCIA,
    DEFAULT_PERMITIR_CARGA_RED,
    DEFAULT_PERMITIR_VENTA_RED,
    DEFAULT_POTENCIA_CONTRATADA,
    DEFAULT_POTENCIA_MAX_CARGA,
    DEFAULT_POTENCIA_MAX_DESCARGA,
    DEFAULT_PRECIO_MAX_COMPRA,
    DEFAULT_PRECIO_MIN_VENTA,
    DEFAULT_PUERTO,
    DEFAULT_SERIE,
    DEFAULT_SOC_MAX,
    DEFAULT_SOC_MIN,
    DEFAULT_USAR_CACHE,
    DEFAULT_VENTANA_HISTORICO,
    DOMAIN,
    TELEMETRY_SOURCE_HA_ENTITIES,
    TELEMETRY_SOURCE_MODBUS,
)

# Los festivos locales se editan como texto separado por comas
FESTIVOS_SEPARADOR = ","


def parsear_festivos(valor: Any) -> list[str]:
    """Convierte "MM-DD, MM-DD" (o lista) en lista de festivos."""
    if isinstance(valor, list):
        return [str(v).strip() for v in valor if str(v).strip()]
    return [
        parte.strip()
        for parte in (valor or "").split(FESTIVOS_SEPARADOR)
        if parte.strip()
    ]


def formatear_festivos(valor: Any) -> str:
    """Lista de festivos → texto editable del formulario."""
    if isinstance(valor, list):
        return ", ".join(str(v) for v in valor)
    return str(valor or "")

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
        # --- Parámetros del plan (F3, §5 del PLAN_F3) ---
        vol.Optional(
            CONF_VENTANA_HISTORICO, default=DEFAULT_VENTANA_HISTORICO
        ): vol.All(int, vol.Range(min=7, max=90)),
        vol.Optional(
            CONF_PERMITIR_CARGA_RED, default=DEFAULT_PERMITIR_CARGA_RED
        ): bool,
        vol.Optional(
            CONF_PERMITIR_VENTA_RED, default=DEFAULT_PERMITIR_VENTA_RED
        ): bool,
        vol.Optional(
            CONF_PRECIO_MAX_COMPRA, default=DEFAULT_PRECIO_MAX_COMPRA
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional(
            CONF_PRECIO_MIN_VENTA, default=DEFAULT_PRECIO_MIN_VENTA
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional(
            CONF_MARGEN_ARBITRAJE, default=DEFAULT_MARGEN_ARBITRAJE
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=0.5)),
        vol.Optional(CONF_SOC_MIN, default=DEFAULT_SOC_MIN): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_SOC_MAX, default=DEFAULT_SOC_MAX): vol.All(
            vol.Coerce(float), vol.Range(min=10, max=100)
        ),
        vol.Optional(
            CONF_POTENCIA_MAX_CARGA, default=DEFAULT_POTENCIA_MAX_CARGA
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
        vol.Optional(
            CONF_POTENCIA_MAX_DESCARGA,
            default=DEFAULT_POTENCIA_MAX_DESCARGA,
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
        vol.Optional(
            CONF_POTENCIA_CONTRATADA, default=DEFAULT_POTENCIA_CONTRATADA
        ): vol.All(vol.Coerce(float), vol.Range(min=1000, max=15000)),
        vol.Optional(
            CONF_MARGEN_POTENCIA, default=DEFAULT_MARGEN_POTENCIA
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=2000)),
        vol.Optional(
            CONF_EFICIENCIA_ROUND_TRIP, default=DEFAULT_EFICIENCIA_ROUND_TRIP
        ): vol.All(int, vol.Range(min=70, max=95)),
        vol.Optional(
            CONF_DESVIACION_SOC_REPLAN, default=DEFAULT_DESVIACION_SOC_REPLAN
        ): vol.All(vol.Coerce(float), vol.Range(min=5, max=30)),
        vol.Optional(CONF_FESTIVOS_LOCALES, default=""): str,
        vol.Optional(
            CONF_CAPACIDAD_BATERIA_KWH, default=DEFAULT_CAPACIDAD_BATERIA_KWH
        ): vol.All(vol.Coerce(float), vol.Range(min=5, max=50)),
        vol.Optional(
            CONF_ENTITY_ID_PREVISION_FV_HOY,
            default=DEFAULT_ENTITY_ID_PREVISION_FV_HOY,
        ): str,
        vol.Optional(
            CONF_ENTITY_ID_PREVISION_FV_MANANA,
            default=DEFAULT_ENTITY_ID_PREVISION_FV_MANANA,
        ): str,
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
            datos = dict(user_input)
            datos[CONF_FESTIVOS_LOCALES] = parsear_festivos(
                datos.get(CONF_FESTIVOS_LOCALES)
            )
            return self.async_create_entry(
                title="Optimizador Batería OMIE", data=datos
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
            datos = dict(user_input)
            datos[CONF_FESTIVOS_LOCALES] = parsear_festivos(
                datos.get(CONF_FESTIVOS_LOCALES)
            )
            return self.async_update_reload_and_abort(
                entry, data_updates=datos
            )

        sugeridos = dict(entry.data)
        sugeridos[CONF_FESTIVOS_LOCALES] = formatear_festivos(
            sugeridos.get(CONF_FESTIVOS_LOCALES)
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                CONFIG_SCHEMA, sugeridos
            ),
        )