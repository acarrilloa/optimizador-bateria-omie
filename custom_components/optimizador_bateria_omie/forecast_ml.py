"""Forecaster ML de consumo por tramos de 15 min (F5, RF-05 capa 2).

Dataset de features y entrenamiento con **ridge en numpy puro**
(0.12.1: scikit-learn no publica wheels musl/ARM64 y el HA Yellow no
puede instalarlo; la interfaz pública no cambia). El modelo **nunca
sustituye a ciegas** al perfil
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
# versión 0.12.1

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
# como NaN (el ridge los imputa con la media de columna del entrenar)
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


# ─── Modelo (F5-U2) ───


class ForecasterML:
    """Modelo de consumo por tramo: ridge en numpy puro (0.12.1).

    F5-U2 lo implementó con ``HistGradientBoostingRegressor``
    (scikit-learn), pero scikit-learn **no publica wheels musl
    (Alpine/ARM64)** y el HA Yellow no puede instalarlo — se
    reimplementa con **regresión ridge (forma cerrada, numpy)** con las
    MISMAS features, banda por cuantiles de residuos, MAPE de backtest
    y persistencia. La interfaz pública no cambia; el guardrail de
    adopción (D-F5.2) sigue decidiendo ML vs perfil por MAPE.

    Los NaN de features se imputan con la media de columna del
    entrenamiento (guardada para predicción). El entrenamiento es
    **bloqueante** (CPU): el llamador debe wrappear con
    ``hass.async_add_executor_job`` (AGENTS §1).
    """

    LAMBDA = 1.0  # regularización ridge (intercepto sin penalizar)

    def __init__(self, ruta_modelo=None) -> None:
        self._ruta_modelo = ruta_modelo
        self._pesos: np.ndarray | None = None
        self._medias_columnas: np.ndarray | None = None
        self._offset_p10 = 0.0
        self._offset_p90 = 0.0
        self.metricas: dict = {}

    # ─── Estado ───

    @property
    def entrenado(self) -> bool:
        """¿Hay modelo cargado/entrenado?"""
        return self._pesos is not None

    @property
    def adoptado(self) -> bool:
        """¿El ML superó al perfil estadístico (guardrail D-F5.2)?"""
        return bool(self.metricas.get("adoptado"))

    # ─── Preparación de matrices ───

    def _imputar(self, x: "np.ndarray") -> "np.ndarray":
        """NaN → media de columna del entrenamiento (0 si toda-NaN)."""
        x = np.atleast_2d(np.asarray(x, dtype=np.float64))
        if self._medias_columnas is None:
            return np.nan_to_num(x, nan=0.0)
        x = x.copy()
        for j in range(x.shape[1]):
            columna = np.isnan(x[:, j])
            if columna.any():
                x[columna, j] = self._medias_columnas[j]
        return x

    @staticmethod
    def _con_intercepto(x: "np.ndarray") -> "np.ndarray":
        n = x.shape[0]
        return np.hstack([np.ones((n, 1)), x])

    # ─── Entrenamiento / evaluación ───

    def entrenar(self, dataset: DatasetML) -> None:
        """Ajusta el ridge y la banda con el dataset dado."""
        if len(dataset) == 0:
            raise ValueError("Dataset vacío")
        x = np.asarray(dataset.x, dtype=np.float64)
        y = np.asarray(dataset.y, dtype=np.float64)

        # Imputación: medias de columna (0 en columnas sin datos)
        medias = np.zeros(x.shape[1])
        for j in range(x.shape[1]):
            validos = x[:, j][~np.isnan(x[:, j])]
            if validos.size:
                medias[j] = validos.mean()
        self._medias_columnas = medias
        x = self._imputar(x)

        xd = self._con_intercepto(x)
        d = xd.shape[1]
        regularizacion = self.LAMBDA * np.eye(d)
        regularizacion[0, 0] = 0.0  # intercepto sin penalizar
        try:
            self._pesos = np.linalg.solve(
                xd.T @ xd + regularizacion, xd.T @ y
            )
        except np.linalg.LinAlgError as err:
            raise ValueError(f"Ridge sin solución: {err}") from err

        residuos = y - xd @ self._pesos
        self._offset_p10 = float(np.quantile(residuos, 0.10))
        self._offset_p90 = float(np.quantile(residuos, 0.90))
        self.metricas["n_entrenamiento"] = len(dataset)

    def evaluar_mape(self, dataset: DatasetML) -> float | None:
        """MAPE % del dataset dado (None sin modelo o sin datos)."""
        if self._pesos is None or len(dataset) == 0:
            return None
        yhat = self.predecir_array(dataset.x)
        y = np.asarray(dataset.y, dtype=np.float64)
        divisor = np.maximum(np.abs(y), 1.0)
        return float(np.mean(np.abs(y - yhat) / divisor) * 100.0)

    def predecir_array(self, x: np.ndarray) -> np.ndarray:
        """p50 (W) por fila; banda disponible como offsets."""
        if self._pesos is None:
            raise RuntimeError("ForecasterML sin entrenar")
        xd = self._con_intercepto(self._imputar(x))
        return xd @ self._pesos

    @property
    def banda(self) -> tuple[float, float]:
        """Offsets (p10, p90) de la banda aditiva."""
        return self._offset_p10, self._offset_p90

    # ─── Persistencia (bloqueante: usar executor job) ───

    def guardar(self) -> None:
        """Serializa pesos, imputación, banda y métricas (pickle stdlib)."""
        if self._ruta_modelo is None or self._pesos is None:
            return
        import pickle

        self._ruta_modelo.parent.mkdir(parents=True, exist_ok=True)
        with open(self._ruta_modelo, "wb") as fichero:
            pickle.dump(
                {
                    "pesos": self._pesos,
                    "medias": self._medias_columnas,
                    "offset_p10": self._offset_p10,
                    "offset_p90": self._offset_p90,
                    "metricas": dict(self.metricas),
                },
                fichero,
            )

    def cargar(self) -> bool:
        """Restaura modelo+banda+métricas; False si no existe."""
        if self._ruta_modelo is None or not self._ruta_modelo.exists():
            return False
        import pickle

        with open(self._ruta_modelo, "rb") as fichero:
            datos = pickle.load(fichero)
        self._pesos = datos["pesos"]
        self._medias_columnas = datos["medias"]
        self._offset_p10 = datos["offset_p10"]
        self._offset_p90 = datos["offset_p90"]
        self.metricas = datos.get("metricas", {})
        return True
