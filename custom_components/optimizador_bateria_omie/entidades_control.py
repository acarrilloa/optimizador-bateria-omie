"""Entidades de control (F4): switch, numbers calientes y sensor de estado.

- ``SwitchControlActivo``: habilitador de escrituras reales (default
  OFF → dry-run forzado).
- ``NumberParametroPlanner``: los 8 parámetros del plan editables **en
  caliente**: escriben en ``entry.data`` (mutación in-place, D16) y
  disparan el recálculo inmediato del plan.
- ``SensorEstadoControl``: diagnóstico con los estados
  ``inactivo`` / ``dry_run`` / ``activo`` / ``fallback_bloqueado``.

Ninguna entidad escribe en el inversor por sí misma: todo pasa por el
``EjecutorControl`` y sus salvaguardas.
"""
# versión 0.10.0

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import UnitOfPower
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from custom_components.optimizador_bateria_omie.const import (
    CONF_EFICIENCIA_ROUND_TRIP,
    CONF_MARGEN_ARBITRAJE,
    CONF_POTENCIA_MAX_CARGA,
    CONF_POTENCIA_MAX_DESCARGA,
    CONF_PRECIO_MAX_COMPRA,
    CONF_PRECIO_MIN_VENTA,
    CONF_SOC_MAX,
    CONF_SOC_MIN,
    DEFAULT_EFICIENCIA_ROUND_TRIP,
    DEFAULT_MARGEN_ARBITRAJE,
    DEFAULT_POTENCIA_MAX_CARGA,
    DEFAULT_POTENCIA_MAX_DESCARGA,
    DEFAULT_PRECIO_MAX_COMPRA,
    DEFAULT_PRECIO_MIN_VENTA,
    DEFAULT_SOC_MAX,
    DEFAULT_SOC_MIN,
    DOMAIN,
    NUMBER_EFICIENCIA_ROUND_TRIP,
    NUMBER_MARGEN_ARBITRAJE,
    NUMBER_POTENCIA_MAX_CARGA,
    NUMBER_POTENCIA_MAX_DESCARGA,
    NUMBER_PRECIO_MAX_COMPRA,
    NUMBER_PRECIO_MIN_VENTA,
    NUMBER_SOC_MAX,
    NUMBER_SOC_MIN,
    SENSOR_ESTADO_CONTROL,
    SWITCH_CONTROL_ACTIVO,
)

_LOGGER = logging.getLogger(__name__)


def _device_info() -> DeviceInfo:
    """Dispositivo compartido con F1/F2/F3."""
    return DeviceInfo(
        identifiers={(DOMAIN, "optimizador_bateria_omie")},
        name="Optimizador Batería OMIE",
        manufacturer="Custom Component",
        model="Optimizador Batería OMIE",
    )


class SwitchControlActivo(SwitchEntity):
    """Habilitador de las escrituras reales del executor (default OFF)."""

    _attr_unique_id = SWITCH_CONTROL_ACTIVO
    _attr_name = "Control activo"
    _attr_has_entity_name = True
    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, ejecutor) -> None:
        """Inicializa con el EjecutorControl."""
        self._ejecutor = ejecutor

    @property
    def device_info(self) -> DeviceInfo:
        """Dispositivo compartido."""
        return _device_info()

    @property
    def is_on(self) -> bool:
        """Estado del habilitador."""
        return self._ejecutor.control_activo

    async def async_turn_on(self, **kwargs) -> None:
        """Habilita las escrituras reales (bajo salvaguardas)."""
        await self._ejecutor.async_set_control_activo(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Fuerza el dry-run (ninguna escritura)."""
        await self._ejecutor.async_set_control_activo(False)


class NumberParametroPlanner(NumberEntity):
    """Parámetro del plan editable en caliente (escribe entry.data).

    Mutación in-place de ``entry.data`` + recálculo inmediato del plan
    (D16): HA no ofrece API oficial para parámetros en caliente y así
    la entrada no se recrea; los valores también quedan persistidos al
    pasar por ``reconfigure``.
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        entry,
        coordinador_plan,
        clave: str,
        nombre: str,
        unique_id: str,
        minimo: float,
        maximo: float,
        paso: float,
        default: float,
        unidad: str | None = None,
    ) -> None:
        """Inicializa el number con su parámetro del plan."""
        self._entry = entry
        self._coordinador_plan = coordinador_plan
        self._clave = clave
        self._default = default
        self._attr_name = nombre
        self._attr_unique_id = unique_id
        self._attr_native_min_value = minimo
        self._attr_native_max_value = maximo
        self._attr_native_step = paso
        if unidad:
            self._attr_native_unit_of_measurement = unidad

    @property
    def device_info(self) -> DeviceInfo:
        """Dispositivo compartido."""
        return _device_info()

    @property
    def native_value(self) -> float:
        """Valor actual del parámetro (entry.data o default)."""
        return float(self._entry.data.get(self._clave, self._default))

    async def async_set_native_value(self, valor: float) -> None:
        """Escribe en entry.data y recalcula el plan inmediatamente."""
        self._entry.data[self._clave] = round(float(valor), 6)
        _LOGGER.info(
            "Parámetro %s = %s (en caliente); recalculando el plan",
            self._clave,
            valor,
        )
        await self._coordinador_plan.async_recalcular("number")


def crear_numbers(entry, coordinador_plan) -> list[NumberParametroPlanner]:
    """Las 8 entities number de los parámetros del plan (§5 del PLAN_F4)."""
    definiciones = [
        (
            CONF_PRECIO_MAX_COMPRA, "Precio máximo de compra",
            NUMBER_PRECIO_MAX_COMPRA, 0.0, 1.0, 0.005,
            DEFAULT_PRECIO_MAX_COMPRA, "€/kWh",
        ),
        (
            CONF_PRECIO_MIN_VENTA, "Precio mínimo de venta",
            NUMBER_PRECIO_MIN_VENTA, 0.0, 1.0, 0.005,
            DEFAULT_PRECIO_MIN_VENTA, "€/kWh",
        ),
        (
            CONF_MARGEN_ARBITRAJE, "Margen de arbitraje",
            NUMBER_MARGEN_ARBITRAJE, 0.0, 0.1, 0.001,
            DEFAULT_MARGEN_ARBITRAJE, "€/kWh",
        ),
        (
            CONF_SOC_MIN, "SOC mínimo",
            NUMBER_SOC_MIN, 5.0, 50.0, 1.0,
            DEFAULT_SOC_MIN, "%",
        ),
        (
            CONF_SOC_MAX, "SOC máximo",
            NUMBER_SOC_MAX, 50.0, 100.0, 1.0,
            DEFAULT_SOC_MAX, "%",
        ),
        (
            CONF_POTENCIA_MAX_CARGA, "Potencia máxima de carga",
            NUMBER_POTENCIA_MAX_CARGA, 0.0, 6900.0, 50.0,
            DEFAULT_POTENCIA_MAX_CARGA, UnitOfPower.WATT,
        ),
        (
            CONF_POTENCIA_MAX_DESCARGA, "Potencia máxima de descarga",
            NUMBER_POTENCIA_MAX_DESCARGA, 0.0, 6900.0, 50.0,
            DEFAULT_POTENCIA_MAX_DESCARGA, UnitOfPower.WATT,
        ),
        (
            CONF_EFICIENCIA_ROUND_TRIP, "Eficiencia round-trip",
            NUMBER_EFICIENCIA_ROUND_TRIP, 70.0, 95.0, 1.0,
            DEFAULT_EFICIENCIA_ROUND_TRIP, "%",
        ),
    ]
    return [
        NumberParametroPlanner(
            entry,
            coordinador_plan,
            clave,
            nombre,
            unique_id,
            minimo,
            maximo,
            paso,
            default,
            unidad,
        )
        for (
            clave,
            nombre,
            unique_id,
            minimo,
            maximo,
            paso,
            default,
            unidad,
        ) in definiciones
    ]


class SensorEstadoControl(SensorEntity):
    """Diagnóstico del estado del control (D12)."""

    _attr_unique_id = SENSOR_ESTADO_CONTROL
    _attr_name = "Estado de control"
    _attr_has_entity_name = True
    _attr_icon = "mdi:shield-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, ejecutor) -> None:
        """Inicializa con el EjecutorControl."""
        self._ejecutor = ejecutor

    @property
    def device_info(self) -> DeviceInfo:
        """Dispositivo compartido."""
        return _device_info()

    async def async_added_to_hass(self) -> None:
        """Repinta cuando el executor cambia de estado."""
        self._cancelar = self._ejecutor.async_registrar_callback(
            self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self) -> None:
        """Cancela la suscripción al executor."""
        cancelar = getattr(self, "_cancelar", None)
        if cancelar is not None:
            cancelar()

    @property
    def native_value(self) -> str | None:
        """Estado visible: inactivo/dry_run/activo/fallback_bloqueado."""
        return self._ejecutor.estado

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Detalle del executor (motivo, fallos, escrituras, modo manual)."""
        return self._ejecutor.estado_detalle
