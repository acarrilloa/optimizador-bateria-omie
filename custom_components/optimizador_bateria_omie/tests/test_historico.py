"""Tests para FuenteHistoricoRecorder y la agregación a tramos (F3, U1)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.optimizador_bateria_omie.historico import (
    ErrorHistorico,
    FuenteHistoricoRecorder,
    agregar_por_tramos,
)

MADRID = ZoneInfo("Europe/Madrid")


def _estado(valor, ts_utc, unidad="W"):
    """Estado HA simulado con last_updated en UTC."""
    estado = MagicMock()
    estado.state = valor
    estado.last_updated = ts_utc
    estado.attributes = (
        {"unit_of_measurement": unidad} if unidad is not None else {}
    )
    return estado


def _ts_local(hora: int, minuto: int, dia: int = 20) -> datetime:
    """Instante UTC correspondiente a una hora local del 2026-10-XX."""
    local = datetime(2026, 10, dia, hora, minuto, tzinfo=MADRID)
    return local.astimezone(UTC)


@pytest.fixture
def hass():
    """Hass mock: el executor ejecuta la función directamente."""
    h = MagicMock()
    h.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args: fn(*args)
    )
    return h


class TestAgregarPorTramos:
    """Agregación de estados a medias por tramo de 15 min."""

    def test_media_por_tramo(self):
        estados = [
            _estado(100, _ts_local(0, 7)),
            _estado(200, _ts_local(0, 11)),
            _estado(400, _ts_local(0, 20)),
        ]

        muestras = agregar_por_tramos(estados)

        assert len(muestras) == 2
        assert muestras[0].valor_w == pytest.approx(150.0)
        assert muestras[1].valor_w == pytest.approx(400.0)

    def test_descarta_unavailable_y_unknown(self):
        estados = [
            _estado("unavailable", _ts_local(0, 5)),
            _estado("unknown", _ts_local(0, 10)),
            _estado(300, _ts_local(0, 12)),
        ]

        muestras = agregar_por_tramos(estados)

        assert len(muestras) == 1
        assert muestras[0].valor_w == pytest.approx(300.0)

    def test_descarta_no_numerico(self):
        estados = [
            _estado("abc", _ts_local(0, 5)),
            _estado(300, _ts_local(0, 10)),
        ]

        muestras = agregar_por_tramos(estados)

        assert len(muestras) == 1
        assert muestras[0].valor_w == pytest.approx(300.0)

    def test_descarta_unidad_no_w(self):
        """Unidad declarada ≠ W se descarta; sin unidad se mantiene."""
        estados = [
            _estado(5, _ts_local(0, 5), unidad="kW"),
            _estado(300, _ts_local(0, 10), unidad=None),
        ]

        muestras = agregar_por_tramos(estados)

        assert len(muestras) == 1
        assert muestras[0].valor_w == pytest.approx(300.0)

    def test_ordenado_por_tiempo(self):
        estados = [
            _estado(400, _ts_local(3, 20)),
            _estado(100, _ts_local(1, 5)),
        ]

        muestras = agregar_por_tramos(estados)

        assert [m.valor_w for m in muestras] == [100.0, 400.0]

    def test_dia_cambio_horario_92_tramos(self):
        """El 2026-03-29 (CET→CEST) tiene 23 h locales → 92 tramos."""
        # 00:00 CET = 2026-03-28T23:00Z; 23 h después termina el día local
        inicio = datetime(2026, 3, 28, 23, 0, tzinfo=UTC)
        estados = [
            _estado(100, inicio + timedelta(minutes=15 * i))
            for i in range(92)
        ]

        muestras = agregar_por_tramos(estados, tz=MADRID)

        assert len(muestras) == 92
        # Todos los tramos caen en el día local 2026-03-29
        fechas = {m.ts_utc.astimezone(MADRID).date() for m in muestras}
        assert fechas == {datetime(2026, 3, 29).date()}

    def test_dia_cambio_horario_octubre_tramos_reloj_pared(self):
        """El 2026-10-25 (CEST→CET) repite la hora local 02:00-03:00.

        El tramo es de reloj pared (0–95): las muestras de las dos
        pasadas por las 02:00-02:45 se fusionan en los mismos tramos,
        por lo que el día de 25 h produce 96 tramos distintos.
        """
        inicio = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)  # 00:00 CEST
        estados = [
            _estado(100, inicio + timedelta(minutes=15 * i))
            for i in range(100)
        ]

        muestras = agregar_por_tramos(estados, tz=MADRID)

        assert len(muestras) == 96


class TestFuenteHistoricoRecorder:
    """Consulta al recorder con ventana configurable."""

    def _fuente(self, hass, entity_id="sensor.consumo"):
        hass.states.get = MagicMock(return_value=MagicMock())
        return FuenteHistoricoRecorder(hass, entity_id)

    @pytest.mark.asyncio
    async def test_obtener_series_ventana_y_agregacion(self, hass):
        fuente = self._fuente(hass)
        estados = [
            _estado(100, _ts_local(0, 5)),
            _estado(200, _ts_local(0, 10)),
        ]

        with patch(
            "custom_components.optimizador_bateria_omie.historico"
            "._consultar_recorder",
            return_value=estados,
        ) as consultar:
            muestras = await fuente.obtener_series(28)

        assert len(muestras) == 1
        assert muestras[0].valor_w == pytest.approx(150.0)
        # La ventana pedida es de 28 días y termina ~ahora
        args = consultar.call_args[0]
        inicio, fin = args[2], args[3]
        assert fin - inicio == timedelta(days=28)
        assert fin > datetime.now(tz=UTC) - timedelta(minutes=1)

    @pytest.mark.asyncio
    async def test_sin_entity_id(self, hass):
        fuente = FuenteHistoricoRecorder(hass, "")

        with pytest.raises(ErrorHistorico, match="entity_id"):
            await fuente.obtener_series(28)

    @pytest.mark.asyncio
    async def test_entidad_inexistente(self, hass):
        hass.states.get = MagicMock(return_value=None)
        fuente = FuenteHistoricoRecorder(hass, "sensor.fantasma")

        with pytest.raises(ErrorHistorico, match="no existe"):
            await fuente.obtener_series(28)

    @pytest.mark.asyncio
    async def test_recorder_sin_datos(self, hass):
        fuente = self._fuente(hass)

        with patch(
            "custom_components.optimizador_bateria_omie.historico"
            "._consultar_recorder",
            return_value=[],
        ), pytest.raises(ErrorHistorico, match="Sin datos válidos"):
            await fuente.obtener_series(28)

    @pytest.mark.asyncio
    async def test_solo_unavailable_en_ventana(self, hass):
        fuente = self._fuente(hass)

        with patch(
            "custom_components.optimizador_bateria_omie.historico"
            "._consultar_recorder",
            return_value=[_estado("unavailable", _ts_local(0, 5))],
        ), pytest.raises(ErrorHistorico, match="Sin datos válidos"):
            await fuente.obtener_series(7)
