"""Validación de services.yaml (claves sin dominio, campos del servicio)."""
# versión 0.8.2

from pathlib import Path

import yaml

from custom_components.optimizador_bateria_omie.const import (
    DOMAIN,
    SERVICE_PROBE_REGISTERS,
)

RUTA = Path(__file__).parent.parent / "services.yaml"


def _cargar() -> dict:
    """Carga services.yaml como diccionario."""
    datos = yaml.safe_load(RUTA.read_text(encoding="utf-8"))
    assert isinstance(datos, dict)
    return datos


def test_claves_sin_dominio():
    """HA exige claves de servicio sin prefijo de dominio en services.yaml.

    Una clave ``dominio.servicio`` provoca "Unable to parse services.yaml:
    Key does not start with ." y el servicio no se registra.
    """
    for clave in _cargar():
        assert "." not in clave, f"La clave '{clave}' no debe llevar dominio"


def test_servicio_probe_registers_definido():
    """El servicio registrado en services.py debe estar descrito aquí."""
    claves = _cargar()
    assert SERVICE_PROBE_REGISTERS in claves, (
        f"Falta la descripción de {DOMAIN}.{SERVICE_PROBE_REGISTERS}"
    )


def test_campos_del_servicio():
    """Los campos declarados coinciden con el esquema voluptuous de services.py."""
    campos = _cargar()[SERVICE_PROBE_REGISTERS]["fields"]
    assert set(campos) == {"expected", "register", "familia", "reset"}
