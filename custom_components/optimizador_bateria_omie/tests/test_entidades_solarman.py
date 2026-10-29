"""Tests para las entidades de telemetría Solarman (U5)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfPower

from custom_components.optimizador_bateria_omie.entidades_solarman import (
    SensorConsumoCasa,
    SensorCorrienteBateria,
    SensorPotenciaBateria,
    SensorProduccionFv,
    SensorSocBateria,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    TelemetriaInversor,
)


@pytest.fixture
def coordinador():
    """Coordinador mock con telemetría completa."""
    c = MagicMock()
    c.last_update_success = True
    c.data = TelemetriaInversor(
        soc=73.0,
        potencia_bateria=-1200.0,
        corriente_bateria=-24.0,
        consumo_casa=350.0,
        produccion_fv=800.0,
        temperatura_inversor=35.0,
        temperatura_bateria=28.0,
        ts_actualizacion=datetime.now(tz=UTC),
    )
    return c


class TestSensoresTelemetria:
    """native_value correcto y None → unavailable para cada sensor."""

    def test_soc(self, coordinador):
        sensor = SensorSocBateria(coordinador)
        assert sensor.native_value == pytest.approx(73.0)
        assert sensor.unique_id == "optimizador_bateria_omie_soc_bateria"
        assert sensor.native_unit_of_measurement == PERCENTAGE
        assert sensor.device_class is SensorDeviceClass.BATTERY
        assert sensor.state_class is SensorStateClass.MEASUREMENT
        assert sensor.available is True

    def test_potencia_bateria(self, coordinador):
        """Convención Deye (D16): +descarga / −carga."""
        sensor = SensorPotenciaBateria(coordinador)
        assert sensor.native_value == pytest.approx(-1200.0)
        assert sensor.native_unit_of_measurement == UnitOfPower.WATT
        assert sensor.device_class is SensorDeviceClass.POWER
        assert "descarga" in SensorPotenciaBateria.__doc__

    def test_corriente_bateria(self, coordinador):
        sensor = SensorCorrienteBateria(coordinador)
        assert sensor.native_value == pytest.approx(-24.0)
        assert sensor.native_unit_of_measurement == (
            UnitOfElectricCurrent.AMPERE
        )
        assert sensor.device_class is SensorDeviceClass.CURRENT

    def test_consumo_casa(self, coordinador):
        sensor = SensorConsumoCasa(coordinador)
        assert sensor.native_value == pytest.approx(350.0)
        assert sensor.device_class is SensorDeviceClass.POWER

    def test_produccion_fv(self, coordinador):
        sensor = SensorProduccionFv(coordinador)
        assert sensor.native_value == pytest.approx(800.0)
        assert sensor.device_class is SensorDeviceClass.POWER


class TestDisponibilidad:
    """Magnitud None o actualización fallida → unavailable."""

    def test_magnitud_none(self, coordinador):
        """Sin corriente en el mapa de la familia → corriente None."""
        coordinador.data.corriente_bateria = None
        sensor = SensorCorrienteBateria(coordinador)

        assert sensor.native_value is None
        assert sensor.available is False

    def test_datos_none(self, coordinador):
        coordinador.data = None
        sensor = SensorSocBateria(coordinador)

        assert sensor.native_value is None
        assert sensor.available is False

    def test_actualizacion_fallida(self, coordinador):
        coordinador.last_update_success = False
        sensor = SensorConsumoCasa(coordinador)

        assert sensor.available is False

    def test_device_info_compartido(self, coordinador):
        """Todos comparten el dispositivo de los sensores de F1."""
        sensor = SensorSocBateria(coordinador)
        identificadores = sensor.device_info["identifiers"]

        assert identificadores == {
            ("optimizador_bateria_omie", "optimizador_bateria_omie")
        }
