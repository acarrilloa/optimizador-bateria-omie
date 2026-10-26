"""Tests para el servicio probe_registers (U4)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.optimizador_bateria_omie.const import DOMAIN
from custom_components.optimizador_bateria_omie.mapa_registros import (
    MapaRegistros,
)
from custom_components.optimizador_bateria_omie.services import ejecutar_probe
from custom_components.optimizador_bateria_omie.solarman_client import (
    ClienteSolarman,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    GestorValidacion,
)
from custom_components.optimizador_bateria_omie.tests.fake_inversor import (
    InversorFake,
    registros_telemetria,
)


class StoreFake:
    """Store de hass.storage simulado para tests."""

    def __init__(self, datos: dict | None = None) -> None:
        self.datos = dict(datos) if datos else {}
        self.guardados: list[dict] = []

    async def async_load(self) -> dict | None:
        return dict(self.datos) if self.datos else None

    async def async_save(self, datos: dict) -> None:
        self.guardados.append(dict(datos))
        self.datos = dict(datos)


@pytest.fixture
def hass():
    """Hass mock: el executor ejecuta la función directamente."""
    h = MagicMock()
    h.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args: fn(*args)
    )
    return h


@pytest.fixture
def escenario(hass):
    """Escenario completo: mapa, gestor, coordinador y cliente fake."""
    mapa = MapaRegistros(hass)
    mapa.cargar()

    gestor = GestorValidacion(hass, store=StoreFake())
    fake = InversorFake()
    cliente = ClienteSolarman(
        hass, ip="192.168.1.76", puerto=8899, serie=3178171873,
        mapa=mapa, fabrica_conexion=lambda: fake,
    )
    coordinador = MagicMock()
    coordinador.cliente = cliente
    coordinador.async_request_refresh = AsyncMock()

    entry = MagicMock()
    entry.entry_id = "entrada_test"
    entry.data = {}

    hass.data = {DOMAIN: {entry.entry_id: {
        "mapa": mapa,
        "gestor": gestor,
        "coordinador_telemetria": coordinador,
    }}}

    return SimpleNamespace(
        hass=hass, entry=entry, gestor=gestor, fake=fake,
        coordinador=coordinador, mapa=mapa,
    )


def _registros(escenario, familia: str, soc: float, potencia_bat: float) -> dict:
    """Registros de una familia con las magnitudes dadas."""
    return registros_telemetria(
        escenario.mapa, familia, soc=soc, potencia_bat=potencia_bat
    )


@pytest.mark.asyncio
async def test_valida_familia_A(escenario):
    """SOC exacto en A y valor absurdo en B → solo A valida."""
    escenario.fake.registros = (
        _registros(escenario, "A", soc=73.0, potencia_bat=-1200.0)
        | {4212: 0, 4214: 0}
    )

    respuesta = await ejecutar_probe(
        escenario.hass, escenario.entry, {"expected": 73}
    )

    assert respuesta["reset"] is False
    assert respuesta["familia_activa"] == "A"
    assert respuesta["familias"]["A"]["resultado"] == "VALIDADA"
    assert respuesta["familias"]["A"]["leido"] == pytest.approx(73.0)
    assert respuesta["familias"]["A"]["plausible"] is True
    assert respuesta["familias"]["B"]["resultado"] == "FUERA_TOLERANCIA"
    # Persistido en el Store
    assert escenario.gestor.familia == "A"
    assert escenario.gestor.estado["probe_addr"] == 588
    assert escenario.gestor.estado["probe_funcion"] == "soc"
    # La telemetría se recargó
    escenario.coordinador.async_request_refresh.assert_awaited()


@pytest.mark.asyncio
async def test_tolerancia_mas_2_pct(escenario):
    """Dentro de ±2 % valida; fuera, no."""
    # 73 esperado, 74.4 leído → desviación ≈ 1.92 % → VALIDADA
    escenario.fake.registros = _registros(
        escenario, "A", soc=74.4, potencia_bat=-100.0
    )
    respuesta = await ejecutar_probe(
        escenario.hass, escenario.entry, {"expected": 73, "familia": "A"}
    )
    assert respuesta["familias"]["A"]["resultado"] == "VALIDADA"

    # 73 esperado, 74.5 leído → desviación ≈ 2.05 % → FUERA_TOLERANCIA
    escenario.gestor.estado = {}
    escenario.fake.registros = _registros(
        escenario, "A", soc=74.5, potencia_bat=-100.0
    )
    respuesta = await ejecutar_probe(
        escenario.hass, escenario.entry, {"expected": 73, "familia": "A"}
    )
    assert respuesta["familias"]["A"]["resultado"] == "FUERA_TOLERANCIA"
    assert respuesta["familia_activa"] is None
    assert escenario.gestor.familia is None


@pytest.mark.asyncio
async def test_plausibilidad_fallida(escenario):
    """SOC plausible pero potencia_bat fuera de umbrales → IMPLAUSIBLE."""
    escenario.fake.registros = _registros(
        escenario, "A", soc=73.0, potencia_bat=8000.0
    )

    respuesta = await ejecutar_probe(
        escenario.hass, escenario.entry, {"expected": 73, "familia": "A"}
    )

    assert respuesta["familias"]["A"]["resultado"] == "IMPLAUSIBLE"
    assert respuesta["familias"]["A"]["plausible"] is False
    assert respuesta["familia_activa"] is None
    assert escenario.gestor.familia is None


@pytest.mark.asyncio
async def test_reset(escenario):
    """reset=true borra la validación, no sondea y devuelve {"reset": true}."""
    escenario.gestor.estado = {"familia": "A", "probe_addr": 588}

    respuesta = await ejecutar_probe(
        escenario.hass, escenario.entry, {"expected": 73, "reset": True}
    )

    assert respuesta == {"reset": True}
    assert escenario.gestor.familia is None
    assert escenario.gestor._store.datos == {}
    # No se tocó Modbus
    assert escenario.fake.llamadas_lectura == 0
    escenario.coordinador.async_request_refresh.assert_awaited()


@pytest.mark.asyncio
async def test_familia_explicita(escenario):
    """Con familia=A solo se sondea A (aunque B también coincidiera)."""
    escenario.fake.registros = (
        _registros(escenario, "A", soc=73.0, potencia_bat=-100.0)
        | _registros(escenario, "B", soc=73.0, potencia_bat=-100.0)
    )

    respuesta = await ejecutar_probe(
        escenario.hass, escenario.entry, {"expected": 73, "familia": "A"}
    )

    assert list(respuesta["familias"]) == ["A"]
    assert respuesta["familia_activa"] == "A"
    assert escenario.gestor.familia == "A"


@pytest.mark.asyncio
async def test_dos_familias_coincidentes_sin_validar(escenario, caplog):
    """Ambas candidatas → ninguna se valida (WARNING, sin persistencia)."""
    escenario.fake.registros = (
        _registros(escenario, "A", soc=73.0, potencia_bat=-100.0)
        | _registros(escenario, "B", soc=73.0, potencia_bat=-100.0)
    )

    with caplog.at_level("WARNING"):
        respuesta = await ejecutar_probe(
            escenario.hass, escenario.entry, {"expected": 73}
        )

    assert respuesta["familia_activa"] is None
    assert escenario.gestor.familia is None
    assert "candidatas" in caplog.text


@pytest.mark.asyncio
async def test_ninguna_coincide(escenario):
    """Ninguna familia dentro de tolerancia → sin validación."""
    escenario.fake.registros = {588: 7300, 590: 0, 4212: 0, 4214: 0}

    respuesta = await ejecutar_probe(
        escenario.hass, escenario.entry, {"expected": 73}
    )

    assert respuesta["familia_activa"] is None
    assert escenario.gestor.familia is None


@pytest.mark.asyncio
async def test_error_comunicacion(escenario):
    """Sin respuesta Modbus → ERROR_COMUNICACION y sin validación."""
    escenario.fake.fallos_restantes = 99

    with patch("asyncio.sleep", new_callable=AsyncMock):
        respuesta = await ejecutar_probe(
            escenario.hass, escenario.entry, {"expected": 73}
        )

    assert respuesta["familias"]["A"]["resultado"] == "ERROR_COMUNICACION"
    assert respuesta["familias"]["A"]["leido"] is None
    assert respuesta["familia_activa"] is None
    assert escenario.gestor.familia is None


@pytest.mark.asyncio
async def test_registro_personalizado(escenario):
    """El campo register sustituye la dirección a sondear."""
    # Registro 999 con valor que tras scale 0.1 (probe funcion soc) da 73.0
    escenario.fake.registros = {999: 730, 590: 0}

    respuesta = await ejecutar_probe(
        escenario.hass, escenario.entry,
        {"expected": 73, "register": 999, "familia": "A"},
    )

    assert respuesta["familias"]["A"]["resultado"] == "VALIDADA"
    assert escenario.fake.tamannos_bloque[0] == 1
