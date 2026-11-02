"""Entidades del plan (F3): predicción, plan, ahorro estimado y festivo.

Todas comparten el dispositivo de F1/F2 y se repintan por callback del
``CoordinadorPlan`` (sin polling). Ninguna toca el inversor.
"""
# versión 0.10.1

import logging
from dataclasses import asdict
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import UnitOfPower
from homeassistant.helpers.entity import DeviceInfo

from custom_components.optimizador_bateria_omie.const import (
    ATTR_FUENTE,
    DOMAIN,
    SENSOR_AHORRO_ESTIMADO_HOY,
    SENSOR_PLAN,
    SENSOR_PREDICCION_CONSUMO,
    SWITCH_FESTIVO_HOY,
)
from custom_components.optimizador_bateria_omie.coordinador_plan import (
    CoordinadorPlan,
)
from custom_components.optimizador_bateria_omie.predictor_consumo import (
    PercentilesTramo,
)

from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store
from datetime import datetime

STORE_KEY_FESTIVO = f"{DOMAIN}/festivo_hoy"
SENSOR_AHORRO_EFECTIVO_HOY = f"{DOMAIN}_ahorro_efectivo_hoy"

_LOGGER = logging.getLogger(__name__)

ATTR_FUENTE_FV = "fuente_fv"


def _curva(
    tramos: list[PercentilesTramo | None],
) -> list[dict[str, Any]]:
    """Curva p10/p50/p90 por tramo para atributos de la UI."""
    return [
        {
            "hora": f"{i // 4:02d}:{(i % 4) * 15:02d}",
            "p10": round(t.p10) if t else None,
            "p50": round(t.p50) if t else None,
            "p90": round(t.p90) if t else None,
        }
        for i, t in enumerate(tramos or [])
    ]


class EntidadBasePlan(SensorEntity):
    """Base común de los sensores del plan (dispositivo compartido)."""

    _attr_has_entity_name = True

    def __init__(self, coordinador: CoordinadorPlan) -> None:
        """Inicializa con el coordinador del plan."""
        self._coordinador = coordinador

    @property
    def device_info(self) -> DeviceInfo:
        """Dispositivo compartido con F1/F2."""
        return DeviceInfo(
            identifiers={(DOMAIN, "optimizador_bateria_omie")},
            name="Optimizador Batería OMIE",
            manufacturer="Custom Component",
            model="Optimizador Batería OMIE",
        )

    async def async_added_to_hass(self) -> None:
        """Repinta el sensor cuando el plan cambia."""
        self._cancelar = self._coordinador.async_registrar_callback(
            self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self) -> None:
        """Cancela la suscripción al coordinador."""
        cancelar = getattr(self, "_cancelar", None)
        if cancelar is not None:
            cancelar()


class SensorPrediccionConsumo(EntidadBasePlan):
    """Predicción de consumo del tramo actual (p50) con curvas."""

    _attr_unique_id = SENSOR_PREDICCION_CONSUMO
    _attr_name = "Predicción consumo"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-areaspline"

    @property
    def native_value(self) -> float | None:
        """p50 del consumo previsto del tramo actual."""
        prediccion = self._coordinador.prediccion_hoy
        if prediccion is None:
            return None
        tramo = self._coordinador.tramo_actual()
        if tramo is None:
            return None
        percentiles = prediccion.consumo[tramo.tramo]
        return round(percentiles.p50, 1) if percentiles else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Curvas de consumo y FV de hoy y mañana con su fuente."""
        hoy = self._coordinador.prediccion_hoy
        manana = self._coordinador.prediccion_manana
        atributos: dict[str, Any] = {
            "consumo_hoy": _curva(hoy.consumo) if hoy else [],
            ATTR_FUENTE: "perfil",
        }
        if hoy:
            atributos["fv_hoy"] = _curva(hoy.fv)
            atributos[ATTR_FUENTE_FV + "_hoy"] = hoy.fuente_fv
        if manana:
            atributos["consumo_manana"] = _curva(manana.consumo)
            atributos["fv_manana"] = _curva(manana.fv)
            atributos[ATTR_FUENTE_FV + "_manana"] = manana.fuente_fv
        return atributos


class SensorPlan(EntidadBasePlan):
    """Acción del plan para el tramo actual (dry-run, sin escrituras)."""

    _attr_unique_id = SENSOR_PLAN
    _attr_name = "Plan"
    _attr_icon = "mdi:battery-charging-50"

    @property
    def native_value(self) -> str | None:
        """Acción del tramo actual o estado del plan."""
        plan = self._coordinador.plan
        if plan is None:
            return None
        tramo = self._coordinador.tramo_actual()
        if tramo is None:
            return plan.estado
        return tramo.accion

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Plan completo, SOC previsto y ahorro estimado."""
        plan = self._coordinador.plan
        if plan is None:
            return {"estado": "sin_plan"}
        tramo = self._coordinador.tramo_actual()
        atributos: dict[str, Any] = {
            "estado": plan.estado,
            "motivos": plan.motivos,
            "ahorro_estimado": self._coordinador.ahorro_estimado_hoy(),
            "coste_sin_plan_eur": plan.coste_sin_plan_eur,
            "coste_con_plan_eur": plan.coste_con_plan_eur,
            "plan": [asdict(t) for t in plan.tramos],
        }
        if tramo is not None:
            atributos["tramo_actual"] = tramo.hora
            atributos["soc_previsto_pct"] = tramo.soc_previsto_pct
            atributos["potencia_bateria_w"] = tramo.potencia_bateria_w
            atributos["potencia_carga_red_w"] = tramo.potencia_carga_red_w
            atributos["potencia_venta_red_w"] = tramo.potencia_venta_red_w
            atributos["precio_referencia_eur_kwh"] = (
                tramo.precio_referencia_eur_kwh
            )
        return atributos


class SensorAhorroEstimadoHoy(EntidadBasePlan):
    """Ahorro teórico estimado del día (el real es F5: ahorro_hoy)."""

    _attr_unique_id = SENSOR_AHORRO_ESTIMADO_HOY
    _attr_name = "Ahorro estimado hoy"
    _attr_native_unit_of_measurement = "€"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:piggy-bank"

    @property
    def native_value(self) -> float | None:
        """Ahorro estimado acumulado del día (teórico, dry-run)."""
        plan = self._coordinador.plan
        if plan is None:
            return None
        return round(self._coordinador.ahorro_estimado_hoy(), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self._coordinador.plan
        if plan is None:
            return {}
        return {
            "teórico": True,
            "nota": "estimación del plan en dry_run; el ahorro real "
            "llega en F5 (ahorro_hoy)",
        }


class SwitchFestivoHoy(SwitchEntity, RestoreEntity):
    """Marca manual de festivo/vacaciones para hoy (D15)."""

    _attr_unique_id = SWITCH_FESTIVO_HOY
    _attr_name = "Festivo hoy"
    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-remove"

    def __init__(self, coordinador: CoordinadorPlan, hass) -> None:
        self._coordinador = coordinador
        self.hass = hass
        self._store = Store(hass, 1, STORE_KEY_FESTIVO)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "optimizador_bateria_omie")},
            name="Optimizador Batería OMIE",
            manufacturer="Custom Component",
            model="Optimizador Batería OMIE",
        )

    async def async_added_to_hass(self) -> None:
        """Restaura el estado y registra el reset diario."""
        await super().async_added_to_hass()
        datos = await self._store.async_load()
        hoy = datetime.now().date().isoformat()
        if datos and datos.get("fecha") == hoy and datos.get("festivo"):
            await self._coordinador.async_set_festivo_hoy(True)
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_time_change(
                self.hass, self._reset_diario,
                hour=0, minute=1, second=0,
            )
        )

    async def _reset_diario(self, now=None) -> None:
        """Al cambiar de día, apaga el switch automáticamente."""
        datos = await self._store.async_load()
        hoy = datetime.now().date().isoformat()
        if datos and datos.get("fecha") != hoy:
            await self._store.async_save({"festivo": False, "fecha": None})
            await self._coordinador.async_set_festivo_hoy(False)
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._coordinador.festivo_hoy

    async def async_turn_on(self, **kwargs) -> None:
        hoy = datetime.now().date().isoformat()
        await self._store.async_save({"festivo": True, "fecha": hoy})
        await self._coordinador.async_set_festivo_hoy(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self._store.async_save({"festivo": False, "fecha": None})
        await self._coordinador.async_set_festivo_hoy(False)
        self.async_write_ha_state()

class SensorAhorroEfectivoHoy(SensorEntity):
    """Ahorro real medido del día (vs. coste sin batería)."""

    _attr_has_entity_name = True
    _attr_unique_id = SENSOR_AHORRO_EFECTIVO_HOY
    _attr_name = "Ahorro efectivo hoy"
    _attr_native_unit_of_measurement = "€"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:currency-eur"

    def __init__(self, auditoria) -> None:
        self._auditoria = auditoria

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "optimizador_bateria_omie")},
            name="Optimizador Batería OMIE",
            manufacturer="Custom Component",
            model="Optimizador Batería OMIE",
        )

    @property
    def native_value(self) -> float:
        return self._auditoria.ahorro_real_hoy

    @property
    def extra_state_attributes(self) -> dict:
        return self._auditoria.desglose