#!/usr/bin/env python3
"""Script de diagnóstico para la integración Optimizador Batería OMIE.

Este script permite probar el servicio OMIE y verificar el formato de los datos
sin necesidad de reiniciar Home Assistant.

Uso:
    python diagnostico.py
"""

import asyncio
import sys
from datetime import UTC, date, datetime
from pathlib import Path

# Añadir el directorio custom_components al path
sys.path.insert(0, str(Path(__file__).parent / "custom_components" / "optimizador_bateria_omie"))

from cliente_omie import ClienteOmie, ConfiguracionOmie


async def test_omie_service():
    """Prueba el servicio OMIE directamente."""
    print("=" * 60)
    print("DIAGNÓSTICO: Optimizador Batería OMIE")
    print("=" * 60)
    
    # Crear cliente sin Home Assistant (simular)
    class MockHass:
        def __init__(self):
            self.config = type('obj', (object,), {'config_dir': '/config'})()
            self.services = MockServices()
            self.states = MockStates()
    
    class MockServices:
        async def async_call(self, domain, service, service_data, blocking, return_response):
            # Simular llamada al servicio OMIE
            if domain == "omie" and service == "get_prices_for_date":
                fecha_str = service_data.get("date", date.today().strftime("%Y-%m-%d"))
                print(f"Llamando a omie.get_prices_for_date con date={fecha_str}")
                
                # Devolver datos de ejemplo en el formato esperado
                # (El usuario debe reemplazar esto con la llamada real)
                return [
                    {"start": f"{fecha_str}T00:00:00+02:00", "end": f"{fecha_str}T00:15:00+02:00", "price": 0.20831},
                    {"start": f"{fecha_str}T00:15:00+02:00", "end": f"{fecha_str}T00:30:00+02:00", "price": 0.20731},
                    {"start": f"{fecha_str}T00:30:00+02:00", "end": f"{fecha_str}T00:45:00+02:00", "price": 0.20501},
                    {"start": f"{fecha_str}T00:45:00+02:00", "end": f"{fecha_str}T01:00:00+02:00", "price": 0.20300},
                ]
            return None
    
    class MockStates:
        def get(self, entity_id):
            return None
    
    # Crear cliente
    config = ConfiguracionOmie(
        fuente_prioritaria="omie",
        usar_cache=False,
        dias_cache=7,
    )
    
    hass = MockHass()
    cliente = ClienteOmie(hass, config)
    
    # Probar obtener precios
    hoy = date.today()
    print(f"\nProbando obtener_precios_del_dia para {hoy}...")
    
    try:
        ranuras = await cliente.obtener_precios_del_dia(hoy)
        print(f"✅ Éxito: Obtenidas {len(ranuras)} ranuras")
        
        if ranuras:
            print("\nPrimeras 5 ranuras:")
            for i, ranura in enumerate(ranuras[:5]):
                print(f"  {i}: {ranura.fecha_hora_utc.isoformat()} - {ranura.precio_eur_kwh}€/kWh (fuente: {ranura.fuente})")
        
        # Probar normalización
        print("\nProbando normalización...")
        datos_ejemplo = [
            {"start": f"{hoy.strftime('%Y-%m-%d')}T00:00:00+02:00", "end": f"{hoy.strftime('%Y-%m-%d')}T00:15:00+02:00", "price": 0.20831},
            {"start": f"{hoy.strftime('%Y-%m-%d')}T00:15:00+02:00", "end": f"{hoy.strftime('%Y-%m-%d')}T00:30:00+02:00", "price": 0.20731},
        ]
        ranuras_norm = cliente._normalizar_a_15min(datos_ejemplo, hoy)
        print(f"✅ Normalización: {len(ranuras_norm)} ranuras")
        
    except Exception as e:  # noqa: BLE001 (script de diagnóstico: se reporta cualquier fallo)
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


async def test_timezone_conversion():
    """Prueba la conversión de timezone."""
    print("\n" + "=" * 60)
    print("PRUEBA: Conversión de Timezone")
    print("=" * 60)
    
    from datetime import timedelta
    
    # Simular el formato del servicio OMIE
    start_str = "2026-10-06T00:00:00+02:00"
    start_dt = datetime.fromisoformat(start_str)
    
    print(f"Original: {start_str}")
    print(f"Parseado: {start_dt.isoformat()}")
    print(f"Timezone: {start_dt.tzinfo}")
    
    # Convertir a UTC
    if start_dt.tzinfo is None:
        start_utc = start_dt.replace(tzinfo=UTC)
    else:
        start_utc = start_dt.astimezone(UTC)
    
    print(f"En UTC: {start_utc.isoformat()}")
    
    # Obtener hora actual en UTC
    ahora_utc = datetime.now(tz=UTC)
    print(f"Ahora UTC: {ahora_utc.isoformat()}")
    
    # Comparar
    if start_utc <= ahora_utc < start_utc + timedelta(minutes=15):
        print("✅ La ranura incluye la hora actual")
    else:
        print("❌ La ranura NO incluye la hora actual")


if __name__ == "__main__":
    print("Ejecutando pruebas de diagnóstico...\n")
    
    # Ejecutar pruebas
    asyncio.run(test_omie_service())
    asyncio.run(test_timezone_conversion())
    
    print("\n" + "=" * 60)
    print("DIAGNÓSTICO COMPLETADO")
    print("=" * 60)
    print("\nSi los sensores muestran 'desconocido', verifica:")
    print("1. Que el servicio omie.get_prices_for_date devuelve datos")
    print("2. Que los datos tienen el formato: [{'start': '...', 'end': '...', 'price': ...}]")
    print("3. Que el directorio cache existe: /config/custom_components/optimizador_bateria_omie/cache")
    print("4. Los logs de Home Assistant para ver mensajes de error")
