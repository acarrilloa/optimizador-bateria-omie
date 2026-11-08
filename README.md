# Optimizador Batería OMIE

![Versión](https://img.shields.io/badge/versión-0.10.0-blue)
![F2](https://img.shields.io/badge/F2-telemetría-completada-brightgreen)
![F3](https://img.shields.io/badge/F3-plan%20dry--run-completada-brightgreen)
![F4](https://img.shields.io/badge/F4-control%20real-completada-brightgreen)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.10.1-41BDF5)
![Python](https://img.shields.io/badge/Python-3.14-3776AB)

**Custom component para Home Assistant** que optimiza la carga/descarga de baterías usando precios del mercado eléctrico español con resolución de 15 minutos (OMIE MTU15) y la tarifa eléctrica 2.0TD.

> **Estado del proyecto**: **F1 (Precios) ✅ en producción** (0.7.x). **F2 (Telemetría) ✅ completada** (v0.8.10) — vía operativa `ha_entities` con la integración oficial Solarman. **F3 (Predicción + Planificador greedy) ✅ completada** (0.9.x) — plan en `dry_run` revisado y validado. **F4 (Executor de control) ✅ completada** (0.10.0) — **escritura del inversor vía los servicios de la integración oficial Solarman**, sin gateway RS485. Diseño completo en `SPEC.md` (v0.9.4).

## Características

### Fase F1 (Implementada) ✅

- **Obtención de precios OMIE a 15 min** vía los sensores de plantilla `sensor.omie_precios_de_es_{ayer,hoy,manana}` (creados con la integración oficial OMIE `omie.get_prices_for_date`).
- **Fallback web oficial de OMIE** (`www.omie.es`, página day-ahead con `period=15`, serie SSR «Precio marginal español» en €/MWh) si los sensores de plantilla no tienen datos — p. ej. cuando la integración aún no ha refrescado su sensor de mañana tras la publicación D+1 (~13:45 hora local).
- **Caché en disco** por día (`cache/YYYY-MM-DD.json`), validada a **92, 96 o 100 tramos** (días con cambio horario). Sobrevive a reinicios; limpieza automática según `dias_cache` (arranque + job diario 04:30).
- **Precio efectivo**: spot + peajes + cargos + impuestos 2.0TD (valores por defecto de `tarifa.py`, editables por *Reconfigurar* desde F4 — deuda #5 resuelta).
- **Clasificación de tramos por período 2.0TD** (`valle` / `llano` / `punta`) según hora local, con fines de semana y festivos nacionales = valle todo el día.
- **Cinco sensores** (los `entity_id` de la instalación de referencia):
  - `sensor.inversor_optimizador_bateria_omie_precio_actual` — precio del tramo actual en €/kWh; atributo `fuente` (`omie`, `omie_web`, `desconocida`).
  - `sensor.inversor_optimizador_bateria_omie_precio_efectivo` — precio con peajes, cargos e impuestos.
  - `sensor.inversor_optimizador_bateria_omie_precio_clasificacion` — `valle` / `llano` / `punta` por período 2.0TD.
  - `sensor.inversor_optimizador_bateria_omie_precio_estadisticas` — estado = **mediana** del día; atributos `min`, `max`, `mediana`, `fuente`, `ts_actualizacion`.
  - `sensor.inversor_optimizador_bateria_omie_precios_efectivos_hoy` — **curva completa de precios efectivos del día** (entrada del planificador de F3).

> **Nota sobre el prefijo `inversor_`**: los `entity_id` dependen del nombre del dispositivo/entrada de configuración. Si renombras el dispositivo en HA, los IDs cambiarán. Los ejemplos usan el nombre de la instalación de referencia. Los `unique_id` son estables (`optimizador_bateria_omie_*`).

### Fase F2 (Completada) ✅ — Telemetría del inversor

Lectura del estado del inversor Deye SUN-6K-SG05LP1-EU-AM2-P.

- **Vía operativa: `ha_entities`** (por defecto). La telemetría se lee de las entidades que publica la **integración oficial Solarman** ya instalada, vía `hass.states.get()` — sin conexión directa al datalogger y sin conflictos con la integración.
- **Vía preparada: `solarman_modbus`** (implementada y probada, **inactiva**). El datalogger serie `3178171873` **no expone Modbus TCP local** (puertos 8899/502 cerrados al protocolo); se activará en el futuro con un gateway RS485→TCP externo (Elfin EW11 o similar). No es necesaria para F4.
- **`registers.yaml`** con la familia estándar Deye confirmada contra los manuales oficiales de Modbus (SOC 184, potencia batería 190, corriente 191, temperaturas 90/182 con offset 1000, consumo 178, FV 186) y, desde F4, la **sección `control`** (210/211 corrientes batería, 230 corriente carga red, 232 habilitación, 217/218 SOC). Las direcciones son configuración, nunca constantes de código, y admiten override del usuario.
- **Servicio `probe_registers`**: funcional (tolerancia ±2 %, plausibilidad, `reset`) pero **inactivo en modo `ha_entities`** — no toca Modbus. Listo para cuando se active `solarman_modbus`.
- **Ocho sensores**: `soc_bateria`, `potencia_bateria` (**+descarga/−carga**, convención Deye), `corriente_bateria` (`unavailable` en `ha_entities`, por diseño), `consumo_casa`, `produccion_fv`, `temperatura_inversor`, `temperatura_bateria` y el diagnóstico `familia_validada`.
- Refresco cada 30 s (configurable 10–300 s) vía `CoordinadorTelemetria`, independiente del coordinador de precios de 15 min.

### Fase F3 (Completada) ✅ — Predicción + Planificador greedy

Calcula un **plan por tramos de 15 min** priorizando autoconsumo y aplicando arbitraje solo si compensa.

- **`PredictorConsumo`**: perfil estadístico (mediana por tramo × tipo de día: laborable / fin-de-semana-o-festivo) con bootstrap desde el **HA recorder** (28 días configurables), banda p10/p50/p90 y persistencia en disco (recalculado diariamente a las 04:35).
- **Previsión de FV con Solcast PV Forecast como fuente preferente** (`sensor.solcast_pv_forecast_forecast_today` / `_forecast_tomorrow`, atributo `detailedForecast`, mapeo 30→15 min), con el perfil estadístico como fallback.
- **`PlanificadorGreedy`** (función pura, determinista): autoconsumo siempre; `CARGA_RED` en tramos baratos en contexto (bajo la mediana del día) **solo si el ciclo compensa contra el máximo de precios futuro del horizonte** (`precio/η + margen`; precio ≤ 0 → carga máxima); `VENTA_RED` solo si el ciclo compensa, **en tramos de pico del horizonte** (`precio ≥ máximo futuro`) y **nunca simultánea con `CARGA_RED`** (nada de importar para verter); restricción dura de **5,75 kW** de importación.
- **Replanificación**: precios nuevos, cambio de tramo (15 min), desviación SOC > 10 %, o servicio `regenerate_plan`.
- **Cuatro entidades**: `prediccion_consumo` (W + curva p10/p50/p90), `plan` (acción del tramo + plan pendiente desde el actual + ahorro estimado), `ahorro_estimado_hoy` (€, teórico) y `switch.festivo_hoy`.
- Parámetros del plan editables por *Reconfigurar* **y en caliente** vía las entidades `number` de F4, con recálculo del plan en ≤ 60 s.

### Fase F4 (Completada) ✅ — Executor de control del inversor

Aplica el plan de F3 **escribiendo en el inversor** con todas las salvaguardas. La escritura se hace **vía los servicios de la integración oficial Solarman** (`solarman.write_single_register` / `solarman.write_multiple_registers`; el deprecado `write_multiple_holding_registers` no se usa) y la verificación relee los registros con `solarman.read_holding_registers`. **Sin gateway RS485 y sin `solarman_modbus`.**

- **Doble puerta de activación**: `switch.*_control_activo`, **por defecto OFF**. Con OFF, todas las acciones se **simulan** (se traducen, se loguean con prefijo `[DRY-RUN]` y se auditan con `ejecutada: false`) pero **nunca se escribe**. La activación real es un acto explícito; se recomienda la primera en **valle nocturno**, observada contra la APP Solarman.
- **Traducción plan → registros solo desde `registers.yaml`** (sección `control`): `CARGA_RED` = habilitar carga red (232) + corriente de carga desde red (230); el límite de carga de batería (210) refleja `potencia_max_carga` (nunca la corriente del tramo, para no estrangular la carga solar); `AUTOCONSUMO` = valores seguros (232=0, 230=0); SOC mínimo (217) y de recuperación (218) solo al activar o cambiar parámetros. **Ninguna dirección ni límite en código.**
- **Write-verify**: tras cada escritura real, espera configurable (`latencia_verify_s`, default 3 s) y relectura vía `solarman.read_holding_registers`. Si la relectura falla → verificación *soft* marcada `parcial`. **3 fallos consecutivos → `fallback`**: valores seguros una vez, `control_activo` a OFF automático + notificación persistente. Reactivación solo manual.
- **Rate limiter**: máx. **12 escrituras/hora** reales (configurable 1–48), con **idempotencia** (no se reescriben valores ya aplicados). Superado el límite → aplaza al siguiente tramo con WARNING.
- **Watchdog**: telemetría obsoleta (> 2× intervalo) o plan ausente en cambio de tramo → no escribe; si hay carga desde red activa → fallback.
- **Guard de potencia contratada**: importación > 95 % de `potencia_contratada` (5,75 kW) → reduce la corriente de carga; ≥ 100 % → 0 A inmediato. Fuente preferente: `entity_id_potencia_red` (registro Deye 169, convención >0 = importación); sin ella, cálculo derivado desde telemetría.
- **Bloqueo por fallo del inversor**: si alguna entidad de `entity_ids_fallo` (registros Deye 103–106, códigos F07–F64) es ≠ 0 → no se escribe; con carga activa → fallback.
- **Activación gradual** (`modo_activacion`, default `gradual`): solo se ejecutan de verdad las `CARGA_RED` en tramos **valle 2.0TD**; `VENTA_RED` y la carga fuera de valle se simulan y auditan. El modo `completo` desbloquea todo el plan. (La venta real vía TOU Selling del Deye queda para una fase posterior; en F4, `VENTA_RED` solo fija el límite de descarga.)
- **Servicios** `force_mode(mode, until)` y `release_manual`: modo manual (`AUTOCONSUMO`/`CARGA_RED`/`VENTA_RED`/`MANTENER`) que respeta **todas** las salvaguardas y expira solo en `until`; en memoria (un reinicio de HA vuelve al plan).
- **Auditoría JSONL** append-only: `config/optimizador_bateria_omie/auditoria_escrituras.jsonl` — cada escritura real o simulada con `{ts, tramo, accion, registros, ejecutada, verificado, latencia, motivo, guard}`. Ubicada fuera de `custom_components/` para sobrevivir a actualizaciones de HACS.
- **Deuda #5 resuelta**: peajes, cargos, impuesto eléctrico e IVA de la tarifa 2.0TD pasan a `entry.data`, editables por *Reconfigurar* (defaults = valores vigentes). La entrada de producción no se rompe.

### Fase F4.5 (0.10.1) — Auditoría de ahorro efectivo ✅

Anticipa parte de la auditoría prevista para F5: mide el **ahorro real** acumulado del día y lo compara con el teórico del plan.

- **`auditoria.py` (`AuditoriaAhorro`)**: en cada ciclo de telemetría, cierra el tramo de 15 min anterior y acumula: descarga/carga de batería, importación/exportación de red, `coste_sin_bateria` (todo el consumo al precio efectivo del tramo) y `coste_real` (energía neta de red al mismo precio). El tramo y el día se indexan **en hora local** (igual que `precios_hoy`). Persistencia en `hass.storage` (`optimizador_bateria_omie/auditoria.json`), con recuperación tras reinicio y reset diario.
- **`sensor.*_ahorro_efectivo_hoy`** (€, `state_class: total`): ahorro real del día. Atributos: `coste_sin_bateria_eur`, `coste_real_eur`, `descarga_bateria_kwh`, `carga_bateria_kwh`, `red_importada_kwh`, `red_exportada_kwh`.
- **Simplificaciones conocidas** (mejorables en F5): la exportación se netea contra la importación al mismo precio (sin precio de excedentes específico); la potencia del tramo se aproxima con la lectura de telemetría en el cierre del tramo; no descuenta pérdidas de descarga.
- **Corrección de `switch.festivo_hoy`**: el estado ahora **persiste** en `hass.storage` (sobrevive reinicios del mismo día) y se **apaga automáticamente al cambiar de día**. Además, con el interruptor activado el precio efectivo y el plan tratan el día completo como **valle** (via `ModeloTarifa.obtener_precio_efectivo(..., festivo_manual=True)`); la tarifa facturada real solo cambia en festivos de calendario, no por vacaciones propias.

### Fases futuras 🚧

- **F5**: Predicción ML + Optimización con programación lineal (la auditoría de ahorro básica ya está entregada en 0.10.1)
- **F6**: Dashboard Lovelace + Notificaciones + Informes

## Requisitos

- **Home Assistant**: 2026.10.1 o superior.
- **Python**: 3.14 o superior.
- **Integración oficial OMIE** instalada y con la plantilla `precios_omie.yaml` activa (ver [Dependencias de F1](#dependencias-de-f1-la-plantilla-de-precios)).
- **Telemetría (F2) y control (F4)**: integración oficial **Solarman** instalada y funcionando con sus **servicios de escritura y lectura de registros** (v25.08.16 o superior). Para el modo alternativo `solarman_modbus` (opcional, innecesario para F4): `pysolarmanv5` (ya en `manifest.json`).
- **F3 (opcional pero recomendado)**: integración **Solcast PV Forecast** para la previsión de FV; sin ella se usa el perfil estadístico.
- **Opcional**: acceso a internet para el fallback de la web de OMIE.

## Dependencias de F1: la plantilla de precios

F1 **no llama directamente a la API de OMIE**. En su lugar consume tres sensores que crea una plantilla `template:` en tu `configuration.yaml` (o en un paquete). La integración oficial OMIE se encarga de la descarga, y nuestra plantilla la expone como atributos `precios` con 96 tramos de 15 min por día:

```yaml
# precios_omie.yaml
- trigger:
    - platform: homeassistant
      event: start
    - platform: time
      at: "13:35:00"          # publicación D+1 de OMIE
    - platform: time
      at: "00:05:00"          # rotación de día
  action:
    - action: omie.get_prices_for_date
      data:
        date: "{{ (now() - timedelta(days=1)).date() }}"
        countries: [es, pt]
      response_variable: precios_ayer
      continue_on_error: true
    - action: omie.get_prices_for_date
      data:
        date: "{{ now().date() }}"
        countries: [es, pt]
      response_variable: precios_hoy
      continue_on_error: true
    - if:
        - condition: template
          value_template: >
            {{ now().hour > 13 or (now().hour == 13 and now().minute >= 35) }}
      then:
        - action: omie.get_prices_for_date
          data:
            date: "{{ (now() + timedelta(days=1)).date() }}"
            countries: [es, pt]
          response_variable: precios_manana
          continue_on_error: true
      else:
        - variables:
            precios_manana: {}
  sensor:
    - name: "OMIE precios de ES ayer"
      unique_id: omie_precios_es_ayer
      state: "{{ now().isoformat() }}"
      attributes:
        precios: >
          {% set datos = (precios_ayer | default({})).es | default([]) %}
          {% set ns = namespace(out=[]) %}
          {% for d in datos %}
            {% set ns.out = ns.out + [{'start': d.start, 'price': d.price | float | round(4)}] %}
          {% endfor %}
          {{ ns.out }}
    # ... análogos para _hoy y _manana
```

## Instalación

### Método 1: HACS (recomendado)

1. Añadir este repositorio como repositorio personalizado en HACS:
   - Ve a **HACS > Integraciones > Tres puntos (⋮) > Repositorios personalizados**
   - Añade URL: `https://github.com/acarrilloa/optimizador-bateria-omie`
   - Selecciona categoría: **Integración**
   - Haz clic en **Añadir**

2. Reinicia Home Assistant

3. Instala el componente:
   - Ve a **HACS > Integraciones**
   - Busca **"Optimizador Batería OMIE"**
   - Haz clic en **INSTALAR**

4. Configura la integración:
   - Ve a **Configuración > Dispositivos y servicios**
   - Haz clic en **AÑADIR INTEGRACIÓN**
   - Busca **"Optimizador Batería OMIE"**
   - Selecciona la fuente de precios (recomendado: **Integración OMIE**) y la fuente de telemetría (por defecto: **entidades de la integración Solarman**)

### Método 2: Manual

1. Copia la carpeta `custom_components/optimizador_bateria_omie/` a tu directorio `custom_components/` de Home Assistant

2. Reinicia Home Assistant

3. Añade la integración:
   - Ve a **Configuración > Dispositivos y servicios**
   - Haz clic en **AÑADIR INTEGRACIÓN**
   - Busca **"Optimizador Batería OMIE"**

## Configuración

Todos los parámetros son editables en **Configuración > Dispositivos y servicios > Optimizador Batería OMIE > *Reconfigurar***, sin borrar y recrear la entrada.

### Parámetros de precios (F1)

| Parámetro | Tipo | Valor por defecto | Descripción |
|---|---|---|---|
| `fuente_prioritaria` | selección | `omie` | Fuente de precios: `omie` (el fallback de la web oficial de OMIE es siempre automático) |
| `usar_cache` | booleano | `true` | Usar la caché en disco |
| `dias_cache` | número | `7` | Días de retención de la caché (1–30) |

### Parámetros de telemetría (F2) ✅

| Parámetro | Tipo | Valor por defecto | Descripción |
|---|---|---|---|
| `telemetry_source` | selección | **`ha_entities`** | `ha_entities` (integración Solarman) o `solarman_modbus` (requiere gateway RS485) |
| `entity_id_soc_bateria` … (×6) | texto | vacío | Con `ha_entities`: entidades de SOC, potencia batería, consumo, FV, temperatura inversor y temperatura batería |
| `intervalo_telemetria_s` | número | `30` | Intervalo de polling del inversor (10–300 s) |
| `ip` / `puerto` / `serie` | texto/número | datalogger | Solo relevantes con `solarman_modbus` |

> **Sin `esios` ni token ESIOS**: la fuente de fallback de precios es únicamente la web oficial de OMIE. ESIOS queda pendiente para fases posteriores.

### Parámetros del plan (F3) ✅

Editables por *Reconfigurar* **y en caliente** con las entidades `number` de F4, con recálculo del plan en ≤ 60 s tras el cambio:

| Parámetro | Default | Descripción |
|---|---|---|
| `ventana_historico_dias` | 28 | Días de recorder para el perfil de consumo (7–90) |
| `permitir_carga_red` / `permitir_venta_red` | true / true | Arbitraje de compra / venta |
| `precio_max_compra` | 0,5 €/kWh | **Tope de seguridad** absoluto de compra: si el precio lo supera, no se carga aunque el ciclo compense (el negocio lo decide la rentabilidad, ver planificador) |
| `precio_min_venta` | 0,0 €/kWh | **Piso de seguridad** de vertido: evita exportar a precios ≈ 0 o negativos (la venta desde batería la guarda `margen_arbitraje` + round-trip) |
| `margen_arbitraje` | 0.015 €/kWh | Margen mínimo del ciclo |
| `soc_min` / `soc_max` | 15 / 95 % | Límites operativos de SOC |
| `potencia_max_carga` / `potencia_max_descarga` | 5000 / 5000 W | Límites de potencia |
| `potencia_contratada` | 5750 W | Restricción dura de importación (alimenta el guard de F4) |
| `margen_potencia` | 300 W | Margen bajo la potencia contratada |
| `eficiencia_round_trip` | 88 % | Pérdida del ciclo (70–95) |
| `desviacion_soc_replan_pct` | 10 % | Umbral de replanificación |
| `festivos_locales` | `[]` | Festivos propios, además de los nacionales |
| `entity_id_prevision_fv_hoy` / `_manana` | sensores de Solcast | Previsión FV preferente (fallback: perfil estadístico) |

### Parámetros del control (F4) ✅

| Parámetro | Default | Rango | Descripción |
|---|---|---|---|
| `modo_activacion` | `gradual` | gradual \| completo | En gradual solo se ejecutan de verdad las `CARGA_RED` en valle 2.0TD; el resto se simula y audita |
| `max_escrituras_hora` | 12 | 1–48 | Límite de escrituras reales por hora (rate limiter) |
| `latencia_verify_s` | 3 | 1–15 | Espera entre escritura y relectura de verificación |
| `verify_tolerancia` | 0 | 0–5 | Tolerancia del write-verify (0 = coincidencia exacta) |
| `voltaje_bateria_nominal` | 51.2 | 40–60 V | Tensión nominal para la conversión W→A de los registros de corriente |
| `soc_recuperacion` | 20 | 10–100 % | Registro 218 (punto de recuperación; siempre > `soc_minimo`) |
| `entity_id_potencia_red` | vacío | entity_id | Entidad de importación para el guard (registro Deye 169, >0 = importación); vacío = cálculo derivado desde telemetría |
| `entity_ids_fallo` | vacío | lista separada por comas | Entidades de fallo del inversor (registros Deye 103–106); con alguna ≠ 0 no se escribe; vacío = bloqueo inactivo con aviso |
| `device_id_solarman` | vacío | device_id | Dispositivo Solarman destino de los servicios; vacío = resolución automática del primer device de la integración (error explícito si no se resuelve) |

### Parámetros de tarifa 2.0TD (deuda #5, F4) ✅

Los peajes, cargos e impuestos dejan de estar en el código: se guardan en `entry.data` con estos defaults y son editables por *Reconfigurar*:

| Parámetro | Default (€/kWh) |
|---|---|
| `peaje_acceso_punta` / `_llano` / `_valle` | 0.033261 / 0.016409 / 0.000077 |
| `cargo_sistema_punta` / `_llano` / `_valle` | 0.064292 / 0.012858 / 0.003215 |
| `impuesto_electrico_pct` | 0.051127 |
| `iva_pct` | 0.21 |
| `aplicar_impuestos_precio` | true |

## Festivos nacionales

El componente incluye automáticamente los festivos oficiales para precios de electricidad en España:
- 01-01 (Año Nuevo)
- 06-01 (Día de Reyes)
- 01-05 (Fiesta del Trabajo)
- 15-08 (Asunción de la Virgen)
- 12-10 (Fiesta Nacional de España)
- 01-11 (Todos los Santos)
- 06-12 (Día de la Constitución)
- 08-12 (Inmaculada Concepción)
- 25-12 (Navidad)

## Sensores y entidades

Los `entity_id` de abajo son los de la instalación de referencia (`sensor.inversor_optimizador_bateria_omie_*`); en otras instalaciones varían según el nombre de la entrada.

### Precios (F1) ✅

| Sensor | Estado | Atributos |
|---|---|---|
| `precio_actual` | Precio del tramo actual en €/kWh (4 dec.) o `unknown` | `fuente` (`omie`, `omie_web`, `desconocida`) |
| `precio_efectivo` | `(spot + peaje + cargo) × (1+IE) × (1+IVA)` | — |
| `precio_clasificacion` | `valle` / `llano` / `punta` por **período 2.0TD** (no percentiles) | — |
| `precio_estadisticas` | **Mediana** del día en €/kWh | `min`, `max`, `mediana`, `fuente`, `ts_actualizacion` |
| `precios_efectivos_hoy` | Mediana del día + **curva completa** de precios efectivos | `precios_ordenados` |

### Telemetría (F2) ✅

Ocho sensores: `soc_bateria`, `potencia_bateria` (**+descarga/−carga**), `corriente_bateria` (`unavailable` en `ha_entities`, por diseño), `consumo_casa`, `produccion_fv`, `temperatura_inversor`, `temperatura_bateria` y el diagnóstico `familia_validada` (`ninguna` en `ha_entities`, por diseño). Todos con `device_class` y `state_class` correctos (`battery`, `power`, `current`, `temperature`, `measurement`).

### Plan (F3) ✅

`prediccion_consumo` (W + curva p10/p50/p90 desde el tramo actual + fuente FV), `plan` (acción del tramo + plan pendiente desde el actual + ahorro estimado), `ahorro_estimado_hoy` (€, teórico) y `switch.festivo_hoy` (persistente durante el día; auto-off al cambiar de día).

### Control (F4) ✅

- `switch.*_control_activo` — habilitador del control real, **default OFF**. Con OFF todo se simula (dry-run) y se audita; con ON se escribe bajo salvaguardas. 3 fallos de verificación → OFF automático + notificación persistente (reactivación manual).
- `sensor.*_estado_control` (diagnóstico) — `inactivo` (switch OFF) / `dry_run` (ON pero la acción actual se simula por gradual) / `activo` (escribiendo de verdad) / `fallback_bloqueado` (requiere intervención). Atributos: motivo, fallos consecutivos, escrituras de la última hora, último verify, modo manual.
- **Ocho `number` calientes** — escriben en `entry.data` y disparan recálculo del plan en ≤ 60 s: `precio_max_compra`, `precio_min_venta`, `margen_arbitraje`, `soc_min`, `soc_max`, `potencia_max_carga`, `potencia_max_descarga`, `eficiencia_round_trip`.

### Auditoría de ahorro (0.10.1) ✅

- `sensor.*_ahorro_efectivo_hoy` (€) — ahorro real del día: `coste_sin_bateria − coste_real`, tramo a tramo, con el precio efectivo 2.0TD de cada tramo. Atributos (`desglose`): `coste_sin_bateria_eur`, `coste_real_eur`, `descarga_bateria_kwh`, `carga_bateria_kwh`, `red_importada_kwh`, `red_exportada_kwh`. Se actualiza con cada ciclo de telemetría, persiste en `hass.storage` y se reinicia cada día.

## Servicios

### `probe_registers` (F2) ✅

Valida el mapa de registros del inversor contra la pantalla/APP. **Funcional pero inactivo en el modo `ha_entities`** (no toca Modbus); operativo cuando se active `solarman_modbus` con el gateway RS485:

```yaml
optimizador_bateria_omie.probe_registers:
  expected: 73        # SOC % que muestra la pantalla o la APP Solarman (obligatorio)
  # register: 184     # opcional: dirección a sondear (default: probe.addr de la familia)
  # reset: true       # opcional: borrar la validación
```

- Compara con **tolerancia ±2 %** y verifica plausibilidad de la familia.
- Respuesta del servicio con el detalle por familia (visible en Herramientas de desarrollador).

### `force_mode` / `release_manual` (F4) ✅

Fija un modo manual que respeta **todas** las salvaguardas y expira automáticamente en `until` (un reinicio de HA también lo libera):

```yaml
# Forzar carga desde red hasta las 06:00
optimizador_bateria_omie.force_mode:
  mode: CARGA_RED          # AUTOCONSUMO | CARGA_RED | VENTA_RED | MANTENER
  until: "2026-11-02 06:00:00"   # obligatorio, debe ser futuro

# Volver al plan inmediatamente
optimizador_bateria_omie.release_manual: {}
```

El modo manual nunca esquiva el guard de potencia, el bloqueo por fallo, el rate limiter ni el dry-run.

## Auditoría de escrituras (F4) ✅

Cada escritura (real o simulada) queda registrada, una línea JSON por evento, en `config/optimizador_bateria_omie/auditoria_escrituras.jsonl`:

```json
{"ts": "2026-11-02T00:15:04", "tramo": "2026-11-02T00:15", "accion": "CARGA_RED", "registros": {"232": 1, "230": 37}, "ejecutada": true, "verificado": "ok", "latencia": 3.1, "motivo": "cambio_tramo", "guard": "ok"}
```

Campos: `ejecutada` (`false` = simulada por dry-run o gradual), `verificado` (`ok` / `parcial`), `guard` (`ok` / `reducido` / `corte`). Sin rotación en esta fase.

## Tarifa 2.0TD (España)

Valores por defecto (editables por *Reconfigurar* desde F4):

### Períodos de energía (€/kWh)
| Período | Peaje | Cargo | Total |
|---|---|---|---|
| Punta | 0.033261 | 0.064292 | 0.097553 |
| Llano | 0.016409 | 0.012858 | 0.029267 |
| Valle | 0.000077 | 0.003215 | 0.003292 |

### Horarios (días laborables)
| Período | Horario |
|---|---|
| Punta | 10:00-14:00, 18:00-22:00 |
| Llano | 08:00-10:00, 14:00-18:00, 22:00-24:00 |
| Valle | 00:00-08:00 |

**Fines de semana y festivos**: Valle todo el día.

## Ejemplo de automatizaciones

### Notificación cuando el precio efectivo está bajo

```yaml
automation:
  - alias: "Aviso precio bajo"
    trigger:
      - platform: template
        value_template: >
          {{ states('sensor.inversor_optimizador_bateria_omie_precio_efectivo') | float(0) < 0.10 }}
    action:
      - action: notify.notify
        data:
          message: "Precio bajo: {{ states('sensor.inversor_optimizador_bateria_omie_precio_efectivo') }} €/kWh"
          title: "OMIE - Precio bajo"
```

### Ventilador del inversor según su temperatura (basada en telemetría F2)

```yaml
automation:
  - alias: "Ventilador según temperatura del inversor"
    trigger:
      - platform: numeric_state
        entity_id: sensor.inversor_optimizador_bateria_omie_temperatura_inversor
        above: 50
    action:
      - action: switch.turn_on
        target:
          entity_id: switch.ventilador_inversor
```

### Aviso si el control se bloquea por fallback (F4)

```yaml
automation:
  - alias: "Aviso fallback del control"
    trigger:
      - platform: state
        entity_id: sensor.inversor_optimizador_bateria_omie_estado_control
        to: "fallback_bloqueado"
    action:
      - action: notify.notify
        data:
          title: "OMIE - Control bloqueado"
          message: "3 verificaciones fallidas: revisa la auditoría y la APP Solarman antes de reactivar el control."
```

> **Activación del control real (F4)**: revisa el plan (`sensor.*_plan`) en `dry_run` durante ≥ 1 semana, confirma las entidades de potencia de red y fallo en *Reconfigurar*, y enciende `switch.*_control_activo` en valle nocturno observando la APP Solarman.

## Solución de problemas

### La integración no aparece
- Verifica que la carpeta está en `custom_components/optimizador_bateria_omie/`
- Reinicia Home Assistant
- Verifica que el archivo `manifest.json` está correcto

### Los sensores de precios muestran "unavailable"
- Verifica que la integración OMIE está instalada y la plantilla `precios_omie.yaml` activa (o que hay conexión para el fallback de la web de OMIE)
- Consulta los logs de Home Assistant: si todas las fuentes fallan, los sensores quedan `unavailable` (no se inventan valores)

### Los precios no coinciden con OMIE
- El componente usa la integración OMIE existente como fuente primaria
- La caché se valida a 92/96/100 tramos; un JSON incompleto se descarta y se vuelve a pedir a la fuente
- Verifica que la integración OMIE está configurada correctamente

### Telemetría: casos esperados que no son errores
- **`corriente_bateria` en `unavailable`**: normal en modo `ha_entities` (Solarman no expone esa magnitud de forma fiable); estará disponible si se activa `solarman_modbus`.
- **`familia_validada` = `ninguna`**: normal en `ha_entities` (la validación de familia solo aplica al modo Modbus).
- **Sensores en `unavailable`**: revisa los 6 `entity_id` configurados (*Reconfigurar*) y que la integración Solarman esté funcionando.

### `probe_registers` no hace nada
- En modo `ha_entities` es lo esperado: el servicio es funcional pero inactivo (no hay conexión Modbus). Responderá con el detalle sin validar nada.

### Control (F4): casos esperados que no son errores
- **`estado_control` = `inactivo`**: `control_activo` está OFF; todo se simula y se audita con `ejecutada: false`.
- **`estado_control` = `dry_run` con el switch ON**: en `modo_activacion: gradual`, las acciones fuera de valle y `VENTA_RED` se simulan por diseño.
- **No escribe aunque el switch esté ON**: comprueba telemetría reciente (watchdog), `entity_ids_fallo` sin fallos, rate limiter no agotado y que la integración Solarman expone los servicios de escritura.
- **`estado_control` = `fallback_bloqueado`**: 3 verificaciones fallidas. Revisa `auditoria_escrituras.jsonl`, contrasta la APP Solarman y vuelve a encender el switch manualmente.
- **La carga de red no arranca tras escribir**: el firmware puede condicionar la carga según los puntos de arranque por tensión/SOC (registros 228/229) o el TOU del LCD; contraste el valor en la APP Solarman antes de asumir un fallo de escritura.

## Desarrollo

### Estructura del proyecto

```
optimizador-bateria-omie/
├── custom_components/
│   └── optimizador_bateria_omie/
│       ├── __init__.py              # Entry point (jobs de caché, servicios, setup F3/F4)
│       ├── manifest.json            # Metadatos HACS (pysolarmanv5, deps: omie + recorder)
│       ├── const.py                 # Constantes
│       ├── cliente_omie.py          # Cliente OMIE + caché + limpieza
│       ├── tarifa.py                # Modelo de tarifa 2.0TD (defaults; configuración desde entry.data)
│       ├── coordinador.py           # CoordinadorDatosOmie (15 min)
│       ├── entidades.py             # Sensores de precio (5)
│       ├── sensor.py               # Setup de plataforma
│       ├── config_flow.py           # Config flow + reconfigure
│       ├── registers.yaml           # F2: mapa de registros + F4: sección control
│       ├── mapa_registros.py        # MapaRegistros (offset, signed, validación)
│       ├── solarman_client.py       # ClienteSolarman (solarman_modbus, inactivo)
│       ├── telemetria.py            # TelemetriaInversor + GestorValidacion + CoordinadorTelemetria
│       ├── entidades_solarman.py    # 8 sensores de telemetría
│       ├── services.py              # probe_registers + force_mode / release_manual
│       ├── services.yaml            # Descripción de servicios
│       ├── historico.py             # F3: FuenteHistoricoRecorder
│       ├── predictor_consumo.py     # F3: perfil estadístico + Solcast
│       ├── planificador.py          # F3: PlanificadorGreedy (puro)
│       ├── coordinador_plan.py      # F3: CoordinadorPlan
│       ├── entidades_plan.py        # F3: predicción, plan, ahorro estimado, festivo (persistente) + ahorro efectivo (0.10.1)
│       ├── auditoria.py             # 0.10.1: AuditoriaAhorro (ahorro real tramo a tramo, .storage)
│       ├── executor.py              # F4: EjecutorControl + EscritorSolarman (servicios Solarman)
│       ├── entidades_control.py     # F4: switch control_activo, 8 numbers, sensor estado_control
│       ├── number.py                # F4: plataforma number
│       └── tests/                   # Suites F1 (62) + F2 + F3 + F4 (executor, force_mode, entidades_control, tarifa_entry_data) + auditoría (0.10.1)
├── hacs.json
├── README.md
└── docs/
    ├── INSTALACION.md
    ├── PLAN_F2.md                   # cerrado
    ├── PLAN_F3.md                   # cerrado
    └── PLAN_F4.md                   # cerrado
```

### Ejecutar tests

```bash
# Instalar dependencias
pip install -r requirements_dev.txt

# Ejecutar tests
pytest -v

# Verificar estilo
ruff check custom_components/optimizador_bateria_omie/
```

Los tests de F4 mockean los servicios `solarman.write_*` / `solarman.read_holding_registers` — la suite nunca toca hardware real, y ninguna escritura real se ejecuta desde el desarrollo.

## Licencia

MIT License - Copyright (c) 2026

## Agradecimientos

- [OMIE](https://www.omie.es) - Mercado Ibérico de la Electricidad
- [Red Eléctrica de España](https://www.ree.es) - Datos del sistema eléctrico
- [pysolarmanv5](https://pysolarmanv5.readthedocs.io) - Librería de referencia para dataloggers Solarman en local
- [Solcast](https://solcast.com) - Previsión de producción fotovoltaica
- [Home Assistant](https://www.home-assistant.io) - Plataforma de automatización