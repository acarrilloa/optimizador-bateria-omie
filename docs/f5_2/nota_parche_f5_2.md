# Nota de parche F5.2: rearme de puntos de arranque (228/229) y cap de exportación (245)

**Para:** Mistral Vibe (Studio Code Server)
**Base:** v0.14.4 (con el parche del livelock del verify ya aplicado y verificado en producción el 21-nov-2026)
**Parche asociado:** `docs/parche_f5_2_arranque_venta.diff`
**Sugerido:** bump de `manifest.json` a `0.14.5`.

## 1. Incidente que motiva el parche (todo verificado con lecturas reales)

A las 11:36 del 21-nov-2026 el executor ejecutó por fin un tramo CARGA_RED completo
(210/230/232 con `verificado: "ok"`) pero la batería quedó a 0 W: ni cargaba ni descargaba
(import = consumo − FV exacto). Lecturas realizadas (Solarman, `address`+`count`):

- Batería sana y comunicando: 183 = 52,03 V, 184 = SOC 25 %, 182 = 26 °C (offset +1000).
- **228 = 4900 (49,00 V)** y **229 = 90**: los puntos de arranque NO estaban en el bypass
  de la noche anterior (228=6100, 229=100). Nadie los tocó a mano.
- 232 = 1 y 230 = 94 correctamente escritos por el executor.

**Semántica confirmada (V118 + evidencia):** el Deye evalúa la carga desde red como **AND**:
solo arranca si voltaje batería < 228 **Y** SOC < 229. La batería a 52 V no pasa el umbral de
49 V → el inversor queda en "carga pendiente", con la descarga suspendida (de ahí los 0 W).
Anoche cargó porque el bypass puso 228=6100 (61 V, siempre cierto).

**Prueba de que el entorno reescribe**: tras reiniciar HA con la 0.14.4, el datalogger
Solarman re-empujó configuración: el ToU propio del inversor reapareció HABILITADO y los
puntos de arranque derivaron (228→4900, 229→90). El usuario deshabilitó el ToU a mano.
Conclusión de diseño: **toda escritura de configuración puede revertirse en cualquier momento**;
el executor debe sanear lo crítico en cada tramo en vez de confiar en valores persistentes.

## 2. Qué hace el parche (3 cambios)

1. **Sanear 228/229 antes de cada CARGA_RED** (`_asegurar_arranque_carga`): relee los dos
   registros (fuera de cuota del rate limiter) y reescribe solo si derivaron del objetivo
   (228=6100, 229=100, configurables vía `arranque_carga_red_v` / `arranque_carga_red_soc`).
   En el caso normal añade 2 lecturas y 0 escrituras. Si no se puede garantizar el estado,
   **aborta el tramo** (estado simulado `arranque_bloqueado` + auditoría): armar un 232=1 con
   el arranque bloqueado congela la batería a 0 W, que es peor que no armar nada.
   El saneamiento corre ANTES del guard D9 (para que cure incluso tramos idempotentes con
   `pendientes` vacíos) y sus escrituras aterrizan ANTES del bucle que escribe 232=1.
2. **Cap de VENTA_RED al límite real de exportación** (`_refrescar_max_exportacion`):
   lee el 245 (caché 1 h) y `_objetivos_tramo` recorta la corriente de venta a
   `min(corriente, 245 / voltaje_nominal)`. Hoy: 245=5000 W y el plan manda 5193 W.
   El registro 245 controla con CT externo (244=2).
3. **Nuevo mapa**: `arranque_carga_red_v` (228), `arranque_carga_red_soc` (229) y
   `max_exportacion_red` (245) en `registers.yaml` (familia estandar, grupo control).

## 3. Articulaciones delicadas (revisar al aplicar)

- El diff de `const.py` y del bloque de imports de `executor.py` indica posiciones por texto,
  no por línea exacta: respetar el orden alfabético de los bloques existentes.
- `_estado_simulado = "arranque_bloqueado"`: comprobar que las entidades de control
  (`entidades_control.py`) toleran el nuevo valor (hoy se usan "gradual" y "rate_limiter");
  si hay un mapeo cerrado de estados, añadirlo. Un test de regresión debería cubrirlo.
- `_param(...)` firma y `mapa.direccion(familia, funcion, grupo)`: ya usados así en el archivo.
- El diff contiene una pérdida accidental de prefijo `+` en una línea de
  `_refrescar_max_exportacion` (señalada con una nota `***`): corregir al aplicar.

## 4. Tests (añadir a tests/test_executor.py)

El fake del servicio HA ya sirve lecturas `address`+`count` (parche livelock). Ampliar:

1. **Deriva**: el fake responde 228=4900 → el tramo CARGA_RED escribe 6100 en 228 (y 229 si
   deriva) ANTES de escribir 232=1; auditoría con `motivo` "…: rearme arranque (leído 4900)".
2. **Sin deriva**: fake responde 6100/100 → `.escrituras` del fake solo contiene 210/230/232
   (cero escrituras extra).
3. **Arranque no asegurable**: la lectura de 228 lanza `ErrorEscrituraSolarman` → tramo
   abortado, `_estado_simulado == "arranque_bloqueado"`, y NO se escribe 232=1.
4. **Cap de venta**: fake responde 245=5000 → tramo VENTA_RED de 5193 W con voltaje nominal
   51,2 V produce corriente 97 A (no 101); con 245 sin leer, corriente sin capar.

Ejecutar `pytest -v` completo y `ruff check .`; actualizar `manifest.json` a 0.14.5.

## 5. Verificación en producción

1. Instalar y re-encender control. En el tramo CARGA_RED actual: la auditoría no debe mostrar
   rearme (el usuario habrá vuelta a fijar 228=6100 a mano); si lo muestra, confirmar que la
   importación salta (consumo + ~4,8 kW).
2. `grep` diario de "deriva del objetivo" como detector temprano de nuevos pushes del
   datalogger: cada aparición indica que Solarman re-empujó configuración y conviene revisar
   también el ToU del inversor (la cura del parche es paliativa; el ToU sigue requiriendo vigilancia).
3. El tramo VENTA_RED del plan debe quedar ≤ 5000 W.

## 6. Fuera de alcance (proponer como F5.3/F6, no incluir aquí)

- planner_lp: capar la potencia de venta planificada al 245 real (hoy planifica 5193 W y el
  ahorro calculado es ligeramente optimista).
- Detectar y avisar de la reactivación del ToU propio del inversor tras reinicios (registro
  exacto del enable de "Program charging" no confirmado; usar `probe_registers` sobre
  248-279 la próxima vez que reaparezca el fenómeno — 248 es el ToU **selling**).
- Saneo general anti-push de 210/211/230/232 (relectura periódica contra deriva): hoy se
  corrigen solos en el siguiente tramo no idempotente, pero una deriva invisible entre tramos
  puede dejarte horas sin carga (similar al incidente de hoy).
