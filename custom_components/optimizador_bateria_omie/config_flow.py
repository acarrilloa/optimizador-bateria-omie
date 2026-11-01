"""Flujo de configuración para la integración Optimizador Batería OMIE."""
# versión 0.9.0

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from custom_components.optimizador_bateria_omie.const import (
    CONF_APLICAR_IMPUESTOS_PRECIO,
    CONF_CAPACIDAD_BATERIA_KWH,
    CONF_CARGO_SISTEMA_LLANO,
    CONF_CARGO_SISTEMA_PUNTA,
    CONF_CARGO_SISTEMA_VALLE,
    CONF_DESVIACION_SOC_REPLAN,
    CONF_DEVICE_ID_SOLARMAN,
    CONF_EFICIENCIA_ROUND_TRIP,
    CONF_ENTITY_ID_CONSUMO_CASA,
    CONF_ENTITY_ID_CORRIENTE_BATERIA,
    CONF_ENTITY_ID_POTENCIA_BATERIA,
    CONF_ENTITY_ID_POTENCIA_RED,
    CONF_ENTITY_ID_PREVISION_FV_HOY,
    CONF_ENTITY_ID_PREVISION_FV_MANANA,
    CONF_ENTITY_ID_PRODUCCION_FV,
    CONF_ENTITY_ID_SOC_BATERIA,
    CONF_ENTITY_ID_TEMP_BATERIA,
    CONF_ENTITY_ID_TEMP_INVERSOR,
    CONF_ENTITY_IDS_FALLO,
    CONF_FESTIVOS_LOCALES,
    CONF_IMPUESTO_ELECTRICO_PCT,
    CONF_INTERVALO_TELEMETRIA,
    CONF_IP,
    CONF_IVA_PCT,
    CONF_LATENCIA_VERIFY_S,
    CONF_MARGEN_ARBITRAJE,
    CONF_MARGEN_POTENCIA,
    CONF_MAX_ESCRITURAS_HORA,
    CONF_MODO_ACTIVACION,
    CONF_PEAYE_ACCESO_LLANO,
    CONF_PEAYE_ACCESO_PUNTA,
    CONF_PEAYE_ACCESO_VALLE,
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
    CONF_SOC_RECUPERACION,
    CONF_TELEMETRY_SOURCE,
    CONF_VENTANA_HISTORICO,
    CONF_VERIFY_TOLERANCIA,
    CONF_VOLTAJE_BATERIA_NOMINAL,
    DEFAULT_APLICAR_IMPUESTOS_PRECIO,
    DEFAULT_CAPACIDAD_BATERIA_KWH,
    DEFAULT_CARGO_SISTEMA_LLANO,
    DEFAULT_CARGO_SISTEMA_PUNTA,
    DEFAULT_CARGO_SISTEMA_VALLE,
    DEFAULT_DESVIACION_SOC_REPLAN,
    DEFAULT_DIAS_CACHE,
    DEFAULT_EFICIENCIA_ROUND_TRIP,
    DEFAULT_ENTITY_ID_CORRIENTE_BATERIA,
    DEFAULT_ENTITY_ID_PREVISION_FV_HOY,
    DEFAULT_ENTITY_ID_PREVISION_FV_MANANA,
    DEFAULT_FUENTE_PRIORITARIA,
    DEFAULT_IMPUESTO_ELECTRICO_PCT,
    DEFAULT_INTERVALO_TELEMETRIA,
    DEFAULT_IP,
    DEFAULT_IVA_PCT,
    DEFAULT_LATENCIA_VERIFY_S,
    DEFAULT_MARGEN_ARBITRAJE,
    DEFAULT_MARGEN_POTENCIA,
    DEFAULT_MAX_ESCRITURAS_HORA,
    DEFAULT_MODO_ACTIVACION,
    DEFAULT_PEAYE_ACCESO_LLANO,
    DEFAULT_PEAYE_ACCESO_PUNTA,
    DEFAULT_PEAYE_ACCESO_VALLE,
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
    DEFAULT_SOC_RECUPERACION,
    DEFAULT_USAR_CACHE,
    DEFAULT_VENTANA_HISTORICO,
    DEFAULT_VERIFY_TOLERANCIA,
    DEFAULT_VOLTAJE_BATERIA_NOMINAL,
    DOMAIN,
    MODO_ACTIVACION_COMPLETO,
    MODO_ACTIVACION_GRADUAL,
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
        vol.Optional(
            CONF_ENTITY_ID_CORRIENTE_BATERIA,
            default=DEFAULT_ENTITY_ID_CORRIENTE_BATERIA,
        ): str,
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
        # --- Parámetros de control (F4, §5 del PLAN_F4) ---
        vol.Optional(
            CONF_MODO_ACTIVACION, default=DEFAULT_MODO_ACTIVACION
        ): vol.In(
            [MODO_ACTIVACION_GRADUAL, MODO_ACTIVACION_COMPLETO]
        ),
        vol.Optional(
            CONF_MAX_ESCRITURAS_HORA, default=DEFAULT_MAX_ESCRITURAS_HORA
        ): vol.All(int, vol.Range(min=1, max=48)),
        vol.Optional(
            CONF_LATENCIA_VERIFY_S, default=DEFAULT_LATENCIA_VERIFY_S
        ): vol.All(int, vol.Range(min=1, max=15)),
        vol.Optional(
            CONF_VERIFY_TOLERANCIA, default=DEFAULT_VERIFY_TOLERANCIA
        ): vol.All(int, vol.Range(min=0, max=5)),
        vol.Optional(
            CONF_VOLTAJE_BATERIA_NOMINAL,
            default=DEFAULT_VOLTAJE_BATERIA_NOMINAL,
        ): vol.All(vol.Coerce(float), vol.Range(min=40, max=60)),
        vol.Optional(
            CONF_SOC_RECUPERACION, default=DEFAULT_SOC_RECUPERACION
        ): vol.All(vol.Coerce(float), vol.Range(min=10, max=100)),
        vol.Optional(CONF_ENTITY_ID_POTENCIA_RED, default=""): str,
        vol.Optional(CONF_ENTITY_IDS_FALLO, default=""): str,
        vol.Optional(CONF_DEVICE_ID_SOLARMAN, default=""): str,
        # --- Tarifa desde entry.data (F4, deuda #5) ---
        vol.Optional(
            CONF_PEAYE_ACCESO_PUNTA, default=DEFAULT_PEAYE_ACCESO_PUNTA
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional(
            CONF_PEAYE_ACCESO_LLANO, default=DEFAULT_PEAYE_ACCESO_LLANO
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional(
            CONF_PEAYE_ACCESO_VALLE, default=DEFAULT_PEAYE_ACCESO_VALLE
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional(
            CONF_CARGO_SISTEMA_PUNTA, default=DEFAULT_CARGO_SISTEMA_PUNTA
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional(
            CONF_CARGO_SISTEMA_LLANO, default=DEFAULT_CARGO_SISTEMA_LLANO
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional(
            CONF_CARGO_SISTEMA_VALLE, default=DEFAULT_CARGO_SISTEMA_VALLE
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional(
            CONF_IMPUESTO_ELECTRICO_PCT,
            default=DEFAULT_IMPUESTO_ELECTRICO_PCT,
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=0.25)),
        vol.Optional(
            CONF_IVA_PCT, default=DEFAULT_IVA_PCT
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=0.3)),
        vol.Optional(
            CONF_APLICAR_IMPUESTOS_PRECIO,
            default=DEFAULT_APLICAR_IMPUESTOS_PRECIO,
        ): bool,
    }
)


def validar_soc_recuperacion(datos: dict[str, Any]) -> dict[str, Any]:
    """218 > 217 siempre: soc_recuperacion > soc_min (semántica Deye)."""
    soc_min = float(datos.get(CONF_SOC_MIN, DEFAULT_SOC_MIN))
    soc_rec = float(
        datos.get(CONF_SOC_RECUPERACION, DEFAULT_SOC_RECUPERACION)
    )
    if soc_rec <= soc_min:
        raise vol.Invalid(
            f"soc_recuperacion ({soc_rec}) debe ser mayor que "
            f"soc_min ({soc_min}): recuperación > corte"
        )
    return datos


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
            datos = validar_soc_recuperacion(dict(user_input))
            datos[CONF_FESTIVOS_LOCALES] = parsear_festivos(
                datos.get(CONF_FESTIVOS_LOCALES)
            )
            datos[CONF_ENTITY_IDS_FALLO] = parsear_festivos(
                datos.get(CONF_ENTITY_IDS_FALLO)
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
            datos = validar_soc_recuperacion(dict(user_input))
            datos[CONF_FESTIVOS_LOCALES] = parsear_festivos(
                datos.get(CONF_FESTIVOS_LOCALES)
            )
            datos[CONF_ENTITY_IDS_FALLO] = parsear_festivos(
                datos.get(CONF_ENTITY_IDS_FALLO)
            )
            return self.async_update_reload_and_abort(
                entry, data_updates=datos
            )

        sugeridos = dict(entry.data)
        sugeridos[CONF_FESTIVOS_LOCALES] = formatear_festivos(
            sugeridos.get(CONF_FESTIVOS_LOCALES)
        )
        sugeridos[CONF_ENTITY_IDS_FALLO] = formatear_festivos(
            sugeridos.get(CONF_ENTITY_IDS_FALLO)
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                CONFIG_SCHEMA, sugeridos
            ),
        )