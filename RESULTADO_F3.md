# RESULTADO_F3 — Predicción de consumo v1 + Planificador greedy (dry-run)

> **Estado**: F3 **implementada** (manifest **0.9.0**), `pytest` en verde (226 tests) y `ruff` limpio.
> **Plan ejecutado**: PLAN_F3 v1.3 (M1 Solcast, M2 deuda #10 aplazada, M3 defaults editables). Commits por unidad lógica U1–U7.

## Qué se implementó

1. **`historico.py`** (`FuenteHistoricoRecorder`): consulta al recorder en executor, agregación de muestras a tramos de 15 min (media), descarte de `unavailable`/`unknown`/no numérico/unidad ≠ W, **sin imputación**; ventana configurable (7–90 días, default 28).
2. **`predictor_consumo.py`** (`PredictorConsumo`): perfil estadístico p10/p50/p90 por `tramo × tipo de día` (laborable / finde-festivo / global), persistido en `cache/perfil_consumo.json`, recálculo diario a las 04:35 y cascada D6 (mínimo 3 días; sin datos → `None`, nunca inventado). **Previsión FV (M1)**: Solcast preferente parseando `detailedForecast` de las entidades de hoy y mañana (`entity_id_prevision_fv_hoy/_manana`, defaults `sensor.solcast_pv_forecast_forecast_today/_tomorrow`), mapeo 30→15 min por repetición, conversión kWh/periodo → W (×2000, D8b); **fallback por día** al perfil estadístico con `INFO`; atributo `fuente_fv` (`solcast`/`perfil`/`none`) expuesto por día.
3. **`planificador.py`** (`PlanificadorGreedy`): función pura con balance SOC; autoconsumo siempre; arbitraje de compra solo si `precio ≤ precio_max_compra` **y** bajo la mediana del día; precio ≤ 0 → carga sin umbral; venta solo si el ciclo compensa (`p_venta ≥ p_compra/η + margen`); restricción dura 5,75 kW (D10, nunca negativa); convención Deye +descarga/−carga; estados `ok`/`sin_precios`/`perfil_insuficiente`/`sin_telemetria`; ahorro estimado teórico por tramo y del día.
4. **`coordinador_plan.py`**: replanifica en arranque, precios nuevos, cambio de tramo (15 min), desviación de SOC > umbral, `festivo_hoy`, servicio `regenerate_plan`; job diario 04:35 (perfil + plan); **sin polling ni escrituras**.
5. **Entidades**: `sensor.*_prediccion_consumo` (p50 + curvas + `fuente_fv`), `sensor.*_plan` (acción del tramo + plan completo), `sensor.*_ahorro_estimado_hoy` (€, teórico), `switch.*_festivo_hoy`.
6. **Reconfigure ampliado (M3)**: todos los parámetros de §5 editables en caliente (`entry.data.get(clave, default)`); el cambio recarga la entrada y replanifica de inmediato (≤ 60 s garantizado). `festivos_locales` como texto "MM-DD, MM-DD".
7. **Servicio** `optimizador_bateria_omie.regenerate_plan: {}` → `{recalculado, accion_actual, ahorro_estimado}`.

## Qué falta / pendiente del usuario (con acceso al hardware)

- **Revisión manual de 1 semana en `dry_run`** (criterio de salida de §8): comprobar que el plan carga en valle, respeta 5,75 kW, el SOC previsto coherente con el real (±10 %) y la previsión FV coherente con Solcast (atributo `fuente_fv` visible).
- **Confirmar los entity_ids de Solcast** en HA si difirieran de los defaults (reconfigurables); verificar la **magnitud en W** de la previsión FV frente a la FV real durante la semana (D8b: si la integración publicara otra unidad, solo cambia `KWH_30MIN_A_W`).
- **InfluxDB**: queda como TODO documentado (D4); no se consulta en F3.
- **Deuda #10 (corriente en `ha_entities`)**: **aplazada** (M2) — pendiente de que el usuario confirme una entidad fiable en Solarman; no bloquea nada.
- La predicción necesita ≥3 días de recorder por tramo×tipo; los primeros días mostrará tramos `insuficientes` hasta acumular ventana.

## Notas de implementación documentadas (desviaciones menores del plan)

- **D8b**: `detailedForecast` publica kWh/periodo de 30 min → potencia W = valor × 2000 al repetir en los dos tramos.
- **Tramos de reloj pared (0–95)**: en el cambio horario de octubre la hora repetida se fusiona en los mismos tramos (96, no 100); en marzo el día tiene 92 tramos.
- **`capacidad_bateria_kwh`** (default 15,36) añadida como parámetro editable: necesaria para el balance SOC de D9 y no estaba en §5.
- Estado adicional `sin_telemetria` cuando no hay SOC real (no se inventa SOC inicial).
- No existe `FASES.md` en el repositorio: el roadmap de fases se mantiene en **SPEC §8** (actualizado con F3 ✅ 0.9.0).

## Archivos

- **Creados**: `historico.py`, `predictor_consumo.py`, `planificador.py`, `coordinador_plan.py`, `entidades_plan.py`, `switch.py`, `tests/test_{historico,predictor_consumo,planificador,coordinador_plan,entidades_plan,reconfigure_f3}.py`, `RESULTADO_F3.md`.
- **Modificados**: `const.py`, `config_flow.py`, `__init__.py`, `sensor.py`, `services.py`, `services.yaml`, `manifest.json` (0.9.0, dependencia `recorder`), `strings.json`, `translations/es.json`, `registers.yaml` (alineado `temperatura_bateria`), `SPEC.md`.

## Verificación

```bash
.venv/Scripts/python.exe -m pytest custom_components/optimizador_bateria_omie/tests -q   # 226 passed
.venv/Scripts/python.exe -m ruff check .                                                # limpio
```

Sin llamadas de red ni hardware en los tests (todo con mocks/fixtures, incluidos los fixtures de `detailedForecast` de Solcast). **Ninguna escritura al inversor**: F3 no contiene código de escritura.
