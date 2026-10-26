#!/usr/bin/env python3
"""Script para instalar/actualizar el custom component en Home Assistant.

Uso:
    python hacs.py install              # Copia a custom_components de HA
    python hacs.py uninstall            # Elimina el componente
    python hacs.py status               # Verifica estado

Requisitos:
    - Este script debe ejecutarse desde el directorio raíz del repositorio
    - Home Assistant debe estar parado o se debe reiniciar después de instalar
"""

import os
import shutil
import sys
from pathlib import Path

#Constantes
COMPONENT_NAME = "optimizador_bateria_omie"
SOURCE_DIR = Path(f"custom_components/{COMPONENT_NAME}")


# Funciones de utilidad

def get_ha_config_dir() -> Path:
    """Detecta el directorio de configuración de Home Assistant."""
    # Intentar diferentes ubicaciones comunes
    home_dir = Path.home()
    
    # Ubicaciones típicas
    locations = [
        home_dir / "homeassistant",
        home_dir / ".homeassistant",
        home_dir / "config",
        Path("/config"),  # Docker
        Path("/usr/share/hassio/homeassistant"),  # HassOS
        Path("/config"),  # HassOS
    ]
    
    for loc in locations:
        if (loc / "configuration.yaml").exists():
            return loc
    
    # Buscar en el sistema
    for root, dirs, files in os.walk(home_dir):
        if "configuration.yaml" in files:
            return Path(root)
    
    raise FileNotFoundError(
        "No se encontró el directorio de configuración de Home Assistant. "
        "Por favor, especifica el path con --config-dir /ruta/a/config"
    )


def check_structure():
    """Verifica que la estructura del proyecto es correcta."""
    required_files = [
        "__init__.py",
        "manifest.json",
        "config_flow.py",
        "sensor.py",
        "cliente_omie.py",
        "tarifa.py", 
        "coordinador.py",
        "entidades.py",
        "const.py",
    ]
    
    if not SOURCE_DIR.exists():
        print(f"❌ Error: Directorio {SOURCE_DIR} no existe")
        sys.exit(1)
    
    missing = []
    for file in required_files:
        if not (SOURCE_DIR / file).exists():
            missing.append(file)
    
    if missing:
        print(f"❌ Error: Faltan archivos: {', '.join(missing)}")
        sys.exit(1)
    
    print("✅ Estructura del componente verificada")


def install_component(config_dir: Path, force: bool = False):
    """Instala el componente en el directorio de HA."""
    target_dir = config_dir / "custom_components" / COMPONENT_NAME
    
    if target_dir.exists():
        if force:
            print("🗑️  Eliminando instalación existente...")
            shutil.rmtree(target_dir)
        else:
            print(f"⚠️  El componente ya está instalado en {target_dir}")
            return
    
    print(f"📦 Copiando {SOURCE_DIR} a {target_dir}...")
    shutil.copytree(SOURCE_DIR, target_dir)
    print(f"✅ Componente instalado en {target_dir}")
    print("\n📝 Para activarlo:")
    print("   1. Reinicie Home Assistant")
    print("   2. Vaya a Configuración → Dispositivos y servicios → + Añadir integración")
    print(f"   3. Busque '{COMPONENT_NAME}'")


def uninstall_component(config_dir: Path):
    """Desinstala el componente de HA."""
    target_dir = config_dir / "custom_components" / COMPONENT_NAME
    
    if not target_dir.exists():
        print(f"⚠️  El componente no está instalado en {target_dir}")
        return
    
    print(f"🗑️  Eliminando {target_dir}...")
    shutil.rmtree(target_dir)
    print("✅ Componente desinstalado")


def check_status(config_dir: Path):
    """Verifica el estado de la instalación."""
    target_dir = config_dir / "custom_components" / COMPONENT_NAME
    
    if not target_dir.exists():
        print("❌ Componente NO instalado")
        return
    
    print(f"✅ Componente instalado en: {target_dir}")
    
    # Verificar archivos clave
    required_files = ["__init__.py", "manifest.json"]
    missing = []
    for file in required_files:
        if not (target_dir / file).exists():
            missing.append(file)
    
    if missing:
        print(f"⚠️  Archivos faltantes: {', '.join(missing)}")
    else:
        print("✅ Todos los archivos presentes")


def main():
    """Punto de entrada principal."""
    # Verificar que estamos en el directorio del repositorio
    if not Path("custom_components").exists():
        print("❌ Error: Ejecuta este script desde el directorio raíz del repositorio")
        sys.exit(1)
    
    # Obtener acción
    if len(sys.argv) < 2:
        print("Uso: python hacs.py [install|uninstall|status]")
        print("\nOpciones:")
        print("  install    - Instala/actualiza el componente en Home Assistant")
        print("  uninstall  - Elimina el componente de Home Assistant")
        print("  status    - Verifica si el componente está instalado")
        sys.exit(1)
    
    action = sys.argv[1].lower()
    force = "--force" in sys.argv
    config_dir = None
    
    # Parsear argumento de directorio personalizado
    if "--config-dir" in sys.argv:
        idx = sys.argv.index("--config-dir")
        if idx + 1 < len(sys.argv):
            config_dir = Path(sys.argv[idx + 1])
    
    # Obtener directorio de configuración de HA
    try:
        if config_dir is None:
            config_dir = get_ha_config_dir()
        print(f"📁 Directorio de configuración: {config_dir}")
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)
    
    # Verificar estructura
    check_structure()
    
    # Ejecutar acción
    if action == "install":
        install_component(config_dir, force)
    elif action == "uninstall":
        uninstall_component(config_dir)
    elif action == "status":
        check_status(config_dir)
    else:
        print(f"❌ Acción desconocida: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
