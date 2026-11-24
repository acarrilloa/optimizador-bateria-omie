"""Flujo de configuración para la integración Optimizador Batería OMIE."""
# versión 0.10.0

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from custom_components.optimizador_bateria_omie.const import (
    CONF_ACTUADOR_TOU,
    CONF_APLICAR_IMPUESTOS_PRECIO,
    CONF_ARRANQUE_CARGA_RED_SOC,
    CONF_ARRANQUE_CARGA_RED_V,
    CONF_CAPACIDAD_BATERIA_KWH,
    CONF_CARGO_SISTEMA_LLANO,
    CONF_CARGO_SISTEMA_PUNTA,
    CONF_CARGO_SISTEMA_VALLE,
    CONF_DESCARGA_MINIMA_BACKUP_A,
    CONF_DESVIACION_SOC_REPLAN,
    CONF_DEVICE_ID_SOLARMAN,
    CONF_EFICIENCIA_ROUND_TRIP,
    CONF_ENTITY_ID_CLIMA,
    CONF_ENTITY_ID_CONSUMO_CASA,
    CONF_ENTITY_ID_CORRIENTE_BATERIA,
    CONF_ENTITY_ID_POTENCIA_BATERIA,
    CONF_ENTITY_ID_POTENCIA_RED,
    CONF_ENTITY_ID_PREVISION_FV_HOY,
    CONF_ENTITY_ID_PREVISION_FV_MANANA,
    CONF_ENTITY_ID_PRODUCCION_FV,
    CONF_ENTITY_ID_SOC_BATERIA,
    CONF_ENTITY_ID_TEMP_BATERIA,
    CONF_ENTITY_ID_TEMP_HISTORICO,
    CONF_ENTITY_ID_TEMP_INVERSOR,
    CONF_ENTITY_IDS_FALLO,
    CONF_FACTOR_RITMO,
    CONF_FESTIVOS_LOCALES,
    CONF_IMPUESTO_ELECTRICO_PCT,
    CONF_INTERVALO_TELEMETRIA,
    CONF_IP,
    CONF_IVA_PCT,
    CONF_LATENCIA_VERIFY_S,
    CONF_MARGEN_ARBITRAJE,
    CONF_MARGEN_COBERTURA_WH,
    CONF_MARGEN_POTENCIA,
    CONF_MAX_ESCRITURAS_HORA,
    CONF_MAX_TABLAS_TOU_HORA,
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
    CONF_SOC_STOP_CARGA_DEFAULT,
    CONF_TELEMETRY_SOURCE,
    CONF_VENTANA_HISTORICO,
    CONF_VERIFY_TOLERANCIA,
    CONF_VOLTAJE_BATERIA_NOMINAL,
    DEFAULT_ACTUADOR_TOU,
    DEFAULT_APLICAR_IMPUESTOS_PRECIO,
    DEFAULT_ARRANQUE_CARGA_RED_SOC,
    DEFAULT_ARRANQUE_CARGA_RED_V,
    DEFAULT_CAPACIDAD_BATERIA_KWH,
    DEFAULT_CARGO_SISTEMA_LLANO,
    DEFAULT_CARGO_SISTEMA_PUNTA,
    DEFAULT_CARGO_SISTEMA_VALLE,
    DEFAULT_DESCARGA_MINIMA_BACKUP_A,
    DEFAULT_DESVIACION_SOC_REPLAN,
    DEFAULT_DIAS_CACHE,
    DEFAULT_EFICIENCIA_ROUND_TRIP,
    DEFAULT_ENTITY_ID_CORRIENTE_BATERIA,
    DEFAULT_ENTITY_ID_PREVISION_FV_HOY,
    DEFAULT_ENTITY_ID_PREVISION_FV_MANANA,
    DEFAULT_FACTOR_RITMO,
    DEFAULT_FUENTE_PRIORITARIA,
    DEFAULT_IMPUESTO_ELECTRICO_PCT,
    DEFAULT_INTERVALO_TELEMETRIA,
    DEFAULT_IP,
    DEFAULT_IVA_PCT,
    DEFAULT_LATENCIA_VERIFY_S,
    DEFAULT_MARGEN_ARBITRAJE,
    DEFAULT_MARGEN_POTENCIA,
    DEFAULT_MAX_ESCRITURAS_HORA,
    DEFAULT_MAX_TABLAS_TOU_HORA,
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
            ["omie"]
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
        # F5.2: puntos de arranque de la carga desde red (228/229).
        # Por defecto no bloquean (6100 = 61 V > tensión del banco;
        # SOC 100), pero 229 debe ser configurable si el datalogger
        # lo rebota (adenda: bajar a 90).
        vol.Optional(
            CONF_ARRANQUE_CARGA_RED_V, default=DEFAULT_ARRANQUE_CARGA_RED_V
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=6300)),
        vol.Optional(
            CONF_ARRANQUE_CARGA_RED_SOC, default=DEFAULT_ARRANQUE_CARGA_RED_SOC
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        # F6/A (24-nov-2026): tabla ToU del Programa 1 como actuador.
        # Con 248=1 los amperajes solos producen "float": la energía
        # por batería la gobierna la tabla 250-279. False conserva el
        # esquema v0.14.x.
        vol.Optional(
            CONF_ACTUADOR_TOU, default=DEFAULT_ACTUADOR_TOU
        ): bool,
        # SOC-stop del segmento de carga (vacío = soc_max)
        vol.Optional(CONF_SOC_STOP_CARGA_DEFAULT): vol.All(
            vol.Coerce(int), vol.Range(min=10, max=100)
        ),
        vol.Optional(
            CONF_MAX_TABLAS_TOU_HORA, default=DEFAULT_MAX_TABLAS_TOU_HORA
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=6)),
        # Respaldo de descarga durante CARGA (receta D1: 0). Si D1
        # confirma que la carga funciona igual con 211≠0, subir da
        # respaldo ante corte nocturno de red
        vol.Optional(
            CONF_DESCARGA_MINIMA_BACKUP_A,
            default=DEFAULT_DESCARGA_MINIMA_BACKUP_A,
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=135)),
        # F6/B: check de cobertura U1 (vacío = 20 % de la capacidad)
        vol.Optional(CONF_MARGEN_COBERTURA_WH): vol.All(
            vol.Coerce(float), vol.Range(min=0)
        ),
        vol.Optional(
            CONF_FACTOR_RITMO, default=DEFAULT_FACTOR_RITMO
        ): vol.All(vol.Coerce(float), vol.Range(min=1.1, max=3.0)),
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
        # --- Clima para el forecaster ML (F5-U5, AEMET; opcional) ---
        vol.Optional(CONF_ENTITY_ID_CLIMA, default=""): str,
        vol.Optional(CONF_ENTITY_ID_TEMP_HISTORICO, default=""): str,
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
    """218 > 217 siempre: soc_recuperacion > soc_min (semántica Deye).

    **Clampa en vez de fallar** (0.14.2): si el ``number`` de SOC
    mínimo se movió en caliente hasta o por encima del SOC de
    recuperación, el reconfigure recibía ``soc_rec <= soc_min`` y la
    ``vol.Invalid`` lanzada fuera del contexto de voluptuous abortaba
    el flujo con "Unknown error occurred" — y ningún cambio del
    formulario (p. ej. ``modo_activacion``) se guardaba. Se aplica la
    misma semántica de clamp que el executor al escribir 217/218:
    ``recuperación = max(recuperación, mínimo + 1)``.
    """
    soc_min = float(datos.get(CONF_SOC_MIN, DEFAULT_SOC_MIN))
    soc_rec = float(
        datos.get(CONF_SOC_RECUPERACION, DEFAULT_SOC_RECUPERACION)
    )
    if soc_rec <= soc_min:
        datos[CONF_SOC_RECUPERACION] = round(soc_min + 1, 1)
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