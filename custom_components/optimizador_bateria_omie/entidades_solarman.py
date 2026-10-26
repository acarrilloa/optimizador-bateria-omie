"""Entidades de telemetría Solarman (F2)."""
# versión 0.8.0

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfPower
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.optimizador_bateria_omie.const import (
    DOMAIN,
    SENSOR_CONSUMO_CASA,
    SENSOR_CORRIENTE_BATERIA,
    SENSOR_FAMILIA_VALIDADA,
    SENSOR_POTENCIA_BATERIA,
    SENSOR_PRODUCCION_FV,
    SENSOR_SOC_BATERIA,
    SIGNAL_VALIDACION_CAMBIADA,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    GestorValidacion,
)


class SensorTelemetriaBase(CoordinatorEntity, SensorEntity):
    """Base de los sensores de telemetría del inversor.

    Todos comparten el ``device_info`` de F1 y quedan ``unavailable``
    cuando la magnitud es ``None`` (nunca se inventan valores).
    """

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Mismo dispositivo que los sensores de precios de F1."""
        return DeviceInfo(
            identifiers={(DOMAIN, "optimizador_bateria_omie")},
            name="Optimizador Batería OMIE",
            manufacturer="Custom Component",
            model="Optimizador Batería OMIE",
        )

    @property
    def available(self) -> bool:
        """Disponible solo con actualización correcta y valor presente."""
        return self.coordinator.last_update_success and (
            self.native_value is not None
        )


class SensorSocBateria(SensorTelemetriaBase):
    """SOC de la batería (%)."""

    _attr_unique_id = SENSOR_SOC_BATERIA
    _attr_name = "SOC batería"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """SOC actual leído del inversor."""
        datos = self.coordinator.data
        return datos.soc if datos is not None else None


class SensorPotenciaBateria(SensorTelemetriaBase):
    """Potencia de la batería (W).

    Convención Deye (D16): **+descarga / −carga**.
    """

    _attr_unique_id = SENSOR_POTENCIA_BATERIA
    _attr_name = "Potencia batería"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Potencia instantánea (+descarga / −carga)."""
        datos = self.coordinator.data
        return datos.potencia_bateria if datos is not None else None


class SensorCorrienteBateria(SensorTelemetriaBase):
    """Corriente de la batería (A).

    Mismo criterio de signo que la potencia (+descarga / −carga).
    """

    _attr_unique_id = SENSOR_CORRIENTE_BATERIA
    _attr_name = "Corriente batería"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Corriente instantánea (+descarga / −carga)."""
        datos = self.coordinator.data
        return datos.corriente_bateria if datos is not None else None


class SensorConsumoCasa(SensorTelemetriaBase):
    """Consumo instantáneo de la casa (W)."""

    _attr_unique_id = SENSOR_CONSUMO_CASA
    _attr_name = "Consumo casa"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Consumo instantáneo."""
        datos = self.coordinator.data
        return datos.consumo_casa if datos is not None else None


class SensorProduccionFv(SensorTelemetriaBase):
    """Producción fotovoltaica instantánea (W)."""

    _attr_unique_id = SENSOR_PRODUCCION_FV
    _attr_name = "Producción FV"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Producción FV instantánea."""
        datos = self.coordinator.data
        return datos.produccion_fv if datos is not None else None


class SensorFamiliaValidada(SensorEntity):
    """Sensor diagnóstico: familia de registros validada.

    Estado: ``"ninguna"`` / ``"A"`` / ``"B"`` (leído del Store de
    validación). Se repinta al recibir la señal del servicio
    ``probe_registers`` o al arrancar.
    """

    _attr_has_entity_name = True
    _attr_unique_id = SENSOR_FAMILIA_VALIDADA
    _attr_name = "Familia validada"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:shield-check"

    def __init__(self, gestor: GestorValidacion) -> None:
        """Inicializa el sensor con el gestor de validación."""
        self._gestor = gestor

    @property
    def device_info(self) -> DeviceInfo:
        """Mismo dispositivo que los sensores de precios de F1."""
        return DeviceInfo(
            identifiers={(DOMAIN, "optimizador_bateria_omie")},
            name="Optimizador Batería OMIE",
            manufacturer="Custom Component",
            model="Optimizador Batería OMIE",
        )

    @property
    def native_value(self) -> str:
        """Familia validada o "ninguna"."""
        return self._gestor.familia or "ninguna"

    @property
    def extra_state_attributes(self) -> dict:
        """Atributos de la última validación (del Store)."""
        estado = self._gestor.estado or {}
        return {
            "probe_addr": estado.get("probe_addr"),
            "probe_funcion": estado.get("probe_funcion"),
            "ultima_validacion_ts": estado.get("ultima_validacion_ts"),
        }

    async def async_added_to_hass(self) -> None:
        """Se suscribe a los cambios del estado de validación."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_VALIDACION_CAMBIADA, self._actualizar
            )
        )

    @callback
    def _actualizar(self) -> None:
        """Repinta el estado al cambiar la validación."""
        self.async_write_ha_state()
