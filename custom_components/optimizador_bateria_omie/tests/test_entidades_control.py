"""Tests de las entidades de control (F4, U7)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.optimizador_bateria_omie.const import (
    CONF_PRECIO_MAX_COMPRA,
    CONF_SOC_MAX,
    CONF_SOC_MIN,
    DEFAULT_PRECIO_MAX_COMPRA,
    DEFAULT_SOC_MAX,
    DEFAULT_SOC_MIN,
)
from custom_components.optimizador_bateria_omie.entidades_control import (
    NumberParametroPlanner,
    SensorEstadoControl,
    SwitchControlActivo,
    crear_numbers,
)


def _entry(data=None):
    entry = MagicMock()
    entry.data = dict(data or {})
    return entry


def _coordinador_plan():
    c = MagicMock()
    c.async_recalcular = AsyncMock(return_value=MagicMock())
    return c


def _ejecutor():
    e = MagicMock()
    e.control_activo = False
    e.estado = "inactivo"
    e.estado_detalle = {
        "motivo": "test",
        "control_activo": False,
        "fallos_consecutivos": 0,
        "escrituras_ultima_hora": 0,
        "modo_manual": None,
        "ultimo_aplicado": {},
        "ruta_auditoria": "x",
    }
    e.async_set_control_activo = AsyncMock()
    e.async_registrar_callback = MagicMock(return_value=MagicMock())
    return e


class TestSwitchControlActivo:
    def test_default_off(self):
        switch = SwitchControlActivo(_ejecutor())

        assert switch.is_on is False
        assert switch.unique_id == "optimizador_bateria_omie_control_activo"

    @pytest.mark.asyncio
    async def test_turn_on_habilita(self):
        ejecutor = _ejecutor()
        switch = SwitchControlActivo(ejecutor)

        await switch.async_turn_on()

        ejecutor.async_set_control_activo.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_turn_off_fuerza_dry_run(self):
        ejecutor = _ejecutor()
        switch = SwitchControlActivo(ejecutor)

        await switch.async_turn_off()

        ejecutor.async_set_control_activo.assert_awaited_once_with(False)


class TestNumbers:
    def test_crear_numbers_nueve_parametros(self):
        numbers = crear_numbers(_entry(), _coordinador_plan())

        assert len(numbers) == 9
        unique_ids = {n.unique_id for n in numbers}
        assert "optimizador_bateria_omie_precio_max_compra" in unique_ids
        assert "optimizador_bateria_omie_eficiencia_round_trip" in unique_ids
        assert "optimizador_bateria_omie_precio_excedentes" in unique_ids

    def test_native_value_default_y_de_entry(self):
        coordinador = _coordinador_plan()
        entry = _entry()
        number = NumberParametroPlanner(
            entry, coordinador, CONF_PRECIO_MAX_COMPRA,
            "Precio máximo de compra", "uid", 0.0, 1.0, 0.005,
            DEFAULT_PRECIO_MAX_COMPRA, "€/kWh",
        )

        assert number.native_value == pytest.approx(DEFAULT_PRECIO_MAX_COMPRA)

        entry.data[CONF_PRECIO_MAX_COMPRA] = 0.05
        assert number.native_value == pytest.approx(0.05)

    @pytest.mark.asyncio
    async def test_set_native_value_escribe_y_recalcula(self):
        coordinador = _coordinador_plan()
        entry = _entry()
        number = NumberParametroPlanner(
            entry, coordinador, CONF_SOC_MIN, "SOC mínimo", "uid",
            5.0, 50.0, 1.0, DEFAULT_SOC_MIN, "%",
        )

        await number.async_set_native_value(25.0)

        # 0.12.2: escritura vía async_update_entry (entry.data es
        # inmutable desde HA 2026.x)
        kwargs = entry.hass.config_entries.async_update_entry.call_args.kwargs
        assert kwargs["data"][CONF_SOC_MIN] == pytest.approx(25.0)
        coordinador.async_recalcular.assert_awaited_once_with("number")

    @pytest.mark.asyncio
    async def test_soc_min_sincroniza_limites_del_inversor(self):
        """0.11.6: mover el number de SOC mínimo reescribe 217/218."""
        ejecutor = MagicMock()
        ejecutor.async_actualizar_limites_soc = AsyncMock()
        coordinador = _coordinador_plan()
        entry = _entry()
        number = NumberParametroPlanner(
            entry, coordinador, CONF_SOC_MIN, "SOC mínimo", "uid",
            5.0, 50.0, 1.0, DEFAULT_SOC_MIN, "%", ejecutor=ejecutor,
        )

        await number.async_set_native_value(20.0)

        ejecutor.async_actualizar_limites_soc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_otro_parametro_no_toca_limites(self):
        ejecutor = MagicMock()
        ejecutor.async_actualizar_limites_soc = AsyncMock()
        number = NumberParametroPlanner(
            _entry(), _coordinador_plan(), CONF_SOC_MAX, "SOC máximo",
            "uid", 50.0, 100.0, 1.0, DEFAULT_SOC_MAX, "%",
            ejecutor=ejecutor,
        )

        await number.async_set_native_value(90.0)

        ejecutor.async_actualizar_limites_soc.assert_not_awaited()

    def test_rango_precio_max_compra_0_a_1(self):
        """Corrección 7: 0-1 €/kWh (precio efectivo hasta ~0,35)."""
        numbers = crear_numbers(_entry(), _coordinador_plan())
        por_clave = {
            n._clave: n for n in numbers
        }
        number = por_clave[CONF_PRECIO_MAX_COMPRA]
        assert number.native_min_value == 0.0
        assert number.native_max_value == 1.0


class TestSensorEstadoControl:
    def test_estado_y_atributos(self):
        ejecutor = _ejecutor()
        sensor = SensorEstadoControl(ejecutor)

        assert sensor.native_value == "inactivo"
        assert sensor.unique_id == "optimizador_bateria_omie_estado_control"
        assert sensor.extra_state_attributes == ejecutor.estado_detalle
        assert sensor.extra_state_attributes["modo_manual"] is None

    def test_cuatro_estados_posibles(self):
        ejecutor = _ejecutor()
        sensor = SensorEstadoControl(ejecutor)

        for estado in (
            "inactivo",
            "dry_run",
            "activo",
            "fallback_bloqueado",
        ):
            ejecutor.estado = estado
            assert sensor.native_value == estado

    @pytest.mark.asyncio
    async def test_callback_registrado_al_añadir(self):
        ejecutor = _ejecutor()
        sensor = SensorEstadoControl(ejecutor)

        await sensor.async_added_to_hass()

        ejecutor.async_registrar_callback.assert_called_once()

        await sensor.async_will_remove_from_hass()
        ejecutor.async_registrar_callback.return_value \
            .assert_called_once()


class TestParamsCalientes:
    """D16: cambiar un number aplica en el recálculo siguiente."""

    @pytest.mark.asyncio
    async def test_recarga_parametros_del_entry(self):
        """CoordinadorPlan reconstruye ParametrosPlanner en cada recálculo."""
        from datetime import UTC, datetime

        from custom_components.optimizador_bateria_omie.coordinador_plan import (
            CoordinadorPlan,
        )
        from custom_components.optimizador_bateria_omie.planificador import (
            ParametrosPlanner,
            PlanificadorGreedy,
        )
        from custom_components.optimizador_bateria_omie.predictor_consumo import (
            PercentilesTramo,
            PrediccionDia,
        )
        from custom_components.optimizador_bateria_omie.tarifa import (
            ModeloTarifa,
        )

        hoy = datetime.now().astimezone().date()
        prediccion = PrediccionDia(
            fecha=hoy,
            consumo=[PercentilesTramo(10, 300, 500)] * 96,
            fv=[PercentilesTramo(0, 0, 0)] * 96,
            fuente_fv="none",
        )
        predictor = MagicMock()
        predictor.predecir = AsyncMock(return_value=prediccion)

        ranuras = [
            MagicMock(
                precio_eur_kwh=0.08,
                fecha_hora_utc=datetime.now(tz=UTC),
            )
        ] * 96
        coordinador_precio = MagicMock()
        coordinador_precio.datos.precios_hoy = ranuras
        coordinador_precio.datos.precios_manana = None
        coordinador_precio.modelo_tarifa = ModeloTarifa()
        coordinador_precio.async_add_listener = MagicMock(
            return_value=MagicMock()
        )

        coordinador_telemetria = MagicMock()
        coordinador_telemetria.data.soc = 20.0

        entry = _entry({"permitir_venta_red": False})
        hass = MagicMock()
        hass.async_create_task = MagicMock(side_effect=lambda coro: coro)
        planificador = PlanificadorGreedy(ParametrosPlanner())
        coordinador = CoordinadorPlan(
            hass=hass,
            entry=entry,
            coordinador_precio=coordinador_precio,
            coordinador_telemetria=coordinador_telemetria,
            predictor=predictor,
            planificador=planificador,
        )

        # Con permitir_venta_red=False el plan no vende ni carga
        await coordinador.async_recalcular("test")
        assert "venta a red deshabilitada" in coordinador.plan.motivos

        # Cambio en caliente del parámetro → el recálculo lo aplica
        entry.data["permitir_venta_red"] = True
        await coordinador.async_recalcular("number")
        assert "venta a red deshabilitada" not in coordinador.plan.motivos
