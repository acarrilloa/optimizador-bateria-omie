"""Executor de control del inversor (F4) vía servicios de Solarman.

Aplica el plan de F3 escribiendo en los registros de control del Deye
con los servicios de la integración oficial Solarman
(``write_single_register`` / ``write_multiple_registers`` /
``read_holding_registers``). **Ninguna dirección vive aquí**: todas
salen de la sección ``control`` de ``registers.yaml``.

Salvaguardas (AGENTS §1 y PLAN_F4): sin ``control_activo=on`` no existe
escritura real (solo traducción y registro); las corrientes se recortan
SIEMPRE a los límites del YAML; el registro 210 (corriente de carga
desde cualquier fuente) se fija a la corriente equivalente de
``potencia_max_carga`` (valor constante entre tramos, nunca la corriente
de un tramo) para no estrangular la carga solar.
"""
# versión 0.10.0

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.optimizador_bateria_omie.const import (
    ACCIONES_MANUALES,
    CONF_DEVICE_ID_SOLARMAN,
    CONF_ENTITY_ID_POTENCIA_RED,
    CONF_ENTITY_IDS_FALLO,
    CONF_LATENCIA_VERIFY_S,
    CONF_MAX_ESCRITURAS_HORA,
    CONF_MODO_ACTIVACION,
    CONF_POTENCIA_CONTRATADA,
    CONF_POTENCIA_MAX_CARGA,
    CONF_POTENCIA_MAX_DESCARGA,
    CONF_SOC_MIN,
    CONF_SOC_RECUPERACION,
    CONF_VERIFY_TOLERANCIA,
    CONF_VOLTAJE_BATERIA_NOMINAL,
    DEFAULT_DEVICE_ID_SOLARMAN,
    DEFAULT_LATENCIA_VERIFY_S,
    DEFAULT_MAX_ESCRITURAS_HORA,
    DEFAULT_MODO_ACTIVACION,
    DEFAULT_POTENCIA_MAX_DESCARGA,
    DEFAULT_SOC_MIN,
    DEFAULT_SOC_RECUPERACION,
    DEFAULT_VERIFY_TOLERANCIA,
    DEFAULT_VOLTAJE_BATERIA_NOMINAL,
    DOMAIN,
    ESTADO_CONTROL_ACTIVO,
    ESTADO_CONTROL_DRY_RUN,
    ESTADO_CONTROL_FALLBACK_BLOQUEADO,
    ESTADO_CONTROL_INACTIVO,
    FALLOS_VERIFY_FALLBACK,
    GUARD_CORTE,
    GUARD_REDUCCION,
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

# Servicios de la integración Solarman (payload aislado aquí, PLAN_F4 Q1:
# verificar contra la integración instalada en la primera activación real)
DOMINIO_SOLARMAN = "solarman"
SERVICIO_ESCRIBIR = "write_single_register"
SERVICIO_ESCRIBIR_BLOQUE = "write_multiple_registers"
SERVICIO_LEER = "read_holding_registers"
CAMPO_DEVICE = "device"
CAMPO_REGISTRO = "register"
CAMPO_VALOR = "value"
CAMPO_VALORES = "values"


class ErrorEscrituraSolarman(Exception):
    """Fallo al invocar un servicio de escritura/lectura de Solarman."""


class EscritorSolarman:
    """Envoltorio de los servicios solarman.write_*/read_holding_registers.

    Único punto del componente que llama a ``hass.services`` del dominio
    ``solarman``: si el payload de la integración cambia, se ajusta aquí.
    El dispositivo se resuelve con ``device_id_solarman`` si está
    configurado o, si no, con el **primer device** de la integración
    Solarman; si no se puede resolver, error explícito (nunca se
    inventa).
    """

    def __init__(self, hass: HomeAssistant, device_id: str = "") -> None:
        """Inicializa el escritor con el device_id opcional."""
        self.hass = hass
        self._device_id = device_id or DEFAULT_DEVICE_ID_SOLARMAN
        self._device_resuelto: str | None = None

    async def _resolver_device(self) -> str:
        """Device del inversor: configurado o primero de la integración."""
        if self._device_id:
            return self._device_id
        if self._device_resuelto:
            return self._device_resuelto
        # pylint: disable=import-outside-toplevel
        from homeassistant.helpers import device_registry as dr

        registro = dr.async_get(self.hass)
        for dispositivo in registro.devices.values():
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
        """Invoca un servicio de Solarman con el dispositivo resuelto."""
        carga = dict(datos)
        carga[CAMPO_DEVICE] = await self._resolver_device()
        try:
            if con_respuesta:
                return await self.hass.services.async_call(
                    DOMINIO_SOLARMAN,
                    servicio,
                    carga,
                    blocking=True,
                    return_response=True,
                )
            await self.hass.services.async_call(
                DOMINIO_SOLARMAN, servicio, carga, blocking=True
            )
            return None
        except HomeAssistantError as err:
            raise ErrorEscrituraSolarman(
                f"El servicio {DOMINIO_SOLARMAN}.{servicio} falló: {err}"
            ) from err

    async def escribir(self, addr: int, valor: int) -> None:
        """Escribe un registro individual."""
        await self._invocar(
            SERVICIO_ESCRIBIR,
            {CAMPO_REGISTRO: int(addr), CAMPO_VALOR: int(valor)},
        )

    async def escribir_bloque(
        self, addr: int, valores: list[int]
    ) -> None:
        """Escribe un bloque contiguo de registros."""
        if not valores:
            return
        await self._invocar(
            SERVICIO_ESCRIBIR_BLOQUE,
            {
                CAMPO_REGISTRO: int(addr),
                CAMPO_VALORES: [int(v) for v in valores],
            },
        )

    async def leer(self, addr: int, cantidad: int = 1) -> list[int]:
        """Lee registros holding vía servicio (relectura de verificación)."""
        respuesta = await self._invocar(
            SERVICIO_LEER,
            {CAMPO_REGISTRO: int(addr), "quantity": int(cantidad)},
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
        self._ultimo_simulado = False
        self._guard_actual: str | None = None
        self._modo_manual: tuple[str, datetime] | None = None
        self._ultimo_aplicado: dict[int, int] = {}
        self._escrituras_ts: list[datetime] = []
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
        await self.async_aplicar_tramo("arranque")

    def _async_plan_cambiado(self) -> None:
        """El plan cambió: aplicar el tramo en una tarea."""
        self.hass.async_create_task(self.async_aplicar_tramo("plan"))

    async def async_descargar(self) -> None:
        """Cancela la suscripción al plan al descargar la entrada."""
        if self._cancelar_plan is not None:
            self._cancelar_plan()
            self._cancelar_plan = None
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
            return {
                addr_carga: limite_carga,
                addr_red: corriente,
                addr_habilitar: 1,
            }
        if tramo.accion == ACCION_VENTA_RED:
            corriente = self._recortar_corriente(
                "corriente_descarga",
                abs(tramo.potencia_venta_red_w) / voltaje,
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
        """Telemetría no obsoleta: ts < 2 × intervalo (watchdog D7)."""
        telemetria = self.coordinador_telemetria.data
        if telemetria is None or telemetria.soc is None:
            return False
        intervalo = float(
            self._param("intervalo_telemetria_s", 30)
        )
        edad = (
            datetime.now(tz=UTC) - telemetria.ts_actualizacion
        ).total_seconds()
        if edad > 2 * intervalo:
            _LOGGER.warning(
                "Watchdog: telemetría obsoleta (%.0f s > %.0f s); "
                "no se escribe",
                edad,
                2 * intervalo,
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
            self._ultimo_simulado = bool(objetivos)
            await self._auditar(
                tramo, objetivos, ejecutada=False,
                motivo=f"{motivo}: control_activo OFF",
            )
            self._actualizar_estado()
            return

        # Watchdog: telemetría obsoleta o sin SOC → no escribir
        if not self._telemetria_fresca():
            if self._carga_activa():
                await self._async_fallback("telemetría obsoleta")
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
            self._ultimo_simulado = bool(objetivos)
            await self._auditar(
                tramo, objetivos, ejecutada=False,
                motivo=f"{motivo}: simulada por modo gradual",
            )
            self._actualizar_estado()
            return

        # Guard dinámico de potencia contratada (D9)
        objetivos = self._aplicar_guard(objetivos)
        pendientes = self._filtrar_idempotente(objetivos)
        if not pendientes:
            self._ultimo_simulado = False
            self._actualizar_estado()
            return

        # Rate limiter (D6)
        maximo = int(self._param(CONF_MAX_ESCRITURAS_HORA,
                                 DEFAULT_MAX_ESCRITURAS_HORA))
        if self._escrituras_ultima_hora() + len(pendientes) > maximo:
            _LOGGER.warning(
                "Rate limiter: %d escrituras en la última hora "
                "(máx %d); aplazado al siguiente tramo",
                self._escrituras_ultima_hora(),
                maximo,
            )
            self._ultimo_simulado = bool(pendientes)
            await self._auditar(
                tramo, pendientes, ejecutada=False,
                motivo=f"{motivo}: aplazado por rate limiter",
            )
            self._actualizar_estado()
            return

        _LOGGER.info(
            "Aplicando %s (%s): %s", tramo.accion, motivo, pendientes
        )
        self._ultimo_simulado = False
        for addr, valor in pendientes.items():
            await self._escritor.escribir(addr, valor)
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
        """Enciende/apaga el control real de escrituras (D18)."""
        if activo == self._control_activo:
            return
        self._control_activo = activo
        if activo:
            _LOGGER.info(
                "control_activo ON: escrituras reales habilitadas "
                "(bajo salvaguardas)"
            )
            await self._escribir_limites_soc()
        else:
            _LOGGER.info("control_activo OFF: modo dry-run forzado")
        await self.async_aplicar_tramo("control_activo")

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
        """Recalcula el estado visible y notifica (D12)."""
        if self._estado != ESTADO_CONTROL_FALLBACK_BLOQUEADO:
            if not self._control_activo:
                self._estado = ESTADO_CONTROL_INACTIVO
            elif self._ultimo_simulado:
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
            "fallos_consecutivos": self._fallos_consecutivos,
            "escrituras_ultima_hora": self._escrituras_ultima_hora(),
            "modo_manual": self.modo_manual,
            "ultimo_aplicado": dict(self._ultimo_aplicado),
            "ruta_auditoria": str(self._ruta_auditoria()),
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
