# PLAN_F5 — ML + Programación Lineal (MAPE < 20 %, ahorro real medible)

Estado: **implementado (0.12.0) — U1..U6 completadas; criterio MAPE con datos reales en observación del usuario**
Base: SPEC §2.4, RF-04/RF-05/RF-06, §5.4, §5.5, §8 (F5) y §12.2. Código actual: v0.11.6 (341 tests… 335 tras 0.11.6).

---

## 1. Objetivo de la fase

Sustituir las dos aproximaciones heurísticas de F3 por:

1. **Predicción de consumo por ML** (scikit-learn, Gradient Boosting) con reentrenamiento diario y fallback al perfil estadístico de F3 si no supera su calidad (guardrail de adopción).
2. **Planificador por programación lineal** (PuLP) con el horizonte hoy+mañana, que reemplace el greedy cuando esté validado, conmutable en caliente.
3. **Refinado de la auditoría de ahorro** (anticipada en F4.5): precio de excedentes diferenciado y energía por tramo con el perfil real (media de lecturas), no la lectura de cierre.

Criterio de salida (SPEC §8): **MAPE del backtest < 20 %** y **ahorro real medible** (auditoría operativa).

## 2. Alcance por unidades (una unidad = un commit con tests en verde)

### F5-U1 — Dependencias y_features del dataset
- Añadir `scikit-learn` y `pulp` a `requirements` del `manifest.json` (ambos permitidos por AGENTS §1 sin pedir confirmación; **se instala en el Yellow al actualizar** — ver riesgos).
- Nuevo `forecast_ml.py` (parte 1): constructor del dataset de features desde el histórico del recorder: `tramo_dia (0-95), dia_semana, es_festivo, es_vacaciones, temperatura_media (TODO: sin clima пока), consumo_mismo_tramo_{7,14,21,28}d`.
- Tests con dataset sintético determinista (seed fija), coherentes con el estilo de `test_predictor_consumo.py`.

### F5-U2 — Forecaster ML + calidad + reentrenamiento
- `forecast_ml.py` (parte 2): modelo `HistGradientBoostingRegressor` (nativo sklearn, rápido en ARM del Yellow), persistencia en `config/optimizador_bateria_omie/modelo_consumo.joblib` (fuera de `custom_components/`, sobrevive a HACS, mismo criterio que la auditoría JSONL).
- **Entrenamiento con `hass.async_add_executor_job`** (bloqueante; AGENTS §1). Job diario junto al recálculo del perfil (04:35) — no en el arranque en caliente.
- **Backtest walk-forward** con seed fija → MAPE. **Guardrail de adopción**: el ML solo predice si su MAPE (ventana móvil) mejora el del perfil estadístico; si no, `fuente: perfil` — nunca se degrada la F3.
- Nuevo sensor diagnóstico `sensor.*_forecast_quality` (MAPE ML vs MAPE perfil, n_muestras, ventana).
- Integración en `predictor_consumo.py` como capa 2 con fallback por día.
- Tests: entrenamiento con dataset sintético, MAPE calculado, guardrail (ML peor → perfil), persistencia/recarga idéntica.

### F5-U3 — Planner por programación lineal (PuLP)
- Nuevo `planner_lp.py` con **el mismo contrato que `planificador.planificar()`** (misma entrada; misma `Plan`/`PlanTramo` de salida) → el `CoordinadorPlan` y el `EjecutorControl` no cambian.
- Variables por tramo del horizonte (hoy+mañana, 192): `carga_red_w`, `venta_red_w`, `carga_bateria_w`, `descarga_bateria_w` (continuas, W).
- Restricciones:
  - Balance SOC por tramo con η round-trip; `soc_min ≤ SOC ≤ soc_max`.
  - Importación total ≤ `potencia_contratada` (5,75 kW) **dura**.
  - `potencia_max_carga/descarga`.
  - **Flujo unidireccional por tramo** (no importar y verter a la vez — la regla 0.11.1).
  - `precio_max_compra` / `precio_min_venta` como guardarraíles absolutos (recortan variables, no el modelo).
- Objetivo: minimizar coste neto del horizonte con los precios efectivos (compra y venta al mismo precio, §5.6).
- Consumos **p50** por tramo (exógenos); la banda **p90** queda como atributo del plan (`margen_p90`) para observación, no como restricción — decisión de diseño (D-F5.1) a confirmar.
- Solver CBC (empaquetado con PuLP, sin red). Tiempo esperado < 1 s para 192×4 variables.
- **Selección en caliente**: nuevo parámetro `algoritmo_planificador: greedy|lp` (reconfigure + entidad o number) — greedy sigue siendo el default hasta que el LP valide en producción (D-F5.2). Si el LP es infactible, recálculo con greedy inmediato + WARNING.
- Tests: coste LP ≤ coste greedy en los escenarios existentes de `test_planificador.py` (reutilizados), caso valle/punta clásico, restricción de importación, unidireccional, infactibilidad → fallback.

### F5-U4 — Auditoría refinada
- `auditoria.py`:
  - **Precio de excedentes diferenciado**: parámetro `precio_excedentes_factor` (0–1, default 1 = precio de importación igual, comportamiento actual) — la exportación se valora `precio_importación × factor` (D-F5.3 a confirmar; OR por defecto el spot sin peajes — decisión).
  - **Energía por tramo con perfil real**: media de las lecturas de telemetría del tramo (requiere mantener una ventana de las últimas N lecturas en memoria), sustituyendo la aproximación por lectura de cierre.
- Tests: factor de excedentes, media de varias lecturas del tramo.

### F5-U5 — Clima (condicionado a datos del usuario)
- Features `temperatura_media` (e `irradiancia`) — **TODO bloqueado**: falta la entidad de clima a confirmar (SPEC §12.2.1). Si no se confirma en la revisión, la unidad queda fuera y el modelo entrena sin clima (el ML de U1 se diseña para tolerar features ausentes).

## 3. Archivos a crear / modificar

| Fichero | Acción | Unidad |
|---|---|---|
| `docs/PLAN_F5.md`, `docs/RESULTADO_F5.md` | crear | U0/U6 |
| `forecast_ml.py` | crear (dataset + modelo + backtest + persistencia) | U1-U2 |
| `planner_lp.py` | crear (LP con contrato igual a greedy) | U3 |
| `tests/test_forecast_ml.py`, `tests/test_planner_lp.py` | crear | U1-U3 |
| `manifest.json` | modificar (`requirements` + version 0.12.0) | U1 |
| `predictor_consumo.py` | modificar (capa ML con fallback) | U2 |
| `const.py` / `config_flow.py` | modificar (`algoritmo_planificador`, `precio_excedentes_factor`, opcionales de clima) | U2-U4 |
| `coordinador_plan.py` | modificar (selección greedy/LP + fallback) | U3 |
| `entidades_plan.py` o `entidades_control.py` | modificar (sensor `forecast_quality`) | U2 |
| `__init__.py` | modificar (job de reentrenamiento 04:35) | U2 |
| `auditoria.py` | modificar (excedentes + perfil por tramo) | U4 |
| `README.md` / `SPEC.md` / `translations` | modificar conforme avanza | todas |
| `docs/FASES.md` | crear/cerrar estado | U6 |

## 4. Decisiones de diseño que requieren tu confirmación

- **D-F5.1**: la LP optimiza con consumos **p50**; la banda p90 se publica como atributo informativo del plan, no como restricción. (Alternativa: plan conservador que reserve SOC según p90 — más complejo, F5.2 si interesa.)
- **D-F5.2**: `algoritmo_planificador` conmutable **greedy|lp**, arrancando en **greedy**; cambio a LP cuando el backtest y 1 semana de comparación a favor del LP lo aconsejen. 
- **D-F5.3**: precio de excedentes: factor configurable sobre el precio de importación (default 1 = hoy), **o** valoración al spot sin peajes/cargos/impuestos. Recomendación: factor configurable, default 1 (no cambia los números actuales).
- **D-F5.4**: ninguna escritura nueva al inversor: la LP produce los mismos `PlanTramo` que el executor ya sabe aplicar. F5 no toca el control real (salvaguardas intactas).
- **Entidad de clima**: nombre/`entity_id` de la integración de weather en tu HA (para U5) — si no se difica, U5 queda fuera.

## 5. Riesgos

1. **Peso de dependencias**: scikit-learn + numpy en el Yellow (ARM) — wheels disponibles; primera instalación HACS más lenta. Mitigación: entrenamiento en executor job, latencia de inicio no bloquea el plan.
2. **Pocos datos**: ventana recorder 28 días default; el ML puede empeorar el perfil → guardrail D-F5.2-c (fallback automático, medido en `forecast_quality`).
3. **LP infactible** (datos mal) → fallback greedy + WARNING, nunca plan vacío.
4. **Determinismo**: seeds fijas en tests y entrenamiento; PuLP/CBC determinista.
5. **Tiempo de planificación**: resolver LP ≤ 1 s en el Yellow; si excede (medir), límite de horizonte a hoy+mañana ya acotado.

## 6. Orden y aceptación por unidad

1. U1 → `pytest` verde, dataset reproducible (seed), `ruff` limpio.
2. U2 → ML entrena, backtest MAPE calculado, guardrail verificado, sensor `forecast_quality` visible.
3. U3 → LP ≤ greedy en coste en todos los escenarios del test suite actual; `algoritmo_planificador` en caliente.
4. U4 → auditoría con excedentes y perfil real; números antes/después documentados en `RESULTADO_F5.md`.
5. U5 (condicionada) → features de clima si hay entidad.
6. U6 → docs de cierre, `docs/FASES.md`, versión **0.12.0**.

**Criterio de salida global**: MAPE < 20 % en backtest validado con datos reales de tu instalación (necesito tu histórico del recorder ≥ 28 días — ya está en tu HA) y ahorro real medible con la auditoría refinada durante ≥ 1 semana.
