"""Tests de force_mode / release_manual (F4, U6, D13)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.optimizador_bateria_omie.executor import (
    EjecutorControl,
)
from custom_components.optimizador_bateria_omie.mapa_registros import (
    MapaRegistros,
)
from custom_components.optimizador_bateria_omie.planificador import (
    ACCION_MANTENER,
    PlanTramo,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    TelemetriaInversor,
)

VOLTAJE = 51.2


def _tramo(accion: str) -> PlanTramo:
    return PlanTramo(
        fecha=datetime.now().astimezone().date(),
        tramo=10,
        hora="02:30",
        accion=accion,
        potencia_bateria_w=0.0,
        potencia_carga_red_w=2450.0,
        potencia_venta_red_w=0.0,
        soc_previsto_pct=50.0,
        precio_referencia_eur_kwh=0.08,
        ahorro_tramo_eur=0.0,
    )


@pytest.fixture
def hass(tmp_path):
    h = MagicMock()
    h.config.config_dir = str(tmp_path)
    h.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args: fn(*args)
    )
    return h


@pytest.fixture
def mapa(hass):
    m = MapaRegistros(hass)
    m.cargar()
    return m


@pytest.fixture
def escritor():
    w = MagicMock()
    w._leidos: dict[int, int] = {}

    async def _escribir(addr, valor):
        w._leidos[addr] = valor

    w.escribir = AsyncMock(side_effect=_escribir)
    w.leer = AsyncMock(
        side_effect=lambda addr, cantidad=1: [w._leidos.get(addr, 0)]
    )
    return w


@pytest.fixture(autouse=True)
def _sin_espera_verify():
    with patch(
        "custom_components.optimizador_bateria_omie.executor.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        yield


@pytest.fixture
def executor(hass, mapa, escritor):
    """Executor con tramo del plan = MANTENER y período valle."""
    entry = MagicMock()
    entry.data = {}
    coordinador = MagicMock()
    coordinador.tramo_actual = MagicMock(return_value=_tramo(ACCION_MANTENER))
    coordinador.data = TelemetriaInversor(
        soc=50.0,
        potencia_bateria=0.0,
        corriente_bateria=0.0,
        consumo_casa=300.0,
        produccion_fv=0.0,
        temperatura_inversor=30.0,
        temperatura_bateria=25.0,
        ts_actualizacion=datetime.now(tz=UTC),
    )
    coordinador.coordinador_precio.modelo_tarifa.obtener_periodo.return_value = (
        "valle"
    )
    return EjecutorControl(
        hass=hass,
        entry=entry,
        mapa=mapa,
        coordinador_plan=coordinador,
        coordinador_telemetria=coordinador,
        escritor=escritor,
    )


class TestForceMode:
    @pytest.mark.asyncio
    async def test_aplica_modo_manual_inmediato(self, executor, escritor):
        """El plan dice MANTENER; force_mode CARGA_RED se aplica ya."""
        executor._control_activo = True
        hasta = datetime.now(tz=UTC) + timedelta(hours=1)

        resultado = await executor.async_force_mode("CARGA_RED", hasta)

        assert resultado["modo_manual"] == "CARGA_RED"
        # CARGA_RED manual: 230 = potencia_max_carga en A, 232 = 1
        assert escritor._leidos[230] == round(5000.0 / VOLTAJE)
        assert escritor._leidos[232] == 1
        assert escritor._leidos[210] == round(5000.0 / VOLTAJE)

    @pytest.mark.asyncio
    async def test_expira_solo_en_until(self, executor, escritor):
        executor._control_activo = True
        await executor.async_force_mode(
            "CARGA_RED", datetime.now(tz=UTC) + timedelta(hours=1)
        )
        assert escritor._leidos[232] == 1

        # Antes de 'until' sigue el modo manual aunque el plan cambie
        await executor.async_aplicar_tramo("tramo")
        assert escritor._leidos[232] == 1

        # Tras 'until' vuelve al plan (MANTENER → sin cambios nuevos)
        executor._modo_manual = (
            "CARGA_RED",
            datetime.now(tz=UTC) - timedelta(minutes=1),
        )
        await executor.async_aplicar_tramo("tramo")
        assert executor.modo_manual is None

    @pytest.mark.asyncio
    async def test_release_manual_inmediato(self, executor, escritor):
        executor._control_activo = True
        await executor.async_force_mode(
            "CARGA_RED", datetime.now(tz=UTC) + timedelta(hours=1)
        )
        assert escritor._leidos[232] == 1

        resultado = await executor.async_release_manual()

        assert resultado["liberado"] is True
        assert executor.modo_manual is None

    @pytest.mark.asyncio
    async def test_until_pasado_rechazado(self, executor):
        with pytest.raises(ValueError, match="futuro"):
            await executor.async_force_mode(
                "CARGA_RED", datetime.now(tz=UTC) - timedelta(minutes=1)
            )

    @pytest.mark.asyncio
    async def test_accion_invalida_rechazada(self, executor):
        with pytest.raises(ValueError, match="no válida"):
            await executor.async_force_mode(
                "INVENTADA", datetime.now(tz=UTC) + timedelta(hours=1)
            )

    @pytest.mark.asyncio
    async def test_manual_respeta_salvaguardas(self, executor, escritor):
        """Modo manual con fallo activo → no escribe (salvaguarda)."""
        executor._control_activo = True
        estado = MagicMock()
        estado.state = "1"
        executor.hass.states.get = MagicMock(return_value=estado)
        executor.entry.data["entity_ids_fallo"] = "sensor.fault1"

        await executor.async_force_mode(
            "CARGA_RED", datetime.now(tz=UTC) + timedelta(hours=1)
        )

        escritor.escribir.assert_not_called()

    @pytest.mark.asyncio
    async def test_manual_respeta_gradual(self, executor, escritor):
        """En gradual, VENTA_RED manual se simula (dry_run parcial)."""
        executor._control_activo = True
        executor.coordinador_plan.coordinador_precio.modelo_tarifa \
            .obtener_periodo.return_value = "valle"

        await executor.async_force_mode(
            "VENTA_RED", datetime.now(tz=UTC) + timedelta(hours=1)
        )

        escritor.escribir.assert_not_called()
        assert executor.estado == "dry_run"

    def test_reinicio_sin_modo_manual(self, executor):
        """Un executor nuevo (reinicio de HA) arranca sin modo manual."""
        assert executor.modo_manual is None


class TestServiciosForceMode:
    """Núcleos testeables de services.py."""

    def _hass_con_executor(self, executor):
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entrada"
        hass.data = {
            "optimizador_bateria_omie": {
                "entrada": {"executor": executor}
            }
        }
        hass.config_entries.async_entries.return_value = [entry]
        return hass

    @pytest.mark.asyncio
    async def test_ejecutar_force_mode(self, executor):
        from custom_components.optimizador_bateria_omie.services import (
            ejecutar_force_mode,
        )
        hass = self._hass_con_executor(executor)

        resultado = await ejecutar_force_mode(
            hass,
            hass.config_entries.async_entries()[0],
            {
                "mode": "CARGA_RED",
                "until": (
                    datetime.now(tz=UTC) + timedelta(hours=1)
                ).isoformat(),
            },
        )
        assert resultado["modo_manual"] == "CARGA_RED"

    @pytest.mark.asyncio
    async def test_ejecutar_force_mode_until_invalido(self, executor):
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.optimizador_bateria_omie.services import (
            ejecutar_force_mode,
        )
        hass = self._hass_con_executor(executor)

        with pytest.raises(ServiceValidationError, match="ISO 8601"):
            await ejecutar_force_mode(
                hass,
                hass.config_entries.async_entries()[0],
                {"mode": "CARGA_RED", "until": "no-fecha"},
            )

    @pytest.mark.asyncio
    async def test_ejecutar_release_manual(self, executor):
        from custom_components.optimizador_bateria_omie.services import (
            ejecutar_release_manual,
        )
        hass = self._hass_con_executor(executor)

        resultado = await ejecutar_release_manual(
            hass, hass.config_entries.async_entries()[0]
        )
        assert resultado["liberado"] is False

    @pytest.mark.asyncio
    async def test_sin_executor_error(self):
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.optimizador_bateria_omie.services import (
            ejecutar_force_mode,
        )
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entrada"
        hass.data = {"optimizador_bateria_omie": {"entrada": {}}}

        with pytest.raises(ServiceValidationError, match="executor"):
            await ejecutar_force_mode(hass, entry, {})
