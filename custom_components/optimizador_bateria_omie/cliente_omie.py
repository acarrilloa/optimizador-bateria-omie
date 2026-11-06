"""Cliente para obtener precios de OMIE."""
# versión 0.10.2

import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from custom_components.optimizador_bateria_omie.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

OMIE_WEB_URL = (
    "https://www.omie.es/es/market-results/daily/daily-market/"
    "day-ahead-price"
)
OMIE_WEB_UA = (
    "Mozilla/5.0 (compatible; optimizador-bateria-omie; "
    "+https://github.com/acarrilloa/optimizador-bateria-omie)"
)
OMIE_TRAMOS_ESPERADOS = (92, 96, 100)


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
            _LOGGER.debug(
                "%s → %d intervalos", sensor_entity_id, len(datos),
            )
            return datos

        _LOGGER.info("Sensor %s sin datos de precios", sensor_entity_id)
        return []

    async def _obtener_desde_omie_web(self, fecha: date) -> list:
        """Precios de la web oficial de OMIE (fallback de la integración).

        La página day-ahead de omie.es incrusta en el HTML (SSR) la serie
        Highcharts «Precio marginal español» con 96 precios en €/MWh
        (92/100 en cambio de hora). Es la misma fuente que la integración
        de HA, pero sin depender de que esta haya refrescado su sensor.
        """
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        url = (
            f"{OMIE_WEB_URL}?date={fecha.strftime('%Y-%m-%d')}"
            "&period=15&scope=daily"
        )
        session = async_get_clientsession(self.hass)
        async with session.get(
            url, headers={"User-Agent": OMIE_WEB_UA}
        ) as response:
            if response.status != 200:
                raise ErrorFuenteOmie(
                    f"OMIE web status {response.status}"
                )
            html = await response.text()

        idx = html.find("Precio marginal espa")
        if idx == -1:
            raise ErrorFuenteOmie(
                "OMIE web: serie de precio español no encontrada "
                "(posiblemente aún sin publicar)"
            )
        m = re.search(
            r'\\?"data\\?"\s*:\s*\[([0-9][0-9.,\s]*)\]',
            html[idx : idx + 3000],
        )
        if not m:
            raise ErrorFuenteOmie("OMIE web: precios no encontrados en HTML")
        precios_mwh = [float(v) for v in m.group(1).split(",") if v.strip()]
        if len(precios_mwh) not in OMIE_TRAMOS_ESPERADOS:
            raise ErrorFuenteOmie(
                f"OMIE web: {len(precios_mwh)} tramos "
                f"(esperados {OMIE_TRAMOS_ESPERADOS}); "
                "posiblemente aún sin publicar"
            )

        # €/MWh → €/kWh; ranuras de 15 min desde las 00:00 hora local
        inicio = datetime(
            fecha.year, fecha.month, fecha.day,
            tzinfo=datetime.now().astimezone().tzinfo,
        )
        return [
            {
                "start": (inicio + timedelta(minutes=15 * i)).isoformat(),
                "price": precios_mwh[i] / 1000.0,
            }
            for i in range(len(precios_mwh))
        ]

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

    async def obtener_precios_del_dia(self, fecha: date) -> list:
        """Obtiene los precios para un día específico.

        Tolerante a los días de cambio de hora (DST):
        - 92 tramos: domingo de primavera (CET → CEST).
        - 96 tramos: día normal.
        - 100 tramos: domingo de otoño (CEST → CET).
        """
        cache_file = self._dir_cache / f"{fecha.strftime('%Y-%m-%d')}.json"

        def _read_cache():
            if cache_file.exists():
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            return None

        def _write_cache(ranuras: list):
            self._dir_cache.mkdir(parents=True, exist_ok=True)
            cache_data = [
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
            ]
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)

        # ─── 1) Caché (solo se toca si usar_cache es true) ───
        if self.config.usar_cache:
            try:
                cached_data = await self.hass.async_add_executor_job(_read_cache)
                # Aceptar los tres casos posibles según el día
                if cached_data and len(cached_data) in (92, 96, 100):
                    _LOGGER.info(
                        "Cache válida para %s → %d ranuras",
                        fecha, len(cached_data),
                    )
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
                        for d in cached_data
                    ]
                if cached_data is not None:
                    _LOGGER.warning(
                        "Cache para %s tiene %d ranuras (esperado 92, 96 o 100). "
                        "Se descarta y se pide a la fuente.",
                        fecha, len(cached_data),
                    )
            except (OSError, ValueError) as e:
                _LOGGER.warning("Error leyendo cache %s: %s", cache_file, e)

        # ─── 2) Cascada de fuentes ───
        datos = None
        fuente = None

        if (
            self.config.fuente_prioritaria == "omie"
            and "omie" in self.hass.config.components
        ):
            try:
                datos = await self._obtener_desde_integracion_omie(fecha)
                fuente = "omie"
                _LOGGER.info(
                    "Datos obtenidos de OMIE: %d intervalos",
                    len(datos or []),
                )
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("OMIE falló: %s (%s)", e, type(e).__name__)
                datos = None

        if not datos:
            try:
                datos = await self._obtener_desde_omie_web(fecha)
                fuente = "omie_web"
                _LOGGER.info(
                    "Datos obtenidos de la web de OMIE: %d intervalos",
                    len(datos or []),
                )
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("OMIE web falló: %s (%s)", e, type(e).__name__)
                datos = None

        # Descartar si todos los precios son 0.0 (fuente devolvió "vacío")
        if datos and all(
            isinstance(d, dict) and float(d.get("price", 0)) == 0.0 for d in datos
        ):
            _LOGGER.warning("Todos los precios recibidos son 0.0, descartando fuente")
            datos = None
            fuente = None

        if not datos:
            raise ErrorFuenteOmie("Todas las fuentes fallaron")

        # ─── 3) Normalizar a 15 min ───
        ranuras = self._normalizar_a_15min(datos, fecha)
        for r in ranuras:
            r.fuente = fuente

        if len(ranuras) not in (92, 96, 100):
            _LOGGER.warning(
                "Normalización de %s produjo %d ranuras "
                "(esperado 92, 96 o 100)",
                fecha, len(ranuras),
            )

        # ─── 4) Persistir caché (solo si usar_cache y hay ranuras) ───
        if self.config.usar_cache and ranuras:
            try:
                await self.hass.async_add_executor_job(_write_cache, ranuras)
            except (OSError, ValueError) as e:
                _LOGGER.warning("Error escribiendo cache %s: %s", cache_file, e)

        return ranuras