"""Tests del forecaster ML de consumo (F5)."""

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from custom_components.optimizador_bateria_omie.forecast_ml import (
    COLUMNAS,
    LAGS_DIAS,
    DatasetML,
    ForecasterML,
    construir_dataset,
    construir_ejemplo,
    indice_lags,
    predecir_features,
    ruta_modelo_por_defecto,
)
from custom_components.optimizador_bateria_omie.historico import (
    Muestra,
)

HOY = date(2026, 11, 9)
FESTIVO = date(2026, 11, 1)


def _muestras_sinteticas(
    dias: int = 35, base_w: float = 300.0, seed: int = 42
) -> list[Muestra]:
    """Muestras por tramo con patrón determinista + ruido con seed."""
    rng = np.random.default_rng(seed)
    muestras = []
    for d in range(dias):
        fecha = HOY - timedelta(days=dias - 1 - d)
        for tramo in range(96):
            # Patrón: base + onda diaria + tipo de día + ruido
            onda = 150.0 * np.sin(np.pi * tramo / 96)
            fin_de_semana = 120.0 if fecha.weekday() >= 5 else 0.0
            valor = (
                base_w + onda + fin_de_semana
                + float(rng.normal(0, 20))
            )
            momento = datetime(
                fecha.year, fecha.month, fecha.day,
                tzinfo=datetime.now().astimezone().tzinfo,
            ) + timedelta(minutes=15 * tramo)
            muestras.append(
                Muestra(ts_utc=momento.astimezone(UTC), valor_w=valor)
            )
    return muestras


class TestColumnas:
    def test_orden_y_lags(self):
        assert COLUMNAS[:4] == (
            "tramo_sen", "tramo_cos", "diasem_sen", "diasem_cos",
        )
        for dias in LAGS_DIAS:
            assert f"consumo_mismo_tramo_{dias}d" in COLUMNAS
        assert "es_festivo" in COLUMNAS
        assert "temperatura_media" in COLUMNAS


class TestConstruirEjemplo:
    def test_features_base_y_festivo(self):
        festivos = {FESTIVO}
        ejemplo = construir_ejemplo(FESTIVO, 0, festivos, {})

        assert ejemplo.features["es_festivo"] == 1.0
        # tramo 0: sen(0)=0, cos(0)=1
        assert ejemplo.features["tramo_sen"] == pytest.approx(0.0)
        assert ejemplo.features["tramo_cos"] == pytest.approx(1.0)
        # lunes festivo (1 nov 2026 es domingo; usar 2 nov lunes festivo)
        ejemplo_laborable = construir_ejemplo(
            date(2026, 11, 3), 0, festivos, {}
        )
        assert ejemplo_laborable.features["es_festivo"] == 0.0

    def test_lag_de_7_dias_aparece(self):
        lags = {(HOY - timedelta(days=7), 10): 123.0}
        ejemplo = construir_ejemplo(HOY, 10, set(), lags)

        valor = ejemplo.features["consumo_mismo_tramo_7d"]
        assert valor == pytest.approx(123.0)
        # lag de 14 d ausente → NaN
        assert np.isnan(ejemplo.features["consumo_mismo_tramo_14d"])

    def test_temperatura_nan_si_falta(self):
        ejemplo = construir_ejemplo(HOY, 5, set(), {}, temperature=None)
        assert np.isnan(ejemplo.features["temperatura_media"])
        ejemplo2 = construir_ejemplo(
            HOY, 5, set(), {}, temperature=17.5
        )
        assert ejemplo2.features["temperatura_media"] == pytest.approx(17.5)


class TestDataset:
    def test_construir_dataset_dimensiones(self):
        muestras = _muestras_sinteticas(dias=35)
        dataset = construir_dataset(muestras, festivos={FESTIVO})

        assert isinstance(dataset, DatasetML)
        assert len(dataset) == len(muestras)
        assert dataset.x.shape == (len(muestras), len(COLUMNAS))
        assert dataset.y.shape == (len(muestras),)
        # Objetivo alineado con la muestra original
        assert dataset.y[0] == pytest.approx(muestras[0].valor_w)

    def test_dataset_vacio(self):
        dataset = construir_dataset([], festivos=set())
        assert len(dataset) == 0
        assert dataset.x.shape == (0, len(COLUMNAS))

    def test_determinismo(self):
        a = construir_dataset(_muestras_sinteticas(), set())
        b = construir_dataset(_muestras_sinteticas(), set())
        # NaN → 0 para comparar (NaN != NaN); los NaN van en las
        # mismas posiciones por construcción determinista
        assert np.array_equal(
            np.nan_to_num(a.x), np.nan_to_num(b.x)
        )
        assert np.array_equal(a.y, b.y)

    def test_indice_lags(self):
        muestras = _muestras_sinteticas(dias=7)
        indice = indice_lags(muestras)

        # El último día generado es HOY; su primer tramo es la
        # muestra en la posición 96·(dias-1)
        assert (HOY, 0) in indice
        assert indice[(HOY, 0)] == pytest.approx(
            muestras[96 * 6].valor_w
        )


class TestPredecirFeatures:
    def test_matriz_96_tramos(self):
        lags = indice_lags(_muestras_sinteticas(dias=30))
        x = predecir_features(HOY + timedelta(days=1), set(), lags)

        assert x.shape == (96, len(COLUMNAS))


class TestRutaModelo:
    def test_fuera_de_custom_components(self, tmp_path):
        class HassFake:
            class config:
                config_dir = str(tmp_path)

        ruta = ruta_modelo_por_defecto(HassFake())
        assert ruta == (
            tmp_path / "optimizador_bateria_omie"
            / "modelo_consumo.joblib"
        )


class TestForecasterML:
    def _dataset(self, dias=30, seed=42):
        return construir_dataset(
            _muestras_sinteticas(dias=dias, seed=seed), set()
        )

    def test_entrenar_y_evaluar(self):
        dataset = self._dataset()
        corte = int(len(dataset) * 0.7)
        entreno = DatasetML(
            x=dataset.x[:corte], y=dataset.y[:corte],
            ejemplos=dataset.ejemplos[:corte],
        )
        prueba = DatasetML(
            x=dataset.x[corte:], y=dataset.y[corte:],
            ejemplos=dataset.ejemplos[corte:],
        )
        ml = ForecasterML()

        assert ml.entrenado is False
        assert ml.evaluar_mape(prueba) is None

        ml.entrenar(entreno)
        assert ml.entrenado is True
        assert ml.metricas["n_entrenamiento"] == corte

        # Dataset sintético con patrón fuerte: el MAPE debe ser bajo
        mape = ml.evaluar_mape(prueba)
        assert mape is not None
        assert mape < 15.0

    def test_bandas_aditivas(self):
        dataset = self._dataset(dias=20)
        ml = ForecasterML()
        ml.entrenar(dataset)

        off10, off90 = ml.banda
        assert off10 <= 0.0 <= off90

    def test_predecir_array_96(self):
        ml = ForecasterML()
        ml.entrenar(self._dataset(dias=20))
        lags = indice_lags(_muestras_sinteticas(dias=20))
        x = predecir_features(HOY, set(), lags)

        p50 = ml.predecir_array(x)
        assert p50.shape == (96,)
        assert np.all(p50 > 0)

    def test_persistencia_redonda(self, tmp_path):
        ruta = tmp_path / "modelo.joblib"
        ml = ForecasterML(ruta_modelo=ruta)
        ml.entrenar(self._dataset(dias=20))
        ml.metricas["mape_ml"] = 8.3
        ml.guardar()

        otro = ForecasterML(ruta_modelo=ruta)
        assert otro.cargar() is True
        assert otro.entrenado is True
        assert otro.metricas.get("mape_ml") == 8.3
        lags = indice_lags(_muestras_sinteticas(dias=20))
        x = predecir_features(HOY, set(), lags)
        a = ml.predecir_array(x)
        b = otro.predecir_array(x)
        assert np.array_equal(a, b)

    def test_cargar_sin_fichero(self, tmp_path):
        ml = ForecasterML(ruta_modelo=tmp_path / "no_existe.joblib")
        assert ml.cargar() is False
