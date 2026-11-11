# Informe de incidencia: fallo del interruptor "Usar planificador LP"

**Fecha:** 2026-11-11 · **Prioridad:** alta (el toggle LP es inoperativo y arrastra a los Number de parámetros calientes) · **Estado:** ✅ **corregido en 0.12.3** (`escribir_parametro_caliente(hass, entry, …)` con `hass` por argumento; test de regresión `TestSwitchUsarLP::test_turn_on_entry_sin_hass` con `ConfigEntry` simulado **sin** atributo `hass`)

## Entorno

- Home Assistant **2026.11.1** (core con **Python 3.14**), Home Assistant Yellow (aarch64)
- Integración desplegada: **0.12.2** (`requirements: numpy>=1.26, pulp, pysolarmanv5` — sin scikit-learn)
- Repositorio fuente: `custom_components/optimizador_bateria_omie` (sincronizado con lo desplegado salvo `manifest.json`, ver Observaciones)

## Síntoma y pasos de reproducción

1. Configuración → Integraciones → Optimizador Batería OMIE.
2. Conmutar el interruptor **"Usar planificador LP"** (entidad `switch.inversor_optimizador_bateria_omie_usar_planificador_lp`), ON u OFF.
3. Resultado:

   ```
   No se pudo realizar la acción switch/turn_on.
   'ConfigEntry' object has no attribute 'hass'
   ```

Consecuencia funcional: la selección en caliente del planner (D-F5.2) no opera; el sistema permanece siempre en greedy.

## Causa raíz

`entidades_control.py`, helper `escribir_parametro_caliente()` (línea ~118):

```python
def escribir_parametro_caliente(entry, clave: str, valor: float) -> None:
    """... (docstring: vía oficial 0.12.2 para entry.data inmutable) ..."""
    datos = dict(entry.data)
    datos[clave] = valor
    entry.hass.config_entries.async_update_entry(entry, data=datos)   # ← aquí
```

**`ConfigEntry.hass` ya no existe en HA ≥ 2026.11.** El comentario del código documenta la migración correcta a `hass.config_entries.async_update_entry(entry, data=…)` (necesaria desde que `entry.data` es un `MappingProxyType` inmutable), pero el acceso a `hass` **a través del `ConfigEntry`** es una API interna que HA ha eliminado en 2026.11. Al conmutar el switch, `entry.hass` resuelve a atributo inexistente → `AttributeError` → HA lo reporta como fallo del servicio `switch/turn_on`.

**Verificación empírica** (core 2026.11.1 desplegado): en `/usr/src/homeassistant/homeassistant/config_entries.py`, la clase `ConfigEntry` (línea 441) ya no define `self.hass`; las ocurrencias de `self.hass` en ese fichero pertenecen a los gestores de *config flows*, no a `ConfigEntry`.

## Alcance

Cualquier entidad que persista parámetros en caliente con ese helper falla igual:

| Afectado | Vía | Consecuencia |
|---|---|---|
| `SwitchUsarLP.async_turn_on/off` (líneas ~153, ~161) | `escribir_parametro_caliente` | Fallo reportado: LP inoperativo |
| Number de parámetros calientes (`async_set_native_value`, línea ~228) — p. ej. `soc_min`, precios, potencias | `escribir_parametro_caliente` | Moverá el slider → mismo AttributeError |
| `SwitchControlActivo`, sensores, plan/executor | no usan el helper | No afectados |

Búsqueda de ocurrencias: `entry.hass` aparece **solo** en este helper en todo el código (verificado con grep sobre el paquete). No hay otros puntos de ruptura por este patrón.

## Corrección sugerida (NO aplicada)

Pasar `hass` explícitamente (las entidades ya lo tienen como `self.hass` heredado de la plataforma):

```python
def escribir_parametro_caliente(hass, entry, clave: str, valor: float) -> None:
    datos = dict(entry.data)
    datos[clave] = valor
    hass.config_entries.async_update_entry(entry, data=datos)
```

Y en los tres call-sites: `escribir_parametro_caliente(self.hass, self._entry, …)`.

**Test de regresión recomendado:** los tests actuales falsifican el `entry` con un objeto que probablemente sí expone `.hass` (por eso la suite no detectó esto). Añadir un test donde el `ConfigEntry` simulado **no tenga** atributo `hass`, garantizando que la persistencia en caliente funciona vía `hass` por argumento. Nota: ni ruff ni mypy cazan esto (atributo dinámico); el test es la única red.

## Observaciones adicionales

1. **Desincronización repo ↔ despliegue**: el `manifest.json` del repo dice **0.12.0** (con `README.md` pendiente de commit), mientras lo desplegado es **0.12.2** (añade `numpy>=1.26`, retira `scikit-learn`). La retirada de scikit-learn era necesaria y acertada: con Python 3.14 no hay rueda `scikit-learn` para aarch64 y el instalador intentaba compilar desde fuente (`meson` sin permisos), lo que bloqueaba **toda** la integración en el arranque (fallo de carga del 2026-11-10, "Requirements … not found: ['scikit-learn>=1.3']"). Conviene commitear el estado real para que repo = fuente de verdad.
2. **ML deshabilitado de facto**: verificar en runtime que `ForecasterML` capture el `ImportError` de sklearn con WARNING claro y degrade al perfil estadístico (el guardrail de adopción D-F5.2 ya contempla el fallback; solo falta confirmar el manejo del import ausente, p. ej. actualizando `metricas_ml["disponible"] = False`).
3. **Auditoría de breaking changes 2026.11**: este fallo sugiere una pasada de revisión de superficie de API del componente frente a los cambios de HA 2026.11 (además de `ConfigEntry.hass`, revisar usos de atributos interns de config entries y cualquier API señalada como deprecada en 2026.x).

## Reproducción mínima (para test)

1. Simular un `ConfigEntry` cuya clase no defina `hass` (fiel al HA 2026.11).
2. Instanciar `SwitchUsarLP` con ese entry y un `CoordinadorPlan` mockeado.
3. `await switch.async_turn_on()`:
   - **Actual:** `AttributeError: 'ConfigEntry' object has no attribute 'hass'`
   - **Tras el fix:** `hass.config_entries.async_update_entry` llamado con `data` actualizado y `coordinador_plan.async_recalcular("algoritmo_lp")` invocado sin error.
