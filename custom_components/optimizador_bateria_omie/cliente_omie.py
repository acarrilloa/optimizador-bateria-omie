"""Cliente para obtener precios de OMIE."""
# versión 0.7.3

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from custom_components.optimizador_bateria_omie.const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ConfiguracionOmie:
    """Configuración del cliente OMIE."""

    def __init__(
        self,
        fuente_prioritaria: str = "omie",
        usar_cache: bool = True,
        dias_cache: int = 7,
    ):
        self.fuente_prioritaria = fuente_prioritaria
        self.usar_cache = usar_cache
        self.dias_cache = dias_cache


class Ranura:
    """Representa un tramo horario de precios."""

    def __init__(
        self,
        fecha_hora_utc: datetime,
        precio_eur_kwh: float,
        fuente: str,
        fecha_publicacion: datetime,
        fecha_hora_fin_utc: datetime | None = None,
    ):
        self.fecha_hora_utc = fecha_hora_utc
        self.precio_eur_kwh = precio_eur_kwh
        self.fuente = fuente
        self.fecha_publicacion = fecha_publicacion
        self.fecha_hora_fin_utc = fecha_hora_fin_utc

    def __eq__(self, other):
        if not isinstance(other, Ranura):
            return False
        return (
            self.fecha_hora_utc == other.fecha_hora_utc
            and self.precio_eur_kwh == other.precio_eur_kwh
            and self.fuente == other.fuente
        )


class ErrorFuenteOmie(Exception):
    """Error al obtener datos de una fuente OMIE."""


class ErrorIntegracionNoDisponible(Exception):
    """Error cuando la integración OMIE no está disponible."""


class ClienteOmie:
    """Cliente para obtener precios de OMIE."""

    def __init__(self, hass, config: ConfiguracionOmie):
        self.hass = hass
        self.config = config
        self._dir_cache = (
            Path(hass.config.config_dir)
            / "custom_components"
            / DOMAIN
            / "cache"
        )

    async def _obtener_desde_integracion_omie(self, fecha: date) -> list:
        """Lee los precios de los sensores de la plantilla OMIE."""
        hoy_local = datetime.now().astimezone().date()

        if fecha == hoy_local - timedelta(days=1):
            sensor_entity_id = "sensor.omie_precios_de_es_ayer"
        elif fecha == hoy_local:
            sensor_entity_id = "sensor.omie_precios_de_es_hoy"
        elif fecha == hoy_local + timedelta(days=1):
            sensor_entity_id = "sensor.omie_precios_de_es_manana"
        else:
            _LOGGER.warning(
                "Fecha %s fuera del rango de los sensores OMIE (hoy local=%s)",
                fecha, hoy_local,
            )
            return []

        state = self.hass.states.get(sensor_entity_id)
        if state is None:
            _LOGGER.warning("Sensor %s no encontrado", sensor_entity_id)
            return []

        datos = state.attributes.get("precios")
        if (
            isinstance(datos, list)
            and datos
            and isinstance(datos[0], dict)
            and "start" in datos[0]
            and "price" in datos[0]
        ):
            _LOGGER.debug("%s → %d intervalos", sensor_entity_id, len(datos))
            return datos

        _LOGGER.info("Sensor %s sin datos de precios", sensor_entity_id)
        return []

    async def _obtener_desde_redata(self, fecha: date) -> list:
        """Obtiene precios desde REData (fallback)."""
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        url = f"https://api.preciodelaluz.es/v1/prices?date={fecha.strftime('%Y-%m-%d')}"
        session = async_get_clientsession(self.hass)

        async with session.get(url) as response:
            if response.status != 200:
                raise ErrorFuenteOmie(f"REData status {response.status}")
            data = await response.json()

        if isinstance(data, dict) and "data" in data:
            return data["data"]
        if isinstance(data, list):
            return data
        _LOGGER.error("Formato de REData inesperado: %s", type(data).__name__)
        return []

    def _normalizar_a_15min(self, datos: list, fecha: date) -> list:
        """Normaliza datos OMIE ({start, end, price}) a 96 Ranuras."""
        if not datos:
            return []

        ranuras = []
        for item in datos:
            try:
                start_dt = datetime.fromisoformat(item["start"])
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=UTC)
                else:
                    start_dt = start_dt.astimezone(UTC)

                end_str = item.get("end")
                if end_str:
                    end_dt = datetime.fromisoformat(end_str)
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=UTC)
                    else:
                        end_dt = end_dt.astimezone(UTC)
                else:
                    end_dt = start_dt + timedelta(minutes=15)

                ranuras.append(
                    Ranura(
                        fecha_hora_utc=start_dt,
                        precio_eur_kwh=float(item["price"]),
                        fuente="omie",
                        fecha_publicacion=datetime.now(tz=UTC),
                        fecha_hora_fin_utc=end_dt,
                    )
                )
            except (KeyError, ValueError) as e:
                _LOGGER.warning("Ranura descartada: %s (%s)", item, e)
                continue

        if len(ranuras) != 96:
            _LOGGER.warning(
                "Se esperaban 96 ranuras para %s, se obtuvieron %d",
                fecha, len(ranuras),
            )

        return ranuras

    def _leer_cache(self, cache_file: Path) -> list[Ranura] | None:
        """Lee la caché de un día.

        Método bloqueante: llamar siempre vía
        ``hass.async_add_executor_job`` para no bloquear el event loop.
        """
        try:
            if not cache_file.exists():
                return None
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
        except (OSError, ValueError) as e:
            _LOGGER.warning("Error leyendo cache %s: %s", cache_file, e)
            return None

        # La caché se valida a 96 entradas; si no, se descarta (SPEC §9.2)
        if not cached or len(cached) != 96:
            return None

        return [
            Ranura(
                fecha_hora_utc=datetime.fromisoformat(d["fecha_hora_utc"]),
                precio_eur_kwh=d["precio_eur_kwh"],
                fuente=d["fuente"],
                fecha_publicacion=datetime.fromisoformat(d["fecha_publicacion"]),
                fecha_hora_fin_utc=(
                    datetime.fromisoformat(d["fecha_hora_fin_utc"])
                    if d.get("fecha_hora_fin_utc") else None
                ),
            )
            for d in cached
        ]

    def _escribir_cache(self, cache_file: Path, ranuras: list) -> None:
        """Escribe la caché de un día.

        Método bloqueante: llamar siempre vía
        ``hass.async_add_executor_job`` para no bloquear el event loop.
        """
        try:
            self._dir_cache.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(
                    [
                        {
                            "fecha_hora_utc": r.fecha_hora_utc.isoformat(),
                            "precio_eur_kwh": r.precio_eur_kwh,
                            "fuente": r.fuente,
                            "fecha_publicacion": r.fecha_publicacion.isoformat(),
                            "fecha_hora_fin_utc": (
                                r.fecha_hora_fin_utc.isoformat()
                                if r.fecha_hora_fin_utc else None
                            ),
                        }
                        for r in ranuras
                    ],
                    f, ensure_ascii=False, indent=2,
                )
        except OSError as e:
            _LOGGER.warning("Error escribiendo cache: %s", e)

    async def obtener_precios_del_dia(self, fecha: date) -> list:
        """Obtiene los precios para un día específico."""
        cache_file = self._dir_cache / f"{fecha.strftime('%Y-%m-%d')}.json"

        # 1) Caché (E/S en executor, fuera del event loop)
        if self.config.usar_cache:
            cached = await self.hass.async_add_executor_job(
                self._leer_cache, cache_file
            )
            if cached:
                return cached

        # 2) Cascada de fuentes
        datos = None
        fuente = None

        if self.config.fuente_prioritaria == "omie" and "omie" in self.hass.config.components:
            try:
                datos = await self._obtener_desde_integracion_omie(fecha)
                fuente = "omie"
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("OMIE falló: %s", e)
                datos = None

        if not datos:
            try:
                datos = await self._obtener_desde_redata(fecha)
                fuente = "redata"
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("REData falló: %s", e)
                datos = None

        if not datos:
            raise ErrorFuenteOmie("Todas las fuentes fallaron")

        # 3) Normalizar
        ranuras = self._normalizar_a_15min(datos, fecha)
        for r in ranuras:
            r.fuente = fuente

        # 4) Persistir caché (E/S en executor, fuera del event loop)
        if self.config.usar_cache and ranuras:
            await self.hass.async_add_executor_job(
                self._escribir_cache, cache_file, ranuras
            )

        return ranuras