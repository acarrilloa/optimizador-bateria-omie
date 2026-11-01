"""Servicios de la integración (F2: probe; F3: regenerate_plan; F4: force/release)."""
# versión 0.10.0

import logging
from datetime import UTC, datetime
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError

from custom_components.optimizador_bateria_omie.const import (
    ACCIONES_MANUALES,
    CONF_IP,
    CONF_PUERTO,
    CONF_SERIE,
    DEFAULT_IP,
    DEFAULT_PUERTO,
    DEFAULT_SERIE,
    DOMAIN,
    SERVICE_FORCE_MODE,
    SERVICE_PROBE_REGISTERS,
    SERVICE_REGENERATE_PLAN,
    SERVICE_RELEASE_MANUAL,
    TOLERANCIA_PROBE_PCT,
)
from custom_components.optimizador_bateria_omie.mapa_registros import (
    ErrorMapaRegistros,
    MapaRegistros,
)
from custom_components.optimizador_bateria_omie.solarman_client import (
    ClienteSolarman,
    ErrorComunicacionSolarman,
)

_LOGGER = logging.getLogger(__name__)

SCHEMA_PROBE = vol.Schema(
    {
        vol.Required("expected"): vol.Coerce(float),
        vol.Optional("register"): vol.All(
            int, vol.Range(min=1, max=9999)
        ),
        # La familia se valida contra el mapa de registros en tiempo de
        # ejecución (ejecutar_probe), nunca contra una lista fija.
        vol.Optional("familia"): str,
        vol.Optional("reset", default=False): bool,
    }
)


def _dentro_de_rango(umbrales: dict, clave: str, valor: float) -> bool:
    """Comprueba un valor contra el rango de plausibilidad del mapa."""
    rango = umbrales.get(clave) if umbrales else None
    if not rango or len(rango) != 2:
        # Sin umbral definido → no limita (M2 con umbrales del YAML)
        return True
    return rango[0] <= valor <= rango[1]


async def _plausible(
    cliente: ClienteSolarman,
    mapa: MapaRegistros,
    familia: str,
    soc: float,
) -> bool:
    """Plausibilidad (M2): SOC y potencia de batería dentro de umbrales."""
    umbrales = mapa.plausibility(familia)
    if not _dentro_de_rango(umbrales, "soc", soc):
        return False

    try:
        addr = mapa.direccion(familia, "potencia_bat")
        params = mapa.parametros(familia, "potencia_bat")
    except ErrorMapaRegistros:
        _LOGGER.warning(
            "TODO: familia %s sin dirección de potencia_bat; "
            "no se puede validar plausibilidad",
            familia,
        )
        return False

    crudo = (await cliente.leer_registros(addr, 1))[0]
    potencia = MapaRegistros.convertir(crudo, params)
    return _dentro_de_rango(umbrales, "potencia_bat", potencia)


async def _sondear(
    cliente: ClienteSolarman,
    mapa: MapaRegistros,
    familia: str,
    expected: float,
    register: int | None,
) -> dict:
    """Sondea una familia: tolerancia ±2 % (M1) + plausibilidad (M2)."""
    probe = mapa.probe(familia)
    addr = register if register is not None else probe["addr"]
    params = mapa.parametros(familia, probe["funcion"])

    resultado = {
        "leido": None,
        "esperado": expected,
        "desviacion_pct": None,
        "plausible": False,
        "resultado": "ERROR_COMUNICACION",
        "candidata": False,
    }

    try:
        crudo = (await cliente.leer_registros(addr, 1))[0]
        leido = MapaRegistros.convertir(crudo, params)
    except ErrorComunicacionSolarman as err:
        _LOGGER.warning(
            "probe_registers: familia %s sin respuesta Modbus: %s", familia, err
        )
        return resultado

    resultado["leido"] = leido

    if expected == 0:
        desviacion = 0.0 if leido == 0 else None
    else:
        desviacion = abs(leido - expected) / expected * 100
    resultado["desviacion_pct"] = desviacion

    if desviacion is None or desviacion > TOLERANCIA_PROBE_PCT:
        resultado["resultado"] = "FUERA_TOLERANCIA"
        _LOGGER.warning(
            "probe_registers: familia %s leyó %s (esperado %s, "
            "desviación %s %%)",
            familia, leido, expected, desviacion,
        )
        return resultado

    try:
        plausible = await _plausible(cliente, mapa, familia, leido)
    except ErrorComunicacionSolarman as err:
        _LOGGER.warning(
            "probe_registers: familia %s sin respuesta Modbus al leer "
            "la potencia: %s",
            familia, err,
        )
        resultado["resultado"] = "ERROR_COMUNICACION"
        return resultado

    resultado["plausible"] = plausible
    if not plausible:
        resultado["resultado"] = "IMPLAUSIBLE"
        _LOGGER.warning(
            "probe_registers: familia %s fuera de los umbrales de "
            "plausibilidad",
            familia,
        )
        return resultado

    resultado["resultado"] = "VALIDADA"
    resultado["candidata"] = True
    return resultado


def _obtener_cliente(hass, entry: ConfigEntry, datos_dominio: dict):
    """Cliente Modbus existente o uno temporal (ha_entities) para sondear."""
    coordinador = datos_dominio.get("coordinador_telemetria")
    if coordinador is not None and coordinador.cliente is not None:
        return coordinador.cliente
    return ClienteSolarman(
        hass,
        ip=entry.data.get(CONF_IP, DEFAULT_IP),
        puerto=entry.data.get(CONF_PUERTO, DEFAULT_PUERTO),
        serie=entry.data.get(CONF_SERIE, DEFAULT_SERIE),
        mapa=datos_dominio["mapa"],
    )


async def ejecutar_probe(hass, entry: ConfigEntry, datos_llamada: dict) -> dict:
    """Núcleo de probe_registers (testeable sin registrar el servicio).

    Con ``reset`` borra la validación y no sondea. Si no, sondea las
    familias (o solo la indicada), exige **exactamente una** candidata
    y persiste el resultado en el Store.
    """
    datos_dominio = hass.data[DOMAIN][entry.entry_id]
    mapa: MapaRegistros = datos_dominio["mapa"]
    gestor = datos_dominio["gestor"]
    coordinador = datos_dominio.get("coordinador_telemetria")

    if datos_llamada.get("reset", False):
        await gestor.async_borrar()
        if coordinador is not None:
            await coordinador.async_request_refresh()
        _LOGGER.info("probe_registers: validación de familia reseteada")
        return {"reset": True}

    expected = float(datos_llamada["expected"])
    register = datos_llamada.get("register")
    familia_pedida = datos_llamada.get("familia")
    if familia_pedida and familia_pedida not in mapa.familias():
        raise ServiceValidationError(
            f"Familia '{familia_pedida}' no existe en el mapa: "
            f"{', '.join(mapa.familias())}"
        )

    cliente = _obtener_cliente(hass, entry, datos_dominio)

    familias = [familia_pedida] if familia_pedida else mapa.familias()
    resultados = {}
    candidatas = []
    for familia in familias:
        resultado = await _sondear(
            cliente, mapa, familia, expected, register
        )
        resultados[familia] = resultado
        if resultado["candidata"]:
            candidatas.append(familia)

    familia_activa = None
    if len(candidatas) == 1:
        familia = candidatas[0]
        probe = mapa.probe(familia)
        await gestor.async_guardar(
            {
                "familia": familia,
                "probe_addr": probe["addr"],
                "probe_funcion": probe["funcion"],
                "ultima_validacion_ts": datetime.now(tz=UTC).isoformat(),
            }
        )
        familia_activa = familia
        _LOGGER.info("probe_registers: familia %s validada", familia)
    else:
        _LOGGER.warning(
            "probe_registers: %d familias candidatas (%s); "
            "no se valida ninguna",
            len(candidatas),
            ", ".join(candidatas) or "ninguna",
        )

    if coordinador is not None:
        await coordinador.async_request_refresh()

    return {
        "reset": False,
        "familias": resultados,
        "familia_activa": familia_activa,
    }


def async_setup_services(hass: HomeAssistant) -> None:
    """Registra los servicios de la integración (idempotente)."""
    if hass.services.has_service(DOMAIN, SERVICE_PROBE_REGISTERS):
        pass
    else:
        async def manejar_probe(llamada: ServiceCall) -> ServiceResponse:
            """Envoltorio del servicio: resuelve la entrada y delega."""
            entry = hass.config_entries.async_entries(DOMAIN)[0]
            return await ejecutar_probe(hass, entry, dict(llamada.data))

        hass.services.async_register(
            DOMAIN,
            SERVICE_PROBE_REGISTERS,
            manejar_probe,
            schema=SCHEMA_PROBE,
            supports_response=SupportsResponse.OPTIONAL,
        )
        _LOGGER.info(
            "Servicio %s.%s registrado", DOMAIN, SERVICE_PROBE_REGISTERS
        )

    if hass.services.has_service(DOMAIN, SERVICE_REGENERATE_PLAN):
        return

    async def manejar_regenerar(llamada: ServiceCall) -> ServiceResponse:
        """Regenera el plan de batería (dry-run, sin escrituras)."""
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        return await ejecutar_regenerate_plan(hass, entry)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REGENERATE_PLAN,
        manejar_regenerar,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    _LOGGER.info("Servicio %s.%s registrado", DOMAIN, SERVICE_REGENERATE_PLAN)

    if hass.services.has_service(DOMAIN, SERVICE_FORCE_MODE):
        return

    async def manejar_force_mode(llamada: ServiceCall) -> ServiceResponse:
        """Fuerza una acción manual hasta 'until' (con salvaguardas)."""
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        return await ejecutar_force_mode(hass, entry, dict(llamada.data))

    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_MODE,
        manejar_force_mode,
        schema=SCHEMA_FORCE_MODE,
        supports_response=SupportsResponse.OPTIONAL,
    )
    _LOGGER.info("Servicio %s.%s registrado", DOMAIN, SERVICE_FORCE_MODE)

    async def manejar_release_manual(llamada: ServiceCall) -> ServiceResponse:
        """Libera el modo manual y vuelve al plan."""
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        return await ejecutar_release_manual(hass, entry)

    hass.services.async_register(
        DOMAIN,
        SERVICE_RELEASE_MANUAL,
        manejar_release_manual,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    _LOGGER.info(
        "Servicio %s.%s registrado", DOMAIN, SERVICE_RELEASE_MANUAL
    )


async def ejecutar_regenerate_plan(hass, entry: ConfigEntry) -> dict:
    """Núcleo de regenerate_plan (D19, testeable sin registrar el servicio).

    Recalcula el plan inmediatamente y devuelve el resumen
    ``{recalculado, accion_actual, ahorro_estimado}``.
    """
    datos_dominio = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinador_plan = datos_dominio.get("coordinador_plan")
    if coordinador_plan is None:
        raise ServiceValidationError(
            "El coordinador del plan no está disponible: la integración "
            "debe estar cargada con F3 activo"
        )
    return await coordinador_plan.async_regenerar()


SCHEMA_FORCE_MODE = vol.Schema(
    {
        vol.Required("mode"): vol.In(list(ACCIONES_MANUALES)),
        vol.Required("until"): vol.All(str),
    }
)


def _parsear_until(valor: Any) -> datetime:
    """Convierte 'until' (ISO 8601) en datetime consciente."""
    try:
        hasta = datetime.fromisoformat(str(valor))
    except (TypeError, ValueError) as err:
        raise ServiceValidationError(
            f"'until' no es una fecha ISO 8601 válida: {valor!r}"
        ) from err
    if hasta.tzinfo is None:
        hasta = hasta.replace(tzinfo=UTC)
    return hasta


async def ejecutar_force_mode(hass, entry: ConfigEntry, datos: dict) -> dict:
    """Núcleo de force_mode (F4, D13, testeable sin registrar el servicio).

    Fuerza una acción manual hasta ``until``; el modo manual respeta
    todas las salvaguardas y expira solo al llegar ``until``.
    """
    datos_dominio = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    ejecutor = datos_dominio.get("executor")
    if ejecutor is None:
        raise ServiceValidationError(
            "El executor de control no está disponible (F4)"
        )
    hasta = _parsear_until(datos["until"])
    try:
        return await ejecutor.async_force_mode(datos["mode"], hasta)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err


async def ejecutar_release_manual(hass, entry: ConfigEntry) -> dict:
    """Núcleo de release_manual (F4, D13): libera el modo manual."""
    datos_dominio = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    ejecutor = datos_dominio.get("executor")
    if ejecutor is None:
        raise ServiceValidationError(
            "El executor de control no está disponible (F4)"
        )
    return await ejecutor.async_release_manual()
