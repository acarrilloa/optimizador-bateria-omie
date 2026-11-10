"""Tests de las features de clima (F5-U5)."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from custom_components.optimizador_bateria_omie import clima
from custom_components.optimizador_bateria_omie.clima import (
    temperatura_media_prevista,
    temperaturas_diarias,
)

HOY = date(2026, 11, 10)


class _Estado:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}
        self.last_updated = datetime.now(tz=UTC)


class TestForecast:
    def _hass(self, prevision):
        estado = _Estado(
            "sunny", {"forecast": prevision}
        )
        hass = SimpleNamespace(
            states=SimpleNamespace(get=lambda eid: estado)
        )
        return hass

    def test_media_de_temperaturas_del_dia(self):
        prevision = [
            {
                "datetime": f"2026-11-10T{hora:02d}:00:00+01:00",
                "temperature": t,
            }
            for hora, t in [(2, 8.0), (8, 12.0), (14, 17.0), (20, 10.0)]
        ]
        assert temperatura_media_prevista(
            self._hass(prevision), "weather.aemet", HOY
        ) == pytest.approx(11.75)

    def test_otro_dia_none(self):
        prevision = [
            {"datetime": "2026-11-11T14:00:00+01:00", "temperature": 15.0}
        ]
        assert temperatura_media_prevista(
            self._hass(prevision), "weather.aemet", HOY
        ) is None

    def test_sin_prevision_none(self):
        hass = self._hass([])
        assert temperatura_media_prevista(
            hass, "weather.aemet", HOY
        ) is None

    def test_entidad_no_disponible_none(self):
        estado = _Estado("unavailable", {"forecast": []})
        hass = SimpleNamespace(
            states=SimpleNamespace(get=lambda eid: estado)
        )
        assert temperatura_media_prevista(
            hass, "weather.aemet", HOY
        ) is None

    def test_sin_entity_id_none(self):
        assert temperatura_media_prevista(None, "", HOY) is None


class TestHistoricoTemperaturas:
    def test_medias_diarias_del_recorder(self, hass, monkeypatch):
        estados = [
            _Estado("12.0"),
            _Estado("14.0"),
            _Estado("no_numérico"),
        ]
        monkeypatch.setattr(
            clima, "_consultar_recorder",
            lambda h, eid, ini, fin: estados,
        )
        medias = temperaturas_diarias(hass, "sensor.temperatura", 28)
        # Tres estados el mismo día (uno no numérico descartado)
        (valor,) = medias.values()
        assert valor == pytest.approx(13.0)

    def test_sin_entity_id_vacio(self, hass):
        assert temperaturas_diarias(hass, "", 28) == {}

    def test_error_recorder_vacio(self, hass, monkeypatch):
        def _explota(h, eid, ini, fin):
            raise RuntimeError("recorder no listo")

        monkeypatch.setattr(
            clima, "_consultar_recorder", _explota
        )
        assert temperaturas_diarias(hass, "sensor.t", 28) == {}


class TestConfigFlowCampos:
    def test_campos_clima_en_el_schema(self):
        from custom_components.optimizador_bateria_omie.config_flow import (
            CONFIG_SCHEMA,
        )
        # Los vol.Optionals aportan su default al validar {}
        resultado = CONFIG_SCHEMA({})
        assert resultado.get("entity_id_clima") == ""
        assert resultado.get("entity_id_temp_historico") == ""
