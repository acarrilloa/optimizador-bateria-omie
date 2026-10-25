"""Coordinador de datos para la integración Optimizador Batería OMIE."""
# versión 0.7.6

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.optimizador_bateria_omie.cliente_omie import (
    ClienteOmie,
    Ranura,
)
from custom_components.optimizador_bateria_omie.tarifa import ModeloTarifa

_LOGGER = logging.getLogger(__name__)


@dataclass
class DatosOmie:
    """Datos de precios OMIE."""

    precios_hoy: list[Ranura]
    precios_manana: list[Ranura] | None
    clasificaciones_hoy: list[str]
    clasificaciones_manana: list[str] | None
    estadisticas_hoy: dict
    estadisticas_manana: dict | None
    fuente: str
    ts_actualizacion: datetime


class CoordinadorDatosOmie(DataUpdateCoordinator):
    """Coordinador para actualizar datos de precios OMIE."""

    def __init__(
        self,
        hass,
        cliente_omie: ClienteOmie,
        modelo_tarifa: ModeloTarifa,
        config_entry: ConfigEntry | None = None,
    ):
        super().__init__(
            hass,
            _LOGGER,
            name="Optimizador Batería OMIE",
            update_interval=timedelta(minutes=15),
            config_entry=config_entry,
        )
        self.cliente_omie = cliente_omie
        self.modelo_tarifa = modelo_tarifa
        self._datos = DatosOmie(
            precios_hoy=[],
            precios_manana=None,
            clasificaciones_hoy=[],
            clasificaciones_manana=None,
            estadisticas_hoy={"min": 0, "max": 0, "mediana": 0},
            estadisticas_manana=None,
            fuente="inicializando",
            ts_actualizacion=datetime.now(tz=UTC),
        )

    async def _async_update_data(self) -> DatosOmie:
        """Actualiza los datos de precios."""
        try:
            hoy = datetime.now().astimezone().date()
            manana = hoy + timedelta(days=1)

            precios_hoy = await self.cliente_omie.obtener_precios_del_dia(hoy)

            try:
                precios_manana = await self.cliente_omie.obtener_precios_del_dia(manana)
            except Exception:  # noqa: BLE001
                precios_manana = None

            precios_efectivos_hoy = [
                self.modelo_tarifa.obtener_precio_efectivo(
                    r.precio_eur_kwh, r.fecha_hora_utc
                )
                for r in precios_hoy
            ]
            # Clasificación POR PERÍODO 2.0TD (valle/llano/punta según
            # hora local + festivos + fines de semana). No por percentiles.
            clasificaciones_hoy = self.modelo_tarifa.clasificar_por_periodo(
                [r.fecha_hora_utc for r in precios_hoy]
            )
            estadisticas_hoy = self.modelo_tarifa.obtener_estadisticas(
                precios_efectivos_hoy
            )

            if precios_manana:
                precios_efectivos_manana = [
                    self.modelo_tarifa.obtener_precio_efectivo(
                        r.precio_eur_kwh, r.fecha_hora_utc
                    )
                    for r in precios_manana
                ]
                clasificaciones_manana = self.modelo_tarifa.clasificar_por_periodo(
                    [r.fecha_hora_utc for r in precios_manana]
                )
                estadisticas_manana = self.modelo_tarifa.obtener_estadisticas(
                    precios_efectivos_manana
                )
            else:
                clasificaciones_manana = None
                estadisticas_manana = None

            self._datos = DatosOmie(
                precios_hoy=precios_hoy,
                precios_manana=precios_manana,
                clasificaciones_hoy=clasificaciones_hoy,
                clasificaciones_manana=clasificaciones_manana,
                estadisticas_hoy=estadisticas_hoy,
                estadisticas_manana=estadisticas_manana,
                fuente=precios_hoy[0].fuente if precios_hoy else "desconocida",
                ts_actualizacion=datetime.now(tz=UTC),
            )
            return self._datos

        except Exception as e:
            _LOGGER.exception("Error al actualizar datos")
            self._datos = DatosOmie(
                precios_hoy=[],
                precios_manana=None,
                clasificaciones_hoy=[],
                clasificaciones_manana=None,
                estadisticas_hoy={"min": 0, "max": 0, "mediana": 0},
                estadisticas_manana=None,
                fuente="desconocida",
                ts_actualizacion=datetime.now(tz=UTC),
            )
            raise UpdateFailed(f"Error al actualizar datos: {e}") from e

    @property
    def datos(self) -> DatosOmie:
        """Devuelve los datos actuales."""
        return self._datos

    def _buscar_ranura(self, ranuras: list[Ranura], ahora: datetime) -> Ranura | None:
        """Busca la ranura que contiene el instante dado."""
        for ranura in ranuras:
            fin = ranura.fecha_hora_fin_utc or (
                ranura.fecha_hora_utc + timedelta(minutes=15)
            )
            if ranura.fecha_hora_utc <= ahora < fin:
                return ranura
        return None

    def obtener_precio_actual(self) -> float | None:
        """Obtiene el precio actual."""
        if not self._datos.precios_hoy:
            return None

        ahora = datetime.now(tz=UTC)
        ranura = self._buscar_ranura(self._datos.precios_hoy, ahora)
        if ranura is None and self._datos.precios_manana:
            ranura = self._buscar_ranura(self._datos.precios_manana, ahora)

        return ranura.precio_eur_kwh if ranura else None

    def obtener_precio_efectivo_actual(self) -> float | None:
        """Obtiene el precio efectivo actual."""
        precio = self.obtener_precio_actual()
        if precio is None:
            return None
        return self.modelo_tarifa.obtener_precio_efectivo(
            precio, datetime.now(tz=UTC)
        )

    def obtener_clasificacion_actual(self) -> str | None:
        """Obtiene la clasificación actual (por índice dentro de precios_hoy)."""
        if not self._datos.precios_hoy or not self._datos.clasificaciones_hoy:
            return None

        ahora = datetime.now(tz=UTC)
        primera = self._datos.precios_hoy[0].fecha_hora_utc
        idx = int((ahora - primera).total_seconds() // 900)

        if 0 <= idx < len(self._datos.clasificaciones_hoy):
            return self._datos.clasificaciones_hoy[idx]
        return None

    def obtener_estadisticas_actual(self) -> dict | None:
        """Obtiene las estadísticas actuales."""
        return self._datos.estadisticas_hoy

    def obtener_precios_efectivos_ordenados(self) -> list[dict]:
        """Curva de precios efectivos del día: [{hora, precio}, ...]."""
        if not self._datos or not self._datos.precios_hoy:
            return []
        return [
            {
                "hora": r.fecha_hora_utc.astimezone().strftime("%H:%M"),
                "precio": round(
                    self.modelo_tarifa.obtener_precio_efectivo(
                        r.precio_eur_kwh, r.fecha_hora_utc
                    ),
                    4,
                ),
            }
            for r in self._datos.precios_hoy
        ]