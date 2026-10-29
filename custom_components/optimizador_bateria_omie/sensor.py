"""Plataforma de sensores para la integración Optimizador Batería OMIE."""
# versión 0.9.0

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.optimizador_bateria_omie.const import DOMAIN
from custom_components.optimizador_bateria_omie.entidades import (
    SensorPrecioActual,
    SensorPrecioClasificacion,
    SensorPrecioEfectivo,
    SensorPrecioEstadisticas,
    SensorPreciosEfectivosHoy,
)
from custom_components.optimizador_bateria_omie.entidades_plan import (
    SensorAhorroEstimadoHoy,
    SensorPlan,
    SensorPrediccionConsumo,
)
from custom_components.optimizador_bateria_omie.entidades_solarman import (
    SensorConsumoCasa,
    SensorCorrienteBateria,
    SensorFamiliaValidada,
    SensorPotenciaBateria,
    SensorProduccionFv,
    SensorSocBateria,
    SensorTemperaturaBateria,
    SensorTemperaturaInversor,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Configuración de los sensores."""
    # Coordinadores creados en el setup de la integración (F1/F2/F3)
    datos_dominio = hass.data[DOMAIN][entry.entry_id]
    coordinador = datos_dominio["coordinador_precio"]
    gestor = datos_dominio["gestor"]

    entidades = [
        SensorPrecioActual(coordinador),
        SensorPrecioEfectivo(coordinador),
        SensorPrecioClasificacion(coordinador),
        SensorPrecioEstadisticas(coordinador),
        SensorPreciosEfectivosHoy(coordinador)
    ]

    # Telemetría Solarman (F2)
    coordinador_telemetria = datos_dominio["coordinador_telemetria"]

    entidades.extend([
        SensorFamiliaValidada(gestor),
        SensorSocBateria(coordinador_telemetria),
        SensorPotenciaBateria(coordinador_telemetria),
        SensorCorrienteBateria(coordinador_telemetria),
        SensorConsumoCasa(coordinador_telemetria),
        SensorProduccionFv(coordinador_telemetria),
        SensorTemperaturaInversor(coordinador_telemetria),
        SensorTemperaturaBateria(coordinador_telemetria),
    ])

    # Entidades del plan (F3): reusan el CoordinadorPlan del setup
    if "coordinador_plan" in datos_dominio:
        coordinador_plan = datos_dominio["coordinador_plan"]
        entidades.extend([
            SensorPrediccionConsumo(coordinador_plan),
            SensorPlan(coordinador_plan),
            SensorAhorroEstimadoHoy(coordinador_plan),
        ])

    async_add_entities(entidades)
