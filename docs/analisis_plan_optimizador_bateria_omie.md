# Análisis del plan de actuación — Optimizador Batería OMIE v0.12.3

**Fecha:** 2026-11-17 · **Autor:** Mistral Vibe · **Fuente:** historial de 48 h del plan,
auditoría de escrituras (`/config/optimizador_bateria_omie/auditoria_escrituras.jsonl`,
4.316 entradas) y código de la integración instalada (`custom_components/optimizador_bateria_omie`).

## Hechos medidos

| Métrica | Valor |
|---|---|
| Transiciones del plan | **87 en 48 h (1,8/hora)** |
| Permanencias ≤ 20 min | 57 % de los estados |
| Saltos directos CARGA↔VENTA | 29 (21 duraron < 45 min) |
| Episodios `sin_telemetria` | 10 (duración media 8 min) |
| Escrituras reales al inversor | **0 de 4.316** — todas `"simulada por modo gradual"` |
| `familia_validada` | `ninguna` |
| Forecast quality (MAPE) | 111,62 % |
| `clasificacion` | estable (cambia cada 4 h, no es el driver del flapping) |

Ejemplos de zigzag (16-11-2026): 19:00 CARGA→19:15 VENTA→19:45 CARGA→20:00 VENTA;
y flapping extremo 02:20–02:45 con cambios cada 1–4 min por telemetría intermitente.

## 1. No se actúa sobre el inversor (confirmado)

No es un fallo: es el **modo de activación `gradual`** (`CONF_MODO_ACTIVACION`,
por defecto `gradual`, `executor.py::_accion_ejecutable`): solo se ejecuta
CARGA_RED en valle real 2.0TD; AUTOCONSUMO y VENTA_RED son siempre simuladas,
de modo que `estado_de_control` queda en `dry_run` (por `_ultimo_simulado`,
`executor.py::_actualizar_estado`).

Además `familia_validada = "ninguna"`: mientras la familia tarifaria no se
valide, **ni siquiera el CARGA_RED nocturno se ejecuta** (los tramos de valle
de la noche del 16-17 figuran simulados). Las únicas escrituras reales
registradas en `ultimo_aplicado` son los límites SOC (`217`, `218`) del arranque.

## 2. Causas del flapping

### a) Replanificación por tramo de 15 min sin memoria ni histéresis
Cada `:00/:15/:30/:45` se redecide el tramo de forma independiente. Con el
margen de arbitraje a 0,015 €/kWh, cualquier ruido inclina la decisión.
Un flip CARGA→VENTA→CARGA en una hora tiene un coste efectivo del 12 %
(round-trip 88 %), con ganancia marginal nula.

### b) Forecast de consumo muy ruidoso
`forecast_quality = 111,62` (MAPE %): el predictor ML apenas mejora al azar.
Cada replanificación usa un consumo previsto distinto → planes distintos.
Es el multiplicador de (a): no solo replanifica cada 15 min, sino sobre
datos que se mueven.

### c) `sin_telemetria` por gaps transitorios de Solarman
El watchdog (D7, `executor.py::_telemetria_fresca`) declara telemetría
obsoleta si supera **2 × `intervalo_telemetria_s` (30 s → 60 s)**. El polling
de Solarman presenta jitter > 60 s con frecuencia (episodios recurrentes en
:06–:11, :17–:21, :30–:36) → 10 apagones de plan al día que se revierten solos.

Nota: la automatización del ACS de este repositorio usa `clasificacion`
(estable, cada 4 h), no el plan — el flapping no afecta al control del ACS.

## 3. Recomendaciones para el desarrollador

1. **Ventana de compromiso + dwell mínimo**: congelar el plan en bloques de
   1 h (4 tramos) y exigir ≥ 2 tramos de permanencia salvo seguridad; y/o
   añadir penalización por cambio de acción en el LP (switching cost).
2. **Histéresis económica**: solo cambiar de acción si la ganancia esperada
   del bloque supera p. ej. 2 × margen de arbitraje × energía del tramo.
3. **Fallback del forecast**: si MAPE > 60 %, usar el perfil histórico en
   lugar del ML (guardrail D-F5.2) — arreglaría la mayor parte del zigzag.
4. **Debounce de telemetría**: exigir 2–3 lecturas consecutivas obsoletas
   antes de declarar `sin_telemetria`; que el watchdog corte escrituras (ya
   lo hace) pero no derribe el plan — mantener el último tramo hasta un
   timeout mayor (p. ej. 10 min).
5. **Pasar de gradual a real**: completar la validación de la familia 2.0TD
   (`familia_validada: ninguna` mantiene todo en simulación) y, con
   confianza, pasar a `modo_activacion: completo`.
6. **Legibilidad**: `dry_run` es engañoso (sugiere prueba global cuando es
   "gradual simulando la acción no ejecutable"); exponer `modo_activacion`
   como atributo del sensor de estado de control.

## Anexos: evidencia

Muestras de la auditoría (todas simuladas):

```json
{"ts": "2026-11-17T15:21:40", "tramo": "16:15", "accion": "CARGA_RED", "ejecutada": false, "motivo": "plan: simulada por modo gradual"}
{"ts": "2026-11-17T16:00:01", "tramo": "17:00", "accion": "CARGA_RED", "ejecutada": false, "motivo": "plan: simulada por modo gradual"}
{"ts": "2026-11-17T16:15:01", "tramo": "17:15", "accion": "AUTOCONSUMO", "ejecutada": false, "motivo": "plan: simulada por modo gradual"}
```

Registros objetivo por acción (Deye): CARGA_RED → `210` (SOC máximo?),
`230` (potencia de carga), `232` (habilitación de carga); VENTA_RED → añade
`211` (SOC de descarga); AUTOCONSUMO → `230`/`232` a 0.
