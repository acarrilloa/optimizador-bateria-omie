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
| F6 — Pulido (dashboard, notificaciones, informes) | 0.13.1–0.14.1 | ✅ implementada | Hotfix incidente ×1000 (regex+sanity), avisos persistentes de datos, dashboard YAML de 3 vistas, servicio `resumen_diario`. **Criterio de salida: 30 días sin intervención** |
| F5.2 — Saneo de arranque 228/229 y cap 245 | 0.14.5 | ✅ implementada | Incidente 21-nov-2026: el AND de arranque (228=49 V) congelaba las cargas; rearme por tramo con cooldown 1 h/regístro y cap de venta al 245 real. Evidencia: `docs/f5_2/` |
| F6/A+B — ToU como actuador + cobertura U1 | 0.15.0–0.15.1 | ✅ implementada | 248 auto-restaurado por firmware: la energía la gobierna la tabla 250-279 (nunca 244-249). Vigilante v2 con reparación 1/h. Check de cobertura cada 30 min (replan inmediato `cobertura_insuficiente`) + ritmo de consumo + bug 2 (entidad SOC verificada). 0.15.1: respaldo por valor de la desviación + observabilidad de SOC congelado (`soc_edad_min`), tras detectar que una entidad congelada (datalogger caído) no emite eventos. Evidencia: `docs/f6/`. **Calibraciones físicas D1-D4 pendientes del usuario** |
| Planner LP realista | 0.15.2 | ✅ implementada | η round-trip en la cadena de SOC (muere el micro-arbitraje < 13,6 %), exportación tapada al 245 físico (5.000 W) y valor residual de la SOC final (adiós al vertido a precios míseros al cerrar el horizonte) |

## Pendientes del usuario

- **Criterio de salida F6**: 30 días sin intervención manual (vigilancia en curso desde 0.14.1).
- **F5.3 (48 h post-despliegue)**: verificar transiciones del plan < 30/48 h y episodios `sin_telemetria` < 3/día; revisar plantillas que comparen `estado_de_control` contra `dry_run` (el caso gradual ahora reporta `gradual`).
- Instalar `apexcharts-card`/`mini-graph-card`, copiar el dashboard (`docs/dashboard/`) y crear la automatización del informe diario (`resumen_diario`, example en el README del dashboard).
- Confirmar el sensor de `consumo_casa` (`load_power` vs `external_power`) — afecta al MAPE del forecast (última medición: 111 %).
- Reconfigurar `entity_id_clima` (weather de AEMET) y `entity_id_temp_historico` (sensor de temperatura) para activar las features de clima del ML.
- Configurar `factor de precio de excedentes` (default 1 = sin cambio en los números).
