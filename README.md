# Optimizador Batería OMIE

![Versión](https://img.shields.io/badge/versión-0.9.0-blue)
![F2](https://img.shields.io/badge/F2-telemetría-completada-brightgreen)
![F3](https://img.shields.io/badge/F3-plan%20dry--run-en%20desarrollo-orange)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.10.1-41BDF5)
![Python](https://img.shields.io/badge/Python-3.14-3776AB)

**Custom component para Home Assistant** que optimiza la carga/descarga de baterías usando precios del mercado eléctrico español con resolución de 15 minutos (OMIE MTU15) y la tarifa eléctrica 2.0TD.

> **Estado del proyecto**: **F1 (Precios) ✅ en producción** (0.7.x). **F2 (Telemetría) ✅ completada** (v0.8.10) — vía operativa `ha_entities` con la integración oficial Solarman. **F3 (Predicción + Planificador greedy en `dry_run`) 🚧 en desarrollo** (plan `docs/PLAN_F3.md` v1.3 confirmado; cierre previsto 0.9.0). Diseño completo en `SPEC.md` (v0.9.0).

## Características

### Fase F1 (Implementada) ✅

- **Obtención de precios OMIE a 15 min** vía los sensores de plantilla `sensor.omie_precios_de_es_{ayer,hoy,manana}` (creados con la integración oficial OMIE `omie.get_prices_for_date`).
- **Fallback REData** (`api.preciodelaluz.es`) si los sensores de plantilla no tienen datos, con timeout de 10 s y 2 reintentos con backoff.
- **Caché en disco** por día (`cache/YYYY-MM-DD.json`), validada a **92, 96 o 100 tramos** (días con cambio horario). Sobrevive a reinicios; limpieza automática según `dias_cache` (arranque + job diario 04:30).
- **Precio efectivo**: spot + peajes + cargos + impuestos 2.0TD (según `tarifa.py`).
- **Clasificación de tramos por período 2.0TD** (`valle` / `llano` / `punta`) según hora local, con fines de semana y festivos nacionales = valle todo el día.
- **Cinco sensores** (los `entity_id` de la instalación de referencia):
  - `sensor.inversor_optimizador_bateria_omie_precio_actual` — precio del tramo actual en €/kWh; atributo `fuente` (`omie`, `redata`, `desconocida`).
  - `sensor.inversor_optimizador_bateria_omie_precio_efectivo` — precio con peajes, cargos e impuestos.
  - `sensor.inversor_optimizador_bateria_omie_precio_clasificacion` — `valle` / `llano` / `punta` por período 2.0TD.
  - `sensor.inversor_optimizador_bateria_omie_precio_estadisticas` — estado = **mediana** del día; atributos `min`, `max`, `mediana`, `fuente`, `ts_actualizacion`.
  - `sensor.inversor_optimizador_bateria_omie_precios_efectivos_hoy` — **curva completa de precios efectivos del día** (entrada del planificador de F3).

> **Nota sobre el prefijo `inversor_`**: los `entity_id` dependen del nombre del dispositivo/entrada de configuración. Si renombras el dispositivo en HA, los IDs cambiarán. Los ejemplos usan el nombre de la instalación de referencia. Los `unique_id` son estables (`optimizador_bateria_omie_*`).

### Fase F2 (Completada) ✅ — Telemetría del inversor

Lectura del estado del inversor Deye SUN-6K-SG05LP1-EU-AM2-P **sin escribir en él en ningún caso** (el control es F4).

- **Vía operativa: `ha_entities`** (por defecto). La telemetría se lee de las entidades que publica la **integración oficial Solarman** ya instalada, vía `hass.states.get()` — sin conexión directa al datalogger y sin conflictos con la integración.
- **Vía preparada: `solarman_modbus`** (implementada y probada, **inactiva**). El datalogger serie `3178171873` **no expone Modbus TCP local** (puertos 8899/502 cerrados al protocolo); se activará en el futuro con un gateway RS485→TCP externo (Elfin EW11 o similar).
- **`registers.yaml`** con la familia estándar Deye confirmada contra los manuales oficiales de Modbus (SOC 184, potencia batería 190, corriente 191, temperaturas 90/182 con offset 1000, consumo 178, FV 186). Las direcciones son configuración, nunca constantes de código, y admiten override del usuario.
- **Servicio `probe_registers`**: funcional (tolerancia ±2 %, plausibilidad, `reset`) pero **inactivo en modo `ha_entities`** — no toca Modbus. Listo para cuando se active `solarman_modbus`.
- **Sin escrituras**: `ClienteSolarman` no incluye método de escritura (reservado a F4).
- **Ocho sensores**:
  - `sensor.*_soc_bateria` (%) — `device_class: battery`
  - `sensor.*_potencia_bateria` (W, **+descarga/−carga**, convención Deye confirmada empíricamente)
  - `sensor.*_corriente_bateria` (A) — **siempre `unavailable` en `ha_entities`** (Solarman no la expone de forma fiable); disponible cuando se active `solarman_modbus`
  - `sensor.*_consumo_casa` (W)
  - `sensor.*_produccion_fv` (W)
  - `sensor.*_temperatura_inversor` (°C) — usada por la automatización del ventilador externo
  - `sensor.*_temperatura_bateria` (°C)
  - `sensor.*_familia_validada` (diagnóstico) — muestra `ninguna` en `ha_entities` (la validación de familia solo aplica a `solarman_modbus`)
- **Deuda técnica de F1 resuelta**: limpieza de caché según `dias_cache`, backoff/timeout en REData, log de actualización con `tramos mañana`.
- Refresco cada 30 s (configurable 10–300 s) vía `CoordinadorTelemetria`, independiente del coordinador de precios de 15 min.

### Fase F3 (En desarrollo) 🚧 — Predicción + Planificador greedy (`dry_run`)

Calcula un **plan por tramos de 15 min** priorizando autoconsumo y aplicando arbitraje solo si compensa. **F3 no escribe en el inversor**: el plan se publica como sensor y se revisa manualmente (≥1 semana) antes del control real de F4. Plan confirmado en `docs/PLAN_F3.md` (v1.3).

Lo que está construyendo esta fase:

- **`PredictorConsumo`**: perfil estadístico (mediana por tramo × tipo de día: laborable / fin-de-semana-o-festivo) con bootstrap desde el **HA recorder** (28 días configurables), banda p10/p50/p90 y persistencia en disco (recalculado diariamente a las 04:35). InfluxDB queda como alternativa documentada.
- **Previsión de FV con Solcast PV Forecast como fuente preferente** (`sensor.solcast_pv_forecast_forecast_today` / `_forecast_tomorrow`, atributo `detailedForecast`, mapeo 30→15 min), con el perfil estadístico como fallback.
- **`PlanificadorGreedy`** (función pura, determinista): autoconsumo siempre; `CARGA_RED` en tramos baratos (bajo umbral y bajo la mediana del día; precio ≤ 0 → carga máxima); `VENTA_RED` solo si el ciclo compensa la eficiencia round-trip + margen; restricción dura de **5,75 kW** de importación.
- **Replanificación**: precios nuevos, cambio de tramo (15 min), desviación SOC > 10 %, o servicio `regenerate_plan`.
- **Cuatro entidades nuevas**:
  - `sensor.*_prediccion_consumo` (W) — p50 del tramo actual + curva p10/p50/p90
  - `sensor.*_plan` — acción del tramo (`AUTOCONSUMO`/`CARGA_RED`/`VENTA_RED`/`MANTENER`) + plan completo y ahorro estimado como atributos
  - `sensor.*_ahorro_estimado_hoy` (€) — estimación **teórica** del día (el ahorro real auditable llega en F5)
  - `switch.*_festivo_hoy` — marca manual de festivo/vacaciones
- **Parámetros del plan** editables por *Reconfigurar* (umbrales de compra/venta, margen de arbitraje, SOC min/max, potencias, η round-trip, festivos locales…), con recálculo del plan en ≤ 60 s.

### Fases futuras 🚧

- **F4**: Control real del inversor con salvaguardas (write-verify, límite de escrituras, watchdog). **Requiere gateway RS485→TCP** para activar `solarman_modbus`
- **F5**: Predicción ML + Optimización con programación lineal + auditoría de ahorro real
- **F6**: Dashboard Lovelace + Notificaciones + Informes

## Requisitos

- **Home Assistant**: 2026.10.1 o superior.
- **Python**: 3.14 o superior.
- **Integración oficial OMIE** instalada y con la plantilla `precios_omie.yaml` activa (ver [Dependencias de F1](#dependencias-de-f1-la-plantilla-de-precios)).
- **Telemetría (F2)**: integración oficial **Solarman** instalada y funcionando (modo `ha_entities`, por defecto). Para el modo alternativo `solarman_modbus` (requiere gateway RS485): `pysolarmanv5` (ya en `manifest.json`).
- **F3 (opcional pero recomendado)**: integración **Solcast PV Forecast** para la previsión de FV; sin ella se usa el perfil estadístico.
- **Opcional**: acceso a internet para el fallback REData.

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
| `fuente_prioritaria` | selección | `omie` | Fuente de precios: `omie`, `redata` |
| `usar_cache` | booleano | `true` | Usar la caché en disco |
| `dias_cache` | número | `7` | Días de retención de la caché (1–30) |

### Parámetros de telemetría (F2) ✅

| Parámetro | Tipo | Valor por defecto | Descripción |
|---|---|---|---|
| `telemetry_source` | selección | **`ha_entities`** | `ha_entities` (integración Solarman) o `solarman_modbus` (requiere gateway RS485) |
| `entity_id_soc_bateria` … (×6) | texto | vacío | Con `ha_entities`: entidades de SOC, potencia batería, consumo, FV, temperatura inversor y temperatura batería |
| `intervalo_telemetria_s` | número | `30` | Intervalo de polling del inversor (10–300 s) |
| `ip` / `puerto` / `serie` | texto/número | datalogger | Solo relevantes con `solarman_modbus` |

> **Sin `esios` ni token ESIOS**: la fuente de fallback de precios es únicamente REData. ESIOS queda pendiente para fases posteriores.

### Parámetros del plan (F3, en desarrollo) 🚧

Disponibles al cerrarse la fase (vía *Reconfigurar*), con recálculo del plan en ≤ 60 s tras el cambio:

| Parámetro | Default | Descripción |
|---|---|---|
| `ventana_historico_dias` | 28 | Días de recorder para el perfil de consumo (7–90) |
| `permitir_carga_red` / `permitir_venta_red` | true / true | Arbitraje de compra / venta |
| `precio_max_compra` | 0.09 €/kWh | Umbral de compra |
| `precio_min_venta` | 0.18 €/kWh | Umbral de venta |
| `margen_arbitraje` | 0.015 €/kWh | Margen mínimo del ciclo |
| `soc_min` / `soc_max` | 15 / 95 % | Límites operativos de SOC |
| `potencia_max_carga` / `potencia_max_descarga` | 5000 / 5000 W | Límites de potencia |
| `potencia_contratada` | 5750 W | Restricción dura de importación |
| `margen_potencia` | 300 W | Margen bajo la potencia contratada |
| `eficiencia_round_trip` | 88 % | Pérdida del ciclo (70–95) |
| `desviacion_soc_replan_pct` | 10 % | Umbral de replanificación |
| `festivos_locales` | `[]` | Festivos propios, además de los nacionales |
| `entity_id_prevision_fv_hoy` / `_manana` | sensores de Solcast | Previsión FV preferente (fallback: perfil estadístico) |

### Festivos nacionales

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

## Sensores

Los `entity_id` de abajo son los de la instalación de referencia (`sensor.inversor_optimizador_bateria_omie_*`); en otras instalaciones varían según el nombre de la entrada.

### Precios (F1) ✅

| Sensor | Estado | Atributos |
|---|---|---|
| `precio_actual` | Precio del tramo actual en €/kWh (4 dec.) o `unknown` | `fuente` (`omie`, `redata`, `desconocida`) |
| `precio_efectivo` | `(spot + peaje + cargo) × (1+IE) × (1+IVA)` | — |
| `precio_clasificacion` | `valle` / `llano` / `punta` por **período 2.0TD** (no percentiles) | — |
| `precio_estadisticas` | **Mediana** del día en €/kWh | `min`, `max`, `mediana`, `fuente`, `ts_actualizacion` |
| `precios_efectivos_hoy` | Mediana del día + **curva completa** de precios efectivos | `precios_ordenados` |

### Telemetría (F2) ✅

Ocho sensores: `soc_bateria`, `potencia_bateria` (**+descarga/−carga**), `corriente_bateria` (`unavailable` en `ha_entities`, por diseño), `consumo_casa`, `produccion_fv`, `temperatura_inversor`, `temperatura_bateria` y el diagnóstico `familia_validada` (`ninguna` en `ha_entities`, por diseño). Todos con `device_class` y `state_class` correctos (`battery`, `power`, `current`, `temperature`, `measurement`).

### Plan (F3, en desarrollo) 🚧

`prediccion_consumo` (W + curva p10/p50/p90 + fuente FV), `plan` (acción del tramo + plan completo + ahorro estimado), `ahorro_estimado_hoy` (€, teórico) y `switch.festivo_hoy`.

## Servicio `probe_registers` (F2) ✅

Valida el mapa de registros del inversor contra la pantalla/APP. **Funcional pero inactivo en el modo `ha_entities`** (no toca Modbus); operativo cuando se active `solarman_modbus` con el gateway RS485:

```yaml
optimizador_bateria_omie.probe_registers:
  expected: 73        # SOC % que muestra la pantalla o la APP Solarman (obligatorio)
  # register: 184     # opcional: dirección a sondear (default: probe.addr de la familia)
  # reset: true       # opcional: borrar la validación
```

- Compara con **tolerancia ±2 %** y verifica plausibilidad de la familia.
- Respuesta del servicio con el detalle por familia (visible en Herramientas de desarrollador).

## Tarifa 2.0TD (España)

El componente aplica automáticamente los peajes y cargos de la tarifa 2.0TD:

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

> El plan de F3 se publica en `sensor.*_plan` para su revisión; el control del inversor (F4) llegará con sus propios switches y salvaguardas.

## Solución de problemas

### La integración no aparece
- Verifica que la carpeta está en `custom_components/optimizador_bateria_omie/`
- Reinicia Home Assistant
- Verifica que el archivo `manifest.json` está correcto

### Los sensores de precios muestran "unavailable"
- Verifica que la integración OMIE está instalada y la plantilla `precios_omie.yaml` activa (o que hay conexión para el fallback REData)
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

## Desarrollo

### Estructura del proyecto

```
optimizador-bateria-omie/
├── custom_components/
│   └── optimizador_bateria_omie/
│       ├── __init__.py              # Entry point (jobs de caché, servicio probe, setup F3)
│       ├── manifest.json            # Metadatos HACS (pysolarmanv5, deps: omie + recorder)
│       ├── const.py                 # Constantes
│       ├── cliente_omie.py          # Cliente OMIE + caché + limpieza
│       ├── tarifa.py                # Modelo de tarifa 2.0TD
│       ├── coordinador.py           # CoordinadorDatosOmie (15 min)
│       ├── entidades.py             # Sensores de precio (5)
│       ├── sensor.py               # Setup de plataforma
│       ├── config_flow.py           # Config flow + reconfigure
│       ├── registers.yaml           # F2: mapa de registros (familia estándar)
│       ├── mapa_registros.py        # F2: MapaRegistros (offset, signed)
│       ├── solarman_client.py       # F2: ClienteSolarman (preparado, inactivo)
│       ├── telemetria.py            # F2: TelemetriaInversor + GestorValidacion + CoordinadorTelemetria
│       ├── entidades_solarman.py    # F2: 8 sensores de telemetría
│       ├── services.py              # F2: probe_registers
│       ├── historico.py             # 🚧 F3: FuenteHistoricoRecorder
│       ├── predictor_consumo.py     # 🚧 F3: perfil estadístico + Solcast
│       ├── planificador.py          # 🚧 F3: PlanificadorGreedy (puro)
│       ├── coordinador_plan.py      # 🚧 F3: CoordinadorPlan
│       ├── entidades_plan.py        # 🚧 F3: predicción, plan, ahorro estimado, festivo
│       └── tests/                   # Suites F1 (62 tests) + F2 + F3
├── hacs.json
├── README.md
└── docs/
    ├── INSTALACION.md
    ├── PLAN_F2.md                   # cerrado
    └── PLAN_F3.md                   # v1.3 confirmado
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

## Licencia

MIT License - Copyright (c) 2026

## Agradecimientos

- [OMIE](https://www.omie.es) - Mercado Ibérico de la Electricidad
- [Red Eléctrica de España](https://www.ree.es) - Datos del sistema eléctrico
- [pysolarmanv5](https://pysolarmanv5.readthedocs.io) - Librería de referencia para dataloggers Solarman en local
- [Solcast](https://solcast.com) - Previsión de producción fotovoltaica
- [Home Assistant](https://www.home-assistant.io) - Plataforma de automatización