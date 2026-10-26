"""Mapa de registros Modbus del inversor (registers.yaml).

Las direcciones de registros viven SOLO en ``registers.yaml`` (empaquetado)
o en su override de usuario ``config/optimizador_bateria_omie/registers.yaml``.
Este módulo carga, valida y expone el mapa; nunca define direcciones.
"""
# versión 0.8.0

import logging
from pathlib import Path
from typing import Any

import yaml

from custom_components.optimizador_bateria_omie.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Límite duro de la limitación del datalogger (AGENTS.md) — el YAML no puede
# ampliarlo, solo reducirlo.
BLOCK_SIZE_MAX = 16


class ErrorMapaRegistros(Exception):
    """Error de carga o validación del mapa de registros."""


class MapaRegistros:
    """Carga y acceso al mapa de registros del inversor.

    Prioridad de carga: override de usuario en
    ``config/optimizador_bateria_omie/registers.yaml`` si existe; si no,
    el fichero empaquetado junto al componente.
    """

    def __init__(self, hass, ruta_override: Path | None = None) -> None:
        """Inicializa el mapa.

        ``ruta_override`` permite inyectar la ruta del override en tests;
        si es ``None`` se deriva de ``hass.config.config_dir``.
        """
        self.hass = hass
        self._ruta_override = ruta_override
        self._datos: dict[str, Any] = {}

    async def async_cargar(self) -> None:
        """Carga el mapa en un executor (lectura de disco bloqueante)."""
        await self.hass.async_add_executor_job(self.cargar)

    def cargar(self) -> None:
        """Carga, valida y almacena el mapa de registros."""
        ruta = self._resolver_ruta()
        texto = ruta.read_text(encoding="utf-8")
        self._avisar_todo(texto, ruta)

        try:
            datos = yaml.safe_load(texto)
        except yaml.YAMLError as err:
            raise ErrorMapaRegistros(f"registers.yaml no es YAML válido: {err}") from err

        self._validar(datos, ruta)
        self._datos = datos
        _LOGGER.info(
            "Mapa de registros cargado (%s), familias: %s, block_size: %d",
            ruta.name,
            ", ".join(self.familias()),
            self.block_size,
        )

    def _resolver_ruta(self) -> Path:
        """Devuelve la ruta del YAML: override de usuario o empaquetado."""
        if self._ruta_override is not None:
            if self._ruta_override.exists():
                return self._ruta_override
            _LOGGER.warning(
                "Override %s no existe; se usa el mapa empaquetado",
                self._ruta_override,
            )
            return Path(__file__).parent / "registers.yaml"

        override = (
            Path(self.hass.config.config_dir) / DOMAIN / "registers.yaml"
        )
        if override.exists():
            _LOGGER.info(
                "Usando override de usuario de registers.yaml: %s", override
            )
            return override
        return Path(__file__).parent / "registers.yaml"

    @staticmethod
    def _avisar_todo(texto: str, ruta: Path) -> None:
        """Los TODO en comentarios son avisos, no errores (AGENTS.md)."""
        for numero, linea in enumerate(texto.splitlines(), start=1):
            if "TODO" in linea:
                _LOGGER.warning(
                    "TODO pendiente en %s:%d: %s",
                    ruta, numero, linea.strip(),
                )

    def _validar(self, datos: Any, ruta: Path) -> None:
        """Valida la estructura mínima del mapa."""
        if not isinstance(datos, dict):
            raise ErrorMapaRegistros(f"{ruta}: el YAML no es un diccionario")

        familias = datos.get("familias")
        if not familias or not isinstance(familias, dict):
            raise ErrorMapaRegistros(
                f"{ruta}: falta la sección 'familias' o está vacía"
            )

        block_size = datos.get("block_size")
        if not isinstance(block_size, int) or not 1 <= block_size <= BLOCK_SIZE_MAX:
            raise ErrorMapaRegistros(
                f"{ruta}: block_size={block_size} no válido "
                f"(límite del datalogger: {BLOCK_SIZE_MAX})"
            )

        for familia, contenido in familias.items():
            if not isinstance(contenido, dict):
                raise ErrorMapaRegistros(f"{ruta}: familia {familia} mal formada")
            probe = contenido.get("probe")
            if not isinstance(probe, dict) or "funcion" not in probe or "addr" not in probe:
                raise ErrorMapaRegistros(
                    f"{ruta}: familia {familia} sin 'probe' completo (funcion, addr)"
                )
            telemetria = contenido.get("telemetria")
            if not isinstance(telemetria, dict) or not telemetria:
                raise ErrorMapaRegistros(
                    f"{ruta}: familia {familia} sin sección 'telemetria'"
                )

    @property
    def inverter(self) -> str:
        """Identificador del perfil de inversor."""
        return str(self._datos.get("inverter", ""))

    @property
    def block_size(self) -> int:
        """Máximo de registros por petición Modbus."""
        return int(self._datos.get("block_size", BLOCK_SIZE_MAX))

    def familias(self) -> list[str]:
        """Nombres de las familias candidatas del mapa."""
        return list(self._datos.get("familias", {}))

    def probe(self, familia: str) -> dict[str, Any]:
        """Sonda de la familia: ``{"funcion": ..., "addr": ...}``."""
        contenido = self._familia(familia)
        probe = contenido.get("probe")
        if not isinstance(probe, dict):
            raise ErrorMapaRegistros(f"Familia {familia} sin 'probe'")
        return dict(probe)

    def plausibility(self, familia: str) -> dict[str, Any]:
        """Umbrales de plausibilidad de la familia (del YAML)."""
        contenido = self._familia(familia)
        plausibilidad = contenido.get("plausibility")
        if not isinstance(plausibilidad, dict):
            return {}
        return plausibilidad

    def parametros(
        self, familia: str, funcion: str, tipo: str = "telemetria"
    ) -> dict[str, Any]:
        """Parámetros de una función (addr, scale, unit, signed, invertir_signo)."""
        contenido = self._familia(familia)
        seccion = contenido.get(tipo)
        if not isinstance(seccion, dict) or funcion not in seccion:
            raise ErrorMapaRegistros(
                f"Función '{funcion}' (tipo '{tipo}') no definida en el mapa "
                f"para la familia {familia}"
            )
        params = dict(seccion[funcion])
        params.setdefault("scale", 1)
        params.setdefault("unit", "")
        params.setdefault("signed", False)
        params.setdefault("invertir_signo", False)
        return params

    def direccion(self, familia: str, funcion: str, tipo: str = "telemetria") -> int:
        """Dirección Modbus de una función de una familia."""
        params = self.parametros(familia, funcion, tipo)
        return int(params["addr"])

    def _familia(self, familia: str) -> dict[str, Any]:
        """Contenido de una familia del mapa."""
        familias = self._datos.get("familias", {})
        if familia not in familias:
            raise ErrorMapaRegistros(
                f"Familia '{familia}' no definida en el mapa: "
                f"{', '.join(familias) or '(ninguna)'}"
            )
        return familias[familia]

    @staticmethod
    def complemento_a_dos(valor: int) -> int:
        """Interpreta un registro de 16 bits en complemento a dos."""
        if valor & 0x8000:
            return valor - 0x10000
        return valor

    @classmethod
    def convertir(cls, crudo: int, params: dict[str, Any]) -> float:
        """Convierte un valor crudo de registro a magnitud física.

        Orden: complemento a dos (si ``signed``) → ``scale`` →
        ``invertir_signo`` (multiplica por −1 tras el complemento a dos).
        """
        valor: int | float = crudo
        if params.get("signed"):
            valor = cls.complemento_a_dos(int(valor))
        valor = valor * float(params.get("scale", 1))
        if params.get("invertir_signo"):
            valor = -valor
        return float(valor)
