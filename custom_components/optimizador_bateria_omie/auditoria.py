"""Auditoría del ahorro real del día (F4.5, refinada en F5-U4)."""
# versión 0.12.0

import logging
from dataclasses import asdict, dataclass
from datetime import datetime

from homeassistant.helpers.storage import Store

from custom_components.optimizador_bateria_omie.const import (
    CONF_PRECIO_EXCEDENTES,
    DEFAULT_PRECIO_EXCEDENTES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STORE_KEY_AUDITORIA = f"{DOMAIN}/auditoria.json"
VERSION_STORE_AUDITORIA = 1
DELTA_T_H = 0.25
# Muestras de telemetría retenidas por tramo (limpieza del búfer)
TRAMOS_RETENIDOS = 2


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
    """Acumula el ahorro real del día tramo a tramo.

    F5-U4: la energía del tramo usa la **media de las lecturas de
    telemetría** de ese tramo (búfer por tramo), no la lectura de
    cierre; y la exportación se	valora al precio de importación por el
    factor ``precio_excedentes`` (0–1, default 1), nunca a negativos:
    ``max(0, precio·factor)`` (D-F5.3).
    """

    def __init__(
        self,
        hass,
        coordinador_precio,
        coordinador_telemetria,
        entry=None,
    ) -> None:
        self.hass = hass
        self._precio = coordinador_precio
        self._telemetria = coordinador_telemetria
        self._entry = entry
        self._store = Store(hass, VERSION_STORE_AUDITORIA, STORE_KEY_AUDITORIA)
        self._dia: AcumuladoDia | None = None
        self._ultimo_tramo: int | None = None
        # Búfer de muestras del tramo en curso y del anterior:
        # (fecha_iso, tramo, potencia_bateria_w, consumo_casa_w)
        self._muestras: list[tuple[str, int, float, float]] = []

    # ─── Factor de excedentes (D-F5.3) ───

    def _factor_excedentes(self) -> float:
        """Factor 0–1 sobre el precio de importación (default 1)."""
        if self._entry is None:
            return DEFAULT_PRECIO_EXCEDENTES
        try:
            return float(
                self._entry.data.get(
                    CONF_PRECIO_EXCEDENTES, DEFAULT_PRECIO_EXCEDENTES
                )
            )
        except (TypeError, ValueError):
            return DEFAULT_PRECIO_EXCEDENTES

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
        """Llamado en cada ciclo de telemetría (muestras del tramo)."""
        ahora_local = ahora.astimezone()
        hoy = ahora_local.date().isoformat()
        if self._dia is None or self._dia.fecha != hoy:
            self._dia = AcumuladoDia(fecha=hoy)
            self._ultimo_tramo = None
            self._muestras = []

        tramo_actual = ahora_local.hour * 4 + ahora_local.minute // 15
        self._registrar_muestra(hoy, tramo_actual)
        if self._ultimo_tramo == tramo_actual:
            return

        if self._ultimo_tramo is not None:
            await self._cerrar_tramo(self._ultimo_tramo)
        self._ultimo_tramo = tramo_actual
        self._limpiar_muestras()
        await self._persistir()

    def _registrar_muestra(self, hoy: str, tramo: int) -> None:
        """Guarda la lectura de telemetría del tramo en curso."""
        datos_tel = self._telemetria.data
        if not datos_tel or datos_tel.soc is None:
            return
        self._muestras.append(
            (
                hoy,
                tramo,
                datos_tel.potencia_bateria or 0.0,
                datos_tel.consumo_casa or 0.0,
            )
        )

    def _limpiar_muestras(self) -> None:
        """Retiene solo los tramos recientes (conserva el cerrado)."""
        limite = (self._ultimo_tramo or 0) - TRAMOS_RETENIDOS
        self._muestras = [
            m for m in self._muestras if m[1] >= limite or m[1] > 96 - 8
        ]

    def _medias_tramo(self, fecha: str, tramo: int) -> tuple[float, float]:
        """Media de potencia_bateria y consumo del tramo (W)."""
        muestras = [
            m for m in self._muestras if m[0] == fecha and m[1] == tramo
        ]
        if not muestras:
            # Sin muestras del tramo: lectura puntual (comportamiento
            # histórico del cierre, mejor que nada)
            datos_tel = self._telemetria.data
            if not datos_tel:
                return 0.0, 0.0
            return (
                datos_tel.potencia_bateria or 0.0,
                datos_tel.consumo_casa or 0.0,
            )
        n = len(muestras)
        return (
            sum(m[2] for m in muestras) / n,
            sum(m[3] for m in muestras) / n,
        )

    async def _cerrar_tramo(self, tramo: int) -> None:
        if self._dia is None:
            return
        if not self._precio.datos or not self._precio.datos.precios_hoy:
            return
        if tramo >= len(self._precio.datos.precios_hoy):
            return
        tramo_precio = self._precio.datos.precios_hoy[tramo]
        precio_eff = self._precio.modelo_tarifa.obtener_precio_efectivo(
            tramo_precio.precio_eur_kwh, tramo_precio.fecha_hora_utc
        )
        # Exportación valorada con el factor de excedentes y nunca
        # a negativos (D-F5.3)
        precio_export = max(0.0, precio_eff * self._factor_excedentes())

        potencia_bateria_w, consumo_w = self._medias_tramo(
            self._dia.fecha, tramo
        )
        # Convención Deye: media firmada +descarga / −carga
        descarga_w = max(0.0, potencia_bateria_w)
        carga_w = max(0.0, -potencia_bateria_w)

        descarga_kwh = descarga_w * DELTA_T_H / 1000
        carga_kwh = carga_w * DELTA_T_H / 1000
        consumo_kwh = consumo_w * DELTA_T_H / 1000

        red_importada_kwh = max(0.0, consumo_kwh + carga_kwh - descarga_kwh)
        red_exportada_kwh = max(0.0, descarga_kwh - consumo_kwh - carga_kwh)

        coste_real = (
            red_importada_kwh * precio_eff
            - red_exportada_kwh * precio_export
        )
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
            "coste_sin_bateria_eur": round(
                self._dia.coste_sin_bateria_eur, 4
            ),
            "coste_real_eur": round(self._dia.coste_real_eur, 4),
            "descarga_bateria_kwh": round(
                self._dia.descarga_bateria_kwh, 3
            ),
            "carga_bateria_kwh": round(self._dia.carga_bateria_kwh, 3),
            "red_importada_kwh": round(self._dia.red_importada_kwh, 3),
            "red_exportada_kwh": round(self._dia.red_exportada_kwh, 3),
        }
