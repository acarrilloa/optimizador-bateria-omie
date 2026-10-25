"""Tests para ModeloTarifa."""

from datetime import UTC, datetime

import pytest

from custom_components.optimizador_bateria_omie.tarifa import (
    ConfiguracionTarifa,
    ModeloTarifa,
)


@pytest.fixture
def modelo_tarifa_por_defecto():
    """Fixture para ModeloTarifa con configuración por defecto."""
    return ModeloTarifa()


@pytest.fixture
def modelo_tarifa_sin_impuestos():
    """Fixture para ModeloTarifa sin impuestos."""
    config = ConfiguracionTarifa(
        potencia_contratada_kw=5.75,
        impuesto_electrico_pct=0.0,
        iva_pct=0.0,
        aplicar_impuestos_precio=False,
    )
    return ModeloTarifa(config)


class TestObtenerPeriodo:
    """Tests para obtener_periodo."""

    def test_obtener_periodo_laborable_punta_manana(
        self, modelo_tarifa_por_defecto
    ):
        """Test de período punta en horario de mañana (10:00-14:00)."""
        # 12:00 en UTC, asumiendo CET (UTC+1) = 13:00 local -> punta
        fecha_hora_utc = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
        periodo = modelo_tarifa_por_defecto.obtener_periodo(fecha_hora_utc)
        assert periodo == "punta"

    def test_obtener_periodo_laborable_punta_tarde(
        self, modelo_tarifa_por_defecto
    ):
        """Test de período punta en horario de tarde (18:00-22:00)."""
        # 19:00 en UTC, asumiendo CET (UTC+1) = 20:00 local -> punta
        fecha_hora_utc = datetime(2025, 1, 15, 19, 0, tzinfo=UTC)
        periodo = modelo_tarifa_por_defecto.obtener_periodo(fecha_hora_utc)
        assert periodo == "punta"

    def test_obtener_periodo_laborable_llano_manana(
        self, modelo_tarifa_por_defecto
    ):
        """Test de período llano en horario de mañana (08:00-10:00)."""
        # 8:00 en UTC, asumiendo CET (UTC+1) = 09:00 local -> llano
        fecha_hora_utc = datetime(2025, 1, 15, 7, 0, tzinfo=UTC)
        periodo = modelo_tarifa_por_defecto.obtener_periodo(fecha_hora_utc)
        assert periodo == "llano"

    def test_obtener_periodo_laborable_llano_tarde(
        self, modelo_tarifa_por_defecto
    ):
        """Test de período llano en horario de tarde (14:00-18:00)."""
        # 15:00 en UTC, asumiendo CET (UTC+1) = 16:00 local -> llano
        fecha_hora_utc = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
        periodo = modelo_tarifa_por_defecto.obtener_periodo(fecha_hora_utc)
        assert periodo == "llano"

    def test_obtener_periodo_laborable_valle(
        self, modelo_tarifa_por_defecto
    ):
        """Test de período valle en horario nocturno (00:00-08:00)."""
        # 5:00 en UTC, asumiendo CET (UTC+1) = 06:00 local -> valle
        fecha_hora_utc = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)
        periodo = modelo_tarifa_por_defecto.obtener_periodo(fecha_hora_utc)
        assert periodo == "valle"

    def test_obtener_periodo_sabado(self, modelo_tarifa_por_defecto):
        """Test de período en sábado -> valle todo el día."""
        # Sábado 12:00 UTC = sábado 13:00 local -> valle
        fecha_hora_utc = datetime(2025, 1, 18, 12, 0, tzinfo=UTC)  # 18 de enero 2025 es sábado
        periodo = modelo_tarifa_por_defecto.obtener_periodo(fecha_hora_utc)
        assert periodo == "valle"

    def test_obtener_periodo_domingo(self, modelo_tarifa_por_defecto):
        """Test de período en domingo -> valle todo el día."""
        # Domingo 12:00 UTC = domingo 13:00 local -> valle
        fecha_hora_utc = datetime(2025, 1, 19, 12, 0, tzinfo=UTC)  # 19 de enero 2025 es domingo
        periodo = modelo_tarifa_por_defecto.obtener_periodo(fecha_hora_utc)
        assert periodo == "valle"

    def test_obtener_periodo_festivo(self, modelo_tarifa_por_defecto):
        """Test de período en festivo nacional -> valle todo el día."""
        # 1 de enero (Año Nuevo) a cualquier hora -> valle
        fecha_hora_utc = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        periodo = modelo_tarifa_por_defecto.obtener_periodo(fecha_hora_utc)
        assert periodo == "valle"

    def test_obtener_periodo_festivo_6_enero(self, modelo_tarifa_por_defecto):
        """Test de período en 6 de enero (Reyes) -> valle."""
        fecha_hora_utc = datetime(2025, 1, 6, 12, 0, tzinfo=UTC)
        periodo = modelo_tarifa_por_defecto.obtener_periodo(fecha_hora_utc)
        assert periodo == "valle"


class TestObtenerPrecioEfectivo:
    """Tests para obtener_precio_efectivo."""

    def test_obtener_precio_efectivo_punta(
        self, modelo_tarifa_por_defecto
    ):
        """Test de cálculo de precio efectivo en período punta."""
        fecha_hora_utc = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)  # punta
        precio_spot = 0.15  # €/kWh

        precio_efectivo = modelo_tarifa_por_defecto.obtener_precio_efectivo(
            precio_spot, fecha_hora_utc
        )

        # punta: peaje=0.033261, cargo=0.064292
        # impuesto_eléctrico=5.1127%, IVA=21%
        peaje_cargo = 0.033261 + 0.064292  # 0.097553
        precio_con_peajes = precio_spot + peaje_cargo  # 0.247553
        factor_impuestos = (1 + 0.051127) * (1 + 0.21)  # 1.051127 * 1.21 ≈ 1.2719
        esperado = round(precio_con_peajes * factor_impuestos, 6)

        assert precio_efectivo == pytest.approx(esperado, rel=1e-4)

    def test_obtener_precio_efectivo_valle(
        self, modelo_tarifa_por_defecto
    ):
        """Test de cálculo de precio efectivo en período valle."""
        fecha_hora_utc = datetime(2025, 1, 15, 5, 0, tzinfo=UTC)  # valle
        precio_spot = 0.05  # €/kWh

        precio_efectivo = modelo_tarifa_por_defecto.obtener_precio_efectivo(
            precio_spot, fecha_hora_utc
        )

        # valle: peaje=0.000077, cargo=0.003215
        peaje_cargo = 0.000077 + 0.003215  # 0.003292
        precio_con_peajes = precio_spot + peaje_cargo  # 0.053292
        factor_impuestos = (1 + 0.051127) * (1 + 0.21)
        esperado = round(precio_con_peajes * factor_impuestos, 6)

        assert precio_efectivo == pytest.approx(esperado, rel=1e-4)

    def test_obtener_precio_efectivo_sin_impuestos(
        self, modelo_tarifa_sin_impuestos
    ):
        """Test de cálculo sin impuestos."""
        fecha_hora_utc = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)  # punta
        precio_spot = 0.15

        precio_efectivo = modelo_tarifa_sin_impuestos.obtener_precio_efectivo(
            precio_spot, fecha_hora_utc
        )

        # Sin impuestos: solo spot + peaje + cargo
        peaje_cargo = 0.033261 + 0.064292
        esperado = round(precio_spot + peaje_cargo, 6)

        assert precio_efectivo == pytest.approx(esperado, rel=1e-6)


class TestClasificarPorPercentiles:
    """Tests para clasificar_por_percentiles."""

    def test_clasificar_por_percentiles_normal(
        self, modelo_tarifa_por_defecto
    ):
        """Test de clasificación con datos normales."""
        # Crear 96 precios con distribución
        precios = [0.05 + i * 0.001 for i in range(96)]

        clasificaciones = modelo_tarifa_por_defecto.clasificar_por_percentiles(
            precios
        )

        # Verificar que hay las tres clasificaciones
        assert "valle" in clasificaciones
        assert "llano" in clasificaciones
        assert "punta" in clasificaciones

        # Verificar que están en orden
        assert clasificaciones == sorted(
            clasificaciones,
            key=lambda x: ["valle", "llano", "punta"].index(x),
        )

    def test_clasificar_por_percentiles_menos_de_3(
        self, modelo_tarifa_por_defecto
    ):
        """Test de clasificación con menos de 3 precios (no clasificable)."""
        precios = [0.10, 0.15]

        clasificaciones = modelo_tarifa_por_defecto.clasificar_por_percentiles(
            precios
        )

        # Con menos de 3 precios no se pueden calcular percentiles
        # y se devuelve todo valle
        assert clasificaciones == ["valle"] * 96


class TestObtenerEstadisticas:
    """Tests para obtener_estadisticas."""

    def test_obtener_estadisticas_normal(
        self, modelo_tarifa_por_defecto
    ):
        """Test de estadísticas con datos normales."""
        precios = [0.05, 0.10, 0.15, 0.20, 0.25]

        estadisticas = modelo_tarifa_por_defecto.obtener_estadisticas(precios)

        assert estadisticas["min"] == 0.05
        assert estadisticas["max"] == 0.25
        assert estadisticas["mediana"] == 0.15

    def test_obtener_estadisticas_vacio(
        self, modelo_tarifa_por_defecto
    ):
        """Test de estadísticas con lista vacía."""
        estadisticas = modelo_tarifa_por_defecto.obtener_estadisticas([])

        assert estadisticas["min"] == 0.0
        assert estadisticas["max"] == 0.0
        assert estadisticas["mediana"] == 0.0


class TestFestivos:
    """Tests para métodos de festivos."""

    def test_es_festivo_ano_nuevo(self, modelo_tarifa_por_defecto):
        """Test de festivo: Año Nuevo."""
        assert modelo_tarifa_por_defecto.es_festivo(
            datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        )

    def test_es_festivo_no_festivo(self, modelo_tarifa_por_defecto):
        """Test de no festivo: día normal."""
        assert not modelo_tarifa_por_defecto.es_festivo(
            datetime(2025, 1, 15, 0, 0, tzinfo=UTC)
        )

    def test_es_fin_de_semana_sabado(self, modelo_tarifa_por_defecto):
        """Test de fin de semana: sábado."""
        assert modelo_tarifa_por_defecto.es_fin_de_semana(
            datetime(2025, 1, 18, 0, 0, tzinfo=UTC)  # Sábado
        )

    def test_es_fin_de_semana_domingo(self, modelo_tarifa_por_defecto):
        """Test de fin de semana: domingo."""
        assert modelo_tarifa_por_defecto.es_fin_de_semana(
            datetime(2025, 1, 19, 0, 0, tzinfo=UTC)  # Domingo
        )

    def test_es_fin_de_semana_lunes(self, modelo_tarifa_por_defecto):
        """Test de no fin de semana: lunes."""
        assert not modelo_tarifa_por_defecto.es_fin_de_semana(
            datetime(2025, 1, 20, 0, 0, tzinfo=UTC)  # Lunes
        )


class TestClasificarPorPeriodo:
    """Tests para clasificar_por_periodo (clasificación del sensor)."""

    def test_dia_laborable(self, modelo_tarifa_por_defecto):
        """Día laborable: los tres períodos aparecen en sus franjas."""
        # Miércoles 15/01/2025, hora local CET = UTC+1
        fechas = [
            datetime(2025, 1, 15, 12, 0, tzinfo=UTC),  # 13:00 local → punta
            datetime(2025, 1, 15, 14, 0, tzinfo=UTC),  # 15:00 local → llano
            datetime(2025, 1, 15, 5, 0, tzinfo=UTC),   # 06:00 local → valle
        ]
        clasificaciones = modelo_tarifa_por_defecto.clasificar_por_periodo(fechas)
        assert clasificaciones == ["punta", "llano", "valle"]

    def test_fin_de_semana_todo_valle(self, modelo_tarifa_por_defecto):
        """Sábado: valle todo el día."""
        fechas = [
            datetime(2025, 1, 18, h, 0, tzinfo=UTC) for h in range(24)
        ]
        clasificaciones = modelo_tarifa_por_defecto.clasificar_por_periodo(fechas)
        assert set(clasificaciones) == {"valle"}

    def test_festivo_todo_valle(self, modelo_tarifa_por_defecto):
        """Festivo nacional (1 de enero): valle todo el día."""
        fechas = [
            datetime(2025, 1, 1, h, 0, tzinfo=UTC) for h in range(24)
        ]
        clasificaciones = modelo_tarifa_por_defecto.clasificar_por_periodo(fechas)
        assert set(clasificaciones) == {"valle"}
