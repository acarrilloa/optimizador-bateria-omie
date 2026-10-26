"""Constantes para el componente Optimizador Batería OMIE."""
# versión 0.8.0

DOMAIN = "optimizador_bateria_omie"

# Store de validación de familia (probe_registers): hass.storage
# genera <key>.json bajo .storage/
STORE_KEY_PROBE = f"{DOMAIN}/probe"
VERSION_STORE_PROBE = 1

# Señal dispatcher al cambiar el estado de validación
SIGNAL_VALIDACION_CAMBIADA = f"{DOMAIN}_validacion_cambiada"

# Telemetría Solarman (F2)
CONF_IP = "ip"
CONF_PUERTO = "puerto"
CONF_SERIE = "serie"
CONF_TELEMETRY_SOURCE = "telemetry_source"
CONF_INTERVALO_TELEMETRIA = "intervalo_telemetria_s"
CONF_ENTITY_ID_SOC = "entity_id_soc"
CONF_ENTITY_ID_PBAT = "entity_id_pbat"
CONF_ENTITY_ID_CONSUMO = "entity_id_consumo"
CONF_ENTITY_ID_FV = "entity_id_fv"

TELEMETRY_SOURCE_MODBUS = "solarman_modbus"
TELEMETRY_SOURCE_HA = "ha_entities"

# Defaults de conexión (D10): editables en el config flow
DEFAULT_IP = "192.168.1.76"
DEFAULT_PUERTO = 8899
DEFAULT_SERIE = 3178171873
DEFAULT_TELEMETRY_SOURCE = TELEMETRY_SOURCE_MODBUS
DEFAULT_INTERVALO_TELEMETRIA = 30

# Servicio probe_registers (F2)
SERVICE_PROBE_REGISTERS = "probe_registers"
TOLERANCIA_PROBE_PCT = 2.0

# Configuración por defecto
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