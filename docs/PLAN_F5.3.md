# PLAN F5.3 — Estabilidad del plan y fiabilidad del watchdog

**Versión objetivo:** 0.13.0 · **Fecha:** 2026-11-17 · **Estado:** ✅ IMPLEMENTADA
**Fuente:** `docs/analisis_plan_optimizador_bateria_omie.md` (48 h de telemetría, 4.316 entradas de auditoría).
Resumen de implementación en `docs/RESULTADO_F5.3.md`.

## Contexto

87 transiciones del plan en 48 h (1,8/hora), 57 % de permanencias ≤ 20 min,
29 saltos CARGA↔VENTA y 10 episodios `sin_telemetria` diarios. El plan
replanifica cada 15 min con SOC real y sin memoria entre tramos; el watchdog
corta con un solo check > 60 s, que el jitter de Solarman supera a diario.

## Decisiones de diseño

### D-F5.3.1 — Bloque de compromiso configurable (congela el zigzag)

- Nueva entidad **Number** `optimizador_bateria_omie_bloque_minutos`
  (rango 15–120, paso 15, **default 60**). Flexible en caliente, sin tocar
  config flow.
- El listener de cambio de tramo **sigue disparando en cada `:00/:15/:30/:45`**
  (barato), pero `async_recalcular("tramo")` solo se ejecuta cuando
  `minuto % bloque_minutos == 0` (bloques alineados a la hora para 60/30,
  a la media hora para 15). Listener manual + property → no requiere
  re-registrar el listener.
- **Compromiso del bloque en curso**: las replanificaciones *no periódicas*
  (`precios`, `perfil_diario`, `festivo`, `arranque`, modos manuales de F4)
  recalculan el horizonte completo pero **exportan los tramos del bloque en
  curso del plan anterior**; el tramo nuevo solo manda a partir del siguiente
  límite de bloque. Lo que muestra el sensor plan = lo que ejecuta el
  executor (sin divergencia).
- **Excepción de seguridad/opportunidad**: `desviacion_soc` (D11, umbral
  existente) y fallo del inversor **sí aplican inmediatamente** — cubren el
  caso hipotético de bajada de SOC que abre una ventana de carga antes del
  fin del bloque. Los precios son estáticos intradía (publicados N-1), así
  que toda "ventana de oportunidad" nueva nace de un cambio de SOC: la
  excepción D11 la captura siempre.
- Nuevos atributos del sensor plan: `bloque_minutos` y `proximo_replan`.

### D-F5.3.2 — Histéresis económica: DEFERIDA

La congelación por bloques elimina la replanificación por ruido (causa a del
informe); los cambios en límites de bloque con margen de arbitraje 0,015 €/kWh
son ya decisiones de bloque completo, no flips marginales de 15 min. Si tras
medir quedan saltos hora a hora, se añade *switching cost* al LP en una
ronda posterior. No se añade complejidad ahora sin evidencia.

### D-F5.3.3 — Watchdog de telemetría con debounce y timeout ampliado

- Umbral de obsolescencia: de `2 × intervalo_telemetria_s` a
  **`4 × intervalo`** (120 s con 30 s configurados; Solarman ahora sondea
  cada 10 s).
- **Debounce**: no basta un check obsoleto → se exigen **2 evaluaciones
  obsolescentes consecutivas** (contador en el executor, reset con lectura
  fresca) antes de declarar `sin_telemetria`.
- **No derribo del plan**: mientras la telemetría está caída pero por debajo
  del timeout mayor (**600 s**, constante), el plan se mantiene publicado y
  el executor conserva el último tramo **sin escribir**; superado el timeout
  → `sin_telemetria` con fallback (comportamiento actual de corte de
  escrituras intacto desde el primer momento: la seguridad no se degrada).
- Las escrituras se siguen cortando desde el primer check obsoleto (D7 se
  mantiene); lo que cambia es la *declaración* de estado y la estabilidad
  del plan.

### D-F5.3.4 — Legibilidad del estado de control

- `estado_de_control` expone atributo nuevo **`modo_activacion`**
  (`gradual` | `completo`).
- Estados diferenciados: `activo` (escrituras ejecutadas), **`gradual`**
  (acción no ejecutable en modo gradual — antes se reportaba como `dry_run`,
  engañoso), `dry_run` (control_activo OFF), `fallback`, `sin_telemetria`.
- ⚠️ Plantillas/automatizaciones del usuario que comparen
  `estado == 'dry_run'` para el caso gradual deben pasarse a `gradual` (se
  documentará en RESULTADO_F5.3).

## Archivos

| Archivo | Cambio |
|---|---|
| `const.py` | Constantes de bloque, debounce (`DEBOUNCE_TELEMETRIA=2`), multiplicador watchdog (`MULTIPLICADOR_WATCHDOG=4`), timeout mayor (600 s), defaults |
| `coordinador_plan.py` | Lógica de bloque en `_async_cambio_tramo`; carry-over del bloque en curso en `async_recalcular` (motivos no excepcionales); atributos nuevos del plan |
| `executor.py` | Contador de debounce en `_telemetria_fresna`; umbral 4×; timeout mayor antes de derribar; estados `gradual` |
| `entidades_control.py` | Number `bloque_minutos`; atributo `modo_activacion` en el sensor `estado_de_control` |
| `translations/es.json` | Nombres del number y estados nuevos |
| `manifest.json` | Versión 0.13.0 |
| `tests/` | Nuevos casos + ajuste de los existentes |
| `docs/` | FASES.md, RESULTADO_F5.3.md, SPEC.md (bullets de estado y entidades) |

## Tests

- Bloque: con `bloque_minutos=60` no replanifica a `:15/:30/:45`; replan `precios` a mitad de bloque conserva los tramos del bloque en curso y aplica el resto del horizonte nuevo; replan `desviacion_soc` a mitad de bloque **sí** cambia la acción del tramo actual.
- Watchdog: 1 check obsoleto → sigue fresca la 2ª evaluación (no declarada obsoleta); 2 consecutivos → obsoleta; lectura fresca resetea; escrituras cortadas desde el primer check.
- Estado: `gradual` vs `dry_run` vs `modo_activacion` en atributos.
- Regresión completa: `pytest -q` (381+ tests) y `ruff check .` en verde.

## Riesgos

1. Plantillas del usuario que usen `dry_run` para el caso gradual (mitigado:
   documentar en RESULTADO + atributo nuevo).
2. Carry-over del bloque: el LP reoptimiza el horizonte **después** del
   bloque en curso con el SOC previsto al final de ese bloque (continuidad
   del balance garantizada por el propio planificador).
3. Tests sensibles a fecha/hora real (usar timestamps sintéticos como en F5).

## Criterios de salida

1. Con 48 h de auditoría post-despliegue: transiciones < 30/48 h y permanencia
   media > 1 h.
2. Episodios `sin_telemetria` declarados < 3/día (los gaps de Solarman
   absorbidos por debounce).
3. `pytest -q` y `ruff check .` en verde.

## Fuera de alcance (esta ronda)

- Validación de familia 2.0TD y `modo_activacion: completo` → acción del
  usuario con hardware (ver RESULTADO cuando se ejecute).
- Switching cost en LP (D-F5.3.2, diferida).
- Mejoras de forecast/MAPE (guardrail ya protege; pendiente confirmar sensor
  `consumo_casa`).
