"""Features de clima para el forecaster ML (F5-U5, RF-05).

Dos fuentes opcionales y configurables por *Reconfigurar*:
- ``entity_id_clima``: entidad ``weather.*`` de la AEMET — su atributo
  ``forecast`` (formato estándar de HA) da la **temperatura media
  prevista** de un día (hoy/mañana para las features de predicción).
- ``entity_id_temp_historico``: sensor de temperatura de la AEMET —
  su history del recorder da las **medias diarias históricas** para las
  features de entrenamiento.

Sin configurar (o sin datos), las features de temperatura viajan
como NaN y ``HistGradientBoosting`` las ignora (máscara de U2).
"""
# versión 0.12.0

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)


def temperatura_media_prevista(
    hass: Any, entity_id: str, fecha: date
) -> float | None:
    """Media de las temperaturas del ``forecast`` de ``fecha``.

    ``None`` si la entidad no existe, no está disponible, no tiene
    ``forecast`` o no hay periodos de esa fecha.
    """
    if not entity_id:
        return None
    estado = hass.states.get(entity_id)
    if estado is None or estado.state in ("unavailable", "unknown"):
        return None
    prevision = (estado.attributes or {}).get("forecast") or []
    valores: list[float] = []
    for periodo in prevision:
        try:
            momento = datetime.fromisoformat(str(periodo.get("datetime")))
        except (TypeError, ValueError):
            continue
        if momento.date() != fecha:
            continue
        temperatura = periodo.get("temperature")
        if isinstance(temperatura, (int, float)):
            valores.append(float(temperatura))
    if not valores:
        return None
    return sum(valores) / len(valores)


def _consultar_recorder(hass, entity_id, inicio, fin) -> list:
    """Consulta bloqueante al recorder (se ejecuta en el executor)."""
    # pylint: disable=import-outside-toplevel
    from homeassistant.components.recorder import history

    datos = history.get_significant_states(hass, inicio, fin, [entity_id])
    return datos.get(entity_id, [])


def temperaturas_diarias(hass, entity_id: str, dias: int) -> dict:
    """Medias diarias (°C) de los últimos ``dias`` días desde el
    recorder de HA — bloqueante: usar con ``async_add_executor_job``.

    Estados no numéricos o vacíos se descartan; sin ``entity_id``
    devuelve ``{}``. Aislada como función para que los tests la
    sustituyan sin tocar la base de datos real.
    """
    if not entity_id:
        return {}
    fin = datetime.now(tz=UTC)
    inicio = fin - timedelta(days=dias)
    try:
        estados = _consultar_recorder(hass, entity_id, inicio, fin)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Histórico de temperatura no disponible: %s", err
        )
        return {}
    por_dia: defaultdict[date, list[float]] = defaultdict(list)
    for estado in estados:
        try:
            valor = float(estado.state)
        except (TypeError, ValueError):
            continue
        momento = getattr(estado, "last_updated", None)
        if momento is None:
            continue
        por_dia[momento.astimezone().date()].append(valor)
    return {
        dia: sum(valores) / len(valores)
        for dia, valores in por_dia.items()
        if valores
    }
