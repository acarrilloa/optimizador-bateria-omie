"""Tests de la deuda #5 (F4-U2): tarifa 2.0TD desde entry.data."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from custom_components.optimizador_bateria_omie.const import (
    CONF_APLICAR_IMPUESTOS_PRECIO,
    CONF_CARGO_SISTEMA_PUNTA,
    CONF_IMPUESTO_ELECTRICO_PCT,
    CONF_IVA_PCT,
    CONF_PEAYE_ACCESO_PUNTA,
    CONF_PEAYE_ACCESO_VALLE,
    CONF_POTENCIA_CONTRATADA,
)
from custom_components.optimizador_bateria_omie.tarifa import (
    ConfiguracionTarifa,
    ModeloTarifa,
)


def _entry(data: dict | None = None) -> MagicMock:
    entry = MagicMock()
    entry.data = dict(data or {})
    return entry


# Instante en punta laborable (miércoles 12:00 CET)
FECHA_PUNTA = datetime(2026, 10, 21, 11, 0, tzinfo=UTC)
# Instante en valle laborable (miércoles 03:00 CET → 02:00 UTC)
FECHA_VALLE = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)


class TestDesdeEntry:
    def test_entry_de_produccion_defaults_actuales(self):
        """Sin claves nuevas → exactamente los valores hardcodeados de F1."""
        cfg = ConfiguracionTarifa.desde_entry(_entry())

        assert cfg.potencia_contratada_kw == pytest.approx(5.75)
        assert cfg.impuesto_electrico_pct == pytest.approx(0.051127)
        assert cfg.iva_pct == pytest.approx(0.21)
        assert cfg.aplicar_impuestos_precio is True
        assert cfg.peajes_acceso["punta"] == pytest.approx(0.033261)
        assert cfg.peajes_acceso["llano"] == pytest.approx(0.016409)
        assert cfg.peajes_acceso["valle"] == pytest.approx(0.000077)
        assert cfg.cargos_sistema["punta"] == pytest.approx(0.064292)
        assert cfg.cargos_sistema["llano"] == pytest.approx(0.012858)
        assert cfg.cargos_sistema["valle"] == pytest.approx(0.003215)

    def test_claves_presentes_sobreescriben(self):
        cfg = ConfiguracionTarifa.desde_entry(
            _entry(
                {
                    CONF_POTENCIA_CONTRATADA: 5800.0,
                    CONF_IMPUESTO_ELECTRICO_PCT: 0.05,
                    CONF_IVA_PCT: 0.10,
                    CONF_APLICAR_IMPUESTOS_PRECIO: False,
                    CONF_PEAYE_ACCESO_PUNTA: 0.05,
                    CONF_PEAYE_ACCESO_VALLE: 0.001,
                    CONF_CARGO_SISTEMA_PUNTA: 0.07,
                }
            )
        )

        assert cfg.potencia_contratada_kw == pytest.approx(5.8)
        assert cfg.impuesto_electrico_pct == pytest.approx(0.05)
        assert cfg.iva_pct == pytest.approx(0.10)
        assert cfg.aplicar_impuestos_precio is False
        assert cfg.peajes_acceso["punta"] == pytest.approx(0.05)
        assert cfg.peajes_acceso["valle"] == pytest.approx(0.001)
        assert cfg.cargos_sistema["punta"] == pytest.approx(0.07)
        # Los periodos no sobreescritos conservan el default
        assert cfg.peajes_acceso["llano"] == pytest.approx(0.016409)

    def test_potencia_contratada_unica_fuente(self):
        """El kW se deriva del W del entry (5750 → 5,75), sin duplicar."""
        cfg = ConfiguracionTarifa.desde_entry(
            _entry({CONF_POTENCIA_CONTRATADA: 5750.0})
        )
        assert cfg.potencia_contratada_kw == pytest.approx(5.75)


class TestModeloTarifaConConfiguracion:
    def test_precio_efectivo_con_defaults_igual_a_f1(self):
        """El precio efectivo con defaults coincide con el de F1."""
        modelo_default = ModeloTarifa()
        modelo_entry = ModeloTarifa(ConfiguracionTarifa.desde_entry(_entry()))

        for fecha in (FECHA_PUNTA, FECHA_VALLE):
            assert modelo_entry.obtener_precio_efectivo(
                0.10, fecha
            ) == pytest.approx(modelo_default.obtener_precio_efectivo(0.10, fecha))

    def test_peajes_custom_cambian_precio_efectivo(self):
        base = ModeloTarifa(ConfiguracionTarifa.desde_entry(_entry()))
        custom = ModeloTarifa(
            ConfiguracionTarifa.desde_entry(
                _entry({CONF_PEAYE_ACCESO_PUNTA: 0.0})
            )
        )

        assert custom.obtener_precio_efectivo(
            0.10, FECHA_PUNTA
        ) < base.obtener_precio_efectivo(0.10, FECHA_PUNTA)

    def test_sin_impuestos(self):
        cfg = ConfiguracionTarifa.desde_entry(
            _entry({CONF_APLICAR_IMPUESTOS_PRECIO: False})
        )
        modelo = ModeloTarifa(cfg)

        # punta: (0.10 + 0.033261 + 0.064292) sin impuestos
        assert modelo.obtener_precio_efectivo(0.10, FECHA_PUNTA) == (
            pytest.approx(0.10 + 0.033261 + 0.064292, abs=1e-6)
        )
