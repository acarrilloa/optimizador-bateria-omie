"""Entidades (sensores) para la integración Optimizador Batería OMIE."""
# versión 0.7.6

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.optimizador_bateria_omie.const import (
    ATTR_FUENTE,
    ATTR_MAX,
    ATTR_MEDIANA,
    ATTR_MIN,
    ATTR_TS_ACTUALIZACION,
    DOMAIN,
    SENSOR_PRECIO_ACTUAL,
    SENSOR_PRECIO_CLASIFICACION,
    SENSOR_PRECIO_EFECTIVO,
    SENSOR_PRECIO_ESTADISTICAS,
)


class SensorBase(CoordinatorEntity, SensorEntity):
    """Clase base para los sensores."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "optimizador_bateria_omie")},
            name="Optimizador Batería OMIE",
            manufacturer="Custom Component",
            model="Optimizador Batería OMIE",
        )


class SensorPrecioActual(SensorBase):
    """Sensor para el precio actual de la electricidad."""

    _attr_unique_id = SENSOR_PRECIO_ACTUAL
    _attr_name = "Precio actual"
    _attr_native_unit_of_measurement = "€/kWh"

    @property
    def native_value(self) -> float | None:
        precio = self.coordinator.obtener_precio_actual()
        return round(precio, 4) if precio is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        return {ATTR_FUENTE: self.coordinator.datos.fuente}


class SensorPrecioEfectivo(SensorBase):
    """Sensor para el precio efectivo (con peajes, cargos e impuestos)."""

    _attr_unique_id = SENSOR_PRECIO_EFECTIVO
    _attr_name = "Precio efectivo"
    _attr_native_unit_of_measurement = "€/kWh"

    @property
    def native_value(self) -> float | None:
        precio = self.coordinator.obtener_precio_efectivo_actual()
        return round(precio, 4) if precio is not None else None


class SensorPrecioClasificacion(SensorBase):
    """Sensor para la clasificación del precio (valle, llano, punta)."""

    _attr_unique_id = SENSOR_PRECIO_CLASIFICACION
    _attr_name = "Clasificación"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.obtener_clasificacion_actual()


class SensorPrecioEstadisticas(SensorBase):
    """Sensor para estadísticas de precios."""

    _attr_unique_id = SENSOR_PRECIO_ESTADISTICAS
    _attr_name = "Estadísticas"
    _attr_native_unit_of_measurement = "€/kWh"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.obtener_estadisticas_actual().get(ATTR_MEDIANA, 0)

    @property
    def extra_state_attributes(self) -> dict:
        est = self.coordinator.obtener_estadisticas_actual()
        datos = self.coordinator.datos
        return {
            ATTR_MIN: est.get(ATTR_MIN, 0),
            ATTR_MAX: est.get(ATTR_MAX, 0),
            ATTR_MEDIANA: est.get(ATTR_MEDIANA, 0),
            ATTR_FUENTE: datos.fuente,
            ATTR_TS_ACTUALIZACION: datos.ts_actualizacion.isoformat(),
        }

class SensorPreciosEfectivosHoy(SensorBase):
    """Curva de precios efectivos (peajes + cargos + impuestos) del día."""

    _attr_unique_id = "optimizador_bateria_omie_precios_efectivos_hoy"
    _attr_name = "Precios efectivos hoy"
    _attr_icon = "mdi:chart-timeline-variant-shimmer"
    _attr_native_unit_of_measurement = "€/kWh"

    @property
    def native_value(self) -> float | None:
        est = self.coordinator.obtener_estadisticas_actual()
        return est.get("mediana") if est else None

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "precios_ordenados": self.coordinator.obtener_precios_efectivos_ordenados()
        }