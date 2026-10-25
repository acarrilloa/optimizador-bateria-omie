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
async def test_fallback_redata(cliente_omie, fixture_respuesta_redata):
    """Sin la integración OMIE, se usa REData con la sesión de HA."""
    cliente_omie.hass.config.components = []

    # AsyncMock: soporta el protocolo "async with" y await response.json()
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value=fixture_respuesta_redata)
    response.__aenter__.return_value = response
    session = MagicMock()
    session.get = MagicMock(return_value=response)

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=session,
    ):
        ranuras = await cliente_omie.obtener_precios_del_dia(hoy_local())

    assert len(ranuras) == 96
    assert all(r.fuente == "redata" for r in ranuras)
    assert ranuras[0].precio_eur_kwh == pytest.approx(0.12)


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
    """Sin OMIE y con REData caído se lanza ErrorFuenteOmie."""
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
