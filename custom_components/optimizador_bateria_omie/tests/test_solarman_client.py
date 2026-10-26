"""Tests para ClienteSolarman (U2)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.optimizador_bateria_omie.mapa_registros import (
    MapaRegistros,
)
from custom_components.optimizador_bateria_omie.solarman_client import (
    ClienteSolarman,
    ErrorComunicacionSolarman,
)
from custom_components.optimizador_bateria_omie.tests.fake_inversor import (
    InversorFake,
    registros_telemetria,
)


@pytest.fixture
def hass():
    """Hass mock: el executor ejecuta la función directamente."""
    h = MagicMock()
    h.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args: fn(*args)
    )
    return h


@pytest.fixture
def mapa(hass):
    """Mapa de registros empaquetado cargado."""
    m = MapaRegistros(hass)
    m.cargar()
    return m


def _cliente(hass, mapa, fake: InversorFake) -> ClienteSolarman:
    """Cliente Solarman con el fake inyectado como conexión."""
    return ClienteSolarman(
        hass, ip="192.168.1.76", puerto=8899, serie=3178171873,
        mapa=mapa, fabrica_conexion=lambda: fake,
    )


@pytest.mark.asyncio
async def test_lectura_telemetria_familia_A(hass, mapa):
    """Lee la telemetría de la familia A con la convención Deye."""
    fake = InversorFake(
        registros_telemetria(
            mapa, "A",
            soc=73.0, potencia_bat=-1200.0, corriente_bat=-24.0,
            consumo_casa=350.0, produccion_fv=800.0,
        )
    )
    cliente = _cliente(hass, mapa, fake)

    telemetria = await cliente.leer_telemetria("A")

    assert telemetria.soc == pytest.approx(73.0)
    # Convención Deye (D16): +descarga / −carga
    assert telemetria.potencia_bateria == pytest.approx(-1200.0)
    assert telemetria.corriente_bateria == pytest.approx(-24.0)
    assert telemetria.consumo_casa == pytest.approx(350.0)
    assert telemetria.produccion_fv == pytest.approx(800.0)
    assert telemetria.ts_actualizacion is not None


@pytest.mark.asyncio
async def test_lectura_telemetria_familia_B(hass, mapa):
    """Lee la telemetría de la familia B; corriente_bat no está en el mapa."""
    fake = InversorFake(
        registros_telemetria(
            mapa, "B",
            soc=73.0, potencia_bat=1200.0,
            consumo_casa=350.0, produccion_fv=800.0,
        )
    )
    cliente = _cliente(hass, mapa, fake)

    telemetria = await cliente.leer_telemetria("B")

    assert telemetria.soc == pytest.approx(73.0)
    assert telemetria.potencia_bateria == pytest.approx(1200.0)
    # Sin dirección en el mapa de la familia B → None (nunca inventado)
    assert telemetria.corriente_bateria is None
    assert telemetria.consumo_casa == pytest.approx(350.0)
    assert telemetria.produccion_fv == pytest.approx(800.0)


@pytest.mark.asyncio
async def test_familia_por_parametro(hass, mapa):
    """La familia llega por parámetro: el mismo fake sirve para ambas."""
    fake = InversorFake(
        registros_telemetria(mapa, "A", soc=42.0)
        | registros_telemetria(mapa, "B", soc=99.0)
    )
    cliente = _cliente(hass, mapa, fake)

    assert (await cliente.leer_telemetria("A")).soc == pytest.approx(42.0)
    assert (await cliente.leer_telemetria("B")).soc == pytest.approx(99.0)


@pytest.mark.asyncio
async def test_troceado_bloques_maximo_16(hass, mapa):
    """Una lectura de 32 registros se trocea en dos peticiones ≤ 16."""
    registros = {100 + i: i * 10 for i in range(32)}
    fake = InversorFake(registros)
    cliente = _cliente(hass, mapa, fake)

    valores = await cliente.leer_registros(100, 32)

    assert len(valores) == 32
    assert valores == [i * 10 for i in range(32)]
    assert fake.tamannos_bloque == [16, 16]


@pytest.mark.asyncio
async def test_semaforo_serializa_accesos(hass, mapa):
    """Accesos concurrentes: todos completan con sus valores correctos."""
    fake = InversorFake({100: 1, 200: 2, 300: 3})
    cliente = _cliente(hass, mapa, fake)

    import asyncio

    resultados = await asyncio.gather(
        cliente.leer_registros(100, 1),
        cliente.leer_registros(200, 1),
        cliente.leer_registros(300, 1),
    )

    assert resultados == [[1], [2], [3]]
    assert fake.llamadas_lectura == 3


@pytest.mark.asyncio
async def test_backoff_recupera(hass, mapa, caplog):
    """Con un fallo inicial, recupera en el segundo intento (M4)."""
    fake = InversorFake({588: 730})
    fake.fallos_restantes = 1
    cliente = _cliente(hass, mapa, fake)

    with patch("asyncio.sleep", new_callable=AsyncMock) as dormir:
        valores = await cliente.leer_registros(588, 1)

    assert valores == [730]
    # 2 intentos, una sola espera entre ellos (0,5 s)
    esperas = [c.args for c in dormir.call_args_list]
    assert esperas == [(0.5,)]


@pytest.mark.asyncio
async def test_backoff_agotado(hass, mapa):
    """Agotados los 3 intentos → ErrorComunicacionSolarman (M4)."""
    fake = InversorFake()
    fake.fallos_restantes = 99
    cliente = _cliente(hass, mapa, fake)

    with (
        patch("asyncio.sleep", new_callable=AsyncMock) as dormir,
        pytest.raises(ErrorComunicacionSolarman),
    ):
        await cliente.leer_registros(588, 1)

    # 3 intentos exactos y esperas 0,5 + 2 + 5 = 7,5 s (≤ 10 s)
    esperas = [c.args[0] for c in dormir.call_args_list]
    assert esperas == [0.5, 2.0, 5.0]
    assert sum(esperas) <= 10


@pytest.mark.asyncio
async def test_reconexion_crea_instancia_nueva(hass, mapa):
    """Tras un fallo se desconecta la conexión vieja y se crea otra."""
    creadas: list[InversorFake] = []

    def fabrica() -> InversorFake:
        if not creadas:
            fake = InversorFake()
            fake.fallos_restantes = 1  # falla solo el primer intento
        else:
            fake = InversorFake({588: 730})
        creadas.append(fake)
        return fake

    cliente = ClienteSolarman(
        hass, ip="192.168.1.76", puerto=8899, serie=3178171873,
        mapa=mapa, fabrica_conexion=fabrica,
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        valores = await cliente.leer_registros(588, 1)

    assert valores == [730]
    assert len(creadas) == 2
    assert creadas[0].desconexiones == 1  # la vieja se desconectó


@pytest.mark.asyncio
async def test_no_se_invoca_connect(hass, mapa):
    """D17: el cliente real nunca invoca connect() en la conexión."""
    fake = InversorFake({588: 730})
    cliente = _cliente(hass, mapa, fake)

    await cliente.leer_registros(588, 1)

    assert fake.conexiones == 0


def test_no_existe_escribir_registros():
    """D9: ClienteSolarman no expone escrituras en F2."""
    assert not hasattr(ClienteSolarman, "escribir_registros")
