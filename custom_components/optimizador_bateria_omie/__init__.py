"""Custom component para optimizar el uso de baterías con precios OMIE."""
# versión 0.12.0

import importlib
import logging
from datetime import UTC, datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from custom_components.optimizador_bateria_omie.auditoria import (
    AuditoriaAhorro,
)
from custom_components.optimizador_bateria_omie.cliente_omie import (
    ClienteOmie,
    ConfiguracionOmie,
)
from custom_components.optimizador_bateria_omie.const import (
    CONF_ENTITY_ID_CONSUMO_CASA,
    CONF_ENTITY_ID_PRODUCCION_FV,
    CONF_VENTANA_HISTORICO,
    DEFAULT_DIAS_CACHE,
    DEFAULT_FUENTE_PRIORITARIA,
    DEFAULT_IP,
    DEFAULT_PUERTO,
    DEFAULT_SERIE,
    DEFAULT_USAR_CACHE,
    DEFAULT_VENTANA_HISTORICO,
    DOMAIN,
    TELEMETRY_SOURCE_MODBUS,
)
from custom_components.optimizador_bateria_omie.coordinador import (
    CoordinadorDatosOmie,
)
from custom_components.optimizador_bateria_omie.coordinador_plan import (
    CoordinadorPlan,
)
from custom_components.optimizador_bateria_omie.executor import (
    EjecutorControl,
)
from custom_components.optimizador_bateria_omie.historico import (
    FuenteHistoricoRecorder,
)
from custom_components.optimizador_bateria_omie.mapa_registros import (
    MapaRegistros,
)
from custom_components.optimizador_bateria_omie.planificador import (
    ParametrosPlanner,
    PlanificadorGreedy,
)
from custom_components.optimizador_bateria_omie.predictor_consumo import (
    PredictorConsumo,
)
from custom_components.optimizador_bateria_omie.services import (
    async_setup_services,
)
from custom_components.optimizador_bateria_omie.solarman_client import (
    ClienteSolarman,
)
from custom_components.optimizador_bateria_omie.tarifa import (
    ConfiguracionTarifa,
    ModeloTarifa,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    CoordinadorTelemetria,
    GestorValidacion,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch", "number"]


def _cargar_plataformas() -> None:
    """Pre-importa los módulos de plataforma fuera del event loop.

    ``async_forward_entry_setups`` importa los módulos de plataforma con
    ``importlib.import_module``, que es una llamada bloqueante. Importándolos
    aquí (mediante ``hass.async_add_executor_job``) se evita la advertencia
    "Detected blocking call to import_module inside the event loop".
    """
    for plataforma in PLATFORMS:
        importlib.import_module(f"{__name__}.{plataforma}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configuración de la integración."""
    hass.data.setdefault(DOMAIN, {})

    # ── F2: mapa de registros y Store de validación de familia ──
    mapa = MapaRegistros(hass)
    await mapa.async_cargar()
    gestor = GestorValidacion(hass)
    await gestor.async_revalidar(mapa)

    # ── F1: coordinador de precios OMIE ──
    # Deuda #5 (F4): la tarifa se configura desde entry.data con los
    # defaults confirmados 2.0TD (la entrada de producción no cambia).
    config = ConfiguracionOmie(
        fuente_prioritaria=entry.data.get(
            "fuente_prioritaria", DEFAULT_FUENTE_PRIORITARIA
        ),
        usar_cache=entry.data.get("usar_cache", DEFAULT_USAR_CACHE),
        dias_cache=entry.data.get("dias_cache", DEFAULT_DIAS_CACHE),
    )
    modelo_tarifa = ModeloTarifa(
        ConfiguracionTarifa.desde_entry(entry)
    )
    coordinador_precio = CoordinadorDatosOmie(
        hass, ClienteOmie(hass, config), modelo_tarifa, entry
    )
    try:
        await coordinador_precio.async_config_entry_first_refresh()
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning(
            "Sin datos iniciales de precios: %s. Se reintentará.", e
        )

    # ── F2: telemetría del inversor ──
    cliente_solarman = None
    if entry.data.get("telemetry_source", TELEMETRY_SOURCE_MODBUS) == (
        TELEMETRY_SOURCE_MODBUS
    ):
        cliente_solarman = ClienteSolarman(
            hass,
            ip=entry.data.get("ip", DEFAULT_IP),
            puerto=entry.data.get("puerto", DEFAULT_PUERTO),
            serie=entry.data.get("serie", DEFAULT_SERIE),
            mapa=mapa,
        )
    coordinador_telemetria = CoordinadorTelemetria(
        hass, cliente_solarman, gestor, entry
    )
    auditoria = AuditoriaAhorro(
        hass, coordinador_precio, coordinador_telemetria, entry
    )
    await auditoria.async_cargar()

    @callback
    def _actualizar_auditoria():
        hass.async_create_task(
            auditoria.async_actualizar(datetime.now(tz=UTC))
        )

    cancelar_auditoria = coordinador_telemetria.async_add_listener(
        _actualizar_auditoria
    )

    # ── F3: predicción (perfil + Solcast) y planificador greedy ──
    fuente_consumo = FuenteHistoricoRecorder(
        hass, entry.data.get(CONF_ENTITY_ID_CONSUMO_CASA, "")
    )
    entity_id_fv = entry.data.get(CONF_ENTITY_ID_PRODUCCION_FV, "")
    fuente_fv = (
        FuenteHistoricoRecorder(hass, entity_id_fv) if entity_id_fv else None
    )
    predictor = PredictorConsumo(
        hass,
        fuente_consumo,
        fuente_fv,
        modelo_tarifa,
        dict(entry.data),
    )
    perfil_cargado = await predictor.async_cargar_perfil()
    ventana_configurada = int(
        entry.data.get(CONF_VENTANA_HISTORICO, DEFAULT_VENTANA_HISTORICO)
    )
    if (
        not perfil_cargado
        or not predictor.perfil.datos
        or predictor.perfil.ventana_dias != ventana_configurada
    ):
        # Sin perfil persistido, con datos antiguos o ventana cambiada
        # por reconfigure (M3: recálculo inmediato al recargar)
        try:
            await predictor.recalcular_perfil()
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("Recálculo del perfil falló: %s", e)

    # F5: modelo ML persistido; si no existe, reentrenamiento en
    # segundo plano (executor job, no bloquea el arranque)
    await predictor.async_cargar_ml()
    if not predictor.metricas_ml:
        hass.async_create_task(predictor.async_preparar_ml())

    planificador = PlanificadorGreedy(
        ParametrosPlanner.desde_entry(entry)
    )
    coordinador_plan = CoordinadorPlan(
        hass,
        entry,
        coordinador_precio,
        coordinador_telemetria,
        predictor,
        planificador,
    )
    await coordinador_plan.async_setup()

    # ── F4: executor de control (vía servicios de Solarman) ──
    # Con control_activo OFF (default) el executor solo simula: ninguna
    # escritura real al inversor hasta que el usuario lo habilite.
    ejecutor = EjecutorControl(
        hass,
        entry,
        mapa,
        coordinador_plan,
        coordinador_telemetria,
    )
    await ejecutor.async_setup()

    hass.data[DOMAIN][entry.entry_id] = {
        "mapa": mapa,
        "gestor": gestor,
        "coordinador_precio": coordinador_precio,
        "coordinador_telemetria": coordinador_telemetria,
        "predictor": predictor,
        "coordinador_plan": coordinador_plan,
        "executor": ejecutor,
        "auditoria": auditoria,
        "_cancelar_auditoria": cancelar_auditoria,
    }

    async_setup_services(hass)

    await hass.async_add_executor_job(_cargar_plataformas)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Desconfiguración de la integración."""
    datos = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if datos:
        if "coordinador_plan" in datos:
            await datos["coordinador_plan"].async_descargar()
        if "executor" in datos:
            await datos["executor"].async_descargar()
        if "_cancelar_auditoria" in datos:
            datos["_cancelar_auditoria"]()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
