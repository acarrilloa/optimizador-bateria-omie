"""Tests del predictor de consumo/FV, incluido el parseo de Solcast (F3, U2)."""

import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.optimizador_bateria_omie.const import (
    CONF_ENTITY_ID_PREVISION_FV_HOY,
    CONF_ENTITY_ID_PREVISION_FV_MANANA,
    CONF_FESTIVOS_LOCALES,
    CONF_VENTANA_HISTORICO,
)
from custom_components.optimizador_bateria_omie.historico import Muestra
from custom_components.optimizador_bateria_omie.predictor_consumo import (
    PercentilesTramo,
    PredictorConsumo,
    _parsear_detailed_forecast,
    _percentil,
)
from custom_components.optimizador_bateria_omie.tarifa import ModeloTarifa

MADRID = ZoneInfo("Europe/Madrid")
HOY = datetime.now().astimezone(MADRID).date()
MANANA = HOY + timedelta(days=1)

# --- Fixtures de Solcast (detailedForecast real, resolución 30 min) ---


def _entrada_solcast(dia: date, hora: int, pv: float) -> dict:
    """Entrada de detailedForecast en el formato de Solcast (kWh/periodo)."""
    inicio = datetime(dia.year, dia.month, dia.day, hora, 0, tzinfo=MADRID)
    return {
        "period_start": inicio.isoformat(),
        "period_end": (inicio + timedelta(minutes=30)).isoformat(),
        "pv_estimate": pv,
        "pv_estimate10": pv * 0.5,
        "pv_estimate90": pv * 1.5,
    }


@pytest.fixture
def detailed_forecast_hoy():
    """48 periodos de 30 min de HOY (formato detailedForecast de Solcast)."""
    return [
        _entrada_solcast(HOY, hora, pv=0.001 * (hora % 12) + 0.01)
        for hora in range(24)
        for _ in (0,)
    ] + [
        {
            "period_start": (
                datetime(
                    HOY.year, HOY.month, HOY.day, hora, 30, tzinfo=MADRID
                ).isoformat()
            ),
            "pv_estimate": 0.002,
            "pv_estimate10": 0.001,
            "pv_estimate90": 0.003,
        }
        for hora in range(24)
    ]


@pytest.fixture
def detailed_forecast_manana():
    """48 periodos de 30 min de MAÑANA."""
    return [
        {
            "period_start": (
                datetime(
                    MANANA.year, MANANA.month, MANANA.day,
                    hora // 2, (hora % 2) * 30, tzinfo=MADRID,
                ).isoformat()
            ),
            "pv_estimate": 0.01,
            "pv_estimate10": 0.005,
            "pv_estimate90": 0.02,
        }
        for hora in range(48)
    ]


def _estado_solcast(detailed):
    estado = MagicMock()
    estado.state = "12.5"
    estado.attributes = {"detailedForecast": detailed}
    return estado


# --- Infraestructura de tests ---


@pytest.fixture
def hass(tmp_path):
    h = MagicMock()
    h.config.config_dir = str(tmp_path)
    h.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args: fn(*args)
    )
    h.states.get = MagicMock(return_value=None)
    return h


def _muestra(dia: date, tramo: int, valor: float) -> Muestra:
    """Muestra a medianoche UTC del día, tramo i-ésimo."""
    local = datetime(
        dia.year, dia.month, dia.day, tzinfo=MADRID
    ) + timedelta(minutes=15 * tramo)
    return Muestra(ts_utc=local.astimezone(UTC), valor_w=valor)


def _muestras_sinteticas(inicio: date, dias: int, seed: int = 42):
    """Días de muestras: laborable 400 W, finde 800 W, ruido con seed fija."""
    rng = random.Random(seed)
    muestras = []
    for d in range(dias):
        dia = inicio + timedelta(days=d)
        laborable = dia.weekday() < 5
        for tramo in range(96):
            base = 400.0 if laborable else 800.0
            muestras.append(
                _muestra(dia, tramo, base + rng.uniform(0, 20))
            )
    return muestras


def _predictor(hass, params=None, fuente_fv=None, ruta=None):
    return PredictorConsumo(
        hass=hass,
        fuente_consumo=MagicMock(),
        fuente_fv=fuente_fv if fuente_fv is not None else MagicMock(),
        festivos=ModeloTarifa(),
        params=params or {},
        ruta_perfil=ruta or Path(hass.config.config_dir) / "perfil.json",
    )


class TestPercentil:
    def test_interpolacion_lineal(self):
        assert _percentil([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
        assert _percentil([1.0, 2.0, 3.0, 4.0], 0.0) == pytest.approx(1.0)
        assert _percentil([1.0, 2.0, 3.0, 4.0], 1.0) == pytest.approx(4.0)
        assert _percentil([5.0], 0.5) == pytest.approx(5.0)

    def test_lista_vacia_error(self):
        with pytest.raises(ValueError):
            _percentil([], 0.5)


class TestParseoSolcast:
    """M1: parseo del detailedForecast y mapeo 30→15 min por repetición."""

    def test_mapeo_30_a_15_por_repeticion(self, detailed_forecast_hoy):
        tramos = _parsear_detailed_forecast(
            detailed_forecast_hoy, HOY, tz=MADRID
        )

        assert len(tramos) == 96
        # Cada estimación de 30 min se repite en los dos tramos
        assert tramos[0].p50 == pytest.approx(0.01 * 2000)
        assert tramos[1].p50 == pytest.approx(0.01 * 2000)
        assert tramos[2].p50 == pytest.approx(0.002 * 2000)
        assert tramos[3].p50 == pytest.approx(0.002 * 2000)
        assert tramos[0].fuente == "solcast"

    def test_ignora_periodos_de_otra_fecha(self, detailed_forecast_hoy):
        tramos = _parsear_detailed_forecast(
            detailed_forecast_hoy, MANANA, tz=MADRID
        )

        assert all(tramo is None for tramo in tramos)

    def test_p10_p50_p90(self, detailed_forecast_manana):
        tramos = _parsear_detailed_forecast(
            detailed_forecast_manana, MANANA, tz=MADRID
        )

        assert tramos[0].p10 == pytest.approx(0.005 * 2000)
        assert tramos[0].p50 == pytest.approx(0.01 * 2000)
        assert tramos[0].p90 == pytest.approx(0.02 * 2000)

    def test_pv_no_numerico_se_descarta(self):
        atributo = [
            {
                "period_start": datetime(
                    HOY.year, HOY.month, HOY.day, 12, 0, tzinfo=MADRID
                ).isoformat(),
                "pv_estimate": "nublado",
                "pv_estimate10": 0.1,
                "pv_estimate90": 0.2,
            }
        ]

        tramos = _parsear_detailed_forecast(atributo, HOY, tz=MADRID)

        assert all(tramo is None for tramo in tramos)

    def test_atributo_no_lista(self):
        tramos = _parsear_detailed_forecast("no soy una lista", HOY)
        assert all(tramo is None for tramo in tramos)


class TestTipoDia:
    def test_laborable_y_finde(self, hass):
        predictor = _predictor(hass)
        # Lunes 2026-10-19 → laborable; sábado 2026-10-24 → finde
        assert predictor.tipo_dia(date(2026, 10, 19)) == "laborable"
        assert predictor.tipo_dia(date(2026, 10, 24)) == "finde"
        # Festivo nacional: 2026-01-01 (jueves) → finde
        assert predictor.tipo_dia(date(2026, 1, 1)) == "finde"

    def test_festivos_locales(self, hass):
        predictor = _predictor(hass, params={CONF_FESTIVOS_LOCALES: ["10-20"]})
        assert predictor.tipo_dia(date(2026, 10, 20)) == "finde"

    def test_festivo_hoy_solo_aplica_a_hoy(self, hass):
        predictor = _predictor(hass)
        assert predictor.tipo_dia(HOY, festivo_hoy=True) == "finde"
        if HOY.weekday() < 5:
            assert (
                predictor.tipo_dia(MANANA, festivo_hoy=True) == "laborable"
            )


class TestPerfil:
    def test_recalcular_perfil_mediana_por_tipo(self, hass):
        muestras = _muestras_sinteticas(date(2026, 10, 5), 14)
        predictor = _predictor(hass)
        stats = predictor._construir_estadisticas(muestras)

        # 2026-10-05 es lunes; la ventana incluye el 12-O (lunes festivo
        # nacional) → 9 laborables y 5 días de finde/festivo
        assert stats["laborable"][0]["n_dias"] == 9
        assert stats["finde"][0]["n_dias"] == 5
        assert stats["todos"][0]["n_dias"] == 14
        # Mediana del p50 laborable ≈ 400 W; finde ≈ 800 W
        assert stats["laborable"][0]["p50"] == pytest.approx(400, abs=15)
        assert stats["finde"][0]["p50"] == pytest.approx(800, abs=15)

    @pytest.mark.asyncio
    async def test_recalcular_y_recargar_perfil_identico(self, hass):
        ruta = Path(hass.config.config_dir) / "perfil.json"
        predictor = _predictor(
            hass,
            params={CONF_VENTANA_HISTORICO: 7},
            ruta=ruta,
        )
        muestras = _muestras_sinteticas(date(2026, 10, 5), 14)
        predictor.fuente_consumo.obtener_series = AsyncMock(
            return_value=muestras
        )
        predictor.fuente_fv.obtener_series = AsyncMock(return_value=[])

        await predictor.recalcular_perfil()
        p50_laborable = predictor.predecir_consumo(date(2026, 10, 19))[0].p50

        # Recarga desde disco: idéntico
        predictor2 = _predictor(hass, ruta=ruta)
        assert await predictor2.async_cargar_perfil() is True
        p50_recargado = predictor2.predecir_consumo(date(2026, 10, 19))[0].p50
        assert p50_recargado == pytest.approx(p50_laborable, abs=0.1)

    @pytest.mark.asyncio
    async def test_fuente_fv_fallida_no_rompe_el_perfil(self, hass):
        predictor = _predictor(hass)
        predictor.fuente_consumo.obtener_series = AsyncMock(
            return_value=_muestras_sinteticas(date(2026, 10, 5), 14)
        )
        predictor.fuente_fv.obtener_series = AsyncMock(
            side_effect=Exception("sin datos FV")
        )

        await predictor.recalcular_perfil()

        consumo = predictor.predecir_consumo(date(2026, 10, 19))
        assert consumo[0] is not None


class TestCascada:
    """D6: tramo sin datos → tipo opuesto → global → None."""

    def _perfil_con_hueco(self, hass):
        predictor = _predictor(hass)
        entrada_laborable = {
            tramo: {"p10": 10, "p50": 400, "p90": 1000, "n_dias": 5}
            for tramo in range(96)
        }
        entrada_finde = {
            tramo: {"p10": 20, "p50": 800, "p90": 2000, "n_dias": 4}
            for tramo in range(96)
            if tramo != 12  # hueco en el finde, tramo 12
        }
        entrada_todos = {
            tramo: {"p10": 15, "p50": 500, "p90": 1500, "n_dias": 9}
            for tramo in range(96)
        }
        predictor._perfil.datos = {
            "consumo": {
                "laborable": entrada_laborable,
                "finde": entrada_finde,
                "todos": entrada_todos,
            }
        }
        return predictor

    def test_tramo_con_datos(self, hass):
        predictor = self._perfil_con_hueco(hass)
        pred = predictor.predecir_consumo(date(2026, 10, 24))  # finde
        assert pred[0].p50 == 800

    def test_cascada_a_global(self, hass):
        predictor = self._perfil_con_hueco(hass)
        pred = predictor.predecir_consumo(date(2026, 10, 24))  # finde
        # Tramo 12: finde vacío → opuesto (laborable, n=5) se usa antes
        assert pred[12].p50 == 400

    def test_cascada_a_none(self, hass):
        predictor = self._perfil_con_hueco(hass)
        predictor._perfil.datos["consumo"]["laborable"].pop(12)
        predictor._perfil.datos["consumo"]["todos"].pop(12)
        pred = predictor.predecir_consumo(date(2026, 10, 24))
        assert pred[12] is None

    def test_n_dias_insuficiente_a_none(self, hass):
        predictor = self._perfil_con_hueco(hass)
        predictor._perfil.datos["consumo"]["finde"][0]["n_dias"] = 2
        predictor._perfil.datos["consumo"]["laborable"][0]["n_dias"] = 2
        predictor._perfil.datos["consumo"]["todos"][0]["n_dias"] = 2
        pred = predictor.predecir_consumo(date(2026, 10, 24))
        assert pred[0] is None


class TestPredecirFv:
    """Solcast preferente + fallback por día al perfil (M1 v1.3)."""

    def _con_solcast(self, hass, detailed_hoy, detailed_manana):
        params = {
            CONF_ENTITY_ID_PREVISION_FV_HOY: "sensor.solcast_hoy",
            CONF_ENTITY_ID_PREVISION_FV_MANANA: "sensor.solcast_manana",
        }
        predictor = _predictor(hass, params=params)
        hass.states.get = MagicMock(
            side_effect=lambda entity_id: {
                "sensor.solcast_hoy": _estado_solcast(detailed_hoy),
                "sensor.solcast_manana": _estado_solcast(detailed_manana),
            }.get(entity_id)
        )
        return predictor

    @pytest.mark.asyncio
    async def test_solcast_preferente_por_dia(
        self, hass, detailed_forecast_hoy, detailed_forecast_manana
    ):
        predictor = self._con_solcast(
            hass, detailed_forecast_hoy, detailed_forecast_manana
        )

        fv_hoy, fuente_hoy = await predictor.predecir_fv(HOY)
        fv_manana, fuente_manana = await predictor.predecir_fv(MANANA)

        assert fuente_hoy == "solcast"
        assert fuente_manana == "solcast"
        assert fv_hoy[0].fuente == "solcast"
        assert fv_manana[0].p50 == pytest.approx(0.01 * 2000)

    @pytest.mark.asyncio
    async def test_fallo_manana_solo_manana_cae_al_perfil(
        self, hass, detailed_forecast_hoy
    ):
        """El de mañana no existe → solo mañana cae al perfil."""
        predictor = self._con_solcast(
            hass, detailed_forecast_hoy, detailed_manana=None
        )
        # perfil FV disponible en el predictor
        predictor._predecir_fv_perfil = MagicMock(
            return_value=[
                PercentilesTramo(p10=1, p50=100, p90=200, fuente="perfil")
            ] * 96
        )

        fv_hoy, fuente_hoy = await predictor.predecir_fv(HOY)
        fv_manana, fuente_manana = await predictor.predecir_fv(MANANA)

        assert fuente_hoy == "solcast"
        assert fuente_manana == "perfil"
        assert fv_hoy[0].fuente == "solcast"
        assert fv_manana[0].fuente == "perfil"

    @pytest.mark.asyncio
    async def test_entidad_sin_atributo_fallback_perfil(
        self, hass, caplog
    ):
        estado = MagicMock()
        estado.state = "12.5"
        estado.attributes = {"detailedForecast": []}
        params = {
            CONF_ENTITY_ID_PREVISION_FV_HOY: "sensor.solcast_hoy",
        }
        predictor = _predictor(hass, params=params)
        hass.states.get = MagicMock(return_value=estado)
        predictor._predecir_fv_perfil = MagicMock(
            return_value=[
                PercentilesTramo(p10=1, p50=100, p90=200) for _ in range(96)
            ]
        )

        with caplog.at_level("INFO"):
            fv, fuente = await predictor.predecir_fv(HOY)

        assert fuente == "perfil"
        assert all(tramo is not None for tramo in fv)
        assert "se usa el perfil" in caplog.text

    @pytest.mark.asyncio
    async def test_entidad_unavailable_fallback_perfil(self, hass):
        estado = MagicMock()
        estado.state = "unavailable"
        predictor = _predictor(
            hass,
            params={CONF_ENTITY_ID_PREVISION_FV_HOY: "sensor.solcast_hoy"},
        )
        hass.states.get = MagicMock(return_value=estado)
        predictor._predecir_fv_perfil = MagicMock(
            return_value=[
                PercentilesTramo(p10=1, p50=100, p90=200) for _ in range(96)
            ]
        )

        _, fuente = await predictor.predecir_fv(HOY)

        assert fuente == "perfil"

    @pytest.mark.asyncio
    async def test_ambos_vacios_solo_perfil_y_none(self, hass):
        """Sin Solcast ni perfil FV → fuente "none" y tramos None."""
        predictor = _predictor(hass, params={})
        predictor._predecir_fv_perfil = MagicMock(return_value=[None] * 96)

        fv, fuente = await predictor.predecir_fv(HOY)

        assert fuente == "none"
        assert all(tramo is None for tramo in fv)

    @pytest.mark.asyncio
    async def test_predecir_expone_fuente_fv(
        self, hass, detailed_forecast_hoy
    ):
        predictor = self._con_solcast(
            hass, detailed_forecast_hoy, detailed_manana=None
        )
        prediccion = await predictor.predecir(HOY)

        assert prediccion.fuente_fv == "solcast"
        assert len(prediccion.consumo) == 96
        assert len(prediccion.fv) == 96
