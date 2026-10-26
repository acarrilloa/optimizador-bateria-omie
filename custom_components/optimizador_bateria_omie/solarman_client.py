"""Cliente Modbus del datalogger Solarman (solo lectura en F2)."""
# versión 0.8.0

import asyncio
import logging
from datetime import UTC, datetime

from custom_components.optimizador_bateria_omie.mapa_registros import (
    ErrorMapaRegistros,
    MapaRegistros,
)
from custom_components.optimizador_bateria_omie.telemetria import (
    TelemetriaInversor,
)

_LOGGER = logging.getLogger(__name__)

# Backoff acotado (M4): 3 intentos por ciclo, esperas 0,5 / 2 / 5 s
# (peor caso ≈ 7,5 s, dentro del ciclo de polling de 30 s).
MAX_INTENTOS = 3
ESPERAS_BACKOFF = (0.5, 2.0, 5.0)

# Funciones de telemetría leídas por leer_telemetria()
FUNCIONES_TELEMETRIA = (
    "soc",
    "potencia_bat",
    "corriente_bat",
    "consumo_casa",
    "produccion_fv",
)


class ErrorComunicacionSolarman(Exception):
    """Fallo de comunicación con el datalogger tras agotar los reintentos."""


class ClienteSolarman:
    """Wrapper asíncrono de solo lectura sobre PySolarmanV5.

    La librería es bloqueante: toda llamada se ejecuta en un executor.
    La instancia de ``PySolarmanV5`` se crea bajo demanda (D17: no se
    invoca ``connect()``, la versión síncrona no lo expone) y en cada
    reconexión se hace ``disconnect()`` de la anterior (ignorando
    fallos) y se crea una nueva.

    La familia de registros llega **por parámetro** en cada lectura
    (C4): este cliente no consulta el estado de validación.

    F2 es estrictamente de solo lectura.
    # TODO F4: escribir_registros() con write-verify, límite de 16
    # registros por petición y guards de seguridad.
    """

    def __init__(
        self,
        hass,
        ip: str,
        puerto: int,
        serie: str | int,
        mapa: MapaRegistros,
        fabrica_conexion=None,
    ) -> None:
        """Inicializa el cliente.

        ``fabrica_conexion`` permite inyectar una conexión de prueba
        (``InversorFake``); con ``None`` se instancia ``PySolarmanV5``.
        """
        self.hass = hass
        self._ip = ip
        self._puerto = int(puerto)
        self._serie = int(serie)
        self._mapa = mapa
        self._fabrica = fabrica_conexion
        self._lock = asyncio.Lock()
        self._conexion = None

    def _obtener_conexion(self):
        """Devuelve la conexión activa, creándola bajo demanda.

        Método bloqueante: ejecutar vía ``hass.async_add_executor_job``.
        """
        if self._conexion is None:
            if self._fabrica is not None:
                self._conexion = self._fabrica()
            else:
                from pysolarmanv5 import PySolarmanV5

                self._conexion = PySolarmanV5(
                    self._ip, self._serie, port=self._puerto, verbose=False
                )
        return self._conexion

    async def _reconectar(self) -> None:
        """Cierra la conexión actual (ignorando fallos) para forzar
        que el siguiente intento cree una instancia nueva."""
        vieja = self._conexion
        self._conexion = None
        if vieja is not None:
            try:
                await self.hass.async_add_executor_job(vieja.disconnect)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("disconnect() de la conexión anterior falló")

    async def _leer_con_reintentos(self, addr: int, qty: int) -> list[int]:
        """Una lectura Modbus con backoff acotado (M4).

        Tras cada fallo se reconecta y se espera antes del siguiente
        intento; agotados los 3 intentos → ``ErrorComunicacionSolarman``.
        """
        ultimo_error: Exception | None = None
        for intento in range(1, MAX_INTENTOS + 1):
            try:
                conexion = self._obtener_conexion()
                return await self.hass.async_add_executor_job(
                    conexion.read_holding_registers, addr, qty
                )
            except Exception as err:  # noqa: BLE001
                ultimo_error = err
                _LOGGER.warning(
                    "Lectura Modbus de %d registro(s) en la dirección %d "
                    "falló (intento %d/%d): %s",
                    qty, addr, intento, MAX_INTENTOS, err,
                )
                await self._reconectar()
                await asyncio.sleep(ESPERAS_BACKOFF[intento - 1])

        raise ErrorComunicacionSolarman(
            f"Lectura de {qty} registro(s) en la dirección {addr} falló "
            f"tras {MAX_INTENTOS} intentos"
        ) from ultimo_error

    async def leer_registros(self, addr: int, qty: int) -> list[int]:
        """Lee registros Modbus troceando en bloques ≤ block_size.

        El límite de 16 registros por petición es una limitación
        documentada del datalogger (AGENTS.md).
        """
        valores: list[int] = []
        bloque = self._mapa.block_size
        async with self._lock:
            for offset in range(0, qty, bloque):
                cantidad = min(bloque, qty - offset)
                leidos = await self._leer_con_reintentos(
                    addr + offset, cantidad
                )
                _LOGGER.debug(
                    "Bloque Modbus %d (+%d registros) leído",
                    addr + offset, cantidad,
                )
                valores.extend(leidos)
        return valores

    async def leer_telemetria(self, familia: str) -> TelemetriaInversor:
        """Lee la telemetría completa de una familia de registros.

        La familia llega por parámetro (C4). Una función sin dirección
        en el mapa deja la magnitud a ``None`` con un WARNING (no se
        inventan direcciones ni valores).
        """
        valores: dict[str, float | None] = {}
        for funcion in FUNCIONES_TELEMETRIA:
            try:
                addr = self._mapa.direccion(familia, funcion)
                params = self._mapa.parametros(familia, funcion)
            except ErrorMapaRegistros:
                _LOGGER.warning(
                    "TODO: la función '%s' no tiene dirección en el mapa "
                    "de la familia %s; la magnitud queda como None",
                    funcion, familia,
                )
                valores[funcion] = None
                continue
            crudo = (await self.leer_registros(addr, 1))[0]
            valores[funcion] = MapaRegistros.convertir(crudo, params)

        return TelemetriaInversor(
            soc=valores.get("soc"),
            potencia_bateria=valores.get("potencia_bat"),
            corriente_bateria=valores.get("corriente_bat"),
            consumo_casa=valores.get("consumo_casa"),
            produccion_fv=valores.get("produccion_fv"),
            ts_actualizacion=datetime.now(tz=UTC),
        )
