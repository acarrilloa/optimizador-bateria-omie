# PLAN_F3 — Predicción de consumo v1 + Planificador greedy (dry-run)

> **Estado**: **v1.3 — CONFIRMADO por el usuario** (oct. 2026). Listo para implementar por unidades lógicas con un commit por unidad.
> **Base**: rama `main` con F1 + F2 en producción (manifest **0.8.10**, `ruff` limpio, suites F1 (62) + F2 en verde). HA 2026.10.1, Python 3.14.
> **Versión de cierre prevista**: `0.9.0`.
> **Referencia**: SPEC.md v0.9.0 completa — RF-05 (capa 1: perfil estadístico), RF-06 (F3: planner greedy en `dry_run`), §5.4, §5.5, §6.3, §8, §10.3, §12.2, §12.3.
> **Cambios v1.0 → v1.1**: alineado con la SPEC completa — la deuda #4 (`OptionsFlow`) ya está resuelta en F2 (`reconfigure`), los parámetros de F3 se editan por esa vía; `ahorro_hoy` real es de F5 (en F3 solo estimación teórica, renombrada); telemetría `ha_entities` con 6 entity_ids y `corriente_bateria = None`; añadida la deuda #10 (corriente en `ha_entities`) como unidad opcional.
> **Cambios v1.1 → v1.2 (confirmación del usuario)**: **M1** — Solcast PV Forecast instalado y funcionando: previsión de FV con **Solcast como fuente preferente** (entidad configurable, parseo del atributo `detailedForecast`, mapeo 30→15 min por repetición) y el perfil estadístico como **fallback**. **M2** — deuda #10 (corriente en `ha_entities`) **aplazada**: fuera de alcance de F3. **M3** — defaults confirmados, todos editables por `reconfigure` con recálculo del plan en **≤ 60 s** tras el cambio. **Q1–Q6 respondidas** (ver §9).
> **Cambios v1.2 → v1.3 (entity_ids de Solcast confirmados por el usuario)**: las entidades reales son **`sensor.solcast_pv_forecast_forecast_today`** y **`sensor.solcast_pv_forecast_forecast_tomorrow`** — un parámetro editable por día (`entity_id_prevision_fv_hoy` / `entity_id_prevision_fv_manana`) con estos values como defaults; cada una aporta el `detailedForecast` de su día (hoy y mañana), cubriendo exactamente el horizonte del plan.

---

## 1. Objetivo y alcance

Añadir la **predicción de consumo** (capa 1: perfil estadístico, mediana por tramo × tipo de día) y el **planificador greedy en `dry_run`** que calcula un plan por tramos de 15 min priorizando autoconsumo y aplicando arbitraje solo cuando es rentable.

**F3 no escribe en el inversor** (el executor es F4 y requiere gateway RS485, §12.2). El plan se publica como sensor con atributos y se revisa manualmente durante ≥ 1 semana antes de pasar a F4.

Unidades del alcance:

1. Predicción estadística (bootstrap desde HA recorder; InfluxDB como **TODO documentado**).
2. Planificador greedy puro en `dry_run` + sensor de plan.
3. Ahorro **estimado** (teórico) del día.
4. `switch.festivo_hoy` (marca manual) — asignado a F3 por la SPEC §6.3.
5. **Extensión del `reconfigure`** con los parámetros del plan (la deuda #4 ya está resuelta en F2; F3 solo añade campos).
6. Servicio `regenerate_plan` (referenciado en SPEC §6.4 para F3/F4).
7. **Previsión FV con Solcast como fuente preferente** (M1) + perfil estadístico como fallback.

~~Opcional (deuda #10): publicar `corriente_bateria` en `ha_entities`~~ — **aplazada por el usuario (M2)**: fuera de alcance de F3; queda pendiente de confirmación de una entidad fiable en Solarman.

Fuera de alcance: ML con clima y LP (F5), executor/escrituras (F4), auditoría de ahorro real (F5), mover tarifa a `entry.data` (deuda #5, F4), `force_mode`/`release_manual` (F4), InfluxDB (TODO), deuda #10 (aplazada).

---

## 2. Decisiones de diseño

| # | Decisión | Motivo |
|---|---|---|
| D1 | Ficheros en español: `predictor_consumo.py` (`PredictorConsumo`), `historico.py` (`FuenteHistoricoRecorder`), `planificador.py` (`PlanificadorGreedy`), `coordinador_plan.py` (`CoordinadorPlan`), `entidades_plan.py` | Convención española de §11. **Nota**: §11 lista `forecaster.py`/`planner.py` como "ficheros futuros" — discrepancia con la propia convención; se sigue el español y la SPEC se corrige al cierre de fase |
| D2 | Predicción F3 = **perfil estadístico**: mediana del consumo por `tramo_dia (0–95) × tipo_de_día (laborable / fin-de-semana-o-festivo)`. Sin clima, sin ML (F5) | RF-05 capa 1. El clima es feature de F5 |
| D3 | Banda `{p10, p50, p90}` por percentiles del mismo perfil (por tramo×tipo). El planner greedy de F3 usa **p50**; p90 queda publicado para inspección y uso conservador en F5 | RF-05 exige la banda; el uso conservador es decisión de F5 |
| D4 | **Bootstrap del histórico desde HA recorder** (entidad de `consumo_casa` ya configurada en F2 — uno de los 6 `entity_id`), ventana configurable `ventana_historico_dias` (default 28, rango 7–90). **InfluxDB queda como TODO documentado (no implementada en F3)**: el recorder cubre la ventana necesaria y evita acoplar el componente a InfluxDB | RF-05: ambos históricos disponibles; el perfil mediana de 28 días no necesita los años de InfluxDB. Menos dependencia = menos riesgo |
| D5 | Consulta al recorder vía `recorder.get_instance(hass)` en executor; muestreo del sensor `consumo_casa` **a tramos de 15 min con agregación por tramo** (media de las muestras del tramo). Estados `unavailable`/`unknown`/no-W → tramo descartado (**sin imputación: no se inventan valores**) | Regla dura de AGENTS y §9.1 "sin datos → no inventar" |
| D6 | Si un tramo×tipo no alcanza el mínimo de **3 días válidos**: **cascada** → 1) mediana del mismo tramo en tipo de día opuesto → 2) mediana global del tramo (ambos tipos) → 3) `None` y el plan marca ese tramo con `prediccion: insuficiente` (el greedy lo trata con el consumo previsto = p90 disponible o 0 W + warning) | Más robusto que copiar el día anterior (fallback de RF-05 adaptado al perfil) |
| D7 | **Perfil persistido en disco** (`data/perfil_consumo.json`, junto a la caché de precios) y recalculado **diariamente a las 04:35** (tras la limpieza de caché de las 04:30) + al cambiar `ventana_historico_dias` o la entidad de consumo vía `reconfigure`. En el arranque se carga el perfil persistido sin reconsultar el recorder | Evita consultas pesadas al recorder en cada arranque; el perfil solo cambia una vez al día |
| D8 | **Previsión de FV (M1 + v1.3, confirmado)**: **Solcast PV Forecast como fuente preferente** (integración instalada y funcionando). **Dos parámetros editables, uno por día**: `entity_id_prevision_fv_hoy` (default **`sensor.solcast_pv_forecast_forecast_today`**) y `entity_id_prevision_fv_manana` (default **`sensor.solcast_pv_forecast_forecast_tomorrow`**). De cada entidad se parsea el atributo **`detailedForecast`** (resolución 30 min, cada una cubre su día) con **mapeo 30 → 15 min repitiendo cada estimación en los dos tramos** (decisión documentada: no se interpola, es el mapeo estándar de Solcast; no se inventan valores). Si una entidad no existe, está `unavailable` o no tiene `detailedForecast` → `INFO` + **fallback al perfil estadístico de FV para ese día** (misma técnica sobre `produccion_fv` del recorder, D4). La fuente usada (`solcast` / `perfil`) se expone como atributo, por día. El parseo valida que los `pv_estimate` sean numéricos; la coherencia de magnitud (W) se verifica contra la FV real durante la semana de `dry_run` | El planner necesita estimar el excedente FV para el autoconsumo; Solcast es más preciso que el perfil. El fallback por día garantiza plan aunque falte mañana |
| D9 | **`PlanificadorGreedy`**: recorre los tramos de hoy (+mañana si hay precios) en orden cronológico con balance SOC por tramo: `soc[k+1] = soc[k] + (carga − descarga) × Δt / capacidad`, `soc ∈ [soc_min, soc_max]`. Decisiones: (1) **autoconsumo siempre**: la batería absorbe `consumo − FV` con excedente y `soc < soc_max`, o descarga `consumo − FV` si falta y `soc > soc_min`; (2) **arbitraje de compra**: `CARGA_RED` en tramos con `precio_efectivo ≤ precio_max_compra` **y** por debajo de la mediana de precios efectivos del día (tramos "baratos en contexto"), hasta `soc_max`, con la potencia limitada por la restricción dura; (3) **arbitraje de venta**: `VENTA_RED` solo si `permitir_venta` y el ciclo completo compensa: `p_venta ≥ p_compra_efectiva / η_round_trip + margen_arbitraje` (con venta simétrica: viable si `p_punta − p_valle > margen + pérdida η`); (4) en el resto, `MANTENER`. **Precio ≤ 0 → siempre `CARGA_RED` hasta `soc_max`**, incluso por encima del umbral | RF-04/RF-06. Reutiliza `clasificar_por_percentiles` de `ModeloTarifa` (reservado al planner desde 0.7.1) |
| D10 | **Restricción dura de 5,75 kW en el plan**: `potencia_carga_máx(tramo) = min(potencia_max_carga, potencia_contratada − consumo_previsto(tramo) − margen_potencia)`, nunca negativa (si el consumo previsto ya invade el margen → 0 W). `potencia_contratada` (default 5,75 kW) y `margen_potencia` (default 300 W) son parámetros, **no constantes de código** | RF-04. El guard dinámico con medición real es del executor (F4) |
| D11 | **Estado inicial del plan** = SOC real de `CoordinadorTelemetria` (`ha_entities` operativo); desde ahí, SOC previsto por tramo. **Replanificación** si `|SOC_real − SOC_previsto| > desviacion_soc_replan_pct` (default 10 %), al llegar precios nuevos (cambio de `coordinator.data`), al cambiar parámetros por `reconfigure`, o al togglear `festivo_hoy` | RF-06. El SOC ya está disponible vía F2 |
| D12 | Entrada de precios: `DatosOmie` del `CoordinadorDatosOmie` (precios de hoy/mañana + clasificaciones). El sensor F1 `precios_efectivos_hoy` confirma que la curva completa de precios efectivos ya está disponible para el planner sin recomputar nada | §5.7/§6.1: la curva existe desde F1; el planner no duplica cálculos de tarifa |
| D13 | **Ahorro estimado teórico**: por tramo, `coste_sin_plan` (todo a precio efectivo, venta al mismo precio) vs. `coste_con_plan` (según decisiones del plan). Suma del día → sensor `ahorro_estimado_hoy` (€). **Nombre distinto del `ahorro_hoy` real de F5** (SPEC §6.3 asigna `ahorro_hoy` a F5; RF-06 pide "ahorro estimado" en F3 — se resuelve con la entidad estimada y etiquetada como teórica) | RF-06 vs §6.3; sin colisión de `unique_id` con F5 |
| D14 | Parámetros del planner editables vía **extensión del `async_step_reconfigure` de F2** (campos nuevos con defaults vía `entry.data.get`); **sin `OptionsFlow`** (deuda #4 ya resuelta en F2) y **sin bump de `VERSION`**. **M3 (confirmado)**: cambiar cualquier parámetro por `reconfigure` dispara el **recálculo del plan en ≤ 60 s** (suscripción al listener de actualización de la entrada) | §7.1: "Sin OptionsFlow (deuda #4 absorbida por reconfigure)"; la entrada de producción no se rompe; RF-04 exige aplicación en ≤ 60 s |
| D15 | `switch.*_festivo_hoy` en F3: fuerza `tipo_de_día = festivo` para hoy en la predicción y en la clasificación del plan (períodos valle). Los festivos locales propios se configuran como lista `festivos_locales` en `reconfigure` (pendiente §12.2.2), además de los nacionales de `ModeloTarifa` | SPEC §6.3 lo asigna a F3; útil para probar el perfil en festivos |
| D16 | **Cálculo del plan**: `CoordinadorPlan` se recalcula en arranque, publicación de precios D+1 (~13:45, escuchando `CoordinadorDatosOmie`), cambio de tramo (cada 15 min), `festivo_hoy`, desviación SOC, y llamada al servicio `regenerate_plan`. No es un `DataUpdateCoordinator` de polling | RF-06. Evita recomputar cada 30 s sin motivo |
| D17 | **Ninguna escritura al inversor**: F3 no tiene código de escritura; `planificador.py` es función pura `planificar(precios, predicción, telemetría, parámetros) -> Plan` | Dry-run inviolable. Pureza = tests triviales |
| D18 | Sin precios de mañana → plan solo con hoy; sin precios de hoy → **sin plan** (estado `sin_precios` + warning). Perfil insuficiente (D6 nivel 3 generalizado) → plan trivial de solo-autoconsumo con estado `perfil_insuficiente` | §9.1 "no inventar planes" |
| D19 | Servicio `optimizador_bateria_omie.regenerate_plan: {}` (sin campos): recalcula el plan inmediatamente y devuelve `{recalculado: true, accion_actual, ahorro_estimado}`. Útil durante la revisión manual de la semana de `dry_run` | SPEC §6.4 lo lista en F3/F4; se incluye en F3 por utilidad de validación |

---

## 3. Ficheros a crear

### 3.1 `custom_components/optimizador_bateria_omie/historico.py`

- `FuenteHistoricoRecorder(hass, entity_id, unidad="W")`.
- `async obtener_series(dias) -> list[Muestra]` con `Muestra(ts_utc, valor_w)`; consulta al recorder en executor, agrega a tramos de 15 min (media de muestras), descarta `unavailable`/`unknown` y estados sin unidad W.
- Errores → `ErrorHistorico` (entidad inexistente, recorder sin datos, ventana vacía). Sin imputación.

### 3.2 `custom_components/optimizador_bateria_omie/predictor_consumo.py`

- `PredictorConsumo(hass, fuente: FuenteHistoricoRecorder, festivos: Calendario)`.
- `async recalcular_perfil()`: bootstrap del recorder para consumo y FV; construye `perfil[función][tipo_día][tramo] = {p10, p50, p90, n_dias}`; persiste en `data/perfil_consumo.json`.
- `cargar_perfil()` en el arranque (desde disco).
- `predecir(fecha) -> PrediccionDia`: tipo de día según `ModeloTarifa.es_festivo`/`es_fin_de_semana` + `festivos_locales` + `festivo_hoy` manual; aplica la cascada de D6.
- **Previsión de FV (M1 + v1.3)**: `async predecir_fv(fecha) -> list[PercentilesTramo]` con Solcast como fuente preferente — `_predecir_fv_solcast()` parsea `state.attributes["detailedForecast"]` de `entity_id_prevision_fv_hoy` o `entity_id_prevision_fv_manana` según el día (mapeo 30→15 min por repetición, D8); si falla → `_predecir_fv_perfil()` (fallback estadístico). Fuente usada expuesta en `PrediccionDia.fuente_fv` (`solcast` / `perfil` / `none`, por día).
- Dataclasses `PrediccionDia` (`consumo: list[PercentilesTramo]`, `fv: list[PercentilesTramo] | None`, `fuente_fv`) y `PercentilesTramo` (`p10, p50, p90, fuente`).

### 3.3 `custom_components/optimizador_bateria_omie/planificador.py`

- Puro y determinista: `PlanificadorGreedy(parametros).planificar(precios_efectivos_hoy, precios_efectivos_manana, prediccion, soc_actual) -> Plan`.
- `PlanTramo`: `accion` (`AUTOCONSUMO`/`CARGA_RED`/`VENTA_RED`/`MANTENER`), `potencia_bateria_w` (convención Deye **+descarga/−carga**, confirmada en F2), `potencia_carga_red_w`, `potencia_venta_red_w`, `soc_previsto_pct`, `precio_referencia_eur_kwh`, `beneficio_marginal_eur`.
- `Plan`: lista de `PlanTramo` + `coste_sin_plan_eur`, `coste_con_plan_eur`, `ahorro_estimado_eur`, `estado` (`ok`/`sin_precios`/`perfil_insuficiente`), `motivos` (resumen de decisiones).
- Reglas D9, D10, D17, D18. Todos los valores de §5 son parámetros (defaults editables), **nada hardcodeado**.

### 3.4 `custom_components/optimizador_bateria_omie/coordinador_plan.py`

- `CoordinadorPlan(hass, entry, coordinador_precio, coordinador_telemetria, predictor, planificador)`.
- Reconstruye el plan según D16; expone `plan_actual()`, `tramo_actual()`, `ahorro_estimado_hoy()`.
- Suscripciones: `async_track_state_change_event` al SOC (desviación), datos del coordinador de precios, `async_track_time_change` cada 15 min, toggle de `festivo_hoy`, servicio `regenerate_plan`.
- Emite el plan como atributos del sensor; **nunca toca el inversor**.

### 3.5 `custom_components/optimizador_bateria_omie/entidades_plan.py`

| Clase | unique_id | Tipo | Estado |
|---|---|---|---|
| `SensorPrediccionConsumo` | `optimizador_bateria_omie_prediccion_consumo` | sensor (W) | p50 del tramo actual; atributos: curva p10/p50/p90 de hoy y mañana, `fuente: perfil`, `fuente_fv: solcast / perfil / none`, `n_dias` del perfil |
| `SensorPlan` | `optimizador_bateria_omie_plan` | sensor | acción del tramo actual; atributos: plan completo (JSON), SOC previsto, potencia objetivo, `ahorro_estimado`, `estado` |
| `SensorAhorroEstimadoHoy` | `optimizador_bateria_omie_ahorro_estimado_hoy` | sensor (€) | ahorro estimado acumulado del día (teórico; el real es `ahorro_hoy` de F5) |
| `SwitchFestivoHoy` | `optimizador_bateria_omie_festivo_hoy` | switch | marca manual de festivo/vacaciones (D15) |

Todas con el `device_info` común (mismo dispositivo que F1/F2).

### 3.6 Tests

- `tests/test_historico.py` — muestreo y agregación a 15 min; descarte de `unavailable` y unidades no-W; entidad inexistente → `ErrorHistorico`; días DST (92/100 tramos).
- `tests/test_predictor_consumo.py` — dataset sintético con **seed fija**: perfil mediana por tramo×tipo; cascada D6 (tramo sin datos → tipo opuesto → global → None); `festivo_hoy` y `festivos_locales` fuerzan tipo festivo; persistencia y recarga del perfil idéntica. **+ tests Solcast (M1)**: fixtures con `detailedForecast` real de Solcast (30 min) de **ambas entidades** (`forecast_today` y `forecast_tomorrow`) → mapeo 30→15 min por repetición correcto y cada día tomado de su sensor; entidad sin atributo o `unavailable` → fallback al perfil de ese día con `INFO`; ambos vacíos → solo perfil; atributo `fuente_fv` correcto por día en cada caso.
- `tests/test_planificador.py` — escenarios exigidos (SPEC §10.3):
  - día valle/punta clásico (carga nocturna hasta `soc_max`, sin venta si no compensa),
  - **precio negativo** (carga máxima aunque supere umbral),
  - rampa de precios (arbitraje solo en tramos bajo la mediana),
  - SOC inicial 0 / 50 / 100,
  - venta deshabilitada (`permitir_venta_red: false`),
  - restricción 5,75 kW (consumo previsto 5,5 kW → potencia de carga 0 W en ese tramo),
  - η round-trip (ciclo con `p_punta − p_valle` < `margen + pérdida η` → sin venta),
  - signo Deye (+descarga/−carga) en `potencia_bateria_w`,
  - precios de mañana ausentes → plan solo hoy; precios ausentes → `sin_precios`.
- `tests/test_coordinador_plan.py` — replanificación por desviación SOC > 10 %, precios nuevos y `festivo_hoy`; respuesta de `regenerate_plan`; idempotencia del tramo actual.
- `tests/test_reconfigure_f3.py` — campos nuevos del `reconfigure` sin recrear la entrada; la entrada de producción sin claves nuevas no se rompe; cambio de `ventana_historico_dias` dispara recálculo de perfil.

---

## 4. Ficheros a modificar

| Fichero | Cambio |
|---|---|
| `config_flow.py` | Extender `async_step_reconfigure` con los parámetros de §5 (F2 ya lo tiene). **Sin `OptionsFlow`** y **sin bump de `VERSION`**; claves nuevas con `entry.data.get(clave, default)` |
| `__init__.py` | Setup de F3: `FuenteHistoricoRecorder` + `PredictorConsumo` (carga de perfil) + `PlanificadorGreedy` + `CoordinadorPlan`; job diario 04:35 de recálculo de perfil; registro del servicio `regenerate_plan`; forward de plataforma `switch` |
| `switch.py` (nuevo) | Setup de la plataforma `switch` para `SwitchFestivoHoy` (o extender el forward a `["sensor", "switch"]`) |
| `sensor.py` | Añadir las 3 entidades de plan/predicción/ahorro estimado |
| `const.py` | Constantes nuevas: `CONF_VENTANA_HISTORICO`, `CONF_PRECIO_MAX_COMPRA`, `CONF_PRECIO_MIN_VENTA`, `CONF_MARGEN_ARBITRAJE`, `CONF_SOC_MIN`, `CONF_SOC_MAX`, `CONF_POTENCIA_MAX_CARGA`, `CONF_POTENCIA_MAX_DESCARGA`, `CONF_POTENCIA_CONTRATADA`, `CONF_MARGEN_POTENCIA`, `CONF_EFICIENCIA_ROUND_TRIP`, `CONF_DESVIACION_SOC_REPLAN`, `CONF_PERMITIR_CARGA_RED`, `CONF_PERMITIR_VENTA_RED`, `CONF_FESTIVOS_LOCALES`, `CONF_ENTITY_ID_PREVISION_FV_HOY`, `CONF_ENTITY_ID_PREVISION_FV_MANANA`, `CONF_CAPACIDAD_BATERIA_KWH` (ver §5), `SERVICE_REGENERATE_PLAN`, unique_ids de F3 |
| `manifest.json` | `version` → `0.9.0`; `dependencies` → `["omie", "recorder"]` (el recorder debe estar cargado antes de consultarlo). **Sin requisitos nuevos** |
| `translations/es.json` + `strings.json` | Etiquetas del `reconfigure` ampliado, del servicio y de las entidades nuevas |
| `SPEC.md` | Al cierre: RF-05/RF-06 con ✅ F3, §5.4/§5.5 con la implementación real (nombres en español, D1), §6 entidades F3, §8 roadmap, §12.3 (deuda #10 si se hace) |

Los sensores de F1 (5) y F2 (8) no cambian de comportamiento.

---

## 5. Parámetros del `reconfigure` (extensión F3)

| Parámetro | Default | Rango | Uso |
|---|---|---|---|
| `ventana_historico_dias` | 28 | 7–90 | días de recorder para el perfil |
| `permitir_carga_red` | true | bool | arbitraje de compra |
| `permitir_venta_red` | true | bool | arbitraje de venta |
| `precio_max_compra` | 0.09 | €/kWh | umbral de compra |
| `precio_min_venta` | 0.18 | €/kWh | umbral de venta |
| `margen_arbitraje` | 0.015 | €/kWh | margen mínimo del ciclo |
| `soc_min` / `soc_max` | 15 / 95 | % | límites operativos |
| `potencia_max_carga` / `potencia_max_descarga` | 5000 / 5000 | W | límites (135 A ≈ 6,9 kW; 5 kW ≈ 98 A con margen) |
| `potencia_contratada` | 5750 | W | restricción dura del plan |
| `margen_potencia` | 300 | W | margen bajo la potencia contratada |
| `eficiencia_round_trip` | 88 | 70–95 % | pérdida del ciclo |
| `desviacion_soc_replan_pct` | 10 | 5–30 % | umbral de replanificación |
| `capacidad_bateria_kwh` | 15.36 | 5–50 kWh | capacidad nominal (Docan 51,2 V 300 Ah). **Necesario para el balance SOC de D9**; se añade como parámetro editable (dato de hardware, SPEC §3) |
| `festivos_locales` | `[]` | lista `"MM-DD"` | festivos propios (además de nacionales) |
| `entity_id_prevision_fv_hoy` (M1) | `sensor.solcast_pv_forecast_forecast_today` | entity_id | sensor de Solcast de **hoy** con atributo `detailedForecast` |
| `entity_id_prevision_fv_manana` (M1) | `sensor.solcast_pv_forecast_forecast_tomorrow` | entity_id | sensor de Solcast de **mañana** con atributo `detailedForecast`. Ambos vacíos = solo perfil estadístico |

## 6. Estrategia de tests

- `PlanificadorGreedy` es función pura: tests sin mocks de HA, solo datos.
- Recorder: mock del instance con series sintéticas (seed fija); sin red ni InfluxDB en los tests.
- Regresión completa de las suites de F1 (62) y F2.
- `pytest` + `ruff check` limpio, mismo enfoque de venv que en F2 (fallback documentado si `pytest-homeassistant-custom-component` no tiene build para HA 2026.10.1).

## 7. Paquetes nuevos

**Ninguno.** El perfil estadístico usa `statistics` (stdlib) y el greedy es aritmética pura. (`scikit-learn`/`PuLP`/`SciPy` se valoran en F5 con confirmación expresa.)

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| Recorder con menos histórico del esperado (`purge_keep_days` bajo o sensor nuevo) | `n_dias` por tramo publicado; cascada D6; `ventana_historico_dias` configurable; si no hay ≥7 días → estado `perfil_insuficiente` y plan trivial de autoconsumo (D18) |
| Sensor de consumo cambia de unidad/estado durante la ventana | Agregación descarta no-numéricos y unidades ≠ W; warning con conteo de muestras descartadas |
| El greedy es subóptimo (carga en el primer tramo barato, no en el más barato) | Documentado: la optimización global es F5 (LP); el criterio "bajo la mediana del día" mitiga los casos peores; la semana de revisión manual valida plausibilidad |
| Consumo previsto p50 con error grande en tramos atípicos | Banda p10–p90 publicada; en F5 el planner usará p90 para decisiones de venta |
| Consultas al recorder lentas en arranque | Perfil persistido (D7); la consulta solo corre a las 04:35 o al cambiar config |
| FV prevista por perfil subestima días atípicos (nubes) | **Mitigado por M1**: Solcast como fuente preferente; el perfil queda solo como fallback. La revisión manual de 1 semana compara el plan con la FV real |
| `regenerate_plan` invocado en ráfaga | Debounce/última escritura gana; el plan es barato de calcular (<100 ms) |

## 9. Respuestas del usuario (Q1–Q6, confirmación v1.2)

| # | Pregunta | Respuesta |
|---|---|---|
| Q1 | Recorder con ≥28 días (`purge_keep_days` ≥ 30) | ✅ **Confirmado** — mantener `ventana_historico_dias` default 28 |
| Q2 | ¿InfluxDB como TODO o consultarlo en F3? | ✅ **TODO documentado** (decisión D4); no se consulta en F3 |
| Q3 | ¿Defaults de §5 confirmados? | ✅ **Confirmados**, con la condición de que sean fácilmente modificables por `reconfigure` (M3) |
| Q4 | ¿Previsión FV? | ✅ **Solcast PV Forecast instalada y funcionando** → M1: Solcast preferente + perfil como fallback |
| Q5 | ¿Bump a 0.9.0 + perfil a las 04:35 + plan por cambio de tramo? | ✅ **Confirmado** |
| Q6 | ¿Entidad de corriente de batería fiable en Solarman? | ⏸️ **Sin respuesta** → deuda #10 **aplazada** (M2); fuera de alcance de F3 |

## 10. Criterios de salida (SPEC §8)

- Predicción p50 por tramo plausible frente al consumo real observado (revisión manual).
- Plan diario **plausible revisado manualmente 1 semana en `dry_run`**: carga en valle, sin escrituras, sin violaciones de 5,75 kW, SOC previsto coherente con el real (±10 %), y **previsión FV coherente con Solcast** (atributo `fuente_fv` visible).
- Parámetros editables por `reconfigure` sin recrear la entrada; **cambio aplicado con recálculo del plan en ≤ 60 s** (M3).
- `pytest` en verde (regresión F1+F2 + nuevas, incluidos tests de Solcast de M1) + `ruff check` limpio.
- **Ninguna escritura al inversor** (F3 no tiene código de escritura).
- `RESULTADO_F3.md`: documentar la deuda #10 como **aplazada** (pendiente de confirmación de entidad fiable de corriente en Solarman).

---

**Plan confirmado (v1.3): se implementa por unidades lógicas con un commit por unidad** (historico → predictor [con Solcast] → planificador → coordinador_plan → entidades → reconfigure → wiring/servicio → docs).