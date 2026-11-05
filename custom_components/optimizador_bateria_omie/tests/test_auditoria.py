"""Tests de la auditoría de ahorro real (0.10.1, F4.5)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.optimizador_bateria_omie.auditoria import (
    DELTA_T_H,
    AuditoriaAhorro,
)
from custom_components.optimizador_bateria_omie.cliente_omie import Ranura


class StoreFake:
    """Store de hass.storage simulado para tests."""

    def __init__(self, datos: dict | None = None) -> None:
        self.datos = dict(datos) if datos else None

    async def async_load(self) -> dict | None:
        return dict(self.datos) if self.datos else None

    async def async_save(self, datos: dict) -> None:
        self.datos = dict(datos)


def _ranuras(precio: float, n: int = 96) -> list[Ranura]:
    hoy = datetime.now().astimezone().date()
    inicio = datetime(hoy.year, hoy.month, hoy.day, tzinfo=UTC)
    return [
        Ranura(
            fecha_hora_utc=inicio + timedelta(minutes=15 * i),
            precio_eur_kwh=precio,
            fuente="omie",
            fecha_publicacion=inicio,
        )
        for i in range(n)
    ]


def _precio_fake(precios: list[Ranura]) -> SimpleNamespace:
    return SimpleNamespace(
        datos=SimpleNamespace(precios_hoy=precios),
        modelo_tarifa=SimpleNamespace(
            obtener_precio_efectivo=lambda p, f, festivo_manual=False: p
        ),
    )


def _telemetria_fake(
    soc: float = 50.0,
    potencia_bateria: float | None = 0.0,
    consumo_casa: float | None = 0.0,
) -> SimpleNamespace:
    datos = SimpleNamespace(
        soc=soc,
        potencia_bateria=potencia_bateria,
        consumo_casa=consumo_casa,
        ts_actualizacion=datetime.now(tz=UTC),
    )
    return SimpleNamespace(data=datos)


@pytest.fixture
def store(monkeypatch):
    store = StoreFake()
    monkeypatch.setattr(
        "custom_components.optimizador_bateria_omie.auditoria.Store",
        lambda hass, version, key: store,
    )
    return store


def _auditoria(tel: SimpleNamespace, precio: float = 0.2) -> AuditoriaAhorro:
    return AuditoriaAhorro(None, _precio_fake(_ranuras(precio)), tel)


def _ahora_local_tramo(hora: int, minuto: int = 0) -> datetime:
    """Instante en UTC equivalente a ``hora:minuto`` hora local de hoy."""
    hoy = datetime.now().astimezone().date()
    local = datetime(hoy.year, hoy.month, hoy.day, hora, minuto).astimezone()
    return local.astimezone(UTC)


@pytest.mark.asyncio
async def test_descarga_cubre_consumo_genera_ahorro(store):
    """Batería descargando 2000 W con consumo 500 W: ahorro positivo."""
    tel = _telemetria_fake(potencia_bateria=2000.0, consumo_casa=500.0)
    aud = _auditoria(tel, precio=0.2)
    await aud.async_cargar()

    await aud.async_actualizar(_ahora_local_tramo(10))
    await aud.async_actualizar(_ahora_local_tramo(10, 15))  # cierra tramo 40

    # descarga 0.5 kWh, consumo 0.125 kWh → exportada 0.375 kWh
    d = aud.desglose
    assert d["descarga_bateria_kwh"] == pytest.approx(0.5)
    assert d["red_exportada_kwh"] == pytest.approx(0.375)
    assert d["red_importada_kwh"] == pytest.approx(0.0)
    # ahorro = coste_sin (0.125·p) − coste_real (−0.375·p) = 0.5·p
    assert aud.ahorro_real_hoy == pytest.approx(0.5 * 0.2)


@pytest.mark.asyncio
async def test_carga_desde_red_nuevo_coste(store):
    """Carga 2000 W con consumo 500 W: todo importado, ahorro negativo."""
    tel = _telemetria_fake(potencia_bateria=-2000.0, consumo_casa=500.0)
    aud = _auditoria(tel, precio=0.2)
    await aud.async_cargar()

    await aud.async_actualizar(_ahora_local_tramo(10))
    await aud.async_actualizar(_ahora_local_tramo(10, 15))

    d = aud.desglose
    assert d["carga_bateria_kwh"] == pytest.approx(0.5)
    # importada = consumo + carga − descarga = 0.125 + 0.5
    assert d["red_importada_kwh"] == pytest.approx(0.625)
    assert aud.ahorro_real_hoy == pytest.approx(round(0.125 * 0.2 - 0.625 * 0.2, 4))


@pytest.mark.asyncio
async def test_tramo_indexado_a_hora_local(store):
    """El tramo cerrado usa el índice de precios_hoy local, no el de UTC."""
    precios = []
    inicio = datetime.now().astimezone().date()
    base = datetime(inicio.year, inicio.month, inicio.day, tzinfo=UTC)
    from custom_components.optimizador_bateria_omie.cliente_omie import Ranura as _R

    for i in range(96):
        precios.append(
            _R(
                fecha_hora_utc=base + timedelta(minutes=15 * i),
                precio_eur_kwh=0.1 + i,  # precio identifica el índice
                fuente="omie",
                fecha_publicacion=base,
            )
        )
    tel = _telemetria_fake(potencia_bateria=-1000.0, consumo_casa=0.0)
    aud = AuditoriaAhorro(None, _precio_fake(precios), tel)
    await aud.async_cargar()

    # 10:00–10:15 hora local = tramo local 40
    await aud.async_actualizar(_ahora_local_tramo(10))
    await aud.async_actualizar(_ahora_local_tramo(10, 15))

    carga_kwh = 1000 * DELTA_T_H / 1000  # 0.25 kWh
    precio_tramo_local = 0.1 + 40
    assert aud.desglose["coste_real_eur"] == pytest.approx(
        round(carga_kwh * precio_tramo_local, 4)
    )


@pytest.mark.asyncio
async def test_mismo_tramo_no_duplica(store):
    tel = _telemetria_fake(potencia_bateria=1000.0, consumo_casa=0.0)
    aud = _auditoria(tel)
    await aud.async_cargar()

    await aud.async_actualizar(_ahora_local_tramo(10))
    await aud.async_actualizar(_ahora_local_tramo(10, 5))  # mismo tramo
    await aud.async_actualizar(_ahora_local_tramo(10, 10))  # mismo tramo

    assert all(v == 0.0 for v in aud.desglose.values())
    assert aud.ahorro_real_hoy == 0.0


@pytest.mark.asyncio
async def test_cambio_de_dia_resetea_acumulado(store):
    tel = _telemetria_fake(potencia_bateria=1000.0, consumo_casa=0.0)
    aud = _auditoria(tel)
    await aud.async_cargar()

    await aud.async_actualizar(_ahora_local_tramo(10))
    await aud.async_actualizar(_ahora_local_tramo(10, 15))
    assert aud.ahorro_real_hoy > 0.0

    manana = _ahora_local_tramo(10) + timedelta(days=1)
    await aud.async_actualizar(manana)
    assert aud.ahorro_real_hoy == 0.0
    assert aud.desglose["descarga_bateria_kwh"] == 0.0


@pytest.mark.asyncio
async def test_persistencia_y_recarga(store):
    tel = _telemetria_fake(potencia_bateria=1000.0, consumo_casa=0.0)
    aud = _auditoria(tel)
    await aud.async_cargar()

    await aud.async_actualizar(_ahora_local_tramo(10))
    await aud.async_actualizar(_ahora_local_tramo(10, 15))
    guardado = store.datos
    assert guardado["ultimo_tramo"] == 10 * 4 + 1
    assert guardado["ahorro_real_eur"] > 0.0

    # Un segundo arranque el mismo día recupera lo acumulado
    aud2 = _auditoria(tel)
    await aud2.async_cargar()
    assert aud2.ahorro_real_hoy == pytest.approx(guardado["ahorro_real_eur"])


@pytest.mark.asyncio
async def test_sin_telemetria_no_acumula(store):
    tel = SimpleNamespace(data=SimpleNamespace(soc=None, potencia_bateria=None,
                                               consumo_casa=None))
    aud = _auditoria(tel)
    await aud.async_cargar()

    await aud.async_actualizar(_ahora_local_tramo(10))
    await aud.async_actualizar(_ahora_local_tramo(10, 15))
    assert aud.ahorro_real_hoy == 0.0
