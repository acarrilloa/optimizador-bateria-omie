# RESULTADO F5.3 — Estabilidad del plan y fiabilidad del watchdog (0.13.0)

**Fecha:** 2026-11-17 · **Estado:** implementada; `pytest -q` 390 passed, `ruff check .` limpio.

## Qué se implementó

1. **Bloque de compromiso (D-F5.3.1)**: el plan periódico solo se redecide en
   límites de bloque alineados a medianoche. Number en caliente
   `number.*_bloque_minutos` (15–120, paso 15, default 60). El listener
   de tramo sigue disparando cada 15 min pero solo replanifica en el
   límite. Los replanes reactivos (precios nuevos, perfil 04:35, numbers,
   festivo) recalculan el horizonte PERO exportan los tramos del bloque
   en curso del plan previo; solo `desviacion_soc` (D11) aplica inmediato
   (seguridad y ventana de oportunidad ante caída de SOC). Atributos
   nuevos del sensor plan: `bloque_minutos` y `proximo_replan`.
2. **Telemetría sin SOC no derriba el plan (D-F5.3.3)**: con un plan
   vigente, los gaps que dejan el SOC a `None` congelan el plan en vez
   de publicar un plan vacío `sin_telemetria` (era el "apagón" del plan
   en cada microcorte de Solarman). Sin plan previo, mantiene el
   comportamiento original.
3. **Watchdog con debounce y timeout mayor (D-F5.3.3)**: umbral de
   obsolescencia a 4 × `intervalo_telemetria_s` (antes 2 ×; el jitter de
   Solarman lo superaba a diario). Las escrituras se cortan desde la
   PRIMERA observación (D7 intacto); el estado `sin_telemetria` se
   declara tras 2 evaluaciones obsoletas consecutivas; el fallback solo
   llega tras 600 s de caída continuada con carga activa (antes caía al
   primer check). El evento móvil `*_sin_telemetria` también exige 2
   observaciones consecutivas caídas (menos alertas falsas); la
   recuperación sigue disparando con la primera lectura buena.
4. **Estados legibles (D-F5.3.4)**: `estado_de_control` diferencia
   `gradual` (acción simulada por modo gradual) de `dry_run` real; nuevo
   estado `sin_telemetria`; atributos nuevos `modo_activacion` y
   `telemetria_fresca`.

## Qué falta / observación

- **Medición post-despliegue por el usuario**: transiciones del plan < 30/48 h
  y permanencia media > 1 h; episodios `sin_telemetria` < 3/día (criterios
  de salida del PLAN_F5.3). El análisis previo medía 87/48 h, 57 % de
  permanencias ≤ 20 min y 10 apagones/día.
- `estado_de_control` ya NO reporta `dry_run` para el caso simulado por
  gradual: **revisar plantillas/automatizaciones que comparen contra
  `dry_run`** (el control OFF sigue reportándose como `inactivo`).
- La histéresis económica y el switching cost del LP quedan diferidos
  (D-F5.3.2): el bloque de compromiso elimina la replanificación por
  ruido; solo se añadirán si quedan saltos hora a hora medibles.
- Pendiente de antes de F5.3: validación de familia 2.0TD con
  `probe_registers` (hoy TODO sigue simulado: 0 escrituras reales de
  4.316 en la auditoría) y confirmar el sensor de `consumo_casa`
  (`load_power` vs `external_power`) para el MAPE.

## Pasos que requieren el usuario

1. Instalar 0.13.0 (HACS/Marketplace) y reiniciar la integración.
2. Ajustar `number.inversor_optimizador_bateria_omie_bloque_minutos`
   si se quiere otra cadencia (default 60 min).
3. Ejecutar `optimizador_bateria_omie.probe_registers` con el SOC actual
   (validación de familia; procedimiento detallado en la conversación).
4. Observar 48 h de auditoría y comparar contra el análisis
   (`docs/analisis_plan_optimizador_bateria_omie.md`).
