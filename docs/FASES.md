# FASES — Estado del proyecto

Fuente única de verdad: `SPEC.md`. Este fichero resume el estado y los criterios de salida por fase (§8 del SPEC).

| Fase | Versión | Estado | Observaciones |
|---|---|---|---|
| F1 — Precios OMIE + tarifa 2.0TD | 0.7.3 | ✅ completada | Cascada: caché → sensores plantilla → web oficial OMIE (0.10.2) |
| F2 — Telemetría (ha_entities operativo) | 0.8.10 | ✅ completada | `solarman_modbus` preparado, no operativo (sin gateway) |
| F3 — Predicción v1 (perfil) + planner greedy | 0.9.0 | ✅ completada | Predicción p10/p50/p90 + Solcast + arbitraje con rentabilidad explícita (0.11.0/0.11.1) |
| F4 — Executor de control real | 0.10.0 | ✅ completada | Primera activación real en producción 2026-11-09; 0.11.3–0.11.6 corrigieron la cadena Solarman (servicios 25.x, FC 16, activación atómica, SOC hot-sync) |
| F4.5 — Auditoría de ahorro (anticipada) | 0.10.1 | ✅ completada | Refinada en 0.12.0 (factor de excedentes + media por tramo) |
| F5 — ML + LP | 0.12.0–0.12.1 | ✅ implementada | Forecaster ML con guardrail, LP conmutable, clima AEMET. **Criterio MAPE < 20 % con datos reales: observación en curso del usuario** |
| F5.3 — Estabilidad del plan y watchdog | 0.13.0 | ✅ implementada | Bloque de compromiso (D-F5.3.1), plan no derribado por gaps de telemetría, watchdog 4× con debounce y timeout de 600 s, estados `gradual`/`sin_telemetria`. Medición de 48 h pendiente del usuario |
| F6 — Pulido (dashboard, informes) | — | 🚧 pendiente | No empezar sin confirmación |

## Pendientes del usuario

- **F5.3 (48 h post-despliegue)**: verificar transiciones del plan < 30/48 h y episodios `sin_telemetria` < 3/día; revisar plantillas que comparen `estado_de_control` contra `dry_run` (el caso gradual ahora reporta `gradual`).
- **Validar la familia 2.0TD** ejecutando `probe_registers` con el SOC actual (hoy `familia_validada: ninguna` mantiene TODO en simulación: 0 escrituras reales de 4.316 en la auditoría).
- Observar `sensor.*_forecast_quality` (MAPE ML vs perfil) durante ~1 semana; decidir conmutar `switch.*_usar_planificador_lp` cuando la comparación lo aconseje.
- Reconfigurar `entity_id_clima` (weather de AEMET) y `entity_id_temp_historico` (sensor de temperatura) para activar las features de clima del ML.
- Configurar `factor de precio de excedentes` (default 1 = sin cambio en los números).
