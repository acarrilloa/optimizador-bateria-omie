"""Tests para entidades (sensores)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.optimizador_bateria_omie.cliente_omie import Ranura
from custom_components.optimizador_bateria_omie.const import (
    ATTR_FUENTE,
    ATTR_MAX,
    ATTR_MEDIANA,
    ATTR_MIN,
    ATTR_TS_ACTUALIZACION,
    SENSOR_PRECIO_ACTUAL,
    SENSOR_PRECIO_CLASIFICACION,
    SENSOR_PRECIO_EFECTIVO,
    SENSOR_PRECIO_ESTADISTICAS,
)
from custom_components.optimizador_bateria_omie.coordinador import (
    CoordinadorDatosOmie,
    DatosOmie,
)
from custom_components.optimizador_bateria_omie.entidades import (
    SensorPrecioActual,
    SensorPrecioClasificacion,
    SensorPrecioEfectivo,
    SensorPrecioEstadisticas,
)
from custom_components.optimizador_bateria_omie.tarifa import ModeloTarifa


@pytest.fixture
def coordinador():
    """Fixture para CoordinadorDatosOmie con 96 ranuras alrededor de ahora."""
    hass = MagicMock()
    ahora = datetime.now(tz=UTC)
    base = ahora.replace(minute=0, second=0, microsecond=0)
    ranuras = [
        Ranura(
            fecha_hora_utc=base + timedelta(minutes=15 * i),
            precio_eur_kwh=0.10 + i * 0.001,
            fuente="omie",
            fecha_publicacion=ahora,
        )
        for i in range(96)
    ]
    c = CoordinadorDatosOmie(hass, MagicMock(), ModeloTarifa(), MagicMock())
    c._datos = DatosOmie(
        precios_hoy=ranuras,
        precios_manana=None,
        clasificaciones_hoy=["valle"] * 96,
        clasificaciones_manana=None,
        estadisticas_hoy={"min": 0.10, "max": 0.245, "mediana": 0.17},
        estadisticas_manana=None,
        fuente="omie",
        ts_actualizacion=ahora,
    )
    c.last_update_success = True
    return c


@pytest.fixture
def sensor_precio_actual(coordinador):
    return SensorPrecioActual(coordinador)


@pytest.fixture
def sensor_precio_efectivo(coordinador):
    return SensorPrecioEfectivo(coordinador)


@pytest.fixture
def sensor_precio_clasificacion(coordinador):
    return SensorPrecioClasificacion(coordinador)


@pytest.fixture
def sensor_precio_estadisticas(coordinador):
    return SensorPrecioEstadisticas(coordinador)


class TestSensorPrecioActual:
    """Tests para SensorPrecioActual."""

    def test_unique_id(self, sensor_precio_actual):
        assert sensor_precio_actual.unique_id == SENSOR_PRECIO_ACTUAL

    def test_nombre(self, sensor_precio_actual):
        assert sensor_precio_actual.name == "Precio actual"

    def test_unidad(self, sensor_precio_actual):
        assert sensor_precio_actual.native_unit_of_measurement == "€/kWh"

    def test_estado(self, sensor_precio_actual, coordinador):
        precio = coordinador.obtener_precio_actual()
        assert sensor_precio_actual.native_value == round(precio, 4)

    def test_disponibilidad_sin_datos(self, sensor_precio_actual, coordinador):
        coordinador.obtener_precio_actual = MagicMock(return_value=None)
        assert sensor_precio_actual.native_value is None

    def test_atributos(self, sensor_precio_actual):
        attrs = sensor_precio_actual.extra_state_attributes
        assert attrs == {ATTR_FUENTE: "omie"}


class TestSensorPrecioEfectivo:
    """Tests para SensorPrecioEfectivo."""

    def test_unique_id(self, sensor_precio_efectivo):
        assert sensor_precio_efectivo.unique_id == SENSOR_PRECIO_EFECTIVO

    def test_nombre(self, sensor_precio_efectivo):
        assert sensor_precio_efectivo.name == "Precio efectivo"

    def test_unidad(self, sensor_precio_efectivo):
        assert sensor_precio_efectivo.native_unit_of_measurement == "€/kWh"

    def test_estado(self, sensor_precio_efectivo, coordinador):
        esperado = round(coordinador.obtener_precio_efectivo_actual(), 4)
        assert sensor_precio_efectivo.native_value == esperado


class TestSensorPrecioClasificacion:
    """Tests para SensorPrecioClasificacion."""

    def test_unique_id(self, sensor_precio_clasificacion):
        assert sensor_precio_clasificacion.unique_id == (
            SENSOR_PRECIO_CLASIFICACION
        )

    def test_nombre(self, sensor_precio_clasificacion):
        assert sensor_precio_clasificacion.name == "Clasificación"

    def test_sin_unidad(self, sensor_precio_clasificacion):
        assert sensor_precio_clasificacion.native_unit_of_measurement is None

    def test_estado(self, sensor_precio_clasificacion, coordinador):
        esperado = coordinador.obtener_clasificacion_actual()
        assert sensor_precio_clasificacion.native_value == esperado


class TestSensorPrecioEstadisticas:
    """Tests para SensorPrecioEstadisticas."""

    def test_unique_id(self, sensor_precio_estadisticas):
        assert sensor_precio_estadisticas.unique_id == (
            SENSOR_PRECIO_ESTADISTICAS
        )

    def test_nombre(self, sensor_precio_estadisticas):
        assert sensor_precio_estadisticas.name == "Estadísticas"

    def test_estado_mediana(self, sensor_precio_estadisticas):
        assert sensor_precio_estadisticas.native_value == 0.17

    def test_atributos(self, sensor_precio_estadisticas):
        attrs = sensor_precio_estadisticas.extra_state_attributes
        assert attrs[ATTR_MIN] == 0.10
        assert attrs[ATTR_MAX] == 0.245
        assert attrs[ATTR_MEDIANA] == 0.17
        assert attrs[ATTR_FUENTE] == "omie"
        assert ATTR_TS_ACTUALIZACION in attrs
