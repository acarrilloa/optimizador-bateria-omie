"""Coordinador del plan (F3): reconstruye el plan y expone el tramo actual.

No es un ``DataUpdateCoordinator`` de polling (D16): recalcula en el
arranque, al llegar precios nuevos (D+1 ~13:45), al cambiar de tramo
(cada 15 min, pero solo se redecide en los límites del bloque de
compromiso, D-F5.3.1), al togglear ``festivo_hoy``, por desviación de
SOC y con el servicio ``regenerate_plan``. **Nunca escribe en el
inversor.**
"""
# versión 0.16.0

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)

from custom_components.optimizador_bateria_omie.const import (
    ALGORITMO_GREEDY,
    ALGORITMO_LP,
    CONF_ALGORITMO_PLANNER,
    CONF_BLOQUE_MINUTOS,
    CONF_CAPACIDAD_BATERIA_KWH,
    CONF_DESVIACION_SOC_REPLAN,
    CONF_ENTITY_ID_SOC_BATERIA,
    CONF_FACTOR_RITMO,
    CONF_MARGEN_COBERTURA_WH,
    DEFAULT_BLOQUE_MINUTOS,
    DEFAULT_CAPACIDAD_BATERIA_KWH,
    DEFAULT_DESVIACION_SOC_REPLAN,
    DEFAULT_FACTOR_RITMO,
    DEFAULT_MARGEN_COBERTURA_WH,
    DOMAIN,
    HORA_AVISO_SIN_PRECIOS_MANANA,
    NOTIFICACION_ML_ADOPCION,
    NOTIFICACION_ML_REENTRENAMIENTO,
    NOTIFICACION_SIN_PRECIOS_MANANA,
    SENSOR_ESTADO_CONTROL,
    SENSOR_FORECAST_QUALITY,
    TRAMOS_POR_DIA,
)
from custom_components.optimizador_bateria_omie.coordinador import (
    CoordinadorDatosOmie,
)
from custom_components.optimizador_bateria_omie.planificador import (
    ACCION_CARGA_RED,
    ParametrosPlanner,
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

# Motivos que replanifican con EFECTO INMEDIATO sobre el bloque en
# curso (D-F5.3.1): seguridad y ventana de oportunidad ante caída de
# SOC. Todo lo demás (precios, perfil, festivo, numbers, tramo del
# límite...) solo cambia el plan desde el siguiente límite de bloque.
# F6/B: la cobertura insuficiente (caso de uso U1) también es inmediata:
# comprar por adelantado no puede esperar al límite del bloque.
MOTIVOS_REPLAN_INMEDIATO = frozenset(
    {"desviacion_soc", "cobertura_insuficiente"}
)
# Cooldown del replan por cobertura: el check corre cada 30 min, pero
# no re-dispara el recálculo (ni la notificación) más de una vez por
# ventana de 45 min aunque la condición persista (el plan ya replanifica
# con el SOC real; persistiría solo si el precio lo desaconseja).
COOLDOWN_REPLAN_COBERTURA_S = 2700.0
# Ritmo excesivo (F6/B): disparar tras 2 muestreos consecutivos por
# encima del factor previsto (30 min entre muestreos → ~1 h)
RITMO_MUESTRAS_DISPARO = 2
# Cooldown compartido de desviación (0.16): listener de eventos y
# respaldo periódico comparten 15 min — una desviación persistente
# (carga que no llega) provoca eventos constantes y sin límite se
# replanificaría en cada polling del datalogger. El respaldo corre
# cada 5 min: la reacción decae a ≤ 15 min aunque el listener muera.
COOLDOWN_DESVIACION_S = 900.0
# Umbral de SOC congelado (0.15.1): la entidad no cambia de estado
# (datalogger caído) → el dato es viejo aunque la telemetría lo selle
# con la hora del sondeo. WARNING audible en el check periódico.
SOC_CONGELADO_AVISO_S = 900.0


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
        # F6-U1: estado de los avisos persistentes de datos
        self._aviso_precios_fecha: date | None = None
        self._aviso_ml_fecha: date | None = None
        self._adoptado_previo: bool | None = None
        # F6/B: check de cobertura (caso de uso U1)
        self._ts_replan_cobertura: datetime | None = None
        # 0.15.1: respaldo periódico de desviación (entidad congelada)
        self._ts_replan_desviacion: datetime | None = None
        self._ritmo_excesos = 0
        # F6/B: bug 2 — existencia del entity_id de SOC verificada al arranque
        self.replan_soc_activo = False
        # 0.16: diagnóstico del listener — eventos recibidos desde el
        # arranque; 0 con SOC fresco = la entidad no emite cambios
        self._eventos_soc = 0

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
        # Desviación de SOC: escucha el SOC real de telemetría.
        # F6/B + 0.15.2: la escucha se registra SIEMPRE que haya
        # entity_id — la carrera del arranque (17:56 del 24-nov:
        # Solarman aún no había creado la entidad y el registro
        # condicional la saltó; lo rescató el respaldo periódico de
        # las 18:00) dejaba el detector primario muerto. HA entrega
        # los eventos de una entidad que aparece MÁS TARDE sin
        # re-registrar. La advertencia de entidad ausente se mantiene
        # (diagnóstico) y ``replan_soc_activo`` se reevalúa en el
        # check periódico.
        entity_id_soc = self.entry.data.get(CONF_ENTITY_ID_SOC_BATERIA)
        if entity_id_soc:
            self._canceladores.append(
                async_track_state_change_event(
                    self.hass, [entity_id_soc], self._async_soc_cambiado
                )
            )
            if self.hass.states.get(entity_id_soc) is None:
                _LOGGER.warning(
                    "entity_id de SOC (%s) todavía no existe en HA "
                    "(carrera de arranque): la escucha queda registrada "
                    "y se activará al aparecer la entidad; el respaldo "
                    "periódico cubre mientras tanto",
                    entity_id_soc,
                )
            else:
                self.replan_soc_activo = True
                _LOGGER.info(
                    "Replanificación por desviación de SOC activa "
                    "(entidad %s)",
                    entity_id_soc,
                )
        else:
            _LOGGER.info(
                "Sin entity_id de SOC: la replanificación por desviación "
                "queda deshabilitada"
            )
        # F6/B: check de cobertura periódico (caso de uso U1): SOC real +
        # FV previsto vs consumo hasta el próximo tramo barato del plan
        self._canceladores.append(
            async_track_time_change(
                self.hass,
                self._async_cobertura_periodica,
                minute=range(0, 60, 5),
                second=0,
            )
        )

    @callback
    def _async_cobertura_periodica(self, ahora) -> None:
        """Cada 30 min: comprueba la cobertura hasta el próximo valle."""
        self.hass.async_create_task(self._async_check_cobertura())

    async def _async_check_cobertura(self) -> None:
        """Cobertura U1: ¿la batería llega al próximo tramo CARGA_RED?

        Déficit si ``SOC real (Wh) − (consumo previsto − FV previsto)
        hasta la ventana < margen``. Si hay déficit → replan inmediato
        ``cobertura_insuficiente`` (bypassa el bloque congelado): el
        planner decide con el SOC real si conviene cargar antes (solo
        si ``precio_max_compra`` lo permite; nunca se fuerza compra
        cara). Segunda barrera: ritmo de consumo real > factor ×
        previsto durante 2 muestreos consecutivos → mismo replan
        (día frío con aerotermia sin esperar al umbral absoluto de SOC).
        """
        plan = self._plan
        telemetria = self.coordinador_telemetria.data
        if plan is None or not plan.tramos:
            return
        if telemetria is None or telemetria.soc is None:
            return
        ahora = datetime.now().astimezone()
        hoy = ahora.date()

        # 0.15.2: reevaluación perezosa de la entidad de SOC — puede
        # haber aparecido tras el arranque (la escucha ya está activa;
        # el atributo del sensor plan vuelve a ser veraz)
        entity_id_soc = self.entry.data.get(CONF_ENTITY_ID_SOC_BATERIA)
        if (
            entity_id_soc
            and not self.replan_soc_activo
            and self.hass.states.get(entity_id_soc) is not None
        ):
            self.replan_soc_activo = True
            _LOGGER.info(
                "Entidad de SOC (%s) disponible tras el arranque: "
                "replan por desviación activa",
                entity_id_soc,
            )

        # 0.15.1 — SOC congelado (datalogger caído): la entidad no
        # cambia de estado, la telemetría sella con la hora del sondeo
        # y el listener de eventos enmudece. Aviso audible.
        soc_ts = getattr(telemetria, "soc_ts", None)
        if soc_ts is not None:
            edad = (ahora - telemetria.soc_ts).total_seconds()
            if edad > SOC_CONGELADO_AVISO_S:
                _LOGGER.warning(
                    "SOC congelado desde hace %.0f min (%.1f %% sin "
                    "actualizaciones de la entidad): los datos del "
                    "plan pueden ser ficticios (¿datalogger caído?)",
                    edad / 60,
                    telemetria.soc,
                )

        # 0.15.1 — respaldo periódico de desviación: el listener de
        # eventos es el detector primario (D11), pero una entidad
        # congelada no emite eventos. Comparación por valor cada ciclo
        # aunque no haya habido cambios de estado.
        tramo_actual = self.tramo_actual()
        if tramo_actual is not None:
            umbral = float(self.entry.data.get(
                CONF_DESVIACION_SOC_REPLAN, DEFAULT_DESVIACION_SOC_REPLAN
            ))
            deviado = (
                abs(telemetria.soc - tramo_actual.soc_previsto_pct) > umbral
            )
            en_enfriamiento = (
                self._ts_replan_desviacion is not None
                and (
                    ahora - self._ts_replan_desviacion
                ).total_seconds() < COOLDOWN_DESVIACION_S
            )
            if deviado and not en_enfriamiento:
                self._ts_replan_desviacion = ahora
                _LOGGER.warning(
                    "SOC real %.1f %% desviado del previsto %.1f %% "
                    "(umbral %.0f %%): replanificación por respaldo "
                    "periódico (eventos del listener desde el arranque: "
                    "%d)",
                    telemetria.soc,
                    tramo_actual.soc_previsto_pct,
                    umbral,
                    self._eventos_soc,
                )
                await self.async_recalcular("desviacion_soc")
                # Plan nuevo: el resto del check, en el próximo ciclo
                return

        # Ventana: inicio del PRÓXIMO tramo CARGA_RED del plan
        ventana: datetime | None = None
        for tramo in plan.tramos:
            fecha = (
                tramo.fecha
                if isinstance(tramo.fecha, date)
                else hoy
            )
            inicio = datetime(
                fecha.year, fecha.month, fecha.day,
                (tramo.tramo * 15) // 60, (tramo.tramo * 15) % 60,
                tzinfo=ahora.tzinfo,
            )
            if inicio > ahora and tramo.accion == ACCION_CARGA_RED:
                ventana = inicio
                break
        if ventana is None:
            return  # sin tramo barato en el horizonte: nada que cubrir

        # Consumo y FV previstos (Wh) desde ahora hasta la ventana
        consumo_wh, fv_wh, tramos_considerados = self._energia_prevista(
            ahora, ventana
        )
        if tramos_considerados == 0:
            return
        consumo_neto = max(consumo_wh - fv_wh, 0.0)

        capacidad = float(
            self.entry.data.get(
                CONF_CAPACIDAD_BATERIA_KWH, DEFAULT_CAPACIDAD_BATERIA_KWH
            )
        )
        margen = self.entry.data.get(
            CONF_MARGEN_COBERTURA_WH, DEFAULT_MARGEN_COBERTURA_WH
        )
        if margen is None:
            margen = 0.2 * capacidad * 1000.0
        margen = float(margen)

        soc_wh = (telemetria.soc / 100.0) * capacidad * 1000.0
        deficit = (soc_wh - consumo_neto) < margen

        # Ritmo real vs previsto (muestreo instantáneo, 2 consecutivos)
        factor = float(
            self.entry.data.get(CONF_FACTOR_RITMO, DEFAULT_FACTOR_RITMO)
        )
        previsto_w = (
            consumo_neto * 4.0 / tramos_considerados  # Wh/25 min → W
        )
        ritmo_excesivo = False
        if (
            telemetria.consumo_casa is not None
            and telemetria.produccion_fv is not None
            and previsto_w > 0
        ):
            neto_real = telemetria.consumo_casa - telemetria.produccion_fv
            if neto_real > factor * previsto_w:
                self._ritmo_excesos += 1
            else:
                self._ritmo_excesos = 0
            ritmo_excesivo = self._ritmo_excesos >= RITMO_MUESTRAS_DISPARO

        if not deficit and not ritmo_excesivo:
            return
        if (
            self._ts_replan_cobertura is not None
            and (ahora - self._ts_replan_cobertura).total_seconds()
            < COOLDOWN_REPLAN_COBERTURA_S
        ):
            return
        self._ts_replan_cobertura = ahora
        self._ritmo_excesos = 0
        _LOGGER.warning(
            "Cobertura insuficiente: SOC %.1f %% (%.0f Wh) con "
            "%.0f Wh de consumo neto previsto hasta las %s (margen "
            "%.0f Wh; %s): replanificación inmediata",
            telemetria.soc,
            soc_wh,
            consumo_neto,
            ventana.strftime("%H:%M"),
            margen,
            "déficit" if deficit else f"ritmo >{factor}× previsto",
        )
        await self._async_notificar(
            "Optimizador Batería OMIE: cobertura insuficiente",
            f"SOC {telemetria.soc:.0f} % con consumo previsto de "
            f"{consumo_neto / 1000:.1f} kWh hasta las "
            f"{ventana.strftime('%H:%M')} (margen {margen / 1000:.1f} "
            "kWh). Se replanifica al momento; el plan decidirá si "
            "conviene cargar antes del valle.",
            f"{DOMAIN}_cobertura_insuficiente",
        )
        await self.async_recalcular("cobertura_insuficiente")

    def _energia_prevista(
        self, ahora: datetime, ventana: datetime
    ) -> tuple[float, float, int]:
        """Consumo/FV previstos (Wh) del intervalo (ahora, ventana].

        Suma p50 − FV de los tramos completos del intervalo desde las
        predicciones en cache (hoy/mañana). Tramos sin predicción
        cuentan como 0 (los gaps del predictor no abortan el check).
        """
        def _dia_de(fecha: date) -> PrediccionDia | None:
            if self._prediccion_hoy is not None and (
                fecha == self._prediccion_hoy.fecha
            ):
                return self._prediccion_hoy
            if self._prediccion_manana is not None and (
                fecha == self._prediccion_manana.fecha
            ):
                return self._prediccion_manana
            return None

        consumo = 0.0
        fv = 0.0
        considerados = 0
        fecha = ahora.date()
        while (
            datetime.combine(
                fecha, datetime.min.time(), tzinfo=ventana.tzinfo
            )
            <= ventana
        ):
            prediccion = _dia_de(fecha)
            base = datetime.combine(
                fecha, datetime.min.time(), tzinfo=ventana.tzinfo
            )
            for idx in range(96):
                inicio = base + timedelta(minutes=15 * idx)
                if inicio <= ahora or inicio >= ventana:
                    continue
                fin = inicio + timedelta(minutes=15)
                fraccion = min(fin, ventana) - max(inicio, ahora)
                peso = fraccion.total_seconds() / 900.0
                if prediccion is not None:
                    c = prediccion.consumo[idx]
                    if c is not None:
                        consumo += c.p50 * 0.25 * peso
                    f = prediccion.fv[idx]
                    if f is not None:
                        fv += f.p50 * 0.25 * peso
                considerados += 1
            fecha += timedelta(days=1)
        return consumo, fv, considerados

    @callback
    def _async_precios_nuevos(self) -> None:
        """El coordinador de precios publicó datos nuevos."""
        self.hass.async_create_task(self.async_recalcular("precios"))

    @callback
    def _async_cambio_tramo(self, ahora) -> None:
        """Cambio de tramo: replanifica solo en límites de bloque.

        El listener sigue disparando cada 15 min, pero dentro del
        bloque de compromiso (D-F5.3.1) no se redecide: los límites
        se alinean a medianoche (p. ej. 60 min → cada hora en punto).
        Las replanificaciones reactivas (desviación de SOC, precios)
        siguen su curso y congelan el bloque en curso.
        """
        actual = ahora if ahora is not None else datetime.now()
        minutos = actual.hour * 60 + actual.minute
        if minutos % self.bloque_minutos() != 0:
            return
        self.hass.async_create_task(self.async_recalcular("tramo"))

    @callback
    def _async_perfil_diario(self, ahora) -> None:
        """04:35: recalcula el perfil tras la limpieza de la caché."""
        self.hass.async_create_task(
            self._async_perfil_y_recalcular("perfil_diario")
        )

    async def _async_perfil_y_recalcular(self, motivo: str) -> None:
        """Recalcula el perfil (y reentrena el ML de F5) y luego el plan."""
        try:
            await self.predictor.recalcular_perfil()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Recálculo de perfil falló: %s", err)
        # F5: reentrenamiento diario del forecaster ML tras el perfil
        try:
            await self.predictor.async_reentrenar_ml()
            await self._async_aviso_ml()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Reentrenamiento ML falló: %s", err)
            hoy = datetime.now().astimezone().date()
            if self._aviso_ml_fecha != hoy:
                self._aviso_ml_fecha = hoy
                await self._async_notificar(
                    "Optimizador Batería OMIE: reentrenamiento ML falló",
                    f"El reentrenamiento diario del forecaster falló: {err}. "
                    "La predicción sigue con el perfil F3 (guardrail). "
                    "Detalles en el log de HA.",
                    NOTIFICACION_ML_REENTRENAMIENTO,
                )
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
        self._eventos_soc += 1
        if self._eventos_soc == 1:
            _LOGGER.info(
                "Listener de SOC operativo: primer evento recibido de %s",
                nuevo.entity_id,
            )
        umbral = float(
            self.entry.data.get(
                CONF_DESVIACION_SOC_REPLAN, DEFAULT_DESVIACION_SOC_REPLAN
            )
        )
        if abs(soc - tramo.soc_previsto_pct) <= umbral:
            return
        # 0.16: cooldown compartido con el respaldo — una desviación
        # persistente (carga que no llega) provoca eventos constantes
        # y sin él se replanificaría en cada polling del datalogger
        ahora = datetime.now().astimezone()
        if (
            self._ts_replan_desviacion is not None
            and (ahora - self._ts_replan_desviacion).total_seconds()
            < COOLDOWN_DESVIACION_S
        ):
            return
        self._ts_replan_desviacion = ahora
        _LOGGER.info(
            "SOC real %.1f %% desviado del previsto %.1f %% "
            "(umbral %.0f %%): replanificando (evento, n.º %d)",
            soc,
            tramo.soc_previsto_pct,
            umbral,
            self._eventos_soc,
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
        self,
        ranuras: list | None,
        festivo_manual: bool = False,
    ) -> list[float] | None:
        """Precios efectivos €/kWh por posición de tramo del día."""
        if not ranuras:
            return None
        return [
            self.coordinador_precio.modelo_tarifa.obtener_precio_efectivo(
                r.precio_eur_kwh,
                r.fecha_hora_utc,
                festivo_manual=festivo_manual,
            )
            for r in ranuras
        ]

    async def _async_notificar(
        self, titulo: str, mensaje: str, notification_id: str
    ) -> None:
        """Notificación persistente tolerante a fallos (F6-U1)."""
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": titulo,
                    "message": mensaje,
                    "notification_id": notification_id,
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "No se pudo emitir la notificación %s: %s",
                notification_id,
                err,
            )
    def _entity_id(self, unique_id: str) -> str:
        """Resuelve el entity_id real de un unique_id del registry.

        El prefijo del entity_id lo define el nombre del config entry,
        así que los mensajes al usuario no deben hardcodearlo (D-F6).
        """
        registro = er.async_get(self.hass)
        return (
            registro.async_get_entity_id_from_unique_id(unique_id)
            or f"sensor.{unique_id}"
        )

    async def _async_aviso_sin_precios_manana(
        self, precios_manana: list | None, hoy: date
    ) -> None:
        """Avisa una vez al día si falta D+1 pasadas las 16:00 (F6-U1).

        La publicación de OMIE llega ~13:45 hora local; sin ella el
        plan nocturno queda cojo (horizonte limitado a hoy). Al llegar
        los precios, la notificación se retira.
        """
        if precios_manana:
            if self._aviso_precios_fecha == hoy:
                try:
                    await self.hass.services.async_call(
                        "persistent_notification",
                        "dismiss",
                        {
                            "notification_id": (
                                NOTIFICACION_SIN_PRECIOS_MANANA
                            )
                        },
                        blocking=True,
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Aviso D+1 ya retirado: %s", err)
                self._aviso_precios_fecha = None
            return
        ahora = datetime.now().astimezone()
        if (
            ahora.hour >= HORA_AVISO_SIN_PRECIOS_MANANA
            and self._aviso_precios_fecha != hoy
        ):
            self._aviso_precios_fecha = hoy
            _LOGGER.warning(
                "Sin precios de mañana pasadas las %02d:00; el plan "
                "nocturno quedará limitado a hoy",
                HORA_AVISO_SIN_PRECIOS_MANANA,
            )
            await self._async_notificar(
                "Optimizador Batería OMIE: sin precios de mañana",
                f"{ahora.strftime('%H:%M')} aún no hay precios de mañana "
                "en ninguna fuente. El plan nocturno quedará limitado a "
                "hoy (horizonte incompleto). Revisa "
                f"{self._entity_id(SENSOR_ESTADO_CONTROL)} "
                "y la integración OMIE; probando acción de nuevo en el "
                "próximo ciclo de 15 min.",
                NOTIFICACION_SIN_PRECIOS_MANANA,
            )

    async def _async_aviso_ml(self) -> None:
        """Avisa de flips de adopción ML↔perfil y fallos de
        entrenamiento (F6-U1). El flip se notifica siempre que cambia
        respecto al reentrenamiento anterior de la sesión.
        """
        adoptado = self.predictor.metricas_ml.get("adoptado")
        if (
            adoptado != self._adoptado_previo
            and self._adoptado_previo is not None
        ):
            _LOGGER.info(
                "Forecast de consumo: fuente %s",
                "ML" if adoptado else "perfil",
            )
            await self._async_notificar(
                "Optimizador Batería OMIE: cambio de fuente de forecast",
                "El reentrenamiento diario cambió la fuente de predicción "
                "a "
                + ("ML (ridge)" if adoptado else "el perfil F3")
                + ". Detalles en "
                + self._entity_id(SENSOR_FORECAST_QUALITY)
                + ".",
                NOTIFICACION_ML_ADOPCION,
            )
        self._adoptado_previo = adoptado

    async def async_recalcular(self, motivo: str) -> Plan:
        """Recalcula el plan completo (hoy + mañana si hay precios).

        Sin SOC de telemetría y con un plan vigente (D-F5.3.3) no se
        derriba el plan: se congela (los gaps transitorios de Solarman
        no deben vaciar el plan cada vez). Con un plan nuevo en curso,
        el bloque en curso se exporta del plan previo salvo que el
        motivo sea inmediato (D-F5.3.1).
        """
        soc = None
        telemetria = self.coordinador_telemetria.data
        if telemetria is not None:
            soc = telemetria.soc

        plan_previo = (
            self._plan if self._plan is not None and self._plan.tramos
            else None
        )
        if soc is None:
            if plan_previo is not None:
                _LOGGER.warning(
                    "Telemetría sin SOC (motivo: %s): el plan vigente "
                    "se congela (no se derriba, D-F5.3.3)",
                    motivo,
                )
                self._notificar()
                return plan_previo
            motivo_congelado = (
                "telemetría sin SOC: sin plan previo que congelar "
                "(D-F5.3.3)"
            )
        else:
            motivo_congelado = None

        datos = self.coordinador_precio.datos
        hoy = datetime.now().astimezone().date()
        manana = hoy.fromordinal(hoy.toordinal() + 1)

        prediccion_hoy = await self.predictor.predecir(
            hoy, festivo_hoy=self._festivo_hoy
        )
        precios_manana = self._precios_efectivos(datos.precios_manana,)
        await self._async_aviso_sin_precios_manana(precios_manana, hoy)
        prediccion_manana = None
        if precios_manana is not None:
            prediccion_manana = await self.predictor.predecir(manana)

        # Recalcular los parámetros del planificador en cada recálculo:
        # los number de F4 mutan entry.data en caliente (D16) y deben
        # aplicar en ≤ 60 s sin recrear la entrada.
        self.planificador.p = ParametrosPlanner.desde_entry(self.entry)
        plan = await self._planificar_con_motor(hoy, precios_manana, prediccion_hoy, prediccion_manana, soc)

        # Bloque de compromiso (D-F5.3.1): fuera de límite y sin motivo
        # inmediato, el bloque en curso se exporta del plan previo
        if (
            plan_previo is not None
            and plan.estado == "ok"
            and motivo not in MOTIVOS_REPLAN_INMEDIATO
        ):
            self._exportar_bloque_en_curso(plan, plan_previo, motivo)
        if motivo_congelado is not None:
            plan.motivos.append(motivo_congelado)

        self._plan = plan
        self._prediccion_hoy = prediccion_hoy
        self._prediccion_manana = prediccion_manana
        if precios_manana is None:
            plan.motivos.append(
                "sin precios de mañana: horizonte limitado a hoy "
                "(publicación D+1 esperable ~13:45 hora local)"
            )
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

    def bloque_minutos(self) -> int:
        """Minutos de bloque de compromiso (múltiplo de 15 en 15–120)."""
        valor = self.entry.data.get(
            CONF_BLOQUE_MINUTOS, DEFAULT_BLOQUE_MINUTOS
        )
        try:
            bloque = int(valor)
        except (TypeError, ValueError):
            return DEFAULT_BLOQUE_MINUTOS
        if bloque % 15 == 0 and 15 <= bloque <= 120:
            return bloque
        return DEFAULT_BLOQUE_MINUTOS

    def proximo_limite_bloque(self, ahora: datetime | None = None) -> datetime:
        """Instante del próximo límite de bloque (replan periódico)."""
        actual = ahora or datetime.now().astimezone()
        minutos = actual.hour * 60 + actual.minute
        bloque = self.bloque_minutos()
        restantes = bloque - minutos % bloque
        return actual + timedelta(minutes=restantes)

    def _exportar_bloque_en_curso(
        self, plan: Plan, plan_previo: Plan, motivo: str
    ) -> None:
        """Copia el bloque en curso del plan previo al plan nuevo.

        Solo aplica si el recálculo ocurre DENTRO del bloque (replan
        reactivo): en un límite exacto el bloque nuevo manda. Lo que
        publica el sensor plan es lo que ejecuta el executor.
        """
        actual = datetime.now().astimezone()
        minutos = actual.hour * 60 + actual.minute
        bloque = self.bloque_minutos()
        if minutos % bloque == 0:
            return
        hoy = actual.date()
        tramos_bloque = bloque // 15
        inicio = (minutos // bloque) * tramos_bloque
        fin = inicio + tramos_bloque
        previos = {(t.fecha, t.tramo): t for t in plan_previo.tramos}
        exportados = 0
        for i, tramo in enumerate(plan.tramos):
            offset = (tramo.fecha - hoy).days * TRAMOS_POR_DIA + tramo.tramo
            previo = previos.get((tramo.fecha, tramo.tramo))
            if inicio <= offset < fin and previo is not None:
                plan.tramos[i] = previo
                exportados += 1
        if exportados:
            plan.motivos.append(
                f"bloque en curso congelado: {exportados} tramos del "
                "plan previo (D-F5.3.1)"
            )
            _LOGGER.info(
                "Bloque en curso congelado: %d tramos del plan previo "
                "(motivo: %s)",
                exportados,
                motivo,
            )

    async def _planificar_con_motor(
        self, hoy, precios_manana, prediccion_hoy, prediccion_manana, soc
    ) -> Plan:
        """Planes con el motor configurado (D-F5.2: greedy | lp).

        El LP es un import perezoso (PuLP solo se carga si se usa) y
        cualquier fallo suyo recalcula con el greedy: nunca sin plan.
        """
        datos = self.coordinador_precio.datos
        precios_hoy = self._precios_efectivos(
            datos.precios_hoy, festivo_manual=self._festivo_hoy
        ) or []
        algoritmo = self.entry.data.get(
            CONF_ALGORITMO_PLANNER, ALGORITMO_GREEDY
        )
        if algoritmo == ALGORITMO_LP:
            try:
                # pylint: disable=import-outside-toplevel
                from custom_components.optimizador_bateria_omie.planner_lp import (
                    ErrorPlannerLP,
                    PlanificadorLP,
                )

                lp = PlanificadorLP(self.planificador.p)
                # PuLP resuelve en disco (MPS/SOL) y lanza CBC: es I/O +
                # CPU bloqueantes y NO puede correr en el event loop
                # (advertencia de HA 2026.11 en producción; regla nº1
                # de AGENTS). Executor job: el loop queda libre.
                plan = await self.hass.async_add_executor_job(
                    lp.planificar,
                    hoy, precios_hoy, precios_manana,
                    prediccion_hoy, prediccion_manana, soc,
                )
                if plan.estado == "ok":
                    plan.motivos.append("planificador LP")
                    return plan
                # Estado degradado (sin telemetría/precios): el greedy
                # genera el mismo mensaje; se le deja el turno
                _LOGGER.warning(
                    "LP sin datos completos (%s); greedy", plan.estado
                )
            except ErrorPlannerLP as err:
                _LOGGER.warning(
                    "Planner LP sin solución (%s); recalculando greedy", err
                )
            except Exception:
                _LOGGER.exception("Planner LP falló; recalculando greedy")
        return self.planificador.planificar(
            hoy, precios_hoy, precios_manana,
            prediccion_hoy, prediccion_manana, soc,
        )

    # ─── Accesores ───

    @property
    def plan(self) -> Plan | None:
        """Plan vigente."""
        return self._plan

    @property
    def eventos_soc(self) -> int:
        """Eventos del listener de SOC desde el arranque (0.16)."""
        return self._eventos_soc

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
