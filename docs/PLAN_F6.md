# PLAN F6 — Pulido: fiabilidad de precios, dashboard, notificaciones e informes (0.14.0)

**Fecha:** 2026-11-20 · **Estado:** ⏳ pendiente de confirmación del usuario
**Fuente:** `SPEC.md` §8 (F6: dashboard, notificaciones, informes; criterio de salida: 30 días sin intervención) e `incidente_precios_x1000_optimizador_omie.md`.

## U0 — Hotfix incidente precios ×1000 (primera entrega, 0.13.1)

Candidato a ir **ANTES** que cualquier otra cosa de F6: envenenó la caché
y todos los derivados (precio efectivo, ahorro, plan LP) durante un día
entero, y es recurrente con precios negativos.

**Solución en `cliente_omie.py::_obtener_desde_omie_web`** (dos capas):

1. **Regex anclado a la serie real y tolerante a negativos/nulos**:
   ```python
   idx = html.find("Precio marginal espa")
   m = re.search(
       r'\\?"data\\?"\s*:\s*\[((?:-?[0-9]+(?:\.[0-9]+)?|null)\s*(?:,\s*(?:-?[0-9]+(?:\.[0-9]+)?|null)\s*)*)\]',
       html[idx : idx + 4500],
   )
   ```
   Filtrando `null` tras el split. Con negativos ya no "salta" al
   siguiente array numérico de otra gráfica (el `31164.7, 30991.0…`).
2. **Sanity check de plausibilidad (defensa en profundidad)**: tras el
   `/1000`, si la media de precios queda fuera de `[-100, 400] €/MWh`
   → `ErrorFuenteOmie` explícito ("serie capturada no plausible"), que la
   cascada trata como fuente no disponible (salta al siguiente eslabón,
   no envenena la caché).

**Tests**: HTML fixture con precios negativos + array señuelo de otra
gráfica (ambos casos del incidente); test de sanity check; test de
regresión del fixture actual. Añado además validación de
`auditoria_del_día`: no — fuera de alcance.

## U1 — Notificaciones y alertas

Hoy existen: fallback (`persistent_notification`), evento
`*_sin_telemetria` (con debounce desde 0.13.0). Faltan alertas de
**datos**, que es lo que demostró el incidente:

1. **Alerta de datos de precios sospechosos** (ya cubierta por el sanity
   check de U0: la cascada lo trata como "fuente caída" y el motivo queda
   en el log/sensor; sin pieza nueva).
2. **Alerta "sin precios de mañana después de las 16:00"**: hora límite
   razonable (publicación ~13:45); `persistent_notification` + atributo
   en el sensor de estado de precios. Evita descubrirlo a las 00:00.
3. **Alerta de cambio de fuente de forecast** (`adoptado` ML↔perfil) y de
   reentrenamiento fallido: solo WARNING de log hoy; pasa a notificación
   persistente (nivel WARNING, no móvil).

## U2 — Dashboard (Lovelace YAML, sin código Python)

Entrega como documentos YAML del repo (`docs/dashboard/*.yaml`) que el
usuario copia a su configuración — HA no admite instalar dashboards
desde un custom component sin "Add-on"/blueprint más complejo:

1. **Vista principal**: precio efectivo hora a hora (ApexCharts, hoy +
   mañana), acción del plan actual (badge con `gradual`/`activo`/…), SOC
   real vs previsto, estado de control.
2. **Vista ahorro**: `ahorro_estimado_hoy` vs `ahorro_efectivo_hoy`
   (gráfico diario), desglose de la auditoría (descarga/carga).
3. **Vista calidad**: `forecast_quality` (MAPE ML vs perfil, fuente
   activa), curva de predicción de consumo.

Depende de `custom:apexcharts-card` y `custom:mini-graph-card` (aviso:
dependencias del usuario, no del componente).

## U3 — Informe diario

1. **Servicio `resumen_diario`** (`services.yaml` + núcleo testeable):
   devuelve dict con ahorro estimado vs efectivo del día, número de
   tramos CARGA/VENTA/AUTOCONSUMO, escrituras reales de la auditoría y
   estado final del SOC. Diseñado para automatización del usuario (lo
   envía como notificación móvil/resumen a la hora que prefiera).
2. **Persistencia semanal opcional**: fichero JSONL `resumenes_diarios`
   en `config/optimizador_bateria_omie/` para tener serie histórica sin
   depender del recorder.

## Fuera de alcance de F6

- La **venta física del Deye** (TOU/fase "F5.2"): decisión separada del
  usuario; el modo `completo` con VENTA_RED por 211 es la vía existente.
- Cambios en planificadores/ML (solo vigilancia del MAPE en curso).
- `solarman_modbus` (bloqueado por hardware, §9 SPEC).

## Orden de entrega propuesto

| Paso | Versión | Contenido |
|---|---|---|
| 1 (hotfix) | 0.13.1 | ✅ **ENTREGADO**: U0 fix regex + sanity check + tests (2026-11-20) |
| 2 | 0.14.0 | U1 + U2 (documentos YAML) |
| 3 | 0.14.1 | U3 informe diario + persistencia |

## Criterios de salida de F6

1. Incidente ×1000: imposible reproducirlo con el HTML del día real
   (fixture con negativos + señuelo) — tests en verde.
2. 30 días sin intervención del usuario en HA (criterio del SPEC).
3. Dashboard desplegado en el HA del usuario e informe diario llegando
   por la vía que elija.

## Archivos previstos

| Archivo | Cambio |
|---|---|
| `cliente_omie.py` | U0 regex + sanity check |
| `tests/test_cliente_omie.py` | Fixtures HTML con negativos/señuelo + tests |
| `coordinador.py` / `entidades*` | U1 alerta sin-precios-mañana, atributos |
| `services.py` + `services.yaml` + `translations` | U3 servicio `resumen_diario` |
| `docs/dashboard/informe_omie.yaml` + 3 vistas | U2 dashboard |
| `FASES.md`, `SPEC.md`, `RESULTADO_F6.md`, `README.md` | cierre de fase |

## Riesgos

1. El HTML de omie.es puede cambiar sin aviso: el sanity check limita el
   daño (mala serie → fuente no disponible, no espacio equivocado).
2. `ApexCharts-card` como dependencia externa del usuario: documentar y
   ofrecer alternativa nativa (`statistics-graph`) para la vista 1.
