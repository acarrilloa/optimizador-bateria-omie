"""Fuente de histórico desde el recorder de Home Assistant (F3).

Consulta el histórico de una entidad (por defecto en W) agregando las
muestras a tramos de 15 min (media del tramo). Los estados no válidos
(``unavailable``/``unknown``, no numéricos o con unidad distinta de W
cuando está declarada) se descartan: **no se imputan valores**.

La consulta al recorder es bloqueante y se ejecuta siempre en el
executor (AGENTS.md); este módulo nunca escribe en el inversor.
"""
# versión 0.9.0

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

MINUTOS_TRAMO = 15


class ErrorHistorico(Exception):
    """Error al consultar el histórico del recorder."""


@dataclass
class Muestra:
    """Media de una magnitud (W) en un tramo de 15 min."""

    ts_utc: datetime
    valor_w: float


def inicio_tramo_local(
    ts_utc: datetime, tz: Any | None = None
) -> datetime:
    """Inicio del tramo de 15 min que contiene ``ts`` en hora local.

    ``tz`` permite fijar la zona en tests; en producción siempre es la
    zona local de HA (``None``).
    """
    local = ts_utc.astimezone(tz) if tz is not None else ts_utc.astimezone()
    return local.replace(
        minute=(local.minute // MINUTOS_TRAMO) * MINUTOS_TRAMO,
        second=0,
        microsecond=0,
    )


def _valor_valido(estado: Any) -> float | None:
    """Valor numérico del estado o ``None`` si no es utilizable."""
    if estado.state in ("unavailable", "unknown", None):
        return None
    unidad = estado.attributes.get("unit_of_measurement") if (
        hasattr(estado, "attributes") and estado.attributes
    ) else None
    if unidad is not None and unidad not in ("W", "w"):
        return None
    try:
        return float(estado.state)
    except (TypeError, ValueError):
        return None


def agregar_por_tramos(
    estados: list[Any], tz: Any | None = None
) -> list[Muestra]:
    """Agrega estados del recorder a medias por tramo de 15 min.

    Devuelve una muestra por tramo con datos válidos, ordenada por
    tiempo. Los estados no numéricos, ``unavailable``/``unknown`` o con
    unidad declarada distinta de W se descartan (sin imputación).
    """
    por_tramo: dict[datetime, list[float]] = defaultdict(list)
    descartados = 0
    for estado in estados:
        valor = _valor_valido(estado)
        if valor is None:
            descartados += 1
            continue
        por_tramo[inicio_tramo_local(estado.last_updated, tz)].append(valor)

    if descartados:
        _LOGGER.debug(
            "%d muestra(s) descartada(s) por no válidas (unavailable, "
            "no numéricas o unidad ≠ W)",
            descartados,
        )

    muestras: list[Muestra] = []
    for tramo, valores in sorted(por_tramo.items()):
        muestras.append(
            Muestra(
                ts_utc=tramo.astimezone(UTC),
                valor_w=sum(valores) / len(valores),
            )
        )
    return muestras


def _consultar_recorder(
    hass: Any, entity_id: str, inicio: datetime, fin: datetime
) -> dict[str, list]:
    """Consulta bloqueante al recorder (se ejecuta en el executor).

    Aislada en función de módulo para que los tests puedan sustituirla
    sin tocar la base de datos real.
    """
    # pylint: disable=import-outside-toplevel
    from homeassistant.components.recorder import history

    datos = history.get_significant_states(hass, inicio, fin, [entity_id])
    return datos.get(entity_id, [])


class FuenteHistoricoRecorder:
    """Lee el histórico de una entidad desde el HA recorder."""

    def __init__(self, hass: Any, entity_id: str) -> None:
        """Inicializa la fuente con la entidad a consultar."""
        self.hass = hass
        self.entity_id = entity_id

    async def obtener_series(self, dias: int) -> list[Muestra]:
        """Muestras agregadas a tramos de la ventana de ``dias`` días.

        La ventana va desde ``dias`` días atrás hasta ahora. Errores
        esperables (``ErrorHistorico``): sin ``entity_id`` configurado,
        entidad inexistente o sin datos válidos en la ventana.
        """
        if not self.entity_id:
            raise ErrorHistorico(
                "Sin entity_id configurado para el histórico"
            )
        if self.hass.states.get(self.entity_id) is None:
            raise ErrorHistorico(
                f"La entidad '{self.entity_id}' no existe en HA"
            )

        fin = datetime.now(tz=UTC)
        inicio = fin - timedelta(days=dias)
        estados = await self.hass.async_add_executor_job(
            _consultar_recorder, self.hass, self.entity_id, inicio, fin
        )
        muestras = agregar_por_tramos(estados or [])

        if not muestras:
            raise ErrorHistorico(
                f"Sin datos válidos de '{self.entity_id}' en los últimos "
                f"{dias} días (recorder)"
            )
        _LOGGER.info(
            "Histórico de %s: %d tramos con datos en %d día(s)",
            self.entity_id,
            len(muestras),
            dias,
        )
        return muestras
