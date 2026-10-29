"""Constantes para el componente Optimizador Batería OMIE."""
# versión 0.8.10

DOMAIN = "optimizador_bateria_omie"

# Store de validación de familia (probe_registers): hass.storage
# genera <key>.json bajo .storage/
STORE_KEY_PROBE = f"{DOMAIN}/probe"
VERSION_STORE_PROBE = 1

# Señal dispatcher al cambiar el estado de validación
SIGNAL_VALIDACION_CAMBIADA = f"{DOMAIN}_validacion_cambiada"

# --- Telemetría Solarman (F2) ---
CONF_IP = "ip"
CONF_PUERTO = "puerto"
CONF_SERIE = "serie"
CONF_TELEMETRY_SOURCE = "telemetry_source"
CONF_INTERVALO_TELEMETRIA = "intervalo_telemetria_s"

# Configuración de entidades externas (telemetry_source: ha_entities)
CONF_ENTITY_ID_SOC_BATERIA = "entity_id_soc_bateria"
CONF_ENTITY_ID_POTENCIA_BATERIA = "entity_id_potencia_bateria"
CONF_ENTITY_ID_CONSUMO_CASA = "entity_id_consumo_casa"
CONF_ENTITY_ID_PRODUCCION_FV = "entity_id_produccion_fv"
CONF_ENTITY_ID_TEMP_INVERSOR = "entity_id_temp_inversor"
CONF_ENTITY_ID_TEMP_BATERIA = "entity_id_temp_bateria"

# Fuentes de telemetría (unificado con config_flow.py)
TELEMETRY_SOURCE_MODBUS = "solarman_modbus"
TELEMETRY_SOURCE_HA_ENTITIES = "ha_entities"

# Defaults de conexión (D10): editables en el config flow
DEFAULT_IP = "192.168.1.76"
DEFAULT_PUERTO = 8899
DEFAULT_SERIE = "3178171873"  # String para consistencia con el config_flow
DEFAULT_TELEMETRY_SOURCE = TELEMETRY_SOURCE_MODBUS
DEFAULT_INTERVALO_TELEMETRIA = 30

# Servicio probe_registers (F2)
SERVICE_PROBE_REGISTERS = "probe_registers"
TOLERANCIA_PROBE_PCT = 2.0

# --- Predicción y planificador (F3) ---
CONF_VENTANA_HISTORICO = "ventana_historico_dias"
CONF_PERMITIR_CARGA_RED = "permitir_carga_red"
CONF_PERMITIR_VENTA_RED = "permitir_venta_red"
CONF_PRECIO_MAX_COMPRA = "precio_max_compra"
CONF_PRECIO_MIN_VENTA = "precio_min_venta"
CONF_MARGEN_ARBITRAJE = "margen_arbitraje"
CONF_SOC_MIN = "soc_min"
CONF_SOC_MAX = "soc_max"
CONF_POTENCIA_MAX_CARGA = "potencia_max_carga"
CONF_POTENCIA_MAX_DESCARGA = "potencia_max_descarga"
CONF_POTENCIA_CONTRATADA = "potencia_contratada"
CONF_MARGEN_POTENCIA = "margen_potencia"
CONF_EFICIENCIA_ROUND_TRIP = "eficiencia_round_trip"
CONF_DESVIACION_SOC_REPLAN = "desviacion_soc_replan_pct"
CONF_FESTIVOS_LOCALES = "festivos_locales"
CONF_CAPACIDAD_BATERIA_KWH = "capacidad_bateria_kwh"
CONF_ENTITY_ID_PREVISION_FV_HOY = "entity_id_prevision_fv_hoy"
CONF_ENTITY_ID_PREVISION_FV_MANANA = "entity_id_prevision_fv_manana"

DEFAULT_VENTANA_HISTORICO = 28
DEFAULT_PERMITIR_CARGA_RED = True
DEFAULT_PERMITIR_VENTA_RED = True
DEFAULT_PRECIO_MAX_COMPRA = 0.09
DEFAULT_PRECIO_MIN_VENTA = 0.18
DEFAULT_MARGEN_ARBITRAJE = 0.015
DEFAULT_SOC_MIN = 15.0
DEFAULT_SOC_MAX = 95.0
DEFAULT_POTENCIA_MAX_CARGA = 5000.0
DEFAULT_POTENCIA_MAX_DESCARGA = 5000.0
DEFAULT_POTENCIA_CONTRATADA = 5750.0
DEFAULT_MARGEN_POTENCIA = 300.0
DEFAULT_EFICIENCIA_ROUND_TRIP = 88  # % (entero: el validador exige int)
DEFAULT_DESVIACION_SOC_REPLAN = 10.0
DEFAULT_FESTIVOS_LOCALES: list[str] = []
DEFAULT_CAPACIDAD_BATERIA_KWH = 15.36
DEFAULT_ENTITY_ID_PREVISION_FV_HOY = (
    "sensor.solcast_pv_forecast_forecast_today"
)
DEFAULT_ENTITY_ID_PREVISION_FV_MANANA = (
    "sensor.solcast_pv_forecast_forecast_tomorrow"
)

# Servicio del plan (F3)
SERVICE_REGENERATE_PLAN = "regenerate_plan"

# Fuentes de la previsión FV (F3, D8)
FUENTE_FV_SOLCAST = "solcast"
FUENTE_FV_PERFIL = "perfil"
FUENTE_FV_NONE = "none"

# Configuración por defecto (F1)
DEFAULT_FUENTE_PRIORITARIA = "omie"
DEFAULT_USAR_CACHE = True
DEFAULT_DIAS_CACHE = 7

# Unique IDs de sensores (prefijados con el dominio para evitar colisiones)
SENSOR_PRECIO_ACTUAL = f"{DOMAIN}_precio_actual"
SENSOR_PRECIO_EFECTIVO = f"{DOMAIN}_precio_efectivo"
SENSOR_PRECIO_CLASIFICACION = f"{DOMAIN}_precio_clasificacion"
SENSOR_PRECIO_ESTADISTICAS = f"{DOMAIN}_precio_estadisticas"
SENSOR_FAMILIA_VALIDADA = f"{DOMAIN}_familia_validada"
SENSOR_SOC_BATERIA = f"{DOMAIN}_soc_bateria"
SENSOR_POTENCIA_BATERIA = f"{DOMAIN}_potencia_bateria"
SENSOR_CORRIENTE_BATERIA = f"{DOMAIN}_corriente_bateria"
SENSOR_CONSUMO_CASA = f"{DOMAIN}_consumo_casa"
SENSOR_PRODUCCION_FV = f"{DOMAIN}_produccion_fv"
# Nuevos unique_ids para temperatura
SENSOR_TEMPERATURA_INVERSOR = f"{DOMAIN}_temperatura_inversor"
SENSOR_TEMPERATURA_BATERIA = f"{DOMAIN}_temperatura_bateria"

# Unique IDs de F3 (predicción, plan, ahorro estimado y festivo)
SENSOR_PREDICCION_CONSUMO = f"{DOMAIN}_prediccion_consumo"
SENSOR_PLAN = f"{DOMAIN}_plan"
SENSOR_AHORRO_ESTIMADO_HOY = f"{DOMAIN}_ahorro_estimado_hoy"
SWITCH_FESTIVO_HOY = f"{DOMAIN}_festivo_hoy"

# Atributos
ATTR_MIN = "min"
ATTR_MAX = "max"
ATTR_MEDIANA = "mediana"
ATTR_FUENTE = "fuente"
ATTR_TS_ACTUALIZACION = "ts_actualizacion"

# Festivos nacionales España: tuplas (mes, día)
FESTIVOS_ESPANA = {
    (1, 1),   # Año Nuevo
    (1, 6),   # Día de Reyes
    (5, 1),   # Fiesta del Trabajo
    (8, 15),  # Asunción de la Virgen
    (10, 12), # Fiesta Nacional de España
    (11, 1),  # Todos los Santos
    (12, 6),  # Día de la Constitución
    (12, 8),  # Inmaculada Concepción
    (12, 25), # Navidad
}