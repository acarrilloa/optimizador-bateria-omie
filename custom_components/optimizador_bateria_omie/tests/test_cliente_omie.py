"""Tests para ClienteOmie."""

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.optimizador_bateria_omie.cliente_omie import (
    ErrorFuenteOmie,
)
from custom_components.optimizador_bateria_omie.tests.conftest import hoy_local


def _estado_con_precios(precios):
    """Crea un estado mock de HA con el atributo precios."""
    state = MagicMock()
    state.state = "on"
    state.attributes = {"precios": precios}
    return state


@pytest.mark.asyncio
async def test_precios_desde_integracion_omie(
    cliente_omie, fixture_precios_omie_96
):
    """La fuente prioritaria (sensor omie_precios_de_es_hoy) se lee y
    normaliza a 96 ranuras en UTC."""
    cliente_omie.hass.states.get = MagicMock(
        return_value=_estado_con_precios(fixture_precios_omie_96)
    )

    ranuras = await cliente_omie.obtener_precios_del_dia(hoy_local())

    assert len(ranuras) == 96
    assert all(r.fuente == "omie" for r in ranuras)
    assert ranuras[0].precio_eur_kwh == pytest.approx(0.10)
    # Medianoche local convertida a UTC y tramos consecutivos de 15 min
    medianoche_utc = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(UTC)
    assert ranuras[0].fecha_hora_utc == medianoche_utc
    for actual, siguiente in pairwise(ranuras):
        assert siguiente.fecha_hora_utc - actual.fecha_hora_utc == (
            timedelta(minutes=15)
        )


@pytest.mark.asyncio
async def test_fallback_omie_web(cliente_omie, fixture_html_omie_web):
    """Sin la integración OMIE, se scrappea la web oficial de OMIE."""
    cliente_omie.hass.config.components = []

    # AsyncMock: soporta el protocolo "async with" y await response.text()
    response = AsyncMock()
    response.status = 200
    response.text = AsyncMock(return_value=fixture_html_omie_web)
    response.__aenter__.return_value = response
    session = MagicMock()
    session.get = MagicMock(return_value=response)

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=session,
    ):
        ranuras = await cliente_omie.obtener_precios_del_dia(hoy_local())

    assert len(ranuras) == 96
    assert all(r.fuente == "omie_web" for r in ranuras)
    # 264.68 €/MWh → 0.26468 €/kWh
    assert ranuras[0].precio_eur_kwh == pytest.approx(0.26468)
    # La primera ranura es la medianoche local del día (en UTC)
    medianoche_local_utc = (
        datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(UTC)
    )
    assert ranuras[0].fecha_hora_utc == medianoche_local_utc


def _html_omie_web(*series: dict) -> str:
    """HTML SSR de omie.es: JSON con las comillas escapadas y la eñe
    en unicode, como la página real incrusta Highcharts (construido
    programáticamente para no duplicar escapes frágiles en el test)."""
    cuerpo = json.dumps({"path": {}, "series": [dict(s) for s in series]})
    return cuerpo.replace('"', '\\"')


def _serie(precios: list) -> dict:
    return {
        "name": "Precio marginal español",
        "type": "column",
        "data": precios,
    }


@pytest.fixture
def fixture_html_omie_web_negativos():
    """Incidente ×1000 (0.13.1): serie real con precios NEGATIVOS y un
    array señuelo posterior de otra gráfica (volumen, escala dispar)."""
    precios = [
        (-5.83 - i * 0.35) if i % 6 == 0 else (40.0 + i * 0.12)
        for i in range(96)
    ]
    senuelo = {
        "name": "Volumen comprado",
        "type": "column",
        "data": [31164.7 - 17.3 * i for i in range(24)],
    }
    return _html_omie_web(_serie(precios), senuelo)


@pytest.fixture
def fixture_html_omie_web_escala():
    """Serie capturable por el regex pero fuera de escala (media
    ~31100 €/MWh): el sanity check debe descartar la fuente."""
    return _html_omie_web(_serie([31164.7 - 17.3 * i for i in range(96)]))


def _sesion_con_html(html: str) -> MagicMock:
    """Session mock de aiohttp que devuelve el HTML indicado."""
    response = AsyncMock()
    response.status = 200
    response.text = AsyncMock(return_value=html)
    response.__aenter__.return_value = response
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


@pytest.mark.asyncio
async def test_fallback_omie_web_precios_negativos(
    cliente_omie, fixture_html_omie_web_negativos
):
    """0.13.1 (incidente ×1000): con negativos, la serie real se
    captura y el array señuelo de la gráfica posterior ya no lo es."""
    cliente_omie.hass.config.components = []
    session = _sesion_con_html(fixture_html_omie_web_negativos)

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=session,
    ):
        ranuras = await cliente_omie.obtener_precios_del_dia(hoy_local())

    assert len(ranuras) == 96
    assert all(r.fuente == "omie_web" for r in ranuras)
    # Tramo 0 negativo: −5.83 €/MWh = −0.00583 €/kWh (el viejo regex
    # habría saltado al señuelo: 31.1647 €/kWh tras el /1000)
    assert ranuras[0].precio_eur_kwh == pytest.approx(-5.83 / 1000.0)
    assert all(-1.0 <= r.precio_eur_kwh <= 1.0 for r in ranuras)


@pytest.mark.asyncio
async def test_fallback_omie_web_sanity_check_descarta_escala(
    cliente_omie, fixture_html_omie_web_escala
):
    """0.13.1 (incidente ×1000): una serie fuera de escala plausible
    para el marginal se descarta (ErrorFuenteOmie), sin tocar caché."""
    cliente_omie.hass.config.components = []
    session = _sesion_con_html(fixture_html_omie_web_escala)

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=session,
    ), pytest.raises(ErrorFuenteOmie):
        await cliente_omie.obtener_precios_del_dia(hoy_local())


@pytest.mark.asyncio
async def test_cache_evita_reconsultas(cliente_omie, fixture_precios_omie_96):
    """La segunda petición del mismo día se sirve desde la caché."""
    llamadas = 0

    def mock_states_get(entity_id):
        nonlocal llamadas
        llamadas += 1
        return _estado_con_precios(fixture_precios_omie_96)

    cliente_omie.hass.states.get = MagicMock(side_effect=mock_states_get)

    ranuras1 = await cliente_omie.obtener_precios_del_dia(hoy_local())
    assert llamadas == 1

    ranuras2 = await cliente_omie.obtener_precios_del_dia(hoy_local())
    assert llamadas == 1  # No se volvió a consultar el sensor

    assert ranuras1 == ranuras2


@pytest.mark.asyncio
async def test_cache_incompleta_se_descarta(
    cliente_omie, fixture_precios_omie_96
):
    """Una caché sin 96 entradas se ignora y se vuelve a la fuente."""
    cliente_omie.hass.states.get = MagicMock(
        return_value=_estado_con_precios(fixture_precios_omie_96)
    )

    # Primera llamada: escribe la caché con 96 entradas
    await cliente_omie.obtener_precios_del_dia(hoy_local())
    assert cliente_omie.hass.states.get.call_count == 1

    # Truncar la caché a 95 entradas (día de cambio horario, corrupta...)
    cache_file = (
        cliente_omie._dir_cache / f"{hoy_local().strftime('%Y-%m-%d')}.json"
    )
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    cache_file.write_text(
        json.dumps(cached[:95], ensure_ascii=False), encoding="utf-8"
    )

    # La caché incompleta se descarta y se consulta de nuevo la fuente
    ranuras = await cliente_omie.obtener_precios_del_dia(hoy_local())
    assert cliente_omie.hass.states.get.call_count == 2
    assert len(ranuras) == 96


@pytest.mark.asyncio
async def test_todas_las_fuentes_fallan(cliente_omie):
    """Sin OMIE y con la web de OMIE caída se lanza ErrorFuenteOmie."""
    cliente_omie.hass.config.components = []

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        side_effect=OSError("sin red"),
    ), pytest.raises(ErrorFuenteOmie):
        await cliente_omie.obtener_precios_del_dia(hoy_local())


@pytest.mark.asyncio
async def test_formato_invalido_descartado(cliente_omie):
    """El sensor OMIE con un formato no reconocido devuelve lista vacía
    (la cascada continuará con el fallback)."""
    cliente_omie.hass.states.get = MagicMock(
        return_value=_estado_con_precios([{"hour": 0, "price": 1.0}])
    )

    datos = await cliente_omie._obtener_desde_integracion_omie(hoy_local())

    assert datos == []


@pytest.mark.asyncio
async def test_sensor_no_encontrado(cliente_omie):
    """Si el sensor OMIE no existe, devuelve lista vacía."""
    cliente_omie.hass.states.get = MagicMock(return_value=None)

    datos = await cliente_omie._obtener_desde_integracion_omie(hoy_local())

    assert datos == []


@pytest.mark.asyncio
async def test_fecha_fuera_de_rango(cliente_omie):
    """Fechas sin sensor asociado (p. ej. hace una semana) → lista vacía."""
    datos = await cliente_omie._obtener_desde_integracion_omie(
        hoy_local() - timedelta(days=7)
    )

    assert datos == []


def test_normalizar_dia_92_tramos(cliente_omie, fixture_precios_omie_92):
    """Un día con cambio horario (92 tramos) se normaliza sin relleno."""
    ranuras = cliente_omie._normalizar_a_15min(
        fixture_precios_omie_92, hoy_local()
    )

    assert len(ranuras) == 92
    for actual, siguiente in pairwise(ranuras):
        assert siguiente.fecha_hora_utc - actual.fecha_hora_utc == (
            timedelta(minutes=15)
        )
