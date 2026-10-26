#!/usr/bin/env python3
"""Script para verificar la estructura del componente."""

import sys
from pathlib import Path

COMPONENT_NAME = "optimizador_bateria_omie"
COMPONENT_DIR = Path(f"custom_components/{COMPONENT_NAME}")


def check_file_exists(file_path: Path) -> bool:
    return file_path.exists()


def check_file_content(file_path: Path, required_content: list[str]) -> bool:
    if not file_path.exists():
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        for required in required_content:
            if required not in content:
                return False
        return True
    except Exception as e:  # noqa: BLE001 (script de diagnóstico: se reporta cualquier fallo)
        print(f"    Error: {e}")
        return False


def main():
    print("=" * 60)
    print("CHECK: Optimizador Bateria OMIE")
    print("=" * 60)
    print()
    
    if not Path("custom_components").exists():
        print("ERROR: No estas en el directorio raiz")
        sys.exit(1)
    
    if not COMPONENT_DIR.exists():
        print(f"ERROR: {COMPONENT_DIR} no existe")
        sys.exit(1)
    
    print(f"Component dir: {COMPONENT_DIR}")
    print()
    
    required_files = {
        "__init__.py": ["async def async_setup_entry", "async def async_unload_entry"],
        "manifest.json": ['"domain": "optimizador_bateria_omie"', '"config_flow": true'],
        "config_flow.py": ["class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN)", "VERSION = 1"],
        "sensor.py": ["async def async_setup_entry", "async_add_entities"],
        "const.py": ['DOMAIN = "optimizador_bateria_omie"', "FESTIVOS_ESPANA"],
        "cliente_omie.py": ["class ClienteOmie", "class Ranura", "class ConfiguracionOmie"],
        "tarifa.py": ["class ModeloTarifa", "class ConfiguracionTarifa"],
        "coordinador.py": ["class CoordinadorDatosOmie", "class DatosOmie"],
        "entidades.py": ["class SensorPrecioActual", "class SensorPrecioEfectivo", "class SensorPrecioClasificacion", "class SensorPrecioEstadisticas"],
    }
    
    required_dirs = {"translations": ["es.json"]}
    
    print("ARCHIVOS:")
    print("-" * 60)
    
    all_ok = True
    for file_name, required_content in required_files.items():
        file_path = COMPONENT_DIR / file_name
        if not check_file_exists(file_path):
            print(f"[X] {file_name}: MISSING")
            all_ok = False
        else:
            if check_file_content(file_path, required_content):
                print(f"[+] {file_name}: OK")
            else:
                print(f"[!] {file_name}: CONTENT ERROR")
                all_ok = False
    
    print()
    print("DIRECTORIOS:")
    print("-" * 60)
    
    for dir_name, files_in_dir in required_dirs.items():
        dir_path = COMPONENT_DIR / dir_name
        if not dir_path.exists():
            print(f"[X] {dir_name}/: MISSING")
            all_ok = False
        else:
            for f in files_in_dir:
                if not (dir_path / f).exists():
                    print(f"[X] {dir_name}/{f}: MISSING")
                    all_ok = False
                else:
                    print(f"[+] {dir_name}/{f}: OK")
    
    print()
    print("=" * 60)
    
    if all_ok:
        print("SUCCESS: Todos los archivos son correctos")
        print()
        print("PASOS:")
        print("  1. python hacs.py uninstall")
        print("  2. python hacs.py install --force")
        print("  3. Reinicia Home Assistant")
    else:
        print("ERROR: Faltan archivos")
        print("  Solucion: python hacs.py install --force")
    
    print("=" * 60)


if __name__ == "__main__":
    main()
