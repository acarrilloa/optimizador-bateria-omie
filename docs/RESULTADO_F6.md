# RESULTADO F6 — Pulido: fiabilidad de precios, avisos, dashboard e informe (0.13.1 + 0.14.0/0.14.1)

**Fecha:** 2026-11-20 · **Estado:** implementada; `pytest -q` 402 passed, `ruff check .` limpio.

## Qué se implementó

1. **U0 (0.13.1) — incidente precios ×1000**: regex del fallback
   `omie_web` tolerante a negativos y anclado a "Precio marginal espa";
   `null` rechazado (rompería la correspondencia temporal); **sanity
   check** de media diaria en `[-100, 400] €/MWh` → serie no plausible
   se descarta (nunca llega a la caché). Tests con fixture del día real
   del incidente (negativos + array señuelo) y serie fuera de escala.
2. **U1 (0.14.0) — avisos persistentes de datos**: alerta "sin precios
   de mañana pasadas las 16:00" (idempotente por día, se retira al
   llegar los precios); notificación de cambio de fuente de forecast
   (ML↔perfil) y de fallo del reentrenamiento (una vez al día).
3. **U2 (0.14.0) — dashboard**: `docs/dashboard/` con tres vistas
   listas para copiar (principal: precios/plan/SOC/estado; ahorro:
   estimado vs efectivo; calidad: forecast). Requiere `apexcharts-card`
   y `mini-graph-card` (alternativa nativa documentada). Ajustar el
   prefijo de entidades si el dispositivo no se llama `inversor`.
4. **U3 (0.14.1) — informe**: servicio
   **`optimizador_bateria_omie.resumen_diario`** con respuesta: ahorro
   estimado vs efectivo + desglose, tramos del plan por acción,
   escrituras REALES del día desde el JSONL de auditoría (día local,
   corrupción tolerada), SOC y estado de control. Pensado para una
   automatización que lo notifique al móvil.

## Qué falta / pendiente del usuario

- Instalar 0.13.1 → después 0.14.1 (o directamente 0.14.1, incluye todo).
- Copiar el dashboard (`docs/dashboard/optimizador_bateria_omie.yaml`);
  instalar `apexcharts-card` y `mini-graph-card` si no están.
- Crear la automatización del informe diario llamando a `resumen_diario`
  (respuesta en `action_response`) y enviándola al móvil; ejemplo de
  plantilla en el README del dashboard.
- **Criterio de salida F6 (SPEC)**: 30 días sin intervención manual.

## Correcciones/decisiones respecto del plan

- La "persistencia semanal opcional" de U3 queda fuera: el recorder de
  HA ya retiene la serie diaria de `ahorro_*` y el JSONL de auditoría es
  la serie permanente; duplicarla tendría coste sin beneficio.
- U1+U2+U3 se entregaron juntos: el manifest final es 0.14.1.
