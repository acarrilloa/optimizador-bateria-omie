"""Fixtures para tests del componente optimizador_bateria_omie."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.optimizador_bateria_omie.cliente_omie import (
    ClienteOmie,
    ConfiguracionOmie,
)


def hoy_local():
    """Fecha local de hoy (misma base que usa el código bajo test)."""
    return datetime.now().astimezone().date()


def medianoche_local():
    """Medianoche local de hoy como datetime consciente."""
    return datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )


@pytest.fixture
def cliente_omie(tmp_path):
    """Fixture para ClienteOmie con configuración mock."""
    hass = MagicMock()
    hass.config = MagicMock()
    hass.config.components = ["omie"]
    hass.config.config_dir = str(tmp_path)
    hass.states = MagicMock()
    hass.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args: fn(*args)
    )

    config = ConfiguracionOmie(
        fuente_prioritaria="omie",
        usar_cache=True,
        dias_cache=7,
    )

    return ClienteOmie(hass, config)


@pytest.fixture
def fixture_precios_omie_96():
    """96 tramos en el formato del sensor omie_precios_de_es_hoy:
    dicts con ``start`` (ISO 8601 con offset local) y ``price``, sin ``end``."""
    base = medianoche_local()
    return [
        {
            "start": (base + timedelta(minutes=15 * i)).isoformat(),
            "price": round(0.10 + i * 0.001, 6),
        }
        for i in range(96)
    ]


@pytest.fixture
def fixture_precios_omie_92():
    """92 tramos (día con cambio horario), mismo formato que el sensor OMIE."""
    base = medianoche_local()
    return [
        {
            "start": (base + timedelta(minutes=15 * i)).isoformat(),
            "price": round(0.10 + i * 0.001, 6),
        }
        for i in range(92)
    ]


@pytest.fixture
def fixture_respuesta_redata():
    """Respuesta de REData: dict con clave "data" y tramos en el formato
    start/price que espera el normalizador."""
    base = medianoche_local()
    data = [
        {
            "start": (base + timedelta(minutes=15 * i)).isoformat(),
            "price": round(0.12 + i * 0.0015, 6),
        }
        for i in range(96)
    ]
    return {"data": data}


@pytest.fixture
def fixture_ranuras_hoy():
    """96 Ranuras reales desde medianoche local (UTC), precios crecientes."""
    base = medianoche_local().astimezone(UTC)
    from custom_components.optimizador_bateria_omie.cliente_omie import Ranura

    return [
        Ranura(
            fecha_hora_utc=base + timedelta(minutes=15 * i),
            precio_eur_kwh=round(0.10 + i * 0.001, 6),
            fuente="omie",
            fecha_publicacion=datetime.now(tz=UTC),
            fecha_hora_fin_utc=base + timedelta(minutes=15 * (i + 1)),
        )
        for i in range(96)
    ]
