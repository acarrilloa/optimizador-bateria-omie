"""Tests del servicio resumen_diario (F6-U3)."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.optimizador_bateria_omie.const import DOMAIN
from custom_components.optimizador_bateria_omie.planificador import (
    ACCION_AUTOCONSUMO,
    ACCION_CARGA_RED,
    PlanTramo,
)
from custom_components.optimizador_bateria_omie.services import (
    _contar_escrituras_del_dia,
    ejecutar_resumen_diario,
)


def _tramo(accion: str, tramo: int) -> PlanTramo:
    hoy = datetime.now().astimezone().date()
    return PlanTramo(
        fecha=hoy,
        tramo=tramo,
        hora=f"{tramo // 4:02d}:{(tramo % 4) * 15:02d}",
        accion=accion,
        potencia_bateria_w=0.0,
        potencia_carga_red_w=0.0,
        potencia_venta_red_w=0.0,
        soc_previsto_pct=50.0,
        precio_referencia_eur_kwh=0.1,
        ahorro_tramo_eur=0.0,
    )


def _hass(tmp_path: Path) -> MagicMock:
    hass = MagicMock()
    hass.config.config_dir = str(tmp_path)
    hass.async_add_executor_job = AsyncMock(
        side_effect=lambda fn, *args: fn(*args)
    )
    return hass


def _datos_dominio(tmp_path: Path) -> dict:
    """Datos en hass.data con plan, auditoría, executor y telemetría."""
    plan = MagicMock()
    plan.tramos = [
        _tramo(ACCION_CARGA_RED, 0),
        _tramo(ACCION_CARGA_RED, 1),
        _tramo(ACCION_AUTOCONSUMO, 2),
    ]
    coordinador_plan = MagicMock()
    coordinador_plan.plan = plan
    coordinador_plan.ahorro_estimado_hoy = MagicMock(return_value=1.25)

    auditoria = MagicMock()
    auditoria.ahorro_real_hoy = 0.98
    auditoria.desglose = {"coste_real_eur": 2.0, "descarga_bateria_kwh": 3.5}

    ejecutor = MagicMock()
    ejecutor.control_activo = True
    ejecutor.estado = "gradual"
    ejecutor.estado_detalle = {
        "modo_activacion": "gradual",
        "control_activo": True,
    }

    telemetria = MagicMock()
    telemetria.data.soc = 63.0

    return {
        "coordinador_plan": coordinador_plan,
        "auditoria": auditoria,
        "executor": ejecutor,
        "coordinador_telemetria": telemetria,
    }


def _escribir_auditoria(tmp_path: Path) -> Path:
    ruta = tmp_path / DOMAIN / "auditoria_escrituras.jsonl"
    ruta.parent.mkdir(parents=True)
    ahora = datetime.now().astimezone()
    lineas = [
        # Dos escrituras reales de hoy
        {"ts": ahora.isoformat(), "accion": "CARGA_RED", "ejecutada": True},
        {"ts": ahora.isoformat(), "accion": "CARGA_RED", "ejecutada": True},
        # Una simulada de hoy (no cuenta)
        {"ts": ahora.isoformat(), "accion": "CARGA_RED", "ejecutada": False},
        # Una real de ayer (no cuenta)
        {
            "ts": (ahora - timedelta(days=1)).isoformat(),
            "accion": "VENTA_RED",
            "ejecutada": True,
        },
        # Línea corrupta (se salta)
        "{no es json",
    ]
    with open(ruta, "w", encoding="utf-8") as fichero:
        fichero.writelines(json.dumps(linea) + "\n" for linea in lineas[:-1])
        fichero.write(lineas[-1])
    return ruta


@pytest.mark.asyncio
async def test_resumen_diario(tmp_path):
    hass = _hass(tmp_path)
    hass.data = {DOMAIN: {"entry-1": _datos_dominio(tmp_path)}}
    _escribir_auditoria(tmp_path)
    entry = MagicMock()
    entry.entry_id = "entry-1"

    resumen = await ejecutar_resumen_diario(hass, entry)

    hoy = datetime.now().astimezone().date()
    assert resumen["fecha"] == hoy.isoformat()
    assert resumen["ahorro_estimado_eur"] == 1.25
    assert resumen["ahorro_efectivo_eur"] == 0.98
    assert resumen["tramos_hoy"] == {"CARGA_RED": 2, "AUTOCONSUMO": 1}
    assert resumen["escrituras_reales_hoy"] == 2
    assert resumen["escrituras_por_accion"] == {"CARGA_RED": 2}
    assert resumen["estado_control"] == "gradual"
    assert resumen["modo_activacion"] == "gradual"
    assert resumen["soc_pct"] == 63.0


@pytest.mark.asyncio
async def test_resumen_diario_sin_auditoria(tmp_path):
    """Sin fichero JSONL: cero escrituras, el resumen funciona igual."""
    hass = _hass(tmp_path)
    hass.data = {DOMAIN: {"entry-1": _datos_dominio(tmp_path)}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    resumen = await ejecutar_resumen_diario(hass, entry)

    assert resumen["escrituras_reales_hoy"] == 0
    assert resumen["escrituras_por_accion"] == {}


@pytest.mark.asyncio
async def test_resumen_diario_integracion_incompleta(tmp_path):
    hass = _hass(tmp_path)
    hass.data = {DOMAIN: {"entry-1": {}}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await ejecutar_resumen_diario(hass, entry)


def test_contar_escrituras_del_dia_unidad(tmp_path):
    """Filtro unitario: día local, solo ejecutadas, corrupción saltada."""
    ruta = _escribir_auditoria(tmp_path)
    hoy = datetime.now().astimezone().date()
    resultado = _contar_escrituras_del_dia(ruta, hoy)
    assert resultado == {"total": 2, "por_accion": {"CARGA_RED": 2}}

    ayer = datetime.now().astimezone().date() - timedelta(days=1)
    assert _contar_escrituras_del_dia(ruta, ayer)["total"] == 1


def test_contar_escrituras_ruta_inexistente(tmp_path):
    resultado = _contar_escrituras_del_dia(
        tmp_path / "no_existe.jsonl", datetime.now().astimezone().date()
    )
    assert resultado == {"total": 0, "por_accion": {}}
