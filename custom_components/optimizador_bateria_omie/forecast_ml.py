"""Forecaster ML de consumo por tramos de 15 min (F5, RF-05 capa 2).

Dataset de features y entrenamiento con ``HistGradientBoostingRegressor``
(scikit-learn). El modelo **nunca sustituye a ciegas** al perfil
estadístico de F3: se adopta solo si el MAPE del backtest lo mejora
(guardrail de adopción, D-F5.2). Persistencia en
``config/optimizador_bateria_omie/modelo_consumo.joblib`` (fuera de
``custom_components/`` para sobrevivir a HACS).

Features (SPEC RF-05): ``tramo_dia`` (doblete sin/cos para la
periodicidad diaria), ``dia_semana`` (sin/cos), ``es_festivo``,
``temperatura_media`` (opcional, features de clima; NaN si no hay
entidad) y ``consumo_mismo_tramo_{7,14,21,28}d`` (NaN si el histórico
no llega).
"""
# versión 0.12.0-U1

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from custom_components.optimizador_bateria_omie.const import (
    TRAMOS_POR_DIA,
)

_LOGGER = logging.getLogger(__name__)

LAGS_DIAS = (7, 14, 21, 28)

# Orden fijo de columnas del dataset; los valores faltantes viajan
# como NaN (HistGradientBoosting los tolera de forma nativa)
COLUMNAS = (
    "tramo_sen",
    "tramo_cos",
    "diasem_sen",
    "diasem_cos",
    "es_festivo",
    "temperatura_media",
    *(
        f"consumo_mismo_tramo_{d}d"
        for d in LAGS_DIAS
    ),
)


@dataclass
class EjemploML:
    """Fila del dataset de entrenamiento/predicción."""

    fecha: date
    tramo: int
    features: dict[str, float]
    consumo_w: float | None = None  # None → fila de predicción


@dataclass
class DatasetML:
    """Matriz X/y alineadas con ``COLUMNAS``."""

    x: np.ndarray
    y: np.ndarray
    ejemplos: list[EjemploML]

    def __len__(self) -> int:
        return len(self.ejemplos)


# ─── Features base ───


def _doblete(valor: float, periodo: float) -> tuple[float, float]:
    """Codificación cíclica seno/coseno."""
    angulo = 2.0 * np.pi * valor / periodo
    return float(np.sin(angulo)), float(np.cos(angulo))


def _valor_lag(
    lags: dict[tuple[date, int], float], fecha: date, tramo: int
) -> float:
    """Consumo del mismo tramo hace ``d`` días (NaN si no existe)."""
    valor = lags.get((fecha, tramo))
    return float(valor) if valor is not None else float("nan")


def construir_ejemplo(
    fecha: date,
    tramo: int,
    festivos: set[date],
    lags: dict[tuple[date, int], float],
    temperature: float | None = None,
    consumo_w: float | None = None,
) -> EjemploML:
    """Fila de dataset para un tramo de un día.

    ``lags`` mapea ``(fecha, tramo) → consumo W`` del histórico; para
    los lags de 7/14/21/28 d se consulta ``(fecha - d, tramo)``.
    """
    tramo_sen, tramo_cos = _doblete(tramo, TRAMOS_POR_DIA)
    diasem_sen, diasem_cos = _doblete(fecha.weekday(), 7)
    features: dict[str, float] = {
        "tramo_sen": tramo_sen,
        "tramo_cos": tramo_cos,
        "diasem_sen": diasem_sen,
        "diasem_cos": diasem_cos,
        "es_festivo": 1.0 if fecha in festivos else 0.0,
        "temperatura_media": (
            float(temperature) if temperature is not None else float("nan")
        ),
    }
    for dias in LAGS_DIAS:
        features[f"consumo_mismo_tramo_{dias}d"] = _valor_lag(
            lags, fecha - timedelta(days=dias), tramo
        )
    return EjemploML(
        fecha=fecha, tramo=tramo, features=features, consumo_w=consumo_w
    )


def indice_lags(muestras: list) -> dict[tuple[date, int], float]:
    """Indexa ``Muestra`` del recorder por ``(fecha local, tramo)``."""
    indice: dict[tuple[date, int], float] = {}
    for muestra in muestras:
        fecha_local = muestra.ts_utc.astimezone().date()
        tramo = _tramo_de(muestra.ts_utc.astimezone())
        indice[(fecha_local, tramo)] = muestra.valor_w
    return indice


def _tramo_de(momento: datetime) -> int:
    """Índice 0-95 del tramo de 15 min en hora local."""
    return momento.hour * 4 + momento.minute // 15


def construir_dataset(
    muestras: list,
    festivos: set[date],
    temperaturas: dict[date, float] | None = None,
) -> DatasetML:
    """Dataset de entrenamiento desde las muestras del recorder.

    Una fila por muestra con su ``consumo_w`` como objetivo; los lags
    faltantes y las temperaturas ausentes viajan como NaN (el modelo
    los tolera).
    """
    temperaturas = temperaturas or {}
    lags = indice_lags(muestras)
    ejemplos = [
        construir_ejemplo(
            fecha.date(),
            _tramo_de(fecha),
            festivos,
            lags,
            temperature=temperaturas.get(fecha.date()),
            consumo_w=muestra.valor_w,
        )
        for fecha, muestra in (
            (m.ts_utc.astimezone(), m) for m in muestras
        )
    ]
    return _empaquetar(ejemplos)


def _empaquetar(ejemplos: list[EjemploML]) -> DatasetML:
    """Ejemplos → X (float32) / y ordenados por ``COLUMNAS``."""
    if not ejemplos:
        return DatasetML(
            x=np.empty((0, len(COLUMNAS)), dtype=np.float32),
            y=np.empty(0, dtype=np.float32),
            ejemplos=[],
        )
    x = np.array(
        [
            [ejemplo.features[col] for col in COLUMNAS]
            for ejemplo in ejemplos
        ],
        dtype=np.float32,
    )
    y = np.array(
        [ejemplo.consumo_w or float("nan") for ejemplo in ejemplos],
        dtype=np.float32,
    )
    return DatasetML(x=x, y=y, ejemplos=ejemplos)


def predecir_features(
    fecha: date,
    festivos: set[date],
    lags: dict[tuple[date, int], float],
    temperaturas: dict[date, float] | None = None,
) -> np.ndarray:
    """Matriz de features de los 96 tramos de ``fecha`` (predicción)."""
    temperaturas = temperaturas or {}
    ejemplos = [
        construir_ejemplo(
            fecha, tramo, festivos, lags,
            temperature=temperaturas.get(fecha),
        )
        for tramo in range(TRAMOS_POR_DIA)
    ]
    return _empaquetar(ejemplos).x


# Ruta estándar del modelo (sobrevive a actualizaciones de HACS)
def ruta_modelo_por_defecto(hass) -> Path:
    """``config/optimizador_bateria_omie/modelo_consumo.joblib``."""
    return (
        Path(hass.config.config_dir)
        / "optimizador_bateria_omie"
        / "modelo_consumo.joblib"
    )
