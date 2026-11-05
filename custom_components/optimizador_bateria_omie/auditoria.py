"""Auditoría del ahorro real del día (F4.5)."""
# versión 0.10.1

import logging
from dataclasses import asdict, dataclass
from datetime import datetime

from homeassistant.helpers.storage import Store

from custom_components.optimizador_bateria_omie.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORE_KEY_AUDITORIA = f"{DOMAIN}/auditoria.json"
VERSION_STORE_AUDITORIA = 1
DELTA_T_H = 0.25


@dataclass
class AcumuladoDia:
    fecha: str
    ahorro_real_eur: float = 0.0
    coste_sin_bateria_eur: float = 0.0
    coste_real_eur: float = 0.0
    descarga_bateria_kwh: float = 0.0
    carga_bateria_kwh: float = 0.0
    red_importada_kwh: float = 0.0
    red_exportada_kwh: float = 0.0


class AuditoriaAhorro:
    """Acumula el ahorro real del día tramo a tramo."""

    def __init__(self, hass, coordinador_precio, coordinador_telemetria):
        self.hass = hass
        self._precio = coordinador_precio
        self._telemetria = coordinador_telemetria
        self._store = Store(hass, VERSION_STORE_AUDITORIA, STORE_KEY_AUDITORIA)
        self._dia: AcumuladoDia | None = None
        self._ultimo_tramo: int | None = None

    async def async_cargar(self) -> None:
        datos = await self._store.async_load()
        hoy = datetime.now().astimezone().date().isoformat()
        if datos and datos.get("fecha") == hoy:
            self._dia = AcumuladoDia(
                **{k: v for k, v in datos.items() if k != "ultimo_tramo"}
            )
            self._ultimo_tramo = datos.get("ultimo_tramo")
        else:
            self._dia = AcumuladoDia(fecha=hoy)

    async def async_actualizar(self, ahora: datetime) -> None:
        """Llamado en cada ciclo de telemetría.

        ``ahora`` puede venir en UTC; ``precios_hoy`` está indexado por
        día local (00:00 local = tramo 0), por lo que el día y el tramo
        se calculan siempre en hora local.
        """
        ahora_local = ahora.astimezone()
        hoy = ahora_local.date().isoformat()
        if self._dia is None or self._dia.fecha != hoy:
            self._dia = AcumuladoDia(fecha=hoy)
            self._ultimo_tramo = None

        tramo_actual = ahora_local.hour * 4 + ahora_local.minute // 15
        if self._ultimo_tramo == tramo_actual:
            return

        if self._ultimo_tramo is not None:
            await self._cerrar_tramo(self._ultimo_tramo)
        self._ultimo_tramo = tramo_actual
        await self._persistir()

    async def _cerrar_tramo(self, tramo: int) -> None:
        datos_tel = self._telemetria.data
        if not datos_tel or datos_tel.soc is None:
            return
        if not self._precio.datos or not self._precio.datos.precios_hoy:
            return
        if tramo >= len(self._precio.datos.precios_hoy):
            return
        tramo_precio = self._precio.datos.precios_hoy[tramo]
        precio_eff = self._precio.modelo_tarifa.obtener_precio_efectivo(
            tramo_precio.precio_eur_kwh, tramo_precio.fecha_hora_utc
        )

        descarga_w = max(0.0, datos_tel.potencia_bateria or 0.0)
        carga_w = max(0.0, -(datos_tel.potencia_bateria or 0.0))
        consumo_w = datos_tel.consumo_casa or 0.0

        descarga_kwh = descarga_w * DELTA_T_H / 1000
        carga_kwh = carga_w * DELTA_T_H / 1000
        consumo_kwh = consumo_w * DELTA_T_H / 1000

        red_importada_kwh = max(0.0, consumo_kwh + carga_kwh - descarga_kwh)
        red_exportada_kwh = max(0.0, descarga_kwh - consumo_kwh - carga_kwh)

        coste_real = (red_importada_kwh - red_exportada_kwh) * precio_eff
        coste_sin_bateria = consumo_kwh * precio_eff

        self._dia.ahorro_real_eur += coste_sin_bateria - coste_real
        self._dia.coste_real_eur += coste_real
        self._dia.coste_sin_bateria_eur += coste_sin_bateria
        self._dia.descarga_bateria_kwh += descarga_kwh
        self._dia.carga_bateria_kwh += carga_kwh
        self._dia.red_importada_kwh += red_importada_kwh
        self._dia.red_exportada_kwh += red_exportada_kwh

    async def _persistir(self) -> None:
        if self._dia is None:
            return
        await self._store.async_save(
            {**asdict(self._dia), "ultimo_tramo": self._ultimo_tramo}
        )

    @property
    def ahorro_real_hoy(self) -> float:
        return round(self._dia.ahorro_real_eur, 4) if self._dia else 0.0

    @property
    def desglose(self) -> dict:
        if not self._dia:
            return {}
        return {
            "coste_sin_bateria_eur": round(self._dia.coste_sin_bateria_eur, 4),
            "coste_real_eur": round(self._dia.coste_real_eur, 4),
            "descarga_bateria_kwh": round(self._dia.descarga_bateria_kwh, 3),
            "carga_bateria_kwh": round(self._dia.carga_bateria_kwh, 3),
            "red_importada_kwh": round(self._dia.red_importada_kwh, 3),
            "red_exportada_kwh": round(self._dia.red_exportada_kwh, 3),
        }