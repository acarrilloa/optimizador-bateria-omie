"""Simulador fake del inversor para tests (creado en F2, reutilizable).

Implementa la interfaz Modbus que ``ClienteSolarman`` espera de
``PySolarmanV5``: ``connect()``/``disconnect()`` (D18) y
``read_holding_registers``. ``write_multiple_holding_registers`` queda
para F4+. Permite inyectar fallos y auditar tamaños de bloque.
"""


class InversorFake:
    """Datalogger Solarman simulado con mapa de registros en memoria."""

    def __init__(self, registros: dict[int, int] | None = None) -> None:
        """Inicializa el fake con un mapa de registros opcional."""
        self.registros: dict[int, int] = dict(registros or {})
        self.llamadas_lectura = 0
        self.tamannos_bloque: list[int] = []
        self.escrituras: list[tuple[int, list[int]]] = []
        self.conexiones = 0
        self.desconexiones = 0
        self.fallos_restantes = 0
        self.error: Exception | None = None

    def connect(self) -> None:
        """Simula la conexión (compatibilidad; el cliente real no la invoca)."""
        self.conexiones += 1

    def disconnect(self) -> None:
        """Simula la desconexión."""
        self.desconexiones += 1

    def read_holding_registers(self, addr: int, qty: int) -> list[int]:
        """Devuelve los registros solicitados o falla según lo inyectado."""
        if self.fallos_restantes > 0:
            self.fallos_restantes -= 1
            raise OSError(f"timeout simulado leyendo {addr}")
        if self.error is not None:
            raise self.error
        self.llamadas_lectura += 1
        self.tamannos_bloque.append(qty)
        return [self.registros.get(addr + i, 0) for i in range(qty)]

    def write_multiple_holding_registers(
        self, addr: int, valores: list[int]
    ) -> list[int]:
        """Escribe registros (reservado a F4+; F2 nunca escribe)."""
        self.escrituras.append((addr, list(valores)))
        for i, valor in enumerate(valores):
            self.registros[addr + i] = valor
        return list(valores)


def registros_telemetria(mapa, familia: str, **magnitudes) -> dict[int, int]:
    """Construye un mapa de registros crudo a partir de magnitudes físicas.

    Los valores crudos se calculan invirtiendo ``convertir``:
    ``crudo = round(valor / scale) + offset`` (enmascarado a 16 bits).
    """
    registros: dict[int, int] = {}
    for funcion, valor in magnitudes.items():
        params = mapa.parametros(familia, funcion)
        crudo = round(valor / params["scale"]) + params.get("offset", 0)
        registros[mapa.direccion(familia, funcion)] = crudo & 0xFFFF
    return registros
