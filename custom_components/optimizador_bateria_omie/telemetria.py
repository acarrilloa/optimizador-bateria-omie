"""Telemetría del inversor: dataclass, Store de validación y coordinador."""
# versión 0.8.5

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.optimizador_bateria_omie.const import (
    CONF_ENTITY_ID_CONSUMO,
    CONF_ENTITY_ID_FV,
    CONF_ENTITY_ID_PBAT,
    CONF_ENTITY_ID_SOC,
    CONF_INTERVALO_TELEMETRIA,
    CONF_TELEMETRY_SOURCE,
    SIGNAL_VALIDACION_CAMBIADA,
    STORE_KEY_PROBE,
    TELEMETRY_SOURCE_MODBUS,
    VERSION_STORE_PROBE,
)
from custom_components.optimizador_bateria_omie.mapa_registros import (
    ErrorMapaRegistros,
    MapaRegistros,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class TelemetriaInversor:
    """Telemetría instantánea del inversor.

    Convención de signos Deye (D16): ``potencia_bateria`` es
    **+descarga / −carga** en W; ``corriente_bateria`` igual (A).
    Una magnitud ``None`` significa "no disponible o sin dirección
    en el mapa de la familia"; nunca se inventan valores.
    """

    soc: float | None
    potencia_bateria: float | None
    corriente_bateria: float | None
    consumo_casa: float | None
    produccion_fv: float | None
    temperatura_inversor: float | None
    temperatura_bateria: float | None
    ts_actualizacion: datetime


class GestorValidacion:
    """Gestiona el estado de validación de familia en ``hass.storage``.

    Persiste ``{"familia", "probe_addr", "probe_funcion",
    "ultima_validacion_ts"}`` (D13) y emite la señal
    ``SIGNAL_VALIDACION_CAMBIADA`` en cada cambio para que el sensor
    diagnóstico se repinte.
    """

    def __init__(self, hass: HomeAssistant, store: Store | None = None) -> None:
        """Inicializa el gestor; ``store`` es inyectable para tests."""
        self.hass = hass
        self._store = store or Store(
            hass, VERSION_STORE_PROBE, STORE_KEY_PROBE
        )
        self.estado: dict = {}

    async def async_cargar(self) -> dict:
        """Carga el estado persistido (vacío si no hay nada)."""
        datos = await self._store.async_load()
        self.estado = datos or {}
        return self.estado

    async def async_guardar(self, datos: dict) -> None:
        """Persiste el estado de validación y notifica el cambio."""
        self.estado = dict(datos)
        await self._store.async_save(self.estado)
        async_dispatcher_send(self.hass, SIGNAL_VALIDACION_CAMBIADA)

    async def async_borrar(self) -> None:
        """Borra la validación (familia a "ninguna") y notifica."""
        self.estado = {}
        await self._store.async_save({})
        async_dispatcher_send(self.hass, SIGNAL_VALIDACION_CAMBIADA)

    async def async_revalidar(self, mapa: MapaRegistros) -> dict:
        """Revalidación al arranque (m1).

        Si la familia del Store ya no existe en el mapa o su
        ``probe_addr`` difiere del YAML actual, se invalida y se
        requiere un nuevo ``probe_registers``.
        """
        datos = await self.async_cargar()
        familia = datos.get("familia") if datos else None
        if not familia:
            return datos

        try:
            addr_actual = mapa.probe(familia)["addr"]
        except ErrorMapaRegistros:
            _LOGGER.warning(
                "La familia %s ya no existe en el mapa de registros: "
                "validación invalidada",
                familia,
            )
            await self.async_borrar()
            return {}

        if datos.get("probe_addr") != addr_actual:
            _LOGGER.warning(
                "probe_addr cambió (Store: %s, YAML: %s): la familia %s "
                "queda invalidada; ejecuta de nuevo probe_registers",
                datos.get("probe_addr"), addr_actual, familia,
            )
            await self.async_borrar()
            return {}

        return datos

    @property
    def familia(self) -> str | None:
        """Familia validada actual (``None`` si no hay validación)."""
        return self.estado.get("familia") or None


class CoordinadorTelemetria(DataUpdateCoordinator[TelemetriaInversor]):
    """Coordinador de telemetría del inversor (intervalo configurable)."""

    def __init__(
        self,
        hass: HomeAssistant,
        cliente,
        gestor: GestorValidacion,
        entry,
    ) -> None:
        """Inicializa el coordinador de telemetría.

        ``cliente`` es un ``ClienteSolarman`` (o ``None`` si la fuente
        es ``ha_entities``); nunca se instancia aquí.
        """
        intervalo = entry.data.get(CONF_INTERVALO_TELEMETRIA, 30)
        super().__init__(
            hass,
            _LOGGER,
            name="Optimizador Batería OMIE Telemetría",
            update_interval=timedelta(seconds=intervalo),
            config_entry=entry,
        )
        self._cliente = cliente
        self._gestor = gestor
        self._telemetry_source = entry.data.get(
            CONF_TELEMETRY_SOURCE, TELEMETRY_SOURCE_MODBUS
        )

    @property
    def cliente(self):
        """Cliente Solarman asociado (None si la fuente es ha_entities)."""
        return self._cliente

    async def _async_update_data(self) -> TelemetriaInversor:
        """Actualiza la telemetría según la fuente configurada."""
        if self._telemetry_source == TELEMETRY_SOURCE_MODBUS:
            # C5: guard ANTES de tocar Modbus
            datos = await self._gestor.async_cargar()
            familia = datos.get("familia") if datos else None
            if familia is None:
                raise UpdateFailed(
                    "Sin familia validada. Ejecuta "
                    "optimizador_bateria_omie.probe_registers con el SOC "
                    "actual de la APP Solarman, o revisa "
                    "sensor.optimizador_bateria_omie_familia_validada."
                )
            return await self._cliente.leer_telemetria(familia)
        return self._leer_de_ha_entities()

    def _leer_de_ha_entities(self) -> TelemetriaInversor:
        """Lee la telemetría de entidades de HA existentes.

        Estados no numéricos o entidades ausentes dejan la magnitud a
        ``None``; nunca se inventan valores.
        """

        def _valor(clave: str) -> float | None:
            entity_id = self.config_entry.data.get(clave)
            if not entity_id:
                _LOGGER.warning(
                    "Sin entity_id configurado para '%s'; magnitud None",
                    clave,
                )
                return None
            estado = self.hass.states.get(entity_id)
            if estado is None:
                _LOGGER.warning("Entidad %s no encontrada", entity_id)
                return None
            try:
                return float(estado.state)
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "Estado no numérico en %s: %r", entity_id, estado.state
                )
                return None

        return TelemetriaInversor(
            soc=_valor(CONF_ENTITY_ID_SOC),
            potencia_bateria=_valor(CONF_ENTITY_ID_PBAT),
            # Sin fuente HA para la corriente en F2 → None (no inventada)
            corriente_bateria=None,
            consumo_casa=_valor(CONF_ENTITY_ID_CONSUMO),
            produccion_fv=_valor(CONF_ENTITY_ID_FV),
            temperatura_inversor=None,
            temperatura_bateria=None, 
            ts_actualizacion=datetime.now(tz=UTC),
        )
