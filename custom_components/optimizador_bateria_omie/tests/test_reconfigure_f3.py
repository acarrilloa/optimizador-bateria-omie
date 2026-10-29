"""Tests del reconfigure ampliado de F3 (M3: editables en caliente)."""

from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.optimizador_bateria_omie.config_flow import (
    CONFIG_SCHEMA,
    ConfigFlow,
    formatear_festivos,
    parsear_festivos,
)
from custom_components.optimizador_bateria_omie.const import (
    CONF_ENTITY_ID_PREVISION_FV_HOY,
    CONF_FESTIVOS_LOCALES,
    CONF_PRECIO_MAX_COMPRA,
    CONF_VENTANA_HISTORICO,
    DEFAULT_ENTITY_ID_PREVISION_FV_HOY,
    DEFAULT_PRECIO_MAX_COMPRA,
    DEFAULT_VENTANA_HISTORICO,
)

# Datos mínimos de una entrada de F2 (sin claves nuevas de F3)
ENTRADA_F2 = {
    "fuente_prioritaria": "omie",
    "usar_cache": True,
    "dias_cache": 7,
    "telemetry_source": "ha_entities",
    "entity_id_soc_bateria": "sensor.soc",
    "entity_id_consumo_casa": "sensor.consumo",
}


class TestParseoFestivos:
    def test_texto_a_lista(self):
        assert parsear_festivos("12-24, 01-06") == ["12-24", "01-06"]

    def test_vacio(self):
        assert parsear_festivos("") == []

    def test_lista_pasa_directo(self):
        assert parsear_festivos(["10-20"]) == ["10-20"]

    def test_formatear(self):
        assert formatear_festivos(["10-20", "11-01"]) == "10-20, 11-01"
        assert formatear_festivos("") == ""
        assert formatear_festivos(None) == ""


class TestSchemaF3:
    def test_defaults_f3(self):
        """El schema rellena los defaults de §5 para claves ausentes."""
        datos = CONFIG_SCHEMA(dict(ENTRADA_F2))

        assert datos[CONF_VENTANA_HISTORICO] == DEFAULT_VENTANA_HISTORICO
        assert datos[CONF_PRECIO_MAX_COMPRA] == DEFAULT_PRECIO_MAX_COMPRA
        assert (
            datos[CONF_ENTITY_ID_PREVISION_FV_HOY]
            == DEFAULT_ENTITY_ID_PREVISION_FV_HOY
        )
        assert datos["permitir_carga_red"] is True
        assert datos["permitir_venta_red"] is True
        assert datos["soc_min"] == 15.0
        assert datos["soc_max"] == 95.0
        assert datos["potencia_contratada"] == 5750.0
        assert datos["eficiencia_round_trip"] == 88
        assert datos["capacidad_bateria_kwh"] == 15.36
        # Deuda #10: corriente de batería con default confirmado
        assert datos["entity_id_corriente_bateria"] == (
            "sensor.inversor_battery_current"
        )

    def test_rangos_validados(self):
        """Fuera de rango → Invalid."""
        with pytest.raises(vol.Invalid):
            CONFIG_SCHEMA({**ENTRADA_F2, CONF_VENTANA_HISTORICO: 3})
        with pytest.raises(vol.Invalid):
            CONFIG_SCHEMA({**ENTRADA_F2, "eficiencia_round_trip": 60})
        with pytest.raises(vol.Invalid):
            CONFIG_SCHEMA({**ENTRADA_F2, "soc_max": 105})

    def test_valores_editables_aceptados(self):
        datos = CONFIG_SCHEMA(
            {
                **ENTRADA_F2,
                "precio_max_compra": "0.05",
                "potencia_contratada": "5800",
                "festivos_locales": "10-20, 12-24",
            }
        )
        assert datos["precio_max_compra"] == 0.05
        assert datos["potencia_contratada"] == 5800.0


def _flow_con_entry(entry_data: dict) -> ConfigFlow:
    """Instancia el flujo con mocks mínimos (sin hass real)."""
    flow = ConfigFlow.__new__(ConfigFlow)
    flow.hass = MagicMock()
    entry = MagicMock()
    entry.data = dict(entry_data)
    flow._get_reconfigure_entry = MagicMock(return_value=entry)
    flow.async_update_reload_and_abort = MagicMock(
        return_value={"type": "abort", "reason": "reconfigured"}
    )
    flow.async_show_form = MagicMock(
        return_value={"type": "form", "step_id": "reconfigure"}
    )
    flow.add_suggested_values_to_schema = MagicMock(
        return_value=CONFIG_SCHEMA
    )
    return flow


class TestReconfigureF3:
    @pytest.mark.asyncio
    async def test_reconfigure_guarda_festivos_como_lista(self):
        """El texto del formulario se persiste como lista "MM-DD"."""
        flow = _flow_con_entry(ENTRADA_F2)

        await flow.async_step_reconfigure(
            {**ENTRADA_F2, CONF_FESTIVOS_LOCALES: "10-20, 12-24"}
        )

        _, kwargs = flow.async_update_reload_and_abort.call_args
        guardados = kwargs["data_updates"]
        assert guardados[CONF_FESTIVOS_LOCALES] == ["10-20", "12-24"]

    @pytest.mark.asyncio
    async def test_reconfigure_actualiza_sin_recrear(self):
        """async_update_reload_and_abort con data_updates (no nueva entrada)."""
        flow = _flow_con_entry(ENTRADA_F2)

        await flow.async_step_reconfigure(
            {**ENTRADA_F2, "precio_max_compra": 0.05}
        )

        flow.async_update_reload_and_abort.assert_called_once()
        _, kwargs = flow.async_update_reload_and_abort.call_args
        assert kwargs["data_updates"]["precio_max_compra"] == 0.05

    @pytest.mark.asyncio
    async def test_reconfigure_form_formatea_festivos(self):
        """La lista existente se muestra como texto editable."""
        flow = _flow_con_entry(
            {**ENTRADA_F2, CONF_FESTIVOS_LOCALES: ["10-20"]}
        )

        await flow.async_step_reconfigure(None)

        # add_suggested_values_to_schema recibe festivos formateados
        schema_arg = flow.add_suggested_values_to_schema.call_args[0][1]
        assert schema_arg[CONF_FESTIVOS_LOCALES] == "10-20"
