"""Tests para CoordinadorDatosOmie."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.optimizador_bateria_omie.cliente_omie import Ranura
from custom_components.optimizador_bateria_omie.coordinador import (
    CoordinadorDatosOmie,
    DatosOmie,
)
from custom_components.optimizador_bateria_omie.tarifa import ModeloTarifa


def _ranura(inicio: datetime, precio: float) -> Ranura:
    """Crea una ranura de 15 minutos en UTC."""
    return Ranura(
        fecha_hora_utc=inicio,
        precio_eur_kwh=precio,
        fuente="omie",
        fecha_publicacion=datetime.now(tz=UTC),
        fecha_hora_fin_utc=inicio + timedelta(minutes=15),
    )


def _tramo_actual() -> datetime:
    """Inicio (alineado a 15 min) del tramo horario actual en UTC."""
    ahora = datetime.now(tz=UTC)
    return ahora.replace(minute=(ahora.minute // 15) * 15, second=0, microsecond=0)


def _coordinador() -> CoordinadorDatosOmie:
    """Coordinador con hass y colaboradores mockeados."""
    return CoordinadorDatosOmie(MagicMock(), MagicMock(), MagicMock(), MagicMock())


def _datos(ranuras, clasificaciones=None, manana=None) -> DatosOmie:
    """Construye un DatosOmie para tests."""
    return DatosOmie(
        precios_hoy=ranuras,
        precios_manana=manana,
        clasificaciones_hoy=clasificaciones or [],
        clasificaciones_manana=None,
        estadisticas_hoy={"min": 0.0, "max": 0.0, "mediana": 0.0},
        estadisticas_manana=None,
        fuente="omie",
        ts_actualizacion=datetime.now(tz=UTC),
    )


class TestObtenerPrecioActual:
    """Tests para obtener_precio_actual."""

    def test_en_precios_hoy(self):
        """La ranura actual se encuentra en precios_hoy."""
        c = _coordinador()
        c._datos = _datos([_ranura(_tramo_actual(), 0.1234)])

        assert c.obtener_precio_actual() == pytest.approx(0.1234)

    def test_fallback_precios_manana(self):
        """Si la ranura actual no está en precios_hoy (00:00-02:00
        locales), se busca en precios_manana."""
        c = _coordinador()
        c._datos = _datos(
            [_ranura(_tramo_actual() - timedelta(hours=2), 0.1111)],
            manana=[_ranura(_tramo_actual(), 0.2222)],
        )

        assert c.obtener_precio_actual() == pytest.approx(0.2222)

    def test_no_disponible(self):
        """Sin ranura actual en ningún día → None."""
        c = _coordinador()
        c._datos = _datos([_ranura(_tramo_actual() - timedelta(hours=5), 0.1111)])

        assert c.obtener_precio_actual() is None

    def test_sin_datos(self):
        """Con precios_hoy vacío → None."""
        c = _coordinador()
        c._datos = _datos([])

        assert c.obtener_precio_actual() is None


class TestObtenerPrecioEfectivoActual:
    """Tests para obtener_precio_efectivo_actual."""

    def test_con_datos(self):
        """Aplica el modelo de tarifa al precio actual."""
        c = _coordinador()
        c.modelo_tarifa = ModeloTarifa()
        c._datos = _datos([_ranura(_tramo_actual(), 0.10)])

        precio = c.obtener_precio_efectivo_actual()
        esperado = ModeloTarifa().obtener_precio_efectivo(
            0.10, datetime.now(tz=UTC)
        )

        assert precio == pytest.approx(esperado)

    def test_sin_datos(self):
        """Sin precio actual → None."""
        c = _coordinador()
        c.modelo_tarifa = ModeloTarifa()
        c._datos = _datos([])

        assert c.obtener_precio_efectivo_actual() is None


class TestObtenerClasificacionActual:
    """Tests para obtener_clasificacion_actual (índice relativo)."""

    def test_indice_relativo_a_primera_ranura(self):
        """El índice se calcula desde la primera ranura, no desde las 00:00."""
        c = _coordinador()
        tramo = _tramo_actual()
        c._datos = _datos(
            [_ranura(tramo - timedelta(minutes=15), 0.1), _ranura(tramo, 0.2)],
            clasificaciones=["primera", "segunda"],
        )

        assert c.obtener_clasificacion_actual() == "segunda"

    def test_fuera_de_rango(self):
        """Con la primera ranura posterior al instante actual → None."""
        c = _coordinador()
        c._datos = _datos(
            [_ranura(_tramo_actual() + timedelta(hours=2), 0.1)],
            clasificaciones=["primera"],
        )

        assert c.obtener_clasificacion_actual() is None

    def test_sin_clasificaciones(self):
        """Sin clasificaciones → None."""
        c = _coordinador()
        c._datos = _datos([_ranura(_tramo_actual(), 0.1)])

        assert c.obtener_clasificacion_actual() is None


class TestAsyncUpdateData:
    """Tests para _async_update_data."""

    @pytest.mark.asyncio
    async def test_actualizacion_completa(self, fixture_ranuras_hoy):
        """Una actualización correcta produce 96 precios, 96
        clasificaciones por período y estadísticas."""
        cliente = MagicMock()
        cliente.obtener_precios_del_dia = AsyncMock(
            return_value=fixture_ranuras_hoy
        )
        c = CoordinadorDatosOmie(MagicMock(), cliente, ModeloTarifa(), MagicMock())

        datos = await c._async_update_data()

        assert len(datos.precios_hoy) == 96
        assert len(datos.clasificaciones_hoy) == 96
        assert set(datos.clasificaciones_hoy) <= {"valle", "llano", "punta"}
        assert datos.fuente == "omie"
        assert "min" in datos.estadisticas_hoy
        assert "mediana" in datos.estadisticas_hoy

    @pytest.mark.asyncio
    async def test_fallo_de_fuente(self):
        """Si todas las fuentes fallan se lanza UpdateFailed."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        cliente = MagicMock()
        cliente.obtener_precios_del_dia = AsyncMock(
            side_effect=RuntimeError("todas las fuentes fallaron")
        )
        c = CoordinadorDatosOmie(MagicMock(), cliente, ModeloTarifa(), MagicMock())

        with pytest.raises(UpdateFailed):
            await c._async_update_data()
