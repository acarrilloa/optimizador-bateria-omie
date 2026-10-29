"""Tests del PlanificadorGreedy (F3, U3) — escenarios de SPEC §10.3.

Función pura: sin mocks de HA, solo datos. Seed fijo no necesario
(no hay aleatoriedad).
"""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.optimizador_bateria_omie.planificador import (
    ACCION_AUTOCONSUMO,
    ACCION_CARGA_RED,
    ACCION_VENTA_RED,
    ESTADO_OK,
    ESTADO_PERFIL_INSUFICIENTE,
    ESTADO_SIN_PRECIOS,
    ESTADO_SIN_TELEMETRIA,
    ParametrosPlanner,
    PlanificadorGreedy,
)
from custom_components.optimizador_bateria_omie.predictor_consumo import (
    PercentilesTramo,
    PrediccionDia,
)

HOY = date(2026, 10, 21)  # miércoles (laborable)
MANANA = HOY + timedelta(days=1)


def _percentiles(p50: float) -> PercentilesTramo:
    return PercentilesTramo(p10=p50 * 0.5, p50=p50, p90=p50 * 1.5)


def _prediccion(
    consumo_w: float = 300.0,
    fv_w: float = 0.0,
    fecha: date = HOY,
    tramos_nulos: int = 0,
) -> PrediccionDia:
    """Predicción con valores constantes y opcionalmente tramos nulos."""
    consumo = [_percentiles(consumo_w)] * 96
    for i in range(tramos_nulos):
        consumo[i] = None
    return PrediccionDia(
        fecha=fecha,
        consumo=consumo,
        fv=[_percentiles(fv_w)] * 96,
        fuente_fv="perfil",
    )


def _precios_constantes(precio: float) -> list[float]:
    return [precio] * 96


def _precios_valle_punta(
    valle: float = 0.08, punta: float = 0.25
) -> list[float]:
    """Noche (0-8h) valle, día punta, tarde-entrada valle parcial."""
    precios = []
    for tramo in range(96):
        hora = tramo // 4
        precios.append(valle if hora < 8 else punta)
    return precios


def _planificador(**overrides) -> PlanificadorGreedy:
    return PlanificadorGreedy(ParametrosPlanner(**overrides))


class TestEstados:
    def test_sin_precios(self):
        plan = _planificador().planificar(
            HOY, [], None, _prediccion(), None, 50.0
        )
        assert plan.estado == ESTADO_SIN_PRECIOS
        assert plan.tramos == []

    def test_sin_telemetria_soc_none(self):
        """Sin SOC real no se inventa un estado inicial."""
        plan = _planificador().planificar(
            HOY, _precios_constantes(0.1), None, _prediccion(), None, None
        )
        assert plan.estado == ESTADO_SIN_TELEMETRIA
        assert plan.tramos == []

    def test_perfil_insuficiente_solo_autoconsumo(self):
        """Más de la mitad de tramos sin predicción → plan trivial."""
        prediccion = _prediccion(tramos_nulos=60)
        plan = _planificador().planificar(
            HOY, _precios_valle_punta(), None, prediccion, None, 50.0
        )
        assert plan.estado == ESTADO_PERFIL_INSUFICIENTE
        assert not any(
            t.accion == ACCION_CARGA_RED for t in plan.tramos
        )
        assert not any(t.accion == ACCION_VENTA_RED for t in plan.tramos)

    def test_tramo_insuficiente_marcado(self):
        """Un tramo aislado sin datos se marca y el plan sigue."""
        prediccion = _prediccion(tramos_nulos=1)
        plan = _planificador().planificar(
            HOY, _precios_valle_punta(), None, prediccion, None, 50.0
        )
        assert plan.estado == ESTADO_OK
        assert plan.tramos[0].insuficiente is True
        assert plan.tramos[1].insuficiente is False


class TestVallePunta:
    def test_carga_nocturna_hasta_soc_max(self):
        """Día valle/punta clásico: carga en valle hasta soc_max."""
        plan = _planificador().planificar(
            HOY, _precios_valle_punta(), None, _prediccion(), None, 20.0
        )

        nocturnos = [t for t in plan.tramos if t.hora < "08:00"]
        assert nocturnos[0].accion == ACCION_CARGA_RED
        # Llega a soc_max durante la noche y no sigue cargando
        assert max(t.soc_previsto_pct for t in nocturnos) <= 95.0
        assert any(t.soc_previsto_pct >= 95.0 for t in nocturnos)
        # Los tramos de punta no cargan desde red
        diurnos = [t for t in plan.tramos if t.hora >= "08:00"]
        assert not any(t.accion == ACCION_CARGA_RED for t in diurnos)

    def test_umbral_precio_max_compra(self):
        """Valle a 0.08 < umbral 0.09 → carga; a 0.095 > umbral → no."""
        precios = _precios_valle_punta(valle=0.095)
        plan = _planificador().planificar(
            HOY, precios, None, _prediccion(), None, 20.0
        )
        assert not any(t.accion == ACCION_CARGA_RED for t in plan.tramos)

    def test_arbitraje_solo_bajo_la_mediana(self):
        """Rampa: con precios crecientes solo la mitad baja carga."""
        precios = [round(0.05 + 0.002 * i, 4) for i in range(96)]
        # Umbral amplio para que decida la mediana, no el umbral fijo
        plan = _planificador(precio_max_compra=0.5).planificar(
            HOY, precios, None, _prediccion(), None, 20.0
        )

        cargas = [t for t in plan.tramos if t.accion == ACCION_CARGA_RED]
        mediana = sorted(precios)[48]
        assert cargas
        assert all(t.precio_referencia_eur_kwh < mediana for t in cargas)
        # La carga llega antes (greedy): primeros tramos
        assert cargas[0].tramo < 48


class TestPrecioNegativo:
    def test_carga_siempre_con_precio_negativo(self):
        """Precio ≤ 0 → CARGA_RED incluso por encima del umbral."""
        precios = _precios_constantes(-0.05)
        plan = _planificador(precio_max_compra=-1.0).planificar(
            HOY, precios, None, _prediccion(), None, 20.0
        )
        assert any(t.accion == ACCION_CARGA_RED for t in plan.tramos)
        # Carga hasta soc_max
        assert any(t.soc_previsto_pct >= 95.0 for t in plan.tramos)


class TestSocInicial:
    def test_soc_cero_carga_y_no_descarga_bajo_min(self):
        plan = _planificador().planificar(
            HOY, _precios_valle_punta(), None, _prediccion(), None, 0.0
        )
        assert plan.tramos[0].soc_previsto_pct >= 0.0

    def test_soc_maximo_no_carga(self):
        """Con la batería llena no hay carga desde red ni autoconsumo."""
        prediccion = _prediccion(consumo_w=300.0, fv_w=0.0)
        plan = _planificador().planificar(
            HOY, _precios_valle_punta(), None, prediccion, None, 95.0
        )
        nocturno = plan.tramos[0]
        assert nocturno.accion != ACCION_CARGA_RED
        assert nocturno.potencia_carga_red_w == 0.0

    def test_soc_medio_autoconsumo_sin_arbitraje(self):
        """Precio constante sin ciclo posible → solo autoconsumo."""
        plan = _planificador().planificar(
            HOY,
            _precios_constantes(0.2),
            None,
            _prediccion(consumo_w=1000.0),
            None,
            50.0,
        )
        assert plan.tramos[0].accion == ACCION_AUTOCONSUMO
        # Sin carga desde red (0.2 > umbral) ni venta (sin ciclo)
        assert not any(t.accion == ACCION_CARGA_RED for t in plan.tramos)
        assert not any(t.accion == ACCION_VENTA_RED for t in plan.tramos)


class TestAutoconsumo:
    def test_deficit_descarga_bateria(self):
        """Consumo 1000 W sin FV → descarga hasta cubrir el déficit."""
        prediccion = _prediccion(consumo_w=1000.0, fv_w=0.0)
        plan = _planificador().planificar(
            HOY, _precios_constantes(0.2), None, prediccion, None, 80.0
        )
        tramo = plan.tramos[0]
        assert tramo.accion == ACCION_AUTOCONSUMO
        assert tramo.potencia_bateria_w == pytest.approx(1000.0)

    def test_excedente_carga_bateria(self):
        """FV 2000 W > consumo 500 W → carga el excedente."""
        prediccion = _prediccion(consumo_w=500.0, fv_w=2000.0)
        plan = _planificador().planificar(
            HOY, _precios_constantes(0.2), None, prediccion, None, 20.0
        )
        tramo = plan.tramos[0]
        assert tramo.accion == ACCION_AUTOCONSUMO
        assert tramo.potencia_bateria_w == pytest.approx(-1500.0)

    def test_respeto_soc_minimo(self):
        """La descarga de autoconsumo nunca baja de soc_min."""
        prediccion = _prediccion(consumo_w=5000.0, fv_w=0.0)
        plan = _planificador().planificar(
            HOY, _precios_constantes(0.2), None, prediccion, None, 16.0
        )
        assert all(t.soc_previsto_pct >= 15.0 for t in plan.tramos)


class TestRestriccionPotencia:
    def test_restriccion_5750_con_consumo_alto(self):
        """Consumo previsto 5,5 kW → potencia de carga desde red 0 W."""
        prediccion = _prediccion(consumo_w=5500.0, fv_w=0.0)
        precios = _precios_valle_punta()
        plan = _planificador().planificar(
            HOY, precios, None, prediccion, None, 20.0
        )
        nocturno = [
            t for t in plan.tramos if t.accion == ACCION_CARGA_RED
        ]
        assert nocturno == []  # no hay margen bajo 5750 W
        # y la potencia de carga desde red queda a 0 (nunca negativa)
        assert all(t.potencia_carga_red_w == 0.0 for t in plan.tramos)

    def test_carga_limitada_por_restriccion(self):
        """Consumo 3 kW → carga desde red ≤ 5750−3000−300 = 2450 W."""
        prediccion = _prediccion(consumo_w=3000.0, fv_w=0.0)
        plan = _planificador().planificar(
            HOY, _precios_valle_punta(), None, prediccion, None, 20.0
        )
        cargas = [
            t.potencia_carga_red_w
            for t in plan.tramos
            if t.accion == ACCION_CARGA_RED
        ]
        assert cargas
        assert max(cargas) <= 2450.0 + 1e-9


class TestVenta:
    def test_venta_deshabilitada(self):
        prediccion = _prediccion(consumo_w=0.0, fv_w=3000.0)
        plan = _planificador(permitir_venta_red=False).planificar(
            HOY, _precios_constantes(0.25), None, prediccion, None, 95.0
        )
        assert not any(t.accion == ACCION_VENTA_RED for t in plan.tramos)
        assert "venta a red deshabilitada" in plan.motivos

    def test_venta_excedente_por_encima_del_minimo(self):
        """Batería llena + excedente + precio ≥ mínimo → venta."""
        prediccion = _prediccion(consumo_w=0.0, fv_w=3000.0)
        plan = _planificador().planificar(
            HOY, _precios_constantes(0.25), None, prediccion, None, 95.0
        )
        tramo = plan.tramos[0]
        assert tramo.accion == ACCION_VENTA_RED
        assert tramo.potencia_venta_red_w == pytest.approx(3000.0)

    def test_ciclo_sin_compensar_no_vende_bateria(self):
        """Spread < margen + pérdida η → no descarga batería a red.

        Compra a 0.08, η 0.88 → venta mínima = 0.08/0.88 + 0.015
        ≈ 0.1058. Con precio de venta 0.10 < 0.1058 no compensa.
        """
        precios = _precios_valle_punta(valle=0.08, punta=0.10)
        prediccion = _prediccion(consumo_w=300.0, fv_w=0.0)
        plan = _planificador().planificar(
            HOY, precios, None, prediccion, None, 20.0
        )
        assert not any(t.accion == ACCION_VENTA_RED for t in plan.tramos)

    def test_ciclo_compensado_vende(self):
        """Spread amplio (compra 0.08, venta 0.25) → venta viable."""
        precios = _precios_valle_punta(valle=0.08, punta=0.25)
        prediccion = _prediccion(consumo_w=300.0, fv_w=0.0)
        plan = _planificador(precio_min_venta=0.15).planificar(
            HOY, precios, None, prediccion, None, 20.0
        )
        assert any(t.accion == ACCION_VENTA_RED for t in plan.tramos)


class TestSignoDeye:
    def test_convencion_mas_descarga_menos_carga(self):
        prediccion_carga = _prediccion(consumo_w=500.0, fv_w=2000.0)
        plan = _planificador().planificar(
            HOY, _precios_constantes(0.2), None, prediccion_carga, None, 20.0
        )
        assert plan.tramos[0].potencia_bateria_w < 0  # carga → negativo

        prediccion_descarga = _prediccion(consumo_w=1000.0, fv_w=0.0)
        plan = _planificador().planificar(
            HOY,
            _precios_constantes(0.2),
            None,
            prediccion_descarga,
            None,
            80.0,
        )
        assert plan.tramos[0].potencia_bateria_w > 0  # descarga → positivo


class TestManana:
    def test_sin_precios_manana_plan_solo_hoy(self):
        plan = _planificador().planificar(
            HOY,
            _precios_valle_punta(),
            None,
            _prediccion(),
            _prediccion(fecha=MANANA),
            20.0,
        )
        assert plan.estado == ESTADO_OK
        assert len(plan.tramos) == 96

    def test_con_precios_manana_192_tramos(self):
        plan = _planificador().planificar(
            HOY,
            _precios_valle_punta(),
            _precios_valle_punta(),
            _prediccion(),
            _prediccion(fecha=MANANA),
            20.0,
        )
        assert len(plan.tramos) == 192
        fechas = {t.fecha for t in plan.tramos}
        assert fechas == {HOY, MANANA}
        # El SOC continúa entre días (sin reset)
        assert plan.tramos[96].soc_previsto_pct >= (
            plan.tramos[95].soc_previsto_pct - 10.0
        )


class TestAhorro:
    def test_ahorro_estimado_coherente(self):
        """ahorro = coste_sin − coste_con, y cargar barato ahorra."""
        plan = _planificador().planificar(
            HOY, _precios_valle_punta(), None, _prediccion(), None, 20.0
        )
        assert plan.ahorro_estimado_eur == pytest.approx(
            plan.coste_sin_plan_eur - plan.coste_con_plan_eur, abs=1e-4
        )
        # Con consumo constante y carga en valle, el plan ahorra
        assert plan.ahorro_estimado_eur > 0

    def test_parametros_desde_entry(self):
        """M3: los parámetros se leen con entry.data.get(clave, default)."""
        entry = MagicMock()
        entry.data = {
            "permitir_carga_red": False,
            "precio_max_compra": 0.05,
        }
        params = ParametrosPlanner.desde_entry(entry)
        assert params.permitir_carga_red is False
        assert params.precio_max_compra == 0.05
        # Defaults para las claves ausentes
        assert params.soc_min == 15.0
        assert params.capacidad_bateria_kwh == 15.36
