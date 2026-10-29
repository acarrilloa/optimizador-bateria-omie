"""Coordinador del plan (F3): reconstruye el plan y expone el tramo actual.

No es un ``DataUpdateCoordinator`` de polling (D16): recalcula en el
arranque, al llegar precios nuevos (D+1 ~13:45), al cambiar de tramo
(cada 15 min), al togglear ``festivo_hoy``, por desviación de SOC y con
el servicio ``regenerate_plan``. **Nunca escribe en el inversor.**
"""
# versión 0.9.0

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)

from custom_components.optimizador_bateria_omie.const import (
    CONF_DESVIACION_SOC_REPLAN,
    CONF_ENTITY_ID_SOC_BATERIA,
    DEFAULT_DESVIACION_SOC_REPLAN,
    DOMAIN,
)
from custom_components.optimizador_bateria_omie.coordinador import (
    CoordinadorDatosOmie,
)
from custom_components.optimizador_bateria_omie.planificador import (
    Plan,
    PlanificadorGreedy,
    PlanTramo,
)
from custom_components.optimizador_bateria_omie.predictor_consumo import (
    PrediccionDia,
    PredictorConsumo,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    CoordinadorTelemetria,
)

_LOGGER = logging.getLogger(__name__)

ENTITY_ID_FESTIVO_HOY = f"switch.{DOMAIN}_festivo_hoy"


def _tramo_actual_index(ahora: datetime | None = None) -> int:
    """Índice de tramo (0–95) del instante actual (reloj pared local)."""
    local = (ahora or datetime.now().astimezone())
    return local.hour * 4 + local.minute // 15


class CoordinadorPlan:
    """Coordina predicción, precios y telemetría para recalcular el plan."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinador_precio: CoordinadorDatosOmie,
        coordinador_telemetria: CoordinadorTelemetria,
        predictor: PredictorConsumo,
        planificador: PlanificadorGreedy,
    ) -> None:
        """Inicializa el coordinador del plan (sin listeners aún)."""
        self.hass = hass
        self.entry = entry
        self.coordinador_precio = coordinador_precio
        self.coordinador_telemetria = coordinador_telemetria
        self.predictor = predictor
        self.planificador = planificador
        self._plan: Plan | None = None
        self._prediccion_hoy: PrediccionDia | None = None
        self._prediccion_manana: PrediccionDia | None = None
        self._festivo_hoy = False
        self._listeners: list[Callable[[], None]] = []
        self._canceladores: list[Any] = []

    # ─── Registro ───

    def async_registrar_callback(
        self, callback_fn: Callable[[], None]
    ) -> Callable[[], None]:
        """Registra un callback que se invoca al cambiar el plan."""
        self._listeners.append(callback_fn)

        def _cancelar() -> None:
            if callback_fn in self._listeners:
                self._listeners.remove(callback_fn)

        return _cancelar

    def _notificar(self) -> None:
        """Notifica a las entidades que el plan cambió."""
        for listener in list(self._listeners):
            listener()

    async def async_setup(self) -> None:
        """Calcula el plan inicial y registra los listeners (D16)."""
        await self.async_recalcular("arranque")

        self._canceladores.append(
            self.coordinador_precio.async_add_listener(
                self._async_precios_nuevos
            )
        )
        # Cambio de tramo (cada 15 min) y recálculo diario del perfil
        self._canceladores.append(
            async_track_time_change(
                self.hass,
                self._async_cambio_tramo,
                minute=range(0, 60, 15),
                second=0,
            )
        )
        self._canceladores.append(
            async_track_time_change(
                self.hass,
                self._async_perfil_diario,
                hour=4,
                minute=35,
                second=0,
            )
        )
        # Desviación de SOC: escucha el SOC real de telemetría
        entity_id_soc = self.entry.data.get(CONF_ENTITY_ID_SOC_BATERIA)
        if entity_id_soc:
            self._canceladores.append(
                async_track_state_change_event(
                    self.hass, [entity_id_soc], self._async_soc_cambiado
                )
            )
        else:
            _LOGGER.info(
                "Sin entity_id de SOC: la replanificación por desviación "
                "queda deshabilitada"
            )

    @callback
    def _async_precios_nuevos(self) -> None:
        """El coordinador de precios publicó datos nuevos."""
        self.hass.async_create_task(self.async_recalcular("precios"))

    @callback
    def _async_cambio_tramo(self, ahora) -> None:
        """Cambio de tramo cada 15 min: replanifica con SOC real."""
        self.hass.async_create_task(self.async_recalcular("tramo"))

    @callback
    def _async_perfil_diario(self, ahora) -> None:
        """04:35: recalcula el perfil tras la limpieza de la caché."""
        self.hass.async_create_task(
            self._async_perfil_y_recalcular("perfil_diario")
        )

    async def _async_perfil_y_recalcular(self, motivo: str) -> None:
        """Recalcula el perfil y luego el plan."""
        try:
            await self.predictor.recalcular_perfil()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Recálculo de perfil falló: %s", err)
        await self.async_recalcular(motivo)

    @callback
    def _async_soc_cambiado(self, evento: Event) -> None:
        """SOC real fuera de tolerancia → replanificación (D11)."""
        nuevo = evento.data.get("new_state")
        if nuevo is None:
            return
        try:
            soc = float(nuevo.state)
        except (TypeError, ValueError):
            return
        tramo = self.tramo_actual()
        if tramo is None:
            return
        umbral = float(
            self.entry.data.get(
                CONF_DESVIACION_SOC_REPLAN, DEFAULT_DESVIACION_SOC_REPLAN
            )
        )
        if abs(soc - tramo.soc_previsto_pct) > umbral:
            _LOGGER.info(
                "SOC real %.1f %% desviado del previsto %.1f %% "
                "(umbral %.0f %%): replanificando",
                soc,
                tramo.soc_previsto_pct,
                umbral,
            )
            self.hass.async_create_task(
                self.async_recalcular("desviacion_soc")
            )

    async def async_descargar(self) -> None:
        """Cancela los listeners al descargar la entrada."""
        for cancelar in self._canceladores:
            cancelar()
        self._canceladores.clear()
        self._listeners.clear()

    # ─── Festivo manual ───

    @property
    def festivo_hoy(self) -> bool:
        """Marca manual de festivo/vacaciones para hoy."""
        return self._festivo_hoy

    async def async_set_festivo_hoy(self, valor: bool) -> None:
        """Cambia la marca de festivo de hoy y replanifica."""
        if valor == self._festivo_hoy:
            return
        self._festivo_hoy = valor
        await self.async_recalcular("festivo_hoy")

    # ─── Cálculo del plan ───

    def _precios_efectivos(
        self, ranuras: list | None
    ) -> list[float] | None:
        """Precios efectivos €/kWh por posición de tramo del día."""
        if not ranuras:
            return None
        return [
            self.coordinador_precio.modelo_tarifa.obtener_precio_efectivo(
                r.precio_eur_kwh, r.fecha_hora_utc
            )
            for r in ranuras
        ]

    async def async_recalcular(self, motivo: str) -> Plan:
        """Recalcula el plan completo (hoy + mañana si hay precios)."""
        datos = self.coordinador_precio.datos
        hoy = datetime.now().astimezone().date()
        manana = hoy.fromordinal(hoy.toordinal() + 1)

        prediccion_hoy = await self.predictor.predecir(
            hoy, festivo_hoy=self._festivo_hoy
        )
        precios_manana = self._precios_efectivos(datos.precios_manana)
        prediccion_manana = None
        if precios_manana is not None:
            prediccion_manana = await self.predictor.predecir(manana)

        soc = None
        telemetria = self.coordinador_telemetria.data
        if telemetria is not None:
            soc = telemetria.soc

        plan = self.planificador.planificar(
            hoy,
            self._precios_efectivos(datos.precios_hoy) or [],
            precios_manana,
            prediccion_hoy,
            prediccion_manana,
            soc,
        )
        self._plan = plan
        self._prediccion_hoy = prediccion_hoy
        self._prediccion_manana = prediccion_manana
        _LOGGER.info(
            "Plan recalculado (motivo: %s): %d tramos, estado %s, "
            "ahorro estimado %.3f €",
            motivo,
            len(plan.tramos),
            plan.estado,
            plan.ahorro_estimado_eur,
        )
        self._notificar()
        return plan

    # ─── Accesores ───

    @property
    def plan(self) -> Plan | None:
        """Plan vigente."""
        return self._plan

    @property
    def prediccion_hoy(self) -> PrediccionDia | None:
        """Predicción de hoy (consumo y FV)."""
        return self._prediccion_hoy

    @property
    def prediccion_manana(self) -> PrediccionDia | None:
        """Predicción de mañana (consumo y FV)."""
        return self._prediccion_manana

    def tramo_actual(self) -> PlanTramo | None:
        """Tramo del plan en curso (o None si no hay plan)."""
        if self._plan is None:
            return None
        hoy = datetime.now().astimezone().date()
        return self.planificador.tramo_actual(
            self._plan, hoy, _tramo_actual_index()
        )

    def ahorro_estimado_hoy(self) -> float:
        """Ahorro teórico del plan para hoy (todos los tramos del día)."""
        if self._plan is None:
            return 0.0
        hoy = datetime.now().astimezone().date()
        return round(
            sum(
                t.ahorro_tramo_eur
                for t in self._plan.tramos
                if t.fecha == hoy
            ),
            4,
        )

    async def async_regenerar(self) -> dict:
        """Servicio regenerate_plan: recálculo inmediato."""
        plan = await self.async_recalcular("servicio")
        tramo = self.tramo_actual()
        return {
            "recalculado": True,
            "accion_actual": tramo.accion if tramo else None,
            "ahorro_estimado": plan.ahorro_estimado_eur,
        }
