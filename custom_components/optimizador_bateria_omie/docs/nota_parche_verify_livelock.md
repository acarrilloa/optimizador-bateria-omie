# Nota de parche: livelock del write-verify del executor (incidente 20-nov-2026)

**Para:** Mistral Vibe (Studio Code Server)
**Componente:** `custom_components/optimizador_bateria_omie/executor.py` (v0.14.2, commit 170e44b)
**Parche asociado:** `docs/parche_verify_livelock.diff` (unified diff contra el contenido actual del archivo)
**Prioridad:** alta. Es el bloqueador que impide que el optimizador gobierne de verdad el inversor Deye.

## 1. Resumen del incidente

La auditoría de escrituras (`/config/optimizador_bateria_omie/auditoria_escrituras.jsonl`, ~5.000 líneas)
contiene **cero entradas con `"ejecutada": true` en toda su historia**. Síntomas observados en producción:

- `escrituras_ultima_hora` clavado en 10 (cuota del rate limiter quemada por escrituras "fantasma").
- Todo lo demás queda permanentemente `"motivo": "aplazado por rate limiter"` (force_mode incluido).
- El никогда verde: 210/217/218 sí aterrizaban en el inversor (verificado por lectura manual), pero el bucle de
  escritura de `async_aplicar_tramo` nunca pasaba del primer registro: 230/232 no se escribían jamás.
- Ningún error ni warning en el log de HA Core durante los episodios.

## 2. Causa raíz (verificada empíricamente)

En la integración Solarman **instalada** (generación legada), el servicio de lectura existente es
`solarman.read_holding_registers` y solo responde al payload `{device, address, count}`:

- `EscritorSolarman.leer()` llamaba con `{register: addr, quantity|count: n}`.
- Ese esquema **pasa la validación del servicio pero la llamada no retorna nunca**: el futuro de
  `hass.services.async_call(..., blocking=True, return_response=True)` queda colgado, sin excepción,
  sin warning y **sin línea de auditoría**.
- Por tanto `_verificar()` nunca regresa tras `escribir(210)`: el bucle muere en el primer registro.
  La cuota (`_escrituras_ts`) ya se había consumido de camino (el append ocurre justo después de
  `escribir`), de ahí los 10 intentos/hora fantasma que saturan el rate limiter.

Nota: `_servicio()` prefiere `read_multiple_holding_registers` como "ACTUAL", pero ese servicio **no existe**
en la instalación del usuario; la detección en caliente cae al legado. Las **escrituras** sí funcionan tal cual
(`{register, values}` vía `write_multiple_registers`): no se tocan.

## 3. Qué hace el parche (3 fixes)

| Fix | Zona | Cambio |
|-----|------|--------|
| A | `EscritorSolarman.leer()` + constantes | Lectura pasa a `{address, count}` en ambas generaciones. Se elimina `CAMPO_CANTIDAD_LEGADO` ("quantity") y se añade `CAMPO_DIRECCION = "address"`. |
| B | `EscritorSolarman._invocar()` | Toda llamada al servicio se envuelve en `asyncio.wait_for(..., timeout=SERVICIO_TIMEOUT_S)` (15 s). `asyncio.TimeoutError` → `ErrorEscrituraSolarman("...no respondió...posible cuelgue")`. Protege lecturas, escrituras, activación atómica y fallback. |
| C | Bucle de escrituras de `async_aplicar_tramo` | `try/except ErrorEscrituraSolarman` alrededor de `escribir()`: audita la tentativa con `ejecutada=false, verificado="error_escritura"` antes de re-lanzar. Ningún fallo queda invisible ni sin rastro; el intento fallido no consume cuota (el append solo ocurre tras éxito). |

Con B, el cuelgue se convierte en `ErrorEscrituraSolarman`, que `_verificar()` ya captura → `verificado="parcial"`
(auditoría visible, no cuenta para fallback, D5). Con A, en la instalación legada el verify directamente funciona.

## 4. Aplicación

```bash
# desde la raíz del repo (HA-acarrilloa-desarrollo)
git apply custom_components/optimizador_bateria_omie/docs/parche_verify_livelock.diff
# o, si prefiere edición manual, aplicar los 4 hunks del diff (uno por zona de la tabla)
```

Si `git apply` retoque por contexto (el archivo puede haber cambiado desde que se generó el diff), aplicar los
hunks a mano: son idempotentes conceptualmente y están anclados a texto exacto de la 0.14.2.

## 5. Tests: actualizar + añadir

Mocks actuales que asumen el esquema viejo de lectura (buscar por `read_holding`,
`read_multiple_holding_registers`, `"quantity"`, `"register"`):

- `tests/test_executor.py` (~líneas 497-506): el test de generación actual afirma
  `carga == {"device": "device-x", "register": 210, "count": 1}` →
  cambiar a `{"device": "device-x", "address": 210, "count": 1}`.
  El test de generación legada (si existe) afirmará `register`+`quantity` → `address`+`count`.
- Cualquier fake/mónimo que implemente el handler del servicio HA y lea `call.data["register"]` para
  lecturas → pasar a `call.data["address"]`. (Ojo: los *writes* siguen validando `register`+`values`:
  test_executor.py ~466 no cambia.)
- `asyncio.wait_for` en `_invocar`: los fakes deben seguir siendo awaitables normales (los actuales ya lo son).

Tests nuevos (añadir a `tests/test_executor.py`):

1. **Timeout de lectura**: handler de lectura que nunca retorna (`await asyncio.sleep(3600)`) →
   `_verificar` devuelve `"parcial"` en ≤ SERVICIO_TIMEOUT_S (parchear la constante a 0.1 s en el test),
   la auditoría registra `ejecutada=true, verificado="parcial"`, y el executor sigue vivo (siguiente tramo aplicable).
2. **Error de escritura auditable**: `escribir` lanza `ErrorEscrituraSolarman` →
   auditoría con `ejecutada=false, verificado="error_escritura"` y la excepción se propaga al llamador.
3. **Cuota intacta**: tras un error de escritura, `_escrituras_ts` no crece (el append va tras el éxito).

Ejecutar: `pytest -v` (403+ tests deben pasar) y `ruff check .` (debe quedar limpio; los f-strings nuevos
cumplen el estilo existente).

## 6. Verificación en producción (después de instalar)

1. Encender `control_activo`; en el primer tramo con escrituras pendientes, la auditoría debe mostrar
   `ejecutada: true` y `verificado: "ok"` en TODOS los registros del tramo (210/211 y, si toca, 230/232),
   no solo el primero.
2. `escrituras_ultima_hora` debe decrecer y no quedarse clavado en 10.
3. Si Solarman vuelve a fallar, deben aparecer (por fin) warnings "posible cuelgue" / "Verify parcial" y
   líneas de auditoría con rastro, nunca silencio.
4. El script `diagnostico_carga_deye` del HA del usuario ya lee con `{device, address, count}`: sirve para
   contraste cruzado tras el parche.

## 7. Contexto operativo (no código, para no des-diagnosticar)

- El ToU propio del inversor ("Program charging/discharging") está ahora en **Disabled** por decisión del
  usuario: los slots ToU cargan desde red por sí solos hasta el SOC objetivo del slot y **no obedecen** al
  registro 232, así que deben permanecer deshabilitados para que el optimizador gobierne. No re-recomendar
  habilitarlos (la recomendación inicial era incorrecta; desestimada con el manual Deye y el protocolo V118).
- Los puntos de arranque (228=4900 → 6100, 229=30 → 100) fueron los bloqueadores físicos de la carga desde
  red; el bypass manual de la madrugada del valle los escribió a mano. El futuro fix F5.2 debería
  gobernarlos desde el executor.
- Registros actuales de referencia: 210=98, 211=135, 217=20, 218=21, 213=1, 243=1, 247=1, 248=0, ToU off.
