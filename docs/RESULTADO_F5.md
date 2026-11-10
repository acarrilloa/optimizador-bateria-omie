# RESULTADO F5 — ML + Programación Lineal (0.12.0)

## Qué se implementó

- **Forecaster ML (RF-05 capa 2)**: `forecast_ml.py` con `HistGradientBoostingRegressor` (scikit-learn), features cíclicas + festivos + lags de 7/14/21/28 días + temperatura (NaN sin clima). Reentrenamiento diario (04:35, tras el perfil) y en arranque solo si no hay modelo; persistencia joblib en `config/optimizador_bateria_omie/`. **Guardrail D-F5.2**: el ML solo predice si su MAPE (holdout cronológico 70/30) mejora el del perfil estadístico de F3; `festivo_hoy` manual cae siempre al perfil. Nuevo sensor diagnóstico `sensor.*_forecast_quality`.
- **Planner LP (RF-06)**: `planner_lp.py` (PuLP 3.x + CBC) con el mismo contrato que el greedy — executor F4 y salvaguardas intactos. Óptimo global del horizonte hoy+mañana con balance de nodo, exclusión carga/descarga y import/export por tramo, curtail solo de FV, restricción dura de importación y guardarraíles absolutos (tope de compra → red solo para la casa; piso de venta). Selector en caliente `switch.*_usar_planificador_lp` (default greedy) con fallback automático a greedy ante cualquier fallo.
- **Auditoría refinada (U4)**: energía por tramo con la **media real de las lecturas** (búfer por tramo) y exportación valorada con `factor de precio de excedentes` (number caliente, default 1) — nunca a negativos.
- **Clima AEMET (U5)**: `entity_id_clima` (weather) para la temperatura prevista y `entity_id_temp_historico` (sensor) para las medias históricas; ambos opcionales por Reconfigurar; sin configurar, el modelo ignora la feature.

## Verificación

- Suite completa: **379 tests** (71 nuevos en F5), todo con mocks/sintético sin red ni hardware. `ruff check` limpio.
- Tests clave: guardrail de adopción (ML peor → perfil), coste LP ≤ greedy en escenarios del greedy, nunca importar-para-verter, guardarraíles y SOC, persistencia del modelo, medias de la auditoría y factor de excedentes.

## Qué falta / requiere el usuario

- **Instalar 0.12.0 en el Yellow** (HACS traerá scikit-learn y PuLP; el primer arranque reentrena en segundo plano con el histórico del recorder ≥ 2 días; luego diariamente a las 04:35).
- Observar `forecast_quality` unos días y **decidir cuándo conmutar a LP** (el switch es en caliente; menos de una semana si los números lo aconsejan — sin coste algorítmico).
- Configurar el clima en Reconfigurar cuando desee activar la feature (IDs reales de su AEMET).
- Criterio de salida §8 (MAPE < 20 % con datos reales y ahorro medible): se cierra con la observación del usuario, no en desarrollo.

## Conocimientos de la activación real incorporados a F5

La primera activación real de F4 (2026-11-09) destapó la cadena Solarman corregida en 0.11.3–0.11.6 (servicios renombrados 25.x, FC 16, activación atómica, sync del SOC). F5 hereda ese camino de datos ya validado: **cero escrituras nuevas al inversor** (D-F5.4) — la LP produce los mismos `PlanTramo` que el executor ya sabe aplicar.
