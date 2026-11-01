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

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.optimizador_bateria_omie.const import (
    CONF_DEVICE_ID_SOLARMAN,
    CONF_POTENCIA_MAX_CARGA,
    CONF_SOC_MIN,
    CONF_SOC_RECUPERACION,
    CONF_VOLTAJE_BATERIA_NOMINAL,
    DEFAULT_DEVICE_ID_SOLARMAN,
    DEFAULT_SOC_MIN,
    DEFAULT_SOC_RECUPERACION,
    DEFAULT_VOLTAJE_BATERIA_NOMINAL,
    DOMAIN,
    ESTADO_CONTROL_ACTIVO,
    ESTADO_CONTROL_FALLBACK_BLOQUEADO,
    ESTADO_CONTROL_INACTIVO,
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
        self._ultimo_aplicado: dict[int, int] = {}
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

    # ─── Ejecución de tramo ───

    async def async_aplicar_tramo(self, motivo: str) -> None:
        """Traduce y aplica el tramo actual (o el modo manual).

        Con ``control_activo`` OFF todo queda **simulado**: se traduce y
        se loguea, pero nunca se invoca un servicio de escritura.
        """
        tramo = self.coordinador_plan.tramo_actual()
        if tramo is None:
            self._motivo_estado = f"sin tramo de plan ({motivo})"
            self._notificar()
            return

        objetivos = self._objetivos_tramo(tramo)
        self._motivo_estado = (
            f"{motivo}: acción {tramo.accion} "
            f"tramo {tramo.hora} ({tramo.fecha})"
        )
        if not self._control_activo:
            _LOGGER.info(
                "[dry-run] %s: %s → %s (control_activo OFF)",
                motivo,
                tramo.accion,
                objetivos or "sin escrituras",
            )
            self._actualizar_estado()
            return

        pendientes = self._filtrar_idempotente(objetivos)
        if not pendientes:
            self._actualizar_estado()
            return
        _LOGGER.info(
            "Aplicando %s (%s): %s", tramo.accion, motivo, pendientes
        )
        for addr, valor in pendientes.items():
            await self._escritor.escribir(addr, valor)
            self._ultimo_aplicado[addr] = valor
        self._actualizar_estado()

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
            self._estado = (
                ESTADO_CONTROL_ACTIVO
                if self._control_activo
                else ESTADO_CONTROL_INACTIVO
            )
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
