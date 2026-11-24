"""Executor de control del inversor (F4) vía servicios de Solarman.

Aplica el plan de F3 escribiendo en los registros de control del Deye
con los servicios de la integración oficial Solarman. TODAS las
escrituras (individuales incluidas) van por función 16 —
``write_multiple_holding_registers`` — porque el datalogger Deye
responde ACKNOWLEDGE a la FC 6; la verificación relee con
``read_multiple_holding_registers`` (25.x, con detección en caliente
de los nombres legados). **Ninguna dirección vive aquí**: todas
salen de la sección ``control`` de ``registers.yaml``.

Salvaguardas (AGENTS §1 y PLAN_F4): sin ``control_activo=on`` no existe
escritura real (solo traducción y registro); las corrientes se recortan
SIEMPRE a los límites del YAML; el registro 210 (corriente de carga
desde cualquier fuente) se fija a la corriente equivalente de
``potencia_max_carga`` (valor constante entre tramos, nunca la corriente
de un tramo) para no estrangular la carga solar.

Watchdog (F5.3, D-F5.3.3): umbral a 4 × ``intervalo_telemetria_s`` y
``sin_telemetria`` solo tras 2 evaluaciones obsoletas consecutivas (el
jitter de Solarman ya no derriba el estado); las escrituras se cortan
desde la PRIMERA observación y el fallback solo llega tras 600 s de
caída continuada con carga activa.
"""
# versión 0.15.1

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_change

from custom_components.optimizador_bateria_omie.const import (
    ACCIONES_MANUALES,
    CONF_ACTUADOR_TOU,
    CONF_ARRANQUE_CARGA_RED_SOC,
    CONF_ARRANQUE_CARGA_RED_V,
    CONF_DESCARGA_MINIMA_BACKUP_A,
    CONF_DEVICE_ID_SOLARMAN,
    CONF_ENTITY_ID_POTENCIA_RED,
    CONF_ENTITY_IDS_FALLO,
    CONF_LATENCIA_VERIFY_S,
    CONF_MAX_ESCRITURAS_HORA,
    CONF_MAX_TABLAS_TOU_HORA,
    CONF_MODO_ACTIVACION,
    CONF_POTENCIA_CONTRATADA,
    CONF_POTENCIA_MAX_CARGA,
    CONF_POTENCIA_MAX_DESCARGA,
    CONF_SOC_MAX,
    CONF_SOC_MIN,
    CONF_SOC_RECUPERACION,
    CONF_SOC_STOP_CARGA_DEFAULT,
    CONF_VERIFY_TOLERANCIA,
    CONF_VOLTAJE_BATERIA_NOMINAL,
    DEBOUNCE_TELEMETRIA,
    DEFAULT_ACTUADOR_TOU,
    DEFAULT_ARRANQUE_CARGA_RED_SOC,
    DEFAULT_ARRANQUE_CARGA_RED_V,
    DEFAULT_DESCARGA_MINIMA_BACKUP_A,
    DEFAULT_DEVICE_ID_SOLARMAN,
    DEFAULT_LATENCIA_VERIFY_S,
    DEFAULT_MAX_ESCRITURAS_HORA,
    DEFAULT_MAX_TABLAS_TOU_HORA,
    DEFAULT_MODO_ACTIVACION,
    DEFAULT_POTENCIA_MAX_DESCARGA,
    DEFAULT_SOC_MAX,
    DEFAULT_SOC_MIN,
    DEFAULT_SOC_RECUPERACION,
    DEFAULT_SOC_STOP_CARGA_DEFAULT,
    DEFAULT_VERIFY_TOLERANCIA,
    DEFAULT_VOLTAJE_BATERIA_NOMINAL,
    DOMAIN,
    ESTADO_CONTROL_ACTIVO,
    ESTADO_CONTROL_DRY_RUN,
    ESTADO_CONTROL_FALLBACK_BLOQUEADO,
    ESTADO_CONTROL_GRADUAL,
    ESTADO_CONTROL_INACTIVO,
    ESTADO_CONTROL_SIN_TELEMETRIA,
    FALLOS_VERIFY_FALLBACK,
    GUARD_CORTE,
    GUARD_REDUCCION,
    MULTIPLICADOR_WATCHDOG,
    TIMEOUT_TELEMETRIA_S,
    TOU_FLAG_CARGA,
    TOU_FLAG_VENTA,
    TOU_VSTOP_DEFAULT,
)
from custom_components.optimizador_bateria_omie.const import (
    MODO_ACTIVACION_COMPLETO as COMPLETO,
)
from custom_components.optimizador_bateria_omie.const import (
    MODO_ACTIVACION_GRADUAL as GRADUAL,
)
from custom_components.optimizador_bateria_omie.coordinador_plan import (
    CoordinadorPlan,
)
from custom_components.optimizador_bateria_omie.mapa_registros import (
    MapaRegistros,
)
from custom_components.optimizador_bateria_omie.planificador import (
    ACCION_AUTOCONSUMO,
    ACCION_CARGA_RED,
    ACCION_MANTENER,
    ACCION_VENTA_RED,
    PlanTramo,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    CoordinadorTelemetria,
)

_LOGGER = logging.getLogger(__name__)

# Servicios de la integración Solarman (payload aislado aquí). La
# integración renombró sus servicios en 25.x (confirmado en la
# primera activación real): la generación ACTUAL es
# write_multiple_holding_registers / read_multiple_holding_registers
# (campo ``count``); la LEGADA era write_multiple_registers /
# read_holding_registers (campo ``quantity``). Se detecta en caliente.
# Las escrituras individuales (FC 6, write_holding_register) NO se
# usan: el datalogger Deye las rechaza con ACKNOWLEDGE (0.11.5).
# ESCRITURA: campos ``register`` + ``values`` (verificado con
# escrituras reales aterrizadas; funciona en ambas generaciones).
# LECTURA: campos ``address`` + ``count`` en ambas generaciones. El
# esquema ``register`` + ``quantity`` pasa la validación del servicio
# pero la llamada no retorna nunca (cuelga sin excepción; incidente
# 20-nov-2026, ver docs/nota_parche_verify_livelock.md).
DOMINIO_SOLARMAN = "solarman"
SERVICIO_ESCRIBIR_BLOQUE_ACTUAL = "write_multiple_holding_registers"
SERVICIO_ESCRIBIR_BLOQUE_LEGADO = "write_multiple_registers"
SERVICIO_LEER_ACTUAL = "read_multiple_holding_registers"
SERVICIO_LEER_LEGADO = "read_holding_registers"
CAMPO_DEVICE = "device"
CAMPO_REGISTRO = "register"
CAMPO_DIRECCION = "address"
CAMPO_VALORES = "values"
CAMPO_CANTIDAD = "count"
# Blindaje de cuelgues del servicio (incidente 20-nov-2026): toda
# llamada a Solarman se envuelve en asyncio.wait_for con este margen.
SERVICIO_TIMEOUT_S = 15.0
# Cooldown del rearme de los puntos de arranque (F5.2, adenda
# 229-rebote): no reescribir el mismo registro más de una vez por hora
# aunque siga derivando (el 229 reaparece en 90 poco después de
# escribirlo; la carga funciona igual mientras SOC < objetivo).
REARME_ARRANQUE_COOLDOWN_S = 3600.0
# Vigilante ToU v2 (F6/A): cooldown de las reparaciones preventivas
# de la tabla (la nube Solarman la pisó una vez: borrado de 250-279
# el 22-nov-2026). Un ciclo por hora es suficiente porque la tabla
# también se reescribe en cada tramo aplicado si deriva.
VIGILANTE_TOU_COOLDOWN_S = 3600.0


class ErrorEscrituraSolarman(Exception):
    """Fallo al invocar un servicio de escritura/lectura de Solarman."""


class EscritorSolarman:
    """Envoltorio de los servicios de escritura/lectura de Solarman.

    Único punto del componente que llama a ``hass.services`` del dominio
    ``solarman``: si el payload de la integración cambia, se ajusta aquí.
    El dispositivo se resuelve con ``device_id_solarman`` si está
    configurado o, si no, con el **primer device** de la integración
    Solarman; si no se puede resolver, error explícito (nunca se
    inventa).

    Detecta en caliente (0.11.4) la generación de servicios instalada
    (renombrados en Solarman 25.x) y cachea la elección.
    """

    def __init__(self, hass: HomeAssistant, device_id: str = "") -> None:
        """Inicializa el escritor con el device_id opcional."""
        self.hass = hass
        self._device_id = device_id or DEFAULT_DEVICE_ID_SOLARMAN
        self._device_resuelto: str | None = None
        self._servicios: dict[str, str] = {}

    def _servicio(self, tipo: str, actual: str, legado: str) -> str:
        """Nombre del servicio según la generación de la integración."""
        if tipo not in self._servicios:
            if self.hass.services.has_service(DOMINIO_SOLARMAN, actual):
                self._servicios[tipo] = actual
            elif self.hass.services.has_service(DOMINIO_SOLARMAN, legado):
                self._servicios[tipo] = legado
                _LOGGER.info(
                    "Integración Solarman con servicios legados: usando "
                    "%s en vez de %s", legado, actual,
                )
            else:
                raise ErrorEscrituraSolarman(
                    f"La integración {DOMINIO_SOLARMAN} no expone ni "
                    f"'{actual}' ni '{legado}': confirma que está "
                    "instalada y actualizada"
                )
        return self._servicios[tipo]

    async def _resolver_device(self) -> str:
        """Device del inversor: configurado o primero de la integración."""
        if self._device_id:
            return self._device_id
        if self._device_resuelto:
            return self._device_resuelto
        # pylint: disable=import-outside-toplevel
        from homeassistant.helpers import device_registry as dr

        registro = dr.async_get(self.hass)
        # device_registry.devices como mapping (p. ej. ``.values()``) está
        # deprecated (HA 2027.9); iterar da directamente los DeviceEntry
        for dispositivo in registro.devices:
            if any(
                identificador[0] == DOMINIO_SOLARMAN
                for identificador in dispositivo.identifiers
            ):
                self._device_resuelto = dispositivo.id
                _LOGGER.info(
                    "Device Solarman resuelto automáticamente: %s",
                    dispositivo.id,
                )
                return dispositivo.id
        raise ErrorEscrituraSolarman(
            "No se pudo resolver el dispositivo de la integración "
            "Solarman: configura device_id_solarman en el reconfigure"
        )

    async def _invocar(
        self,
        servicio: str,
        datos: dict[str, Any],
        con_respuesta: bool = False,
    ) -> Any:
        """Invoca un servicio de Solarman con el dispositivo resuelto.

        Blindaje de cuelgues (incidente 20-nov-2026): toda llamada se
        envuelve en ``asyncio.wait_for`` (SERVICIO_TIMEOUT_S). Un
        servicio que no retorna (p. ej. lectura con un esquema de
        campos que el datalogger no atiende) aborta con un error
        audible en vez de colgar el executor sin rastro en la
        auditoría.
        """
        carga = dict(datos)
        carga[CAMPO_DEVICE] = await self._resolver_device()
        try:
            if con_respuesta:
                return await asyncio.wait_for(
                    self.hass.services.async_call(
                        DOMINIO_SOLARMAN,
                        servicio,
                        carga,
                        blocking=True,
                        return_response=True,
                    ),
                    timeout=SERVICIO_TIMEOUT_S,
                )
            await asyncio.wait_for(
                self.hass.services.async_call(
                    DOMINIO_SOLARMAN, servicio, carga, blocking=True
                ),
                timeout=SERVICIO_TIMEOUT_S,
            )
            return None
        except TimeoutError as err:
            raise ErrorEscrituraSolarman(
                f"El servicio {DOMINIO_SOLARMAN}.{servicio} no respondió "
                f"en {SERVICIO_TIMEOUT_S:.0f} s (posible cuelgue)"
            ) from err
        except HomeAssistantError as err:
            raise ErrorEscrituraSolarman(
                f"El servicio {DOMINIO_SOLARMAN}.{servicio} falló: {err}"
            ) from err

    async def escribir(self, addr: int, valor: int) -> None:
        """Escribe un registro individual vía bloque de un valor (FC 16).

        El datalogger de Deye responde con ACKNOWLEDGE (excepción
        Modbus 5, "long duration of time will be required") a la
        escritura individual (FC 6); la escritura por bloques con un
        solo registro es la vía que acepta (0.11.5). El propio
        services.yaml de la integración Solarman recomienda FC 16
        cuando el dispositivo rechaza FC 6.
        """
        await self.escribir_bloque(addr, [valor])

    async def escribir_bloque(
        self, addr: int, valores: list[int]
    ) -> None:
        """Escribe un bloque contiguo de registros."""
        if not valores:
            return
        await self._invocar(
            self._servicio(
                "escribir_bloque",
                SERVICIO_ESCRIBIR_BLOQUE_ACTUAL,
                SERVICIO_ESCRIBIR_BLOQUE_LEGADO,
            ),
            {
                CAMPO_REGISTRO: int(addr),
                CAMPO_VALORES: [int(v) for v in valores],
            },
        )

    async def leer(self, addr: int, cantidad: int = 1) -> list[int]:
        """Lee registros holding vía servicio (relectura de verificación).

        Ambas generaciones se llaman con ``address`` + ``count``: es
        el esquema que responde. El par ``register`` + ``quantity``
        pasa la validación del servicio pero la llamada no retorna
        nunca (cuelga sin excepción; incidente 20-nov-2026, ver
        docs/nota_parche_verify_livelock.md).
        """
        servicio = self._servicio(
            "leer", SERVICIO_LEER_ACTUAL, SERVICIO_LEER_LEGADO
        )
        respuesta = await self._invocar(
            servicio,
            {CAMPO_DIRECCION: int(addr), CAMPO_CANTIDAD: int(cantidad)},
            con_respuesta=True,
        )
        return _extraer_registros(respuesta, cantidad)


def _extraer_registros(respuesta: Any, cantidad: int) -> list[int]:
    """Normaliza la respuesta del servicio de lectura a lista de enteros.

    Aísla el formato de respuesta concreto de la integración Solarman
    (lista, ``{"data": [...]}``, ``{"registers": [...]}``).
    """
    if respuesta is None:
        raise ErrorEscrituraSolarman("Respuesta vacía del servicio de lectura")
    if isinstance(respuesta, list):
        return [int(v) for v in respuesta[:cantidad]]
    if isinstance(respuesta, dict):
        for clave in ("data", "registers", "valores", "result"):
            if clave in respuesta and respuesta[clave] is not None:
                return _extraer_registros(respuesta[clave], cantidad)
        # dict registro → valor (una sola lectura)
        if all(isinstance(v, (int, float)) for v in respuesta.values()):
            return [int(v) for v in list(respuesta.values())[:cantidad]]
    raise ErrorEscrituraSolarman(
        f"Formato de respuesta no reconocido: {respuesta!r}"
    )


class EjecutorControl:
    """Aplica el plan al inversor con todas las salvaguardas (F4)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry,
        mapa: MapaRegistros,
        coordinador_plan: CoordinadorPlan,
        coordinador_telemetria: CoordinadorTelemetria,
        escritor: EscritorSolarman | None = None,
    ) -> None:
        """Inicializa el executor; ``escritor`` es inyectable en tests."""
        self.hass = hass
        self.entry = entry
        self.mapa = mapa
        self.coordinador_plan = coordinador_plan
        self.coordinador_telemetria = coordinador_telemetria
        self._escritor = escritor or EscritorSolarman(
            hass, entry.data.get(CONF_DEVICE_ID_SOLARMAN, "")
        )
        self._control_activo = False
        self._estado = ESTADO_CONTROL_INACTIVO
        self._motivo_estado = "arranque"
        self._estado_simulado: str | None = None
        self._ultimo_watchdog_ok = True
        self._watchdog_caidas = 0
        self._ts_watchdog_caida: datetime | None = None
        self._guard_actual: str | None = None
        self._modo_manual: tuple[str, datetime] | None = None
        self._ultimo_aplicado: dict[int, int] = {}
        self._escrituras_ts: list[datetime] = []
        # F5.2: límite de exportación (245, caché 1 h) y cooldown de
        # rearme de los puntos de arranque 228/229 (adenda 229-rebote)
        self._max_exportacion_w: int | None = None
        self._max_exportacion_ts: datetime | None = None
        self._ts_rearme: dict[int, datetime] = {}
        # F6/A: tabla ToU como actuador
        self._tabla_tou_aplicada: dict[int, int] | None = None
        self._tabla_tou_objetivo: dict[int, int] | None = None
        self._tablas_ts: list[datetime] = []
        self._ts_vigilante: datetime | None = None
        self._cancelar_vigilante: Callable[[], None] | None = None
        self._fallos_consecutivos = 0
        self._aviso_fallo_emitido = False
        self._listeners: list[Callable[[], None]] = []
        self._cancelar_plan: Callable[[], None] | None = None

    # ─── Registro de entidades ───

    def async_registrar_callback(
        self, callback_fn: Callable[[], None]
    ) -> Callable[[], None]:
        """Registra un callback que se invoca al cambiar el estado."""
        self._listeners.append(callback_fn)

        def _cancelar() -> None:
            if callback_fn in self._listeners:
                self._listeners.remove(callback_fn)

        return _cancelar

    def _notificar(self) -> None:
        """Notifica a las entidades de control un cambio de estado."""
        for listener in list(self._listeners):
            listener()

    async def async_setup(self) -> None:
        """Suscribe el executor a los replanificados del CoordinadorPlan."""
        self._cancelar_plan = (
            self.coordinador_plan.async_registrar_callback(
                self._async_plan_cambiado
            )
        )
        # Vigilante ToU v2 (F6/A): en cada cambio de tramo, releer la
        # tabla del inversor y repararla si la nube/gateway la pisó
        if self._actuador_tou():
            self._cancelar_vigilante = async_track_time_change(
                self.hass,
                self._async_vigilante_tou,
                minute=range(0, 60, 15),
                second=0,
            )
        await self.async_aplicar_tramo("arranque")

    def _async_plan_cambiado(self) -> None:
        """El plan cambió: aplicar el tramo en una tarea."""
        self.hass.async_create_task(self.async_aplicar_tramo("plan"))

    async def async_descargar(self) -> None:
        """Cancela la suscripción al plan al descargar la entrada."""
        if self._cancelar_plan is not None:
            self._cancelar_plan()
            self._cancelar_plan = None
        if self._cancelar_vigilante is not None:
            self._cancelar_vigilante()
            self._cancelar_vigilante = None
        self._listeners.clear()

    # ─── Parámetros ───

    def _param(self, clave: str, default: Any) -> Any:
        """Parámetro de entry.data con default (nunca rompe la entrada)."""
        return self.entry.data.get(clave, default)

    def _voltaje_nominal(self) -> float:
        """Tensión nominal de batería para la conversión W → A."""
        return float(
            self._param(CONF_VOLTAJE_BATERIA_NOMINAL,
                         DEFAULT_VOLTAJE_BATERIA_NOMINAL)
        )

    def _limites(self, funcion: str) -> tuple[int, int]:
        """Límites [min, max] de un registro de control del YAML."""
        params = self.mapa.parametros("estandar", funcion, "control")
        return int(params["min"]), int(params["max"])

    def _recortar_corriente(self, funcion: str, amperios: float) -> int:
        """Corriente recortada a los límites del YAML (nunca fuera)."""
        minimo, maximo = self._limites(funcion)
        return round(min(max(amperios, minimo), maximo))

    def _corriente_limite_carga(self) -> int:
        """Corriente equivalente a potencia_max_carga (registro 210).

        El 210 limita la carga de batería desde **cualquier fuente**
        (PV + red): se fija a este valor constante entre tramos y nunca
        se baja a la corriente de un tramo concreto (corrección D3).
        """
        potencia = float(
            self._param(CONF_POTENCIA_MAX_CARGA, 5000.0)
        )
        return self._recortar_corriente(
            "corriente_carga", potencia / self._voltaje_nominal()
        )

    # ─── Traducción acción → registros ───

    def _objetivos_tramo(self, tramo: PlanTramo) -> dict[int, int]:
        """Registros objetivo de un tramo del plan (direcciones del YAML).

        MANTENER no escribe. AUTOCONSUMO deja 210 en su valor constante
        y deshabilita la carga desde red (valores seguros). CARGA_RED
        habilita la carga desde red con la corriente del tramo (230) y
        210 en su valor constante. VENTA_RED fija la corriente de
        descarga (211) y deshabilita la carga desde red.
        """
        addr_carga = self.mapa.direccion(
            "estandar", "corriente_carga", "control"
        )
        addr_descarga = self.mapa.direccion(
            "estandar", "corriente_descarga", "control"
        )
        addr_red = self.mapa.direccion(
            "estandar", "corriente_carga_red", "control"
        )
        addr_habilitar = self.mapa.direccion(
            "estandar", "habilitar_carga_red", "control"
        )

        limite_carga = self._corriente_limite_carga()
        voltaje = self._voltaje_nominal()

        if tramo.accion == ACCION_MANTENER:
            return {}
        if tramo.accion == ACCION_AUTOCONSUMO:
            return {
                addr_carga: limite_carga,
                addr_red: 0,
                addr_habilitar: 0,
            }
        if tramo.accion == ACCION_CARGA_RED:
            corriente = self._recortar_corriente(
                "corriente_carga_red", tramo.potencia_carga_red_w / voltaje
            )
            objetivos = {
                addr_carga: limite_carga,
                addr_red: corriente,
                addr_habilitar: 1,
            }
            # F6/A: receta D1 confirmada como default (230=corriente,
            # 211=0): sin descarga programada durante la carga. El
            # parámetro opcional `descarga_minima_backup_a` (>0) da
            # respaldo ante corte nocturno de red si D1 demuestra que
            # la carga a plena potencia funciona igual con 211≠0.
            if self._actuador_tou():
                backup = int(
                    self._param(
                        CONF_DESCARGA_MINIMA_BACKUP_A,
                        DEFAULT_DESCARGA_MINIMA_BACKUP_A,
                    )
                )
                objetivos[addr_descarga] = self._recortar_corriente(
                    "corriente_descarga", backup
                )
            return objetivos
        if tramo.accion == ACCION_VENTA_RED:
            corriente = self._recortar_corriente(
                "corriente_descarga",
                abs(tramo.potencia_venta_red_w) / voltaje,
            )
            # F5.2: el inversor recorta físicamente la exportación al
            # límite del registro 245; capar aquí (a la baja: int())
            # evita tramos de venta inalcanzables (p. ej. plan de
            # 5193 W con 245=5000).
            if self._max_exportacion_w is not None:
                corriente = min(
                    corriente,
                    int(self._max_exportacion_w / voltaje),
                )
            return {
                addr_carga: limite_carga,
                addr_descarga: corriente,
                addr_red: 0,
                addr_habilitar: 0,
            }
        _LOGGER.warning("Acción desconocida en el plan: %s", tramo.accion)
        return {}

    def _filtrar_idempotente(self, objetivos: dict[int, int]) -> dict[int, int]:
        """Descarta escrituras cuyo valor ya está aplicado (D8)."""
        return {
            addr: valor
            for addr, valor in objetivos.items()
            if self._ultimo_aplicado.get(addr) != valor
        }

    # ─── F5.2: rearme de arranque y cap de exportación (21-nov-2026) ───

    async def _asegurar_arranque_carga(
        self, tramo: PlanTramo, motivo: str
    ) -> bool:
        """Sanea los puntos de arranque de carga desde red (228/229).

        El Deye combina los puntos de arranque como AND: la carga desde
        red solo arranca si el voltaje de batería es menor que 228 Y el
        SOC menor que 229. Con 228 de fábrica (4900 = 49 V) una batería
        a ~52 V nunca pasa el umbral: el inversor queda en "carga
        pendiente", sin cargar y además sin descargar. Como el
        datalogger Solarman re-empuja configuración tras reinicios de
        HA (incidente 21-nov-2026: ToU reactivado, 228→4900, 229→90),
        antes de armar una CARGA_RED se releen 228/229 y solo se
        reescriben si derivaron del objetivo (las lecturas no consumen
        cuota del rate limiter).

        Cooldown por registro (adenda 229-rebote): nunca reescribir el
        mismo registro más de una vez por hora aunque siga derivando;
        con el cooldown activo se audita ``rearme_enfriamiento`` y el
        tramo continúa (la carga funciona igual mientras SOC <
        objetivo). Devuelve False solo si no se pudo garantizar el
        estado: abortar el tramo es más seguro que armar un 232=1 con
        los puntos de arranque bloqueados (batería congelada a 0 W).
        """
        addr_v = self.mapa.direccion(
            "estandar", "arranque_carga_red_v", "control"
        )
        addr_soc = self.mapa.direccion(
            "estandar", "arranque_carga_red_soc", "control"
        )
        objetivos = {
            addr_v: int(self._param(
                CONF_ARRANQUE_CARGA_RED_V, DEFAULT_ARRANQUE_CARGA_RED_V,
            )),
            addr_soc: int(self._param(
                CONF_ARRANQUE_CARGA_RED_SOC, DEFAULT_ARRANQUE_CARGA_RED_SOC,
            )),
        }
        for addr, esperado in objetivos.items():
            try:
                leido = (await self._escritor.leer(addr, 1))[0]
            except ErrorEscrituraSolarman as err:
                _LOGGER.warning(
                    "Rearme arranque: no se pudo leer %d (%s); tramo abortado",
                    addr, err,
                )
                return False
            if leido == esperado:
                continue
            ahora = datetime.now(tz=UTC)
            ultimo = self._ts_rearme.get(addr)
            if (
                ultimo is not None
                and (ahora - ultimo).total_seconds()
                < REARME_ARRANQUE_COOLDOWN_S
            ):
                _LOGGER.warning(
                    "Punto de arranque %d=%d deriva del objetivo %d "
                    "(rearme en enfriamiento; se continúa, F5.2)",
                    addr, leido, esperado,
                )
                await self._auditar(
                    tramo, {addr: esperado}, ejecutada=False,
                    verificado="rearme_enfriamiento",
                    motivo=f"{motivo}: rearme arranque (leído {leido})",
                )
                continue
            _LOGGER.warning(
                "Punto de arranque %d=%d deriva del objetivo %d: "
                "reescribiendo (F5.2)", addr, leido, esperado,
            )
            inicio = datetime.now(tz=UTC)
            try:
                await self._escritor.escribir(addr, esperado)
            except ErrorEscrituraSolarman as err:
                _LOGGER.error(
                    "Error escribiendo el punto de arranque %d=%d: %s",
                    addr, esperado, err,
                )
                await self._auditar(
                    tramo, {addr: esperado}, ejecutada=False,
                    verificado="error_escritura",
                    motivo=f"{motivo}: rearme arranque: {err}",
                )
                return False
            self._escrituras_ts.append(datetime.now(tz=UTC))
            self._ts_rearme[addr] = ahora
            verificado = await self._verificar(addr, esperado)
            latencia = (datetime.now(tz=UTC) - inicio).total_seconds()
            await self._auditar(
                tramo, {addr: esperado}, ejecutada=True,
                verificado=verificado, latencia_s=latencia,
                motivo=f"{motivo}: rearme arranque (leído {leido})",
            )
            self._ultimo_aplicado[addr] = esperado
        return True

    async def _refrescar_max_exportacion(self) -> None:
        """F5.2: lee el límite de exportación a red (245) con caché 1 h.

        Sin dato el cap queda inactivo (no se inventa); la lectura no
        consume cuota del rate limiter.
        """
        ahora = datetime.now(tz=UTC)
        if (
            self._max_exportacion_ts is not None
            and (ahora - self._max_exportacion_ts).total_seconds() < 3600
        ):
            return
        addr = self.mapa.direccion(
            "estandar", "max_exportacion_red", "control"
        )
        try:
            leido = (await self._escritor.leer(addr, 1))[0]
        except ErrorEscrituraSolarman as err:
            _LOGGER.warning(
                "No se pudo leer el límite de exportación 245: %s", err
            )
            return
        if leido > 0:
            self._max_exportacion_w = int(leido)
            _LOGGER.info(
                "Límite de exportación a red (245): %d W", leido
            )
        self._max_exportacion_ts = ahora

    # ─── F6/A: tabla ToU como actuador (24-nov-2026) ───

    def _actuador_tou(self) -> bool:
        """Modo actuador ToU (True) o esquema v0.14.x (False)."""
        return bool(
            self._param(CONF_ACTUADOR_TOU, DEFAULT_ACTUADOR_TOU)
        )

    def _direcciones_tabla_tou(self) -> list[int]:
        """Direcciones de la tabla 250-279, ordenadas por campo y punto."""
        return [
            self.mapa.direccion("estandar", f"tou_{campo}_{i}", "control")
            for campo in ("hora", "potencia", "vstop", "soc_stop", "flag")
            for i in range(1, 7)
        ]

    def _clamp_tou(self, funcion: str, valor: int) -> int:
        """Valor recortado a los límites del YAML (igual que D3)."""
        minimo, maximo = self._limites(funcion)
        return int(min(max(valor, minimo), maximo))

    def _soc_stop_carga(self) -> int:
        """SOC-stop del segmento de carga (config o soc_max)."""
        parado = self._param(
            CONF_SOC_STOP_CARGA_DEFAULT, DEFAULT_SOC_STOP_CARGA_DEFAULT
        )
        if parado is None:
            parado = self._param(CONF_SOC_MAX, DEFAULT_SOC_MAX)
        return int(min(max(float(parado), 1), 100))

    def _segmento_tou(
        self,
        tramo: PlanTramo,
        hasta_manual: str | None = None,
    ) -> dict[str, int] | None:
        """Atributos del segmento ToU de la acción del tramo (o None).

        El segmento abarca el **run completo** de tramos consecutivos
        con la misma acción alrededor del actual (con la potencia
        máxima del run: la corriente fina la modula el 230/211 en cada
        tramo, la tabla solo delimita la ventana). En modo manual la
        ventana es [ahora, hasta]. Fuera de medianoche: el fin se
        recorta a 23:59 (HHMM no admite 2400 y un cruce de día en un
        solo Programa 1 es ambiguo).
        """
        if tramo.accion not in (ACCION_CARGA_RED, ACCION_VENTA_RED):
            return None
        es_carga = tramo.accion == ACCION_CARGA_RED
        flag = TOU_FLAG_CARGA if es_carga else TOU_FLAG_VENTA
        soc_stop = (
            self._soc_stop_carga()
            if es_carga
            else int(float(self._param(CONF_SOC_MIN, DEFAULT_SOC_MIN)))
        )
        if hasta_manual is not None:
            ahora = datetime.now().astimezone()
            final = datetime.fromisoformat(hasta_manual)
            hora_inicio = ahora.hour * 100 + ahora.minute
            hora_fin = final.hour * 100 + final.minute
            potencia = (
                tramo.potencia_carga_red_w
                if es_carga
                else abs(tramo.potencia_venta_red_w)
            )
            return {
                "hora_inicio": hora_inicio,
                "hora_fin": hora_fin,
                "potencia": int(max(potencia, 0.0)),
                "soc_stop": soc_stop,
                "flag": flag,
            }
        run = [tramo]
        plan = self.coordinador_plan.plan
        tramos = getattr(plan, "tramos", None)
        if isinstance(tramos, list) and tramos:
            posicion = next(
                (
                    i
                    for i, t in enumerate(tramos)
                    if t.fecha == tramo.fecha and t.tramo == tramo.tramo
                ),
                None,
            )
            if posicion is not None:
                # Extender el run solo dentro del mismo día: un run que
                # cruza medianoche genera hora_fin inválida (< inicio)
                atras = posicion
                while atras > 0 and (
                    tramos[atras - 1].fecha == tramo.fecha
                    and tramos[atras - 1].accion == tramo.accion
                ):
                    atras -= 1
                adelante = posicion
                while adelante + 1 < len(tramos) and (
                    tramos[adelante + 1].fecha == tramo.fecha
                    and tramos[adelante + 1].accion == tramo.accion
                ):
                    adelante += 1
                run = tramos[atras : adelante + 1]
        primer_hhmm = run[0].hora.split(":")
        final_idx = run[-1].tramo
        fin_min = min(final_idx * 15 + 15, 1439)  # no cruza medianoche
        potencias = [
            t.potencia_carga_red_w if es_carga else abs(t.potencia_venta_red_w)
            for t in run
        ]
        return {
            "hora_inicio": int(primer_hhmm[0]) * 100 + int(primer_hhmm[1]),
            "hora_fin": (fin_min // 60) * 100 + (fin_min % 60),
            "potencia": int(max(max(potencias), 0.0)),
            "soc_stop": soc_stop,
            "flag": flag,
        }

    def _objetivos_tabla_tou(
        self,
        tramo: PlanTramo,
        hasta_manual: str | None = None,
    ) -> dict[int, int]:
        """Tabla 250-279 objetivo del tramo (24 registros, YAML).

        Usa el punto 1 (con el punto 2 como marcador de fin; sus
        campos van a 0) y deja el resto de puntos desactivados: si la
        nube o el ToU propio del inversor dejasen segmentos residuales
        de venta, se limpian. CARGA_RED/VENTA_RED → segmento 1;
        AUTOCONSUMO/MANTENER → tabla vacía (float deseado).
        """
        direcciones = self._direcciones_tabla_tou()
        tabla = {addr: 0 for addr in direcciones}
        segmento = self._segmento_tou(tramo, hasta_manual)
        if segmento is None:
            return tabla
        tabla[direcciones[0]] = self._clamp_tou(
            "tou_hora_1", segmento["hora_inicio"]
        )
        tabla[direcciones[1]] = self._clamp_tou(
            "tou_hora_2", segmento["hora_fin"]
        )
        tabla[direcciones[6]] = self._clamp_tou(
            "tou_potencia_1", segmento["potencia"]
        )
        tabla[direcciones[12]] = TOU_VSTOP_DEFAULT  # observado funcionando
        tabla[direcciones[18]] = self._clamp_tou(
            "tou_soc_stop_1", segmento["soc_stop"]
        )
        tabla[direcciones[24]] = self._clamp_tou(
            "tou_flag_1", segmento["flag"]
        )
        return tabla

    def _presupuesto_tablas(self) -> bool:
        """¿Queda presupuesto de tablas en la ventana de 1 h? (2/h)."""
        ahora = datetime.now(tz=UTC)
        self._tablas_ts = [
            ts
            for ts in self._tablas_ts
            if (ahora - ts).total_seconds() <= 3600
        ]
        maximo = int(self._param(
            CONF_MAX_TABLAS_TOU_HORA, DEFAULT_MAX_TABLAS_TOU_HORA
        ))
        return len(self._tablas_ts) < maximo

    async def _escribir_tabla_tou(
        self,
        tramo: PlanTramo | None,
        tabla: dict[int, int],
        motivo: str,
        forzar: bool = False,
    ) -> str:
        """Escribe la tabla si cambió, por bloques de 6 registros.

        NUNCA se toca 244-249 (248 lo auto-restaura el firmware y 249
        activaría la venta autónoma): las direcciones salen del YAML y
        se verifica defensivamente el rango del bloque. El presupuesto
        es propio (tablas/hora, no escrituras individuales); el verify
        relee la tabla completa en bloques ≤16. Devuelve "igual",
        "aplazada", "ok", "parcial", "fallo" o "fallback" (y relanza
        excepciones de escritura dejando auditoría audible). Con
        ``forzar`` (reparación del vigilante) se salta la idempotencia:
        la tabla del inversor ya no coincide aunque el objetivo sea el
        mismo.
        """
        if tabla == self._tabla_tou_aplicada and not forzar:
            return "igual"
        # Blindaje defensivo: la tabla solo puede ser 250-279 (YAML)
        direcciones = self._direcciones_tabla_tou()
        assert set(tabla) == set(direcciones), (
            "tabla ToU fuera del rango esperado 250-279"
        )
        if not self._presupuesto_tablas():
            _LOGGER.warning(
                "Presupuesto de tablas ToU agotado: %d/%d en la última "
                "hora; el tramo vuelve a intentarse en su momento",
                len(self._tablas_ts),
                int(self._param(
                    CONF_MAX_TABLAS_TOU_HORA, DEFAULT_MAX_TABLAS_TOU_HORA
                )),
            )
            await self._auditar(
                tramo, tabla, ejecutada=False,
                motivo=f"{motivo}: tabla ToU aplazada por presupuesto",
            )
            return "aplazada"
        inicio = datetime.now(tz=UTC)
        anteriores = {} if forzar else (self._tabla_tou_aplicada or {})
        try:
            for campo in range(5):
                addr0 = direcciones[campo * 6]
                valores = [
                    tabla[direcciones[campo * 6 + i]] for i in range(6)
                ]
                if all(
                    anteriores.get(direcciones[campo * 6 + i]) == v
                    for i, v in enumerate(valores)
                ):
                    continue  # bloque idéntico: no reescribir
                await self._escritor.escribir_bloque(addr0, valores)
        except ErrorEscrituraSolarman as err:
            _LOGGER.error("Error escribiendo la tabla ToU: %s", err)
            await self._auditar(
                tramo, tabla, ejecutada=False,
                verificado="error_escritura",
                motivo=f"{motivo}: tabla ToU: {err}",
            )
            raise
        self._tablas_ts.append(datetime.now(tz=UTC))
        latencia = float(
            self._param(CONF_LATENCIA_VERIFY_S, DEFAULT_LATENCIA_VERIFY_S)
        )
        await asyncio.sleep(latencia)
        verificado = "parcial"
        try:
            leida = await self._leer_tabla_tou()
        except ErrorEscrituraSolarman as err:
            _LOGGER.warning("Verify parcial de la tabla ToU: %s", err)
        else:
            verificado = "ok" if leida == tabla else "fallo"
            if verificado == "fallo":
                _LOGGER.warning(
                    "Verify fallido de la tabla ToU: aplicada %s, "
                    "leída %s", tabla, leida
                )
        duracion = (datetime.now(tz=UTC) - inicio).total_seconds()
        await self._auditar(
            tramo, tabla, ejecutada=True,
            verificado=f"tou_{verificado}", latencia_s=duracion,
            motivo=f"{motivo}: tou_tabla",
        )
        if verificado == "ok":
            self._tabla_tou_aplicada = tabla
            self._fallos_consecutivos = 0
        elif verificado == "fallo":
            self._fallos_consecutivos += 1
            if (
                self._fallos_consecutivos
                >= FALLOS_VERIFY_FALLBACK
            ):
                await self._async_fallback(
                    f"verify de tabla ToU fallido "
                    f"{self._fallos_consecutivos} veces consecutivas"
                )
                return "fallback"
        return verificado

    async def _leer_tabla_tou(self) -> dict[int, int]:
        """Lee la tabla 250-279 completa en bloques de ≤16 registros."""
        direcciones = self._direcciones_tabla_tou()
        valores: dict[int, int] = {}
        # 250-265 (16) y 266-279 (14): 30 registros, ≤16 por lectura
        for offset, cantidad in ((0, 16), (16, 14)):
            leidos = await self._escritor.leer(
                direcciones[offset], cantidad
            )
            valores.update(
                {
                    direcciones[offset + i]: int(v)
                    for i, v in enumerate(leidos)
                }
            )
        return valores

    async def _async_notificar_tou(self, mensaje: str) -> None:
        """Notificación persistente de problemas con la tabla (F6/A)."""
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Optimizador Batería OMIE: tabla ToU",
                    "message": mensaje,
                    "notification_id": f"{DOMAIN}_tou_sin_segmento",
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("No se pudo emitir la notificación: %s", err)

    async def _async_vigilante_tou(self, ahora) -> None:
        """Releo la tabla del inversor y repárala si la pisaron.

        Vigilante v2 (F6/A): sustituye al previsto sobre 248 (auto-
        restaurado por firmware: nada que vigilar). Compara la tabla
        real con la última aplicada y restaura la última OBJETIVO un
        máximo de una vez por hora (la nube Solarman borró 248-279 el
        22-nov-2026). Solo con control activo y tabla aplicada.
        """
        if not self._control_activo or self._tabla_tou_objetivo is None:
            return
        ahora_utc = datetime.now(tz=UTC)
        if (
            self._ts_vigilante is not None
            and (ahora_utc - self._ts_vigilante).total_seconds()
            < VIGILANTE_TOU_COOLDOWN_S
        ):
            return
        try:
            leida = await self._leer_tabla_tou()
        except ErrorEscrituraSolarman as err:
            _LOGGER.warning(
                "Vigilante ToU: no se pudo leer la tabla: %s", err
            )
            self._ts_vigilante = ahora_utc
            await self._auditar(
                None, {}, ejecutada=False,
                verificado="tou_sin_segmento",
                motivo=f"vigilante: lectura fallida: {err}",
            )
            await self._async_notificar_tou(
                "No se pudo leer la tabla ToU (250-279) del inversor. "
                "Verifica la conectividad del datalogger."
            )
            return
        if leida == self._tabla_tou_objetivo:
            self._ts_vigilante = None
            return
        _LOGGER.warning(
            "Vigilante ToU: la tabla del inversor derivó del objetivo "
            "(¿push de la nube/datalogger?): reparando"
        )
        try:
            resultado = await self._escribir_tabla_tou(
                None, self._tabla_tou_objetivo, "vigilante tou", forzar=True
            )
        except ErrorEscrituraSolarman as err:
            self._ts_vigilante = ahora_utc
            await self._auditar(
                None, self._tabla_tou_objetivo, ejecutada=False,
                verificado="tou_sin_segmento",
                motivo=f"vigilante: reparación fallida: {err}",
            )
            await self._async_notificar_tou(
                "La tabla ToU (250-279) derivó del plan y no se pudo "
                "reparar automáticamente. Revisa el inversor."
            )
            return
        if resultado == "ok":
            await self._auditar(
                None, self._tabla_tou_objetivo, ejecutada=False,
                verificado="tou_tabla_reparada",
                motivo="vigilante: tabla restaurada tras deriva",
            )
            self._ts_vigilante = ahora_utc
        elif resultado in ("fallo", "parcial"):
            self._ts_vigilante = ahora_utc
            await self._auditar(
                None, self._tabla_tou_objetivo, ejecutada=False,
                verificado="tou_sin_segmento",
                motivo=f"vigilante: reparación {resultado}",
            )
            await self._async_notificar_tou(
                "La tabla ToU (250-279) derivó y la reparación no "
                "verificó. Se reintentará en el siguiente ciclo."
            )
        elif resultado == "aplazada":
            self._ts_vigilante = ahora_utc
            await self._async_notificar_tou(
                "La tabla ToU (250-279) derivó del plan y el "
                "presupuesto de tablas/hora está agotado."
            )

    # ─── Salvaguardas (U4) ───

    def _carga_activa(self) -> bool:
        """Hay carga desde red aplicada (232=1 o 230>0)."""
        addr_red = self.mapa.direccion(
            "estandar", "corriente_carga_red", "control"
        )
        addr_habilitar = self.mapa.direccion(
            "estandar", "habilitar_carga_red", "control"
        )
        return (
            self._ultimo_aplicado.get(addr_habilitar) == 1
            or self._ultimo_aplicado.get(addr_red, 0) > 0
        )

    def _telemetria_fresca(self) -> bool:
        """Telemetría no obsoleta: ts < 4 × intervalo (D7, D-F5.3.3).

        El umbral pasó de 2 × a 4 × ``intervalo_telemetria_s``: el
        jitter del polling de Solarman superaba el umbral original
        a diario. La caída se **declara** tras 2 evaluaciones obsoletas
        consecutivas (debounce), pero este método devuelve ``False``
        desde la primera: las escrituras se cortan de inmediato.
        """
        telemetria = self.coordinador_telemetria.data
        if telemetria is None or telemetria.soc is None:
            return False
        intervalo = float(
            self._param("intervalo_telemetria_s", 30)
        )
        edad = (
            datetime.now(tz=UTC) - telemetria.ts_actualizacion
        ).total_seconds()
        umbral = MULTIPLICADOR_WATCHDOG * intervalo
        if edad > umbral:
            _LOGGER.warning(
                "Watchdog: telemetría obsoleta (%.0f s > %.0f s); "
                "no se escribe",
                edad,
                umbral,
            )
            return False
        return True

    def _entity_ids_fallo(self) -> list[str]:
        """Lista de entity_ids de fallo (texto separado por comas)."""
        valor = self._param(CONF_ENTITY_IDS_FALLO, "")
        if isinstance(valor, list):
            return [str(v).strip() for v in valor if str(v).strip()]
        return [
            parte.strip()
            for parte in str(valor).split(",")
            if parte.strip()
        ]

    def _fallo_activo(self) -> bool:
        """Algún registro de fallo (Deye 103-106) es ≠ 0 (D10)."""
        entity_ids = self._entity_ids_fallo()
        if not entity_ids:
            if not self._aviso_fallo_emitido:
                self._aviso_fallo_emitido = True
                _LOGGER.warning(
                    "entity_ids_fallo sin configurar: el bloqueo de "
                    "escrituras por fallo del inversor queda INACTIVO"
                )
            return False
        for entity_id in entity_ids:
            estado = self.hass.states.get(entity_id)
            if estado is None or estado.state in ("unknown", "unavailable"):
                continue
            try:
                if float(estado.state) != 0:
                    _LOGGER.warning(
                        "Fallo activo del inversor en %s: %s → "
                        "escrituras bloqueadas",
                        entity_id,
                        estado.state,
                    )
                    return True
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "Estado no numérico en la entidad de fallo %s: %r "
                    "(se ignora)",
                    entity_id,
                    estado.state,
                )
        return False

    def _importacion_red_w(self) -> float | None:
        """Importación de red normalizada a positiva (D9).

        Fuente preferente: ``entity_id_potencia_red`` (registro Deye 169:
        >0 = importación). Sin entidad: cálculo derivado desde telemetría
        ``consumo_casa − produccion_fv − potencia_bateria`` (convención
        Deye +descarga/−carga). OJO: los registros 424-425 (medidor
        externo) usan la convención OPUESTA (compra negativa): si se
        apunta a ellos, invertir el signo antes de configurar.
        """
        entity_id = self._param(CONF_ENTITY_ID_POTENCIA_RED, "")
        if entity_id:
            estado = self.hass.states.get(entity_id)
            if estado is None or estado.state in ("unknown", "unavailable"):
                return None
            try:
                return max(0.0, float(estado.state))
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "Estado no numérico en %s: %r", entity_id, estado.state
                )
                return None
        telemetria = self.coordinador_telemetria.data
        if (
            telemetria is None
            or telemetria.consumo_casa is None
            or telemetria.produccion_fv is None
            or telemetria.potencia_bateria is None
        ):
            return None
        return max(
            0.0,
            telemetria.consumo_casa
            - telemetria.produccion_fv
            - telemetria.potencia_bateria,
        )

    def _aplicar_guard(self, objetivos: dict[int, int]) -> dict[int, int]:
        """Guard dinámico de potencia contratada (D9, RF-04).

        Importación > 95 % → recorta la corriente de carga desde red al
        margen restante; ≥ 100 % → 0 A inmediato (solo valores seguros).
        Sin dato de importación disponible, los objetivos quedan intactos
        (no se inventa; el guard del plan ya limita la potencia).
        """
        importacion = self._importacion_red_w()
        if importacion is None:
            return objetivos
        contratada = float(self._param(CONF_POTENCIA_CONTRATADA, 5750.0))
        fraccion = importacion / contratada
        if fraccion < GUARD_REDUCCION:
            return objetivos
        addr_red = self.mapa.direccion(
            "estandar", "corriente_carga_red", "control"
        )
        addr_habilitar = self.mapa.direccion(
            "estandar", "habilitar_carga_red", "control"
        )
        if fraccion >= GUARD_CORTE:
            self._guard_actual = (
                f"corte: importación {importacion:.0f} W ≥ "
                f"{contratada:.0f} W contratados"
            )
            _LOGGER.warning(
                "Guard: importación %.0f W ≥ %.0f W contratados → "
                "corriente de carga desde red a 0",
                importacion,
                contratada,
            )
            return {
                **objetivos,
                addr_red: 0,
                addr_habilitar: 0,
            }
        margen_w = contratada * GUARD_REDUCCION - importacion
        reducida = self._recortar_corriente(
            "corriente_carga_red", margen_w / self._voltaje_nominal()
        )
        self._guard_actual = (
            f"reducción: importación {importacion:.0f} W al "
            f"{fraccion * 100:.0f} % de {contratada:.0f} W"
        )
        _LOGGER.warning(
            "Guard: importación al %.0f %% de la contratada → "
            "corriente de carga desde red reducida a %d A",
            fraccion * 100,
            reducida,
        )
        return {**objetivos, addr_red: min(objetivos.get(addr_red, 0), reducida)}

    def _escrituras_ultima_hora(self) -> int:
        """Escrituras reales en la ventana deslizante de 1 h (D6)."""
        ahora = datetime.now(tz=UTC)
        self._escrituras_ts = [
            ts
            for ts in self._escrituras_ts
            if (ahora - ts).total_seconds() <= 3600
        ]
        return len(self._escrituras_ts)

    # ─── Ejecución de tramo ───

    async def async_aplicar_tramo(self, motivo: str) -> None:
        """Traduce y aplica el tramo actual (o el modo manual).

        Con ``control_activo`` OFF todo queda **simulado**: se traduce y
        se loguea, pero nunca se invoca un servicio de escritura. Con ON,
        el pipeline aplica las salvaguardas: watchdog (D7), fallo (D10),
        guard (D9), idempotencia (D8), rate limiter (D6) y write-verify
        (D5) con fallback tras 3 fallos consecutivos.
        """
        tramo = self.coordinador_plan.tramo_actual()
        manual = self.modo_manual
        if manual is not None:
            tramo = self._tramo_manual(manual["accion"])
        if tramo is None:
            # Watchdog: plan ausente → no escribir; carga activa → fallback
            self._motivo_estado = f"sin tramo de plan ({motivo})"
            if self._control_activo and self._carga_activa():
                await self._async_fallback("plan ausente")
            self._notificar()
            return

        # F5.2: el cap de exportación (245) debe estar fresco ANTES de
        # traducir el tramo, porque _objetivos_tramo ya recorta la
        # corriente de venta con él.
        if tramo.accion == ACCION_VENTA_RED:
            await self._refrescar_max_exportacion()
        objetivos = self._objetivos_tramo(tramo)
        self._motivo_estado = (
            f"{motivo}: acción {tramo.accion} "
            f"tramo {tramo.hora} ({tramo.fecha})"
        )
        self._guard_actual = None
        if not self._control_activo:
            _LOGGER.info(
                "[dry-run] %s: %s → %s (control_activo OFF)",
                motivo,
                tramo.accion,
                objetivos or "sin escrituras",
            )
            self._estado_simulado = None
            await self._auditar(
                tramo, objetivos, ejecutada=False,
                motivo=f"{motivo}: control_activo OFF",
            )
            self._actualizar_estado()
            return

        # Watchdog (D7 + D-F5.3.3): las escrituras se cortan desde la
        # PRIMERA observación obsoleta; el estado ``sin_telemetria``
        # se declara tras el debounce y el último tramo se mantiene
        # (carga activa incluida) hasta TIMEOUT_TELEMETRIA_S, momento
        # en el que el fallback aplica los valores seguros.
        fresca = self._telemetria_fresca()
        self._ultimo_watchdog_ok = fresca
        if fresca:
            self._watchdog_caidas = 0
            self._ts_watchdog_caida = None
        else:
            self._watchdog_caidas += 1
            if self._ts_watchdog_caida is None:
                self._ts_watchdog_caida = datetime.now(tz=UTC)
            en_timeout = (
                datetime.now(tz=UTC) - self._ts_watchdog_caida
            ).total_seconds() >= TIMEOUT_TELEMETRIA_S
            if self._carga_activa() and en_timeout:
                await self._async_fallback(
                    "telemetría obsoleta > "
                    f"{TIMEOUT_TELEMETRIA_S} s"
                )
                return
            self._actualizar_estado()
            return
        # Bloqueo por fallo del inversor (D10)
        if self._fallo_activo():
            if self._carga_activa():
                await self._async_fallback("fallo del inversor")
            self._actualizar_estado()
            return

        # Activación gradual (D11): solo CARGA_RED en valle 2.0TD real
        if not self._accion_ejecutable(tramo):
            _LOGGER.info(
                "[gradual] %s (%s) simulada: solo CARGA_RED en valle "
                "2.0TD se ejecuta en modo gradual",
                tramo.accion,
                motivo,
            )
            # MANTENER no traduce escrituras: no hay nada simulado
            self._estado_simulado = "gradual" if objetivos else None
            await self._auditar(
                tramo, objetivos, ejecutada=False,
                motivo=f"{motivo}: simulada por modo gradual",
            )
            self._actualizar_estado()
            return

        # F5.2 (21-nov-2026): sanear los puntos de arranque de la carga
        # desde red ANTES de armar el tramo (el AND 228/229 puede
        # derivar por push del datalogger; con 228=49 V la carga queda
        # congelada y la descarga suspendida). Solo se ejecuta cuando
        # la acción ya superó el filtro de modo gradual.
        if (
            tramo.accion == ACCION_CARGA_RED
            and not await self._asegurar_arranque_carga(tramo, motivo)
        ):
            self._estado_simulado = "arranque_bloqueado"
            await self._auditar(
                tramo, objetivos, ejecutada=False,
                motivo=f"{motivo}: puntos de arranque no asegurables",
            )
            self._actualizar_estado()
            return

        # Guard dinámico de potencia contratada (D9)
        objetivos = self._aplicar_guard(objetivos)
        pendientes = self._filtrar_idempotente(objetivos)
        # F6/A (24-nov-2026): tabla ToU objetivo del tramo. Con la
        # tabla como actuador, los amperajes solos no mueven energía:
        # cada tramo ejecutable fija su segmento en 250-279 (AUTOCONSUMO/
        # MANTENER dejan la tabla vacía = float deseado).
        tabla_objetivo = None
        if self._actuador_tou():
            hasta_manual = manual["hasta"] if manual is not None else None
            tabla_objetivo = self._objetivos_tabla_tou(tramo, hasta_manual)
            self._tabla_tou_objetivo = tabla_objetivo
        if (
            not pendientes
            and (
                tabla_objetivo is None
                or tabla_objetivo == self._tabla_tou_aplicada
            )
        ):
            self._estado_simulado = None
            self._actualizar_estado()
            return

        # Rate limiter (D6)
        maximo = int(self._param(CONF_MAX_ESCRITURAS_HORA,
                                 DEFAULT_MAX_ESCRITURAS_HORA))
        if (
            pendientes
            and self._escrituras_ultima_hora() + len(pendientes) > maximo
        ):
            _LOGGER.warning(
                "Rate limiter: %d escrituras en la última hora "
                "(máx %d); aplazado al siguiente tramo",
                self._escrituras_ultima_hora(),
                maximo,
            )
            self._estado_simulado = "rate_limiter"
            await self._auditar(
                tramo, pendientes, ejecutada=False,
                motivo=f"{motivo}: aplazado por rate limiter",
            )
            self._actualizar_estado()
            return

        # F6/A: la tabla ToU va PRIMERO: si su escritura falla, el
        # inversor conserva la configuración previa completa (tabla y
        # amperajes coherentes entre sí) y el error se relanza
        if tabla_objetivo is not None:
            resultado_tabla = await self._escribir_tabla_tou(
                tramo, tabla_objetivo, motivo
            )
            if resultado_tabla == "fallback":
                return
            if resultado_tabla == "aplazada":
                self._estado_simulado = "rate_limiter"
                self._actualizar_estado()
                return

        _LOGGER.info(
            "Aplicando %s (%s): %s", tramo.accion, motivo, pendientes
        )
        self._estado_simulado = None
        for addr, valor in pendientes.items():
            try:
                await self._escritor.escribir(addr, valor)
            except ErrorEscrituraSolarman as err:
                _LOGGER.error(
                    "Error escribiendo %d=%d (%s): %s",
                    addr, valor, motivo, err,
                )
                await self._auditar(
                    tramo, {addr: valor}, ejecutada=False,
                    verificado="error_escritura", motivo=f"{motivo}: {err}",
                )
                self._actualizar_estado()
                raise
            self._escrituras_ts.append(datetime.now(tz=UTC))
            inicio = datetime.now(tz=UTC)
            verificado = await self._verificar(addr, valor)
            latencia = (datetime.now(tz=UTC) - inicio).total_seconds()
            await self._auditar(
                tramo, {addr: valor}, ejecutada=True,
                verificado=verificado, latencia_s=latencia,
                motivo=motivo,
            )
            if verificado == "fallo":
                self._fallos_consecutivos += 1
                if self._fallos_consecutivos >= FALLOS_VERIFY_FALLBACK:
                    await self._async_fallback(
                        f"verify fallido {self._fallos_consecutivos} "
                        "veces consecutivas"
                    )
                    return
            elif verificado == "ok":
                self._fallos_consecutivos = 0
            # "parcial" (relectura con error de servicio) no cuenta (D5)
            self._ultimo_aplicado[addr] = valor
        self._actualizar_estado()

    def _modo_activacion(self) -> str:
        """Modo de activación configurado (gradual por defecto)."""
        modo = str(
            self._param(CONF_MODO_ACTIVACION, DEFAULT_MODO_ACTIVACION)
        )
        return modo if modo in (GRADUAL, COMPLETO) else GRADUAL

    def _periodo_actual(self) -> str:
        """Período 2.0TD del instante actual (valle/llano/punta)."""
        modelo_tarifa = (
            self.coordinador_plan.coordinador_precio.modelo_tarifa
        )
        return modelo_tarifa.obtener_periodo(datetime.now(tz=UTC))

    def _accion_ejecutable(self, tramo: PlanTramo) -> bool:
        """¿La acción se ejecuta de verdad en el modo actual? (D11)."""
        if self._modo_activacion() == COMPLETO:
            return True
        return tramo.accion == ACCION_CARGA_RED and self._periodo_actual() == "valle"

    # ─── Modo manual (D13) ───

    @property
    def modo_manual(self) -> dict[str, Any] | None:
        """Modo manual activo: {accion, hasta} o None si expiró."""
        if self._modo_manual is None:
            return None
        accion, hasta = self._modo_manual
        if datetime.now(tz=UTC) >= hasta:
            _LOGGER.info(
                "Modo manual %s expirado en %s: vuelta al plan",
                accion,
                hasta.isoformat(),
            )
            self._modo_manual = None
        return (
            {"accion": accion, "hasta": hasta.isoformat()}
            if self._modo_manual is not None
            else None
        )

    async def async_force_mode(
        self, accion: str, hasta: datetime
    ) -> dict[str, Any]:
        """Fuerza una acción manual hasta ``hasta`` (D13).

        El modo manual respeta **todas** las salvaguardas (límites YAML,
        guard, fallo, watchdog, rate limiter, gradual) y expira solo al
        llegar ``hasta``. ``hasta`` en el pasado → ``ValueError``.
        """
        if accion not in ACCIONES_MANUALES:
            raise ValueError(
                f"Acción manual no válida: {accion}. Válidas: "
                f"{', '.join(ACCIONES_MANUALES)}"
            )
        if hasta <= datetime.now(tz=UTC):
            raise ValueError(
                f"'until' debe estar en el futuro: {hasta.isoformat()}"
            )
        self._modo_manual = (accion, hasta)
        _LOGGER.info(
            "Modo manual %s activado hasta %s (con salvaguardas)",
            accion,
            hasta.isoformat(),
        )
        await self.async_aplicar_tramo("force_mode")
        return {
            "modo_manual": accion,
            "hasta": hasta.isoformat(),
            "estado": self.estado,
        }

    async def async_release_manual(self) -> dict[str, Any]:
        """Libera el modo manual inmediatamente (vuelta al plan)."""
        liberado = self._modo_manual is not None
        self._modo_manual = None
        _LOGGER.info("Modo manual liberado (%s)", "manual" if liberado else "no había")
        await self.async_aplicar_tramo("release_manual")
        return {"liberado": liberado, "estado": self.estado}

    def _tramo_manual(self, accion: str) -> PlanTramo:
        """Tramo sintético del modo manual con los límites del plan.

        CARGA_RED manual usa la potencia máxima de carga; VENTA_RED la
        de descarga; AUTOCONSUMO/MANTENER los valores seguros. La
        conversión a A y el recorte a los límites del YAML los hace la
        traducción normal.
        """
        ahora = datetime.now().astimezone()
        potencia_carga = float(
            self._param(CONF_POTENCIA_MAX_CARGA, 5000.0)
        )
        potencia_descarga = float(
            self._param(
                CONF_POTENCIA_MAX_DESCARGA, DEFAULT_POTENCIA_MAX_DESCARGA
            )
        )
        return PlanTramo(
            fecha=ahora.date(),
            tramo=ahora.hour * 4 + ahora.minute // 15,
            hora=ahora.strftime("%H:%M"),
            accion=accion,
            potencia_bateria_w=0.0,
            potencia_carga_red_w=(
                potencia_carga if accion == ACCION_CARGA_RED else 0.0
            ),
            potencia_venta_red_w=(
                potencia_descarga if accion == ACCION_VENTA_RED else 0.0
            ),
            soc_previsto_pct=0.0,
            precio_referencia_eur_kwh=0.0,
            ahorro_tramo_eur=0.0,
        )

    # ─── Auditoría (D14) ───

    async def _auditar(
        self,
        tramo: PlanTramo,
        registros: dict[int, int],
        ejecutada: bool,
        verificado: str | None = None,
        latencia_s: float | None = None,
        motivo: str = "",
    ) -> None:
        """Añade una línea a la auditoría JSONL append-only.

        La auditoría vive en ``config/optimizador_bateria_omie/`` para
        sobrevivir a las actualizaciones de HACS; el directorio se crea
        si no existe. Una línea por escritura real (con su verify) o por
        aplicación simulada (``ejecutada: false``).
        """
        linea = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "tramo": tramo.hora if tramo else None,
            "accion": tramo.accion if tramo else "FALLBACK",
            "registros": {str(addr): valor for addr, valor in registros.items()},
            "ejecutada": ejecutada,
            "verificado": verificado,
            "latencia": round(latencia_s, 3) if latencia_s is not None else None,
            "motivo": motivo,
            "guard": self._guard_actual,
        }
        try:
            await self.hass.async_add_executor_job(
                self._anexar_linea_auditoria, linea
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Auditoría: no se pudo escribir la línea: %s", err)

    def _anexar_linea_auditoria(self, linea: dict) -> None:
        """Escribe una línea JSON en la auditoría (bloqueante, executor)."""
        import json  # pylint: disable=import-outside-toplevel

        ruta = self._ruta_auditoria()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta, "a", encoding="utf-8") as fichero:
            fichero.write(json.dumps(linea, ensure_ascii=False) + "\n")

    async def _verificar(self, addr: int, valor: int) -> str:
        """Write-verify: relectura tras la latencia del datalogger.

        Devuelve ``"ok"`` (valor coincide con tolerancia), ``"fallo"``
        (valor distinto; cuenta para el fallback) o ``"parcial"`` (la
        relectura falló por error del servicio; verify soft, no cuenta).
        """
        latencia = float(
            self._param(CONF_LATENCIA_VERIFY_S, DEFAULT_LATENCIA_VERIFY_S)
        )
        tolerancia = int(
            self._param(CONF_VERIFY_TOLERANCIA, DEFAULT_VERIFY_TOLERANCIA)
        )
        await asyncio.sleep(latencia)
        try:
            leido = (await self._escritor.leer(addr, 1))[0]
        except ErrorEscrituraSolarman as err:
            _LOGGER.warning(
                "Verify parcial en %d: relectura falló (%s)", addr, err
            )
            return "parcial"
        if abs(leido - valor) <= tolerancia:
            return "ok"
        _LOGGER.warning(
            "Verify fallido en %d: escrito %d, leído %d", addr, valor, leido
        )
        return "fallo"

    async def _async_fallback(self, motivo: str) -> None:
        """Fallback (D5): valores seguros AUTOCONSUMO + bloqueo manual.

        Escribe 232=0 y 230=0 (direcciones del YAML), NO sujeto al rate
        limiter, apaga ``control_activo`` y emite una notificación
        persistente. Reactivación solo manual (encender el switch).
        """
        _LOGGER.error("FALLBACK: %s", motivo)
        self._estado = ESTADO_CONTROL_FALLBACK_BLOQUEADO
        addr_red = self.mapa.direccion(
            "estandar", "corriente_carga_red", "control"
        )
        addr_habilitar = self.mapa.direccion(
            "estandar", "habilitar_carga_red", "control"
        )
        try:
            await self._escritor.escribir(addr_habilitar, 0)
            await self._escritor.escribir(addr_red, 0)
            self._ultimo_aplicado[addr_habilitar] = 0
            self._ultimo_aplicado[addr_red] = 0
            await self._auditar(
                None,
                {addr_habilitar: 0, addr_red: 0},
                ejecutada=True,
                verificado="fallback",
                motivo=f"fallback: {motivo}",
            )
        except ErrorEscrituraSolarman as err:
            _LOGGER.error(
                "Fallback: no se pudieron escribir los valores seguros "
                "(%s). Revisa el inversor manualmente AHORA",
                err,
            )
        self._control_activo = False
        self._fallos_consecutivos = 0
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Optimizador Batería OMIE: fallback",
                    "message": (
                        f"Escrituras bloqueadas ({motivo}). Se han "
                        "aplicado valores seguros (carga desde red a 0). "
                        "Revisa el inversor y vuelve a encender "
                        "control_activo para reactivar."
                    ),
                    "notification_id": f"{DOMAIN}_fallback",
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("No se pudo emitir la notificación: %s", err)
        self._notificar()

    # ─── Control activo ───

    async def async_set_control_activo(self, activo: bool) -> None:
        """Enciende/apaga el control real de escrituras (D18).

        La activación es **atómica** (0.11.3): la bandera solo se pone
        a ``True`` si la escritura de los límites SOC (217/218) tiene
        éxito; si falla, se aborta quedando OFF con el motivo explícito
        y relanzando el error para que la UI lo muestre. Sin esto, un
        fallo de la escritura dejaba el switch en ON con el estado
        congelado en ``inactivo`` y sin escribir nunca.
        """
        if activo == self._control_activo:
            return
        if activo:
            try:
                await self._escribir_limites_soc()
            except Exception as err:
                _LOGGER.error(
                    "control_activo ON abortado: fallo escribiendo "
                    "límites SOC (217/218): %s",
                    err,
                )
                self._motivo_estado = (
                    "activación abortada: fallo escribiendo límites "
                    f"SOC (217/218): {err}"
                )
                self._estado_simulado = None
                self._actualizar_estado()
                raise
            self._control_activo = True
            _LOGGER.info(
                "control_activo ON: escrituras reales habilitadas "
                "(bajo salvaguardas)"
            )
        else:
            self._control_activo = False
            _LOGGER.info("control_activo OFF: modo dry-run forzado")
        await self.async_aplicar_tramo("control_activo")

    async def async_actualizar_limites_soc(self) -> None:
        """Reescribe 217/218 al cambiar ``soc_min`` en caliente (0.11.6).

        Solo con ``control_activo`` ON: con OFF no se toca el inversor
        (dry-run) y los límites se escribirán al activar.
        """
        if not self._control_activo:
            _LOGGER.info(
                "Límites SOC no reescritos (control_activo OFF): se "
                "aplicarán al activar"
            )
            return
        await self._escribir_limites_soc()

    async def _escribir_limites_soc(self) -> None:
        """Escribe 217/218 al activar (218 > 217 siempre, clamp)."""
        addr_min = self.mapa.direccion(
            "estandar", "soc_minimo", "control"
        )
        addr_rec = self.mapa.direccion(
            "estandar", "soc_recuperacion", "control"
        )
        soc_min = float(self._param(CONF_SOC_MIN, DEFAULT_SOC_MIN))
        soc_rec = float(
            self._param(CONF_SOC_RECUPERACION, DEFAULT_SOC_RECUPERACION)
        )
        # Clamp semántico Deye: recuperación > corte (validación 2)
        soc_rec = max(soc_rec, soc_min + 1)
        minimo_min, maximo_max = self._limites("soc_minimo")
        rec_min, rec_max = self._limites("soc_recuperacion")
        valor_min = round(min(max(soc_min, minimo_min), maximo_max))
        valor_rec = round(min(max(soc_rec, rec_min), rec_max))
        await self._escritor.escribir(addr_min, valor_min)
        await self._escritor.escribir(addr_rec, valor_rec)
        self._ultimo_aplicado[addr_min] = valor_min
        self._ultimo_aplicado[addr_rec] = valor_rec
        _LOGGER.info(
            "Límites SOC escritos: minimo=%d, recuperacion=%d",
            valor_min,
            valor_rec,
        )

    # ─── Estado ───

    def _actualizar_estado(self) -> None:
        """Recalcula el estado visible y notifica (D12, D-F5.3.4).

        ``sin_telemetria`` se declara tras el debounce (D-F5.3.3) y
        ``gradual`` diferencia la simulación del modo gradual del
        ``dry_run`` real (control OFF ya se reporta como ``inactivo``).
        F5.2: ``arranque_bloqueado`` (puntos de arranque 228/229 no
        asegurables) cae en la rama genérica de ``dry_run``: al ser
        una condición transitoria del datalogger no merece estado
        propio, pero el motivo queda en los atributos del sensor.
        """
        if self._estado != ESTADO_CONTROL_FALLBACK_BLOQUEADO:
            if not self._control_activo:
                self._estado = ESTADO_CONTROL_INACTIVO
            elif (
                not self._ultimo_watchdog_ok
                and self._watchdog_caidas >= DEBOUNCE_TELEMETRIA
            ):
                self._estado = ESTADO_CONTROL_SIN_TELEMETRIA
            elif self._estado_simulado == "gradual":
                self._estado = ESTADO_CONTROL_GRADUAL
            elif self._estado_simulado is not None:
                self._estado = ESTADO_CONTROL_DRY_RUN
            else:
                self._estado = ESTADO_CONTROL_ACTIVO
        self._notificar()

    @property
    def control_activo(self) -> bool:
        """Habilitador de escrituras reales."""
        return self._control_activo

    @property
    def estado(self) -> str:
        """Estado visible del control (sensor diagnóstico)."""
        return self._estado

    @property
    def estado_detalle(self) -> dict[str, Any]:
        """Atributos del sensor diagnóstico de estado."""
        return {
            "motivo": self._motivo_estado,
            "control_activo": self._control_activo,
            "modo_activacion": self._modo_activacion(),
            "telemetria_fresca": self._ultimo_watchdog_ok,
            "fallos_consecutivos": self._fallos_consecutivos,
            "escrituras_ultima_hora": self._escrituras_ultima_hora(),
            "modo_manual": self.modo_manual,
            "ultimo_aplicado": dict(self._ultimo_aplicado),
            "actuador_tou": self._actuador_tou(),
            "tabla_tou": self._resumen_tabla_tou(),
            "ruta_auditoria": str(self._ruta_auditoria()),
        }

    def _resumen_tabla_tou(self) -> dict[str, int] | None:
        """Resumen del segmento 1 de la tabla aplicada (diagnóstico)."""
        if self._tabla_tou_aplicada is None:
            return None
        direcciones = self._direcciones_tabla_tou()
        tabla = self._tabla_tou_aplicada
        return {
            "hora_1": tabla[direcciones[0]],
            "hora_2": tabla[direcciones[1]],
            "potencia_1": tabla[direcciones[6]],
            "soc_stop_1": tabla[direcciones[18]],
            "flag_1": tabla[direcciones[24]],
        }

    def _ruta_auditoria(self) -> Path:
        """Ruta de la auditoría JSONL (config/, sobrevive a HACS)."""
        return (
            Path(self.hass.config.config_dir)
            / DOMAIN
            / "auditoria_escrituras.jsonl"
        )

    @property
    def escritor(self) -> EscritorSolarman:
        """Escritor Solarman (para tests del envoltorio)."""
        return self._escritor
