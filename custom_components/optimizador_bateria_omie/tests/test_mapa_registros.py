"""Tests para MapaRegistros (U1)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.optimizador_bateria_omie.mapa_registros import (
    ErrorMapaRegistros,
    MapaRegistros,
)

YAML_VALIDO = """\
inverter: "deye-test"
block_size: 16
familias:
  A:
    probe:  { funcion: soc, addr: 588 }
    plausibility:
      soc:          [0, 100]
      potencia_bat: [-7000, 7000]
    telemetria:
      soc:            { addr: 588, scale: 0.1, unit: "%" }
      potencia_bat:   { addr: 590, scale: 1, unit: "W", signed: true, invertir_signo: false }
      consumo_casa:   { addr: 670, scale: 0.1, unit: "W" }
  B:
    probe:  { funcion: soc, addr: 4212 }
    telemetria:
      soc:            { addr: 4212, scale: 1, unit: "%" }
      consumo_casa:   { addr: 4226, scale: 0.1, unit: "W" }
"""

YAML_SIN_FAMILIAS = """\
inverter: "deye-test"
block_size: 16
"""

YAML_BLOCK_SIZE_GRANDE = """\
inverter: "deye-test"
block_size: 32
familias:
  A:
    probe:  { funcion: soc, addr: 588 }
    telemetria:
      soc: { addr: 588, scale: 0.1, unit: "%" }
"""


@pytest.fixture
def hass(tmp_path):
    """Hass mock con config_dir en un directorio temporal."""
    h = MagicMock()
    h.config.config_dir = str(tmp_path)
    h.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args: fn(*args)
    )
    return h


@pytest.fixture
def mapa(hass):
    """Mapa cargado desde el registers.yaml empaquetado."""
    m = MapaRegistros(hass)
    m.cargar()
    return m


def _escribir_override(tmp_path, contenido: str) -> Path:
    """Escribe un override de usuario y devuelve su ruta."""
    ruta = tmp_path / "optimizador_bateria_omie" / "registers.yaml"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


class TestCarga:
    """Tests de carga y validación del YAML."""

    def test_yaml_empaquetado(self, mapa):
        assert mapa.familias() == ["A", "B"]
        assert mapa.block_size == 16
        assert mapa.inverter == "deye-sun-6k-sg05lp1-am2"

    def test_yaml_invalido_sin_familias(self, hass, tmp_path):
        _escribir_override(tmp_path, YAML_SIN_FAMILIAS)
        with pytest.raises(ErrorMapaRegistros, match="familias"):
            MapaRegistros(hass).cargar()

    def test_todo_como_warning(self, hass, caplog):
        """Los TODO del YAML empaquetado avisan, no rompen la carga."""
        with caplog.at_level("WARNING"):
            mapa = MapaRegistros(hass)
            mapa.cargar()

        assert mapa.familias() == ["A", "B"]
        assert "TODO" in caplog.text

    def test_override_usuario(self, hass, tmp_path):
        _escribir_override(
            tmp_path,
            YAML_VALIDO.replace("addr: 588 }", "addr: 999 }"),
        )
        mapa = MapaRegistros(hass)
        mapa.cargar()

        assert mapa.probe("A") == {"funcion": "soc", "addr": 999}

    def test_override_inexistente_cae_al_empaquetado(self, hass, tmp_path):
        mapa = MapaRegistros(hass, ruta_override=tmp_path / "no_existe.yaml")
        mapa.cargar()

        assert mapa.familias() == ["A", "B"]

    def test_block_size_mayor_de_16(self, hass, tmp_path):
        _escribir_override(tmp_path, YAML_BLOCK_SIZE_GRANDE)
        with pytest.raises(ErrorMapaRegistros, match="block_size"):
            MapaRegistros(hass).cargar()

    @pytest.mark.asyncio
    async def test_async_cargar(self, hass):
        mapa = MapaRegistros(hass)
        await mapa.async_cargar()

        assert mapa.familias() == ["A", "B"]
        hass.async_add_executor_job.assert_called_once()


class TestAccesores:
    """Tests de probe, plausibility y parametros."""

    def test_probe(self, mapa):
        assert mapa.probe("A") == {"funcion": "soc", "addr": 588}
        assert mapa.probe("B") == {"funcion": "soc", "addr": 4212}

    def test_plausibility(self, mapa):
        plausibilidad = mapa.plausibility("A")
        assert plausibilidad["soc"] == [0, 100]
        assert plausibilidad["potencia_bat"] == [-7000, 7000]

    def test_parametros_defaults(self, mapa):
        params = mapa.parametros("A", "consumo_casa")
        assert params["addr"] == 670
        assert params["scale"] == 0.1
        assert params["unit"] == "W"
        assert params["signed"] is False
        assert params["invertir_signo"] is False

    def test_parametros_signed(self, mapa):
        params = mapa.parametros("A", "potencia_bat")
        assert params["signed"] is True
        assert params["invertir_signo"] is False

    def test_direccion(self, mapa):
        assert mapa.direccion("A", "soc") == 588
        assert mapa.direccion("B", "soc") == 4212

    def test_familia_inexistente(self, mapa):
        with pytest.raises(ErrorMapaRegistros, match="Familia 'C'"):
            mapa.probe("C")

    def test_funcion_inexistente(self, mapa):
        with pytest.raises(ErrorMapaRegistros, match="no definida"):
            mapa.parametros("A", "funcion_imaginaria")


class TestConversion:
    """Tests de complemento a dos, scale e invertir_signo."""

    def test_complemento_a_dos(self):
        assert MapaRegistros.complemento_a_dos(0xFF00) == -256
        assert MapaRegistros.complemento_a_dos(0x8000) == -32768
        assert MapaRegistros.complemento_a_dos(0x0064) == 100
        assert MapaRegistros.complemento_a_dos(0x7FFF) == 32767

    def test_convertir_sin_signo(self):
        params = {"scale": 0.1, "signed": False, "invertir_signo": False}
        assert MapaRegistros.convertir(730, params) == pytest.approx(73.0)

    def test_convertir_signed(self):
        """0xFF00 con signed → −256 tras complemento a dos."""
        params = {"scale": 1, "signed": True, "invertir_signo": False}
        assert MapaRegistros.convertir(0xFF00, params) == pytest.approx(-256.0)

    def test_convertir_signed_con_scale(self):
        params = {"scale": 0.1, "signed": True, "invertir_signo": False}
        assert MapaRegistros.convertir(0xFF00, params) == pytest.approx(-25.6)

    def test_convertir_invertir_signo(self):
        """invertir_signo multiplica por −1 tras el complemento a dos."""
        params = {"scale": 1, "signed": True, "invertir_signo": True}
        assert MapaRegistros.convertir(0xFF00, params) == pytest.approx(256.0)

    def test_convertir_invertir_signo_sin_signed(self):
        params = {"scale": 1, "signed": False, "invertir_signo": True}
        assert MapaRegistros.convertir(100, params) == pytest.approx(-100.0)
