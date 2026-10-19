# Diseño y Especificación — `optimizador_bateria_omie`

**Custom component de Home Assistant** para optimizar carga/descarga de batería (inversor Deye vía datalogger Solarman) con precios del mercado eléctrico español en tramos de 15 minutos (OMIE) y predicción de consumo del hogar.

> **Estado del documento** (octubre 2026). Este documento es la **fuente única de verdad** del proyecto. Se actualiza a medida que avanzan las fases.
>
> - **F1** — Precios: ✅ **implementada y en producción doméstica** (versión **0.7.1**).
> - **F2** a **F6** — Telemetría, predicción, planificación y control: 🚧 **diseño**, no implementadas.
>
> A lo largo del documento, cada apartado lleva la marca **✅ F1** o **🚧 Fn** según corresponda.

---

## 1. Visión general

El componente se ejecuta dentro de Home Assistant y, en su versión completa (F1–F6), hará lo siguiente:

1. **Obtener los precios del mercado diario OMIE con resolución de 15 minutos** (MTU15, operativo en España desde el 1 de octubre de 2025).
2. **Leer por Solarman (Modbus local)** el estado del inversor Deye: SOC de batería, potencias de carga/descarga, consumo de casa, producción fotovoltaica, estado de red.
3. **Predecir el consumo** de la casa con un modelo que usa histórico + calendario (laborable/festivo/vacaciones, hora, día de semana) + clima (temperatura exterior, irradiancia).
4. **Calcular un plan horario por tramos de 15 min** que prioriza el autoconsumo y aplica arbitraje (comprar barato / vender caro) solo cuando es rentable tras aplicar la eficiencia round-trip y los parámetros ajustables del usuario.
5. **Aplicar el plan escribiendo** en el inversor (corriente de carga/descarga, límites de SOC, carga desde red, venta a red) con salvaguardas: modo dry-run, límite de escrituras, watchdog y fallback a autoconsumo básico si algo falla.

Prioridad de estrategia (fijada por el usuario): **autoconsumo + arbitraje cuando convenga**.

En la fase actual (F1), el componente **solo implementa el punto 1**: obtiene los precios, calcula el precio efectivo con peajes/cargos/impuestos 2.0TD, los clasifica por período tarifario y publica cuatro sensores de solo lectura.

---

## 2. Contexto del sistema existente

| Elemento | Detalle |
|---|---|
| Plataforma | Home Assistant Yellow (HAOS), en uso desde ~2023 |
| Inversor | **Deye SUN-6K-SG05LP1-EU-AM2-P** — híbrido monofásico, 6.000 W nominales, PV máx. 7.800 W, rango de batería 40–60 V, corriente máx. de carga/descarga configurable **0–135 A** |
| Batería | **Docan 51,2 V 300 Ah LiFePO4** — **15,36 kWh nominales**; fabricante recomienda 140 A de carga/descarga (máx. 200 A), pero el límite real lo impone el inversor (135 A ≈ 6,9 kW) |
| Datalogger | Solarman serie **3178171873**, IP fija **192.168.1.76** |
| Históricos | Node-RED + InfluxDB (producción y consumo), estadísticas de HA |
| **Fuente de precios** | **Integración oficial OMIE** + plantilla `precios_omie.yaml` (ver §5.1). Sustituye a la antigua plantilla RESTful `config/plantillas/energia.yaml` |
| Clima | Integración weather de HA (pendiente de confirmar en F3) |
| Tarifa | Indexada **2.0TD** con **5,75 kW contratados**; peajes y cargos en §5.6 |
| Remuneración de excedentes | **Al mismo precio que la compra** (contrato con mayorista) |

Supuestos confirmados: inversor, batería, datalogger Solarman, tarifa 2.0TD con 5,75 kW y remuneración simétrica.

Pendientes de confirmar: entidad de clima, lista de festivos locales y previsión FV (opcional).

---

## 3. Requisitos funcionales

### RF-01 — Precios OMIE a 15 minutos ✅ F1

- Consultar el precio del mercado diario español (MDA) con **resolución de 15 minutos** para hoy y mañana, en €/kWh.
- Fuentes en cascada con registro del origen en atributos:
  1. **Sensores de plantilla** `sensor.omie_precios_de_es_ayer`, `_hoy` y `_manana`, creados por el usuario a partir de la acción `omie.get_prices_for_date` de la integración oficial OMIE (ver §5.1).
  2. **REData** (`https://api.preciodelaluz.es/v1/prices?date=...`) como fallback si los sensores no contienen datos.
- **Caché en disco** por día (`custom_components/optimizador_bateria_omie/cache/YYYY-MM-DD.json`), validada a 96 tramos, que sobrevive a reinicios y evita saturar las fuentes.
- **Refresco**: coordinador cada 15 minutos + triggers de la plantilla (13:35 CET para precios D+1 y 00:05 CET para rotación de día) + arranque de HA.
- **Exponer** (vía sensores): precio del tramo actual, precio efectivo, clasificación por **período 2.0TD** (valle/llano/punta) y estadísticas del día (mín/máx/mediana).

> **Deuda técnica**: la dependencia de los sensores de plantilla se puede eliminar en F2 añadiendo un cliente directo contra la API pública de OMIE (`api.omie.es`), manteniendo la plantilla como acelerador opcional.

### RF-02 — Telemetría Solarman 🚧 F2

- Conexión **local** al datalogger Solarman vía `pysolarmanv5` (TCP, puerto 8899, nº de serie del datalogger) mediante Modbus.
- Lecturas periódicas (por defecto cada 30 s, configurable): SOC batería, tensión/corriente/potencia batería, temperatura; consumo de casa (W y energía acumulada); producción FV; importación/exportación de red; estado del inversor; parámetros actuales de control (corrientes y límites programados).
- Persistencia interna de históricos de consumo a 15 min (para el predictor) en `data_interval_statistics` / SQLite propio, además de lo que ya exista en InfluxDB.

### RF-03 — Control de carga/descarga 🚧 F4

- Servicios para ajustar, en cualquier momento: corriente/potencia de carga, corriente/potencia de descarga, SOC objetivo de carga, SOC mínimo, habilitación de carga desde red, habilitación de venta a red.
- **Mapa de registros configurable en YAML** (`registers.yaml`); el componente no debe hardcodear direcciones. Modo `probe_registers` que lee un registro, lo compara con el valor mostrado en pantalla/APP y valida antes de permitir escrituras.
- Límite de seguridad: máximo N escrituras por hora (por defecto 12) y verificación de lectura tras escritura (write-verify).

### RF-04 — Compra/venta con parámetros ajustables 🚧 F4/F5

- Decisión de **comprar** (cargar batería desde red) cuando el precio proyectado del tramo esté por debajo del umbral ajustable y por debajo del precio medio ponderado al que se espera descargar. El coste de compra se valora con el **precio efectivo** = spot + peajes/cargos del tramo (§5.6) + impuestos configurables.
- Decisión de **vender** (verter batería a red más allá del autoconsumo) cuando el precio supere el umbral de venta y compense la eficiencia round-trip (solo si `p_venta ≥ p_compra_efectiva / η_round_trip + margen`). Con el contrato actual la venta se valora **al mismo precio que la compra** (§5.6).
- **Restricción dura de potencia contratada**: la importación total de red (consumo de la casa + carga de batería) nunca superará los **5,75 kW**.
- Todos los umbrales como entidades `number`/`switch` de HA modificables sin reinicios; aplicar cambios recalcula el plan en ≤ 60 s.

### RF-05 — Predicción de consumo 🚧 F3/F5

- Predicción de la curva de consumo por tramos de 15 min para el horizonte del plan (hoy + mañana si hay precios).
- Modelo **avanzado** con variables de calendario y clima:
  - Calendario: hora del día, día de semana, festivo nacional/local (lista configurable), periodos vacacionales configurables, mes/estación.
  - Clima: temperatura exterior (media prevista del tramo), irradiancia/previsión solar si está disponible.
  - Histórico: consumo del mismo tramo en los últimos N días (N configurable, por defecto 28), percentiles por tramo×tipo de día.
- Implementación por capas (fallback en cascada):
  1. **Modelo ML** (Gradient Boosting / LightGBM con scikit-learn), reentrenado semanalmente por la noche, con métricas de backtesting (MAPE) expuestas como sensor diagnóstico (F5).
  2. **Perfil estadístico**: mediana del consumo por tramo y tipo de día (laborable/festivo/vacaciones) (F3).
  3. **Persistencia**: consumo del mismo día de la semana anterior.
- La predicción incluye banda de incertidumbre (p10–p90) que el planificador puede usar de forma conservadora.

### RF-06 — Planificador y ejecutor 🚧 F3/F4

- Genera un **plan por tramos de 15 min** con: acción (`AUTOCONSUMO` / `CARGA_RED` / `VENTA_RED` / `MANTENER`), potencia objetivo, SOC previsto, precio de referencia y beneficio marginal estimado.
- Publica el plan como sensor con atributos JSON y lo expone en un dashboard; guarda un **registro de auditoría** de cada decisión, plan aplicado y desviación real vs. previsto.
- Ejecutor con **modo dry-run** (calcula y publica el plan pero no escribe en el inversor) — obligatorio en la fase inicial de puesta en marcha.
- Recalcula ante: llegada de precios nuevos, cambio de parámetros, desviación > X % entre SOC real y previsto (por defecto 10 %), o fallo de comunicación.

---

## 4. Arquitectura

### 4.1 Diagrama de componentes

```mermaid
flowchart TD
  subgraph Fuentes
    OMIE["Integración OMIE + plantilla\n(sensores omie_precios_de_es_*)"]
    REDATA["REData\n(fallback)"]
    SOLAR["Datalogger Solarman\n(F2+)"]
    WEATHER["Clima / calendario\n(F3+)"]
    HIST["Histórico consumo\n(F3+)"]
  end
  subgraph Componente["optimizador_bateria_omie"]
    PRICE["ClienteOmie ✅ F1"]
    TARIFF["ModeloTarifa ✅ F1"]
    COORD["CoordinadorDatosOmie ✅ F1"]
    CONFIG["Config Flow + Entidades ✅ F1"]
    TELEM["SolarmanClient 🚧 F2"]
    FORECAST["ConsumptionForecaster 🚧 F3/F5"]
    PLANNER["Planner 🚧 F3/F5"]
    EXECUTOR["Executor 🚧 F4"]
    AUDIT["Audit / Stats 🚧 F5"]
  end
  OMIE --> PRICE
  REDATA --> PRICE
  SOLAR --> TELEM
  HIST --> FORECAST
  WEATHER --> FORECAST
  PRICE --> TARIFF
  PRICE --> COORD
  TARIFF --> COORD
  TARIFF --> PLANNER
  COORD --> CONFIG
  FORECAST --> PLANNER
  TELEM --> PLANNER
  CONFIG --> PLANNER
  PLANNER --> EXECUTOR
  EXECUTOR --> SOLAR
  PLANNER --> AUDIT
  TELEM --> AUDIT
```

### 4.2 Ciclo de actualización (F1)

```mermaid
sequenceDiagram
  participant T as Temporizador (cada 15 min)
  participant C as CoordinadorDatosOmie
  participant CL as ClienteOmie
  participant P as Plantilla OMIE (sensores)
  participant R as REData (fallback)
  participant Cache as cache/*.json
  participant E as Entidades (4 sensores)
  T->>C: _async_update_data
  C->>CL: obtener_precios_del_dia(hoy) / (mañana)
  CL->>Cache: leer si existe y válida
  alt caché válida
    Cache-->>CL: 96 ranuras
  else sin caché
    CL->>P: leer state.attributes["precios"]
    alt OMIE OK
      P-->>CL: 96 dicts {start, price}
    else OMIE sin datos
      CL->>R: GET api.preciodelaluz.es
      R-->>CL: precios
    end
    CL->>Cache: guardar
  end
  CL-->>C: list[Ranura]
  C->>C: precio_efectivo + clasificar_por_periodo + estadísticas
  C-->>E: coordinator.data = DatosOmie
  E->>E: native_value por sensor
```

### 4.3 Máquina de estados de la batería (F4+, referencia)

```mermaid
stateDiagram-v2
  [*] --> Autoconsumo
  Autoconsumo --> CargaRed : precio ≤ umbral_compra y SOC < SOC_max
  Autoconsumo --> VentaRed : precio ≥ umbral_venta y SOC > SOC_min
  CargaRed --> Autoconsumo : fin de tramo barato o SOC_max alcanzado
  VentaRed --> Autoconsumo : fin de tramo caro o SOC_min alcanzado
  CargaRed --> Fallback : error de comunicación
  VentaRed --> Fallback : error de comunicación
  Fallback --> Autoconsumo : recuperación / escritura de valores seguros
```

---

## 5. Módulos del componente

### 5.1 `ClienteOmie` ✅ F1

Fichero: `custom_components/optimizador_bateria_omie/cliente_omie.py`.

**Clases públicas**:
- `ConfiguracionOmie(fuente_prioritaria, usar_cache, dias_cache)`.
- `Ranura(fecha_hora_utc, precio_eur_kwh, fuente, fecha_publicacion, fecha_hora_fin_utc)`.
- `ErrorFuenteOmie`, `ErrorIntegracionNoDisponible`.
- `ClienteOmie(hass, config)`.

**Métodos**:

| Método | Descripción |
|---|---|
| `_obtener_desde_integracion_omie(fecha)` | Localiza el sensor `sensor.omie_precios_de_es_{ayer,hoy,manana}` correspondiente (usando fecha **local**) y devuelve `state.attributes["precios"]` como `list[dict]` con `{start, price}`. Devuelve `[]` si no hay datos. |
| `_obtener_desde_redata(fecha)` | HTTP GET a `api.preciodelaluz.es`. Devuelve `list[dict]` o `[]`. |
| `_normalizar_a_15min(datos, fecha)` | Convierte `list[dict]` a `list[Ranura]`. Solo soporta el formato OMIE `{start, end, price}` con timezone. Loguea warning si no se obtienen exactamente 96 ranuras. |
| `obtener_precios_del_dia(fecha)` | Flujo completo: caché → cascada de fuentes (OMIE → REData) → normalización → persistir caché. Lanza `ErrorFuenteOmie` si todas las fuentes fallan. |

**Decisiones de diseño de F1**:

| Aspecto | Decisión | Motivo |
|---|---|---|
| Fuente primaria | Sensores de plantilla OMIE | La integración oficial ya gestiona autenticación, parsing y publicación D+1. Reimplementar la API sería duplicar mantenimiento. |
| Fuente de fallback | Solo REData | `apidatos.ree.es` no publica a 15 min de forma fiable; ESIOS requiere token personal y su parser merece una fase propia. |
| Formatos soportados en `_normalizar_a_15min` | **Solo** `{start, end, price}` | Formatos legacy (hour/minute, datetime, JSON-string) eliminados en 0.7.0 por no usarse nunca. |
| Relleno a 96 ranuras | **No** se rellena con ceros | Enmascaraba fallos; si faltan ranuras, se loguea warning y se devuelve lo que haya. |
| Caché | Válida solo si tiene **exactamente 96 ranuras** | Evita que un JSON con `[]` o incompleto bloquee la fuente real. |
| Zona horaria | Selección de sensor por **fecha local** (`datetime.now().astimezone().date()`) | Los sensores de plantilla se indexan por `now().date()` local; compararlos con UTC desfasaba 2 h en CEST. |

**Plantilla externa requerida** (`precios_omie.yaml`):

La integración **depende** de que el usuario tenga esta plantilla activa en HA:

```yaml
- trigger:
    - platform: homeassistant
      event: start
    - platform: time
      at: "13:35:00"
    - platform: time
      at: "00:05:00"
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
    - name: "OMIE precios de ES hoy"
      unique_id: omie_precios_es_hoy
      state: "{{ now().isoformat() }}"
      attributes:
        precios: >
          {% set datos = (precios_hoy | default({})).es | default([]) %}
          {% set ns = namespace(out=[]) %}
          {% for d in datos %}
            {% set ns.out = ns.out + [{'start': d.start, 'price': d.price | float | round(4)}] %}
          {% endfor %}
          {{ ns.out }}
    - name: "OMIE precios de ES mañana"
      unique_id: omie_precios_es_manana
      state: "{{ now().isoformat() }}"
      attributes:
        precios: >
          {% set datos = (precios_manana | default({})).es | default([]) %}
          {% set ns = namespace(out=[]) %}
          {% for d in datos %}
            {% set ns.out = ns.out + [{'start': d.start, 'price': d.price | float | round(4)}] %}
          {% endfor %}
          {{ ns.out }}
```

**F2+**: se podrá añadir `_obtener_desde_api_omie()` para prescindir de la plantilla si se desea.

### 5.2 `SolarmanClient` 🚧 F2

- Wrapper sobre **pysolarmanv5**: `PySolarmanV5(ip, serial, port=8899, verbose=False)` con `read_holding_registers(addr, qty)` y `write_multiple_holding_registers(addr, values)`.
- Polling asíncrono con `asyncio` (`pysolarmanv5` es bloqueante → ejecutar en `executor`), reconexión automática, semáforo para serializar escrituras.
- **Modo solo-lectura opcional**: si el usuario prefiere, la telemetría puede consumirse de las entidades de la integración Solarman ya instalada (via `hass.states`), reservando la conexión Modbus propia únicamente para escrituras. Opción `telemetry_source: solarman_modbus | ha_entities` en el config flow.

### 5.3 Mapa de registros Deye 🚧 F2

⚠️ **El SUN-6K-SG05LP1-EU-AM2-P monta placa de la generación "AM2"**, cuya dirección de registros difiere de las generaciones anteriores. La comunidad documenta dos familias candidatas; el componente debe arrancar en modo `probe` (lectura y comparación con pantalla/APP) y **solo habilitar escrituras tras validar la familia correcta**:

| Función | Familia A (placas nuevas SG04LP3/AM2/HP3) | Familia B (placas clásicas SG01/03LP1) |
|---|---|---|
| SOC / potencia batería | 586–591 (588 SOC, 590 potencia ±, 591 corriente) | 4210–4233 aprox. |
| Corriente de carga objetivo | 128 (A, 0–135 para 6 kW) | dentro del bloque TOU |
| Flags de control (carga red, SOC límites) | 108, 130, 166, 172 | 4341 y afines |
| Time-of-Use (slots) | direcciones propias, hasta 6 slots | 4310–4312 (habilitación), 4315–4338 (6 programas) |

Señalización comunitaria: en esta generación conviene limitar las peticiones Modbus a bloques ≤ 16 registros para evitar timeouts.

Esquema del `registers.yaml`:

```yaml
inverter: "deye-sun-6k-sg05lp1-am2"
telemetry:
  soc:      { addr: 588, scale: 0.1, unit: "%" }
  bat_power:{ addr: 590, scale: 1,   unit: "W", signed: true }
  load_power:{ addr: 670, scale: 0.1, unit: "W" }
  pv_power: { addr: 633, scale: 0.1, unit: "W" }
control:
  charge_current_a: { addr: 128, min: 0, max: 135 }
  charge_from_grid: { addr: 166, write: [0, 1] }
  soc_charge_limit: { addr: 172, scale: 1, min: 10, max: 100 }
  soc_discharge_limit: { addr: 108, scale: 1, min: 10, max: 100 }
tou_slots:
  enabled: true
  max_slots: 6
  block_size: 16
```

Las direcciones son **configuración, no constantes de código**. El servicio `probe_registers` valida la familia A/B antes de habilitar escrituras.

### 5.4 `ConsumptionForecaster` 🚧 F3/F5

- Entrenamiento nocturno (semanal) sobre el histórico de consumo a 15 min con features: `tramo_dia (0-95)`, `dia_semana`, `es_festivo`, `es_vacaciones`, `temperatura_media_prevista`, `irradiancia_prevista`, `consumo_mismo_tramo_{7,14,21,28}d`.
- Salida: `prediccion[tramo] = {p50, p10, p90}` en W.
- Backtesting walk-forward con métricas (MAPE, sesgo) → sensor `sensor.optimizador_bateria_omie_forecast_quality` (F5).
- Reentrenamiento bajo `job` de HA; modelo persistido en `directorio_datos_integración/model.pkl`. Dependencias admitidas: scikit-learn. **Excluidas**: torch, tensorflow.

### 5.5 `Planner` 🚧 F3/F5

- **Entrada**: precios (96–192 tramos), predicción consumo (p50/p90), predicción FV si existe, estado batería, parámetros.
- **Estrategia en dos capas**:
  1. **Autoconsumo siempre**: la batería absorbe `consumo − FV` cuando `SOC > soc_min` (respetando potencia de descarga); nunca carga batería desde red por debajo de `soc_min` salvo decisión de arbitraje.
  2. **Arbitraje**: en tramos de precio bajo, carga desde red hasta `soc_max` si el coste esperado del ciclo completo supera un margen ajustable. En tramos caros, descarga adicional a red hasta `soc_min` solo si el precio neto de venta lo justifica.
- **Implementación**:
  - **F3**: greedy con reglas heurísticas, en `dry_run`.
  - **F5**: programación lineal entera mixta con PuLP/SciPy. Variables: energía comprada/vendida/almacenada por tramo; restricciones de potencia, SOC y **potencia contratada ≤ 5,75 kW**. Tamaño ≈192 variables × 4, resuelve en <1 s.
- El plan incluye `coste_sin_plan` vs. `coste_con_plan` → **ahorro estimado del día**, expuesto como sensor.

### 5.6 `ModeloTarifa` ✅ F1

Fichero: `custom_components/optimizador_bateria_omie/tarifa.py`.

**Clases públicas**:
- `ConfiguracionTarifa(potencia_contratada_kw=5.75, impuesto_electrico_pct=0.051127, iva_pct=0.21, aplicar_impuestos_precio=True)`.
- `PeriodoTarifa` (métodos estáticos `es_punta`, `es_llano`, `es_valle`).
- `ModeloTarifa(config)`: cálculo completo.

**Cargos por energía** (se suman al spot para el precio efectivo de compra):

| Período | Días | Horas locales | Peaje €/kWh | Cargo €/kWh | **Total €/kWh** |
|---|---|---|---|---|---|
| Punta | L–V | 10:00–14:00 y 18:00–22:00 | 0,033261 | 0,064292 | **0,097553** |
| Llano | L–V | 08:00–10:00, 14:00–18:00, 22:00–24:00 | 0,016409 | 0,012858 | **0,029267** |
| Valle | L–V | 00:00–08:00 | 0,000077 | 0,003215 | **0,003292** |
| Valle | S–D y festivos nacionales | 00:00–24:00 | 0,000077 | 0,003215 | **0,003292** |

**Cargos por potencia** (solo informes, no afectan decisiones F1):

| Periodo | Franja | Peaje €/kW·día | Cargo €/kW·día |
|---|---|---|---|
| P1 | L–V 08:00–24:00 | 0,063904 | 0,011999 |
| P2 | L–V 00:00–08:00, S–D y festivos | 0,001216 | 0,000772 (×30 días/mes) |

**Fórmula**:

```
precio_compra_efectivo(tramo) = (spot_15min + peaje + cargo) × (1 + IE) × (1 + IVA)
```

con IE = 5,1127 % y IVA = 21 % por defecto (configurables en `ConfiguracionTarifa`).

**Restricción de potencia contratada** (aplicable en F4+): importación total (consumo casa + carga batería) ≤ **5,75 kW**. El planificador limitará la carga de la batería a `5,75 kW − consumo previsto − margen`; el Executor añadirá un guard dinámico que reduce la corriente de carga si la importación medida supera el 95 %.

**Zona horaria y clasificación** (corregido en 0.7.1):

- `PeriodoTarifa` evalúa los horarios sobre la **hora local del sistema** (`datetime.astimezone()`), respetando automáticamente CET/CEST.
- `obtener_periodo(fecha_hora_utc)` convierte primero la fecha UTC a local y clasifica:
  - **Fines de semana y festivos nacionales → `valle` todo el día.**
  - Laborables: `punta` (10–14 h y 18–22 h), `llano` (8–10 h, 14–18 h y 22–24 h), `valle` (resto).
- El sensor `precio_clasificacion` usa **`clasificar_por_periodo`** (período 2.0TD real), **no** `clasificar_por_percentiles`. La clasificación por percentiles se conserva como utilidad auxiliar reservada para el planner (F3+), que puede necesitar saber si un tramo es caro *dentro* del día independientemente del período tarifario.

**Funciones implementadas**:

| Método | Retorno |
|---|---|
| `obtener_periodo(fecha_hora_utc)` | `"punta"` / `"llano"` / `"valle"` según hora local + festivos + fines de semana |
| `obtener_peaje_cargo(periodo)` | Suma peaje + cargo del periodo |
| `obtener_precio_efectivo(precio_spot, fecha_hora_utc)` | Precio con peajes + cargos + impuestos |
| `clasificar_por_periodo(fechas_hora_utc)` | Lista de `"valle"` / `"llano"` / `"punta"` por período 2.0TD. **Usado por el sensor.** |
| `clasificar_por_percentiles(precios)` | Utilidad auxiliar (tercios por percentil). **No usada por el sensor**; reservada para el planner (F3+). |
| `obtener_estadisticas(precios)` | `{"min", "max", "mediana"}` |
| `es_festivo(fecha)` | Bool contra `FESTIVOS_ESPANA` |
| `es_fin_de_semana(fecha)` | Bool (sábado/domingo) |

### 5.7 `CoordinadorDatosOmie` ✅ F1

Fichero: `custom_components/optimizador_bateria_omie/coordinador.py`.

**Dataclass `DatosOmie`**:
```python
@dataclass
class DatosOmie:
    precios_hoy: list[Ranura]
    precios_manana: list[Ranura] | None
    clasificaciones_hoy: list[str]
    clasificaciones_manana: list[str] | None
    estadisticas_hoy: dict
    estadisticas_manana: dict | None
    fuente: str
    ts_actualizacion: datetime
```

**Método principal**: `async _async_update_data() -> DatosOmie`. `update_interval = timedelta(minutes=15)`. En caso de éxito devuelve `self._datos` (imprescindible para que `coordinator.data` se rellene); en caso de fallo actualiza `_datos` a valores vacíos y lanza `UpdateFailed`.

En el ciclo de actualización, las clasificaciones se calculan **por período 2.0TD**:

```python
clasificaciones_hoy = self.modelo_tarifa.clasificar_por_periodo(
    [r.fecha_hora_utc for r in precios_hoy]
)
# análogamente para precios_manana
```

**Métodos públicos**:
- `obtener_precio_actual() -> float | None`: busca la ranura vigente por `datetime.now(tz=UTC)`.
- `obtener_precio_efectivo_actual() -> float | None`.
- `obtener_clasificacion_actual() -> str | None`: calcula el índice con `(ahora - precios_hoy[0].fecha_hora_utc) // 900 s` y devuelve `clasificaciones_hoy[idx]`.
- `obtener_estadisticas_actual() -> dict`.
- `_buscar_ranura(ranuras, ahora)`: helper.

**Detalle crítico**: `_buscar_ranura` recorre las ranuras comparando `fecha_hora_utc <= ahora < fecha_hora_fin_utc`. Como `ahora` está en UTC y las ranuras se almacenan en UTC, la comparación es correcta independientemente de la zona local. Para cubrir la madrugada local (00:00–02:00 CEST), `obtener_precio_actual` cae a `precios_manana` si no encuentra coincidencia en `precios_hoy`.

### 5.8 `entidades.py` ✅ F1

Fichero: `custom_components/optimizador_bateria_omie/entidades.py`.

**Clase base**:

```python
class SensorBase(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "optimizador_bateria_omie")},
            name="Optimizador Batería OMIE",
            manufacturer="Custom Component",
            model="Optimizador Batería OMIE",
        )
```

> ⚠️ **La herencia de `SensorEntity` es crítica**. Sin ella, `Entity.state` devuelve `STATE_UNKNOWN` y `native_value` no se consulta nunca. Fue el bug raíz que mantuvo los sensores en `unknown` durante la depuración de la versión 0.6.x.

**Clases de sensor**: `SensorPrecioActual`, `SensorPrecioEfectivo`, `SensorPrecioClasificacion`, `SensorPrecioEstadisticas`. Ver §6 para detalles.

### 5.9 `sensor.py` ✅ F1

Fichero: `custom_components/optimizador_bateria_omie/sensor.py`.

Setup de la plataforma. Lee `entry.data` para construir `ConfiguracionOmie` con los parámetros elegidos en el config flow (no con defaults hardcodeados). Crea el `ClienteOmie`, el `CoordinadorDatosOmie`, ejecuta `async_config_entry_first_refresh()` (no rompe si falla) y añade las cuatro entidades.

### 5.10 `config_flow.py` ✅ F1

Fichero: `custom_components/optimizador_bateria_omie/config_flow.py`.

- Un solo paso `user` con `CONFIG_SCHEMA` de tres campos.
- `VERSION = 1`.
- `_abort_if_unique_id_configured()`: instancia única.
- Sin `OptionsFlow` en F1.
- **`fuente_prioritaria`** restringido a `vol.In(["omie", "redata"])`.
- **`dias_cache`** con `vol.All(int, vol.Range(min=1, max=30))`.

---

## 6. Entidades y servicios de Home Assistant

### 6.1 Entidades implementadas ✅ F1

Los `entity_id` dependen del nombre del dispositivo/entrada de configuración. En la instalación de referencia son `sensor.inversor_optimizador_bateria_omie_*`; en otras instalaciones variarán según el nombre del dispositivo. Los `unique_id` son estables: `optimizador_bateria_omie_precio_actual`, `_precio_efectivo`, `_precio_clasificacion`, `_precio_estadisticas`.

#### `sensor.*_precio_actual`

| Campo | Valor |
|---|---|
| Estado | Precio del tramo actual (€/kWh, 4 decimales) o `unknown` si no hay tramo vigente |
| Unidad | €/kWh |
| Atributos | `fuente`: `omie` / `redata` / `desconocida` |
| Disponibilidad | `obtener_precio_actual() is not None` |

#### `sensor.*_precio_efectivo`

| Campo | Valor |
|---|---|
| Estado | Precio efectivo = `(spot + peaje + cargo) × (1 + IE) × (1 + IVA)`, 4 decimales |
| Unidad | €/kWh |
| Atributos | — |
| Disponibilidad | `obtener_precio_efectivo_actual() is not None` |

#### `sensor.*_precio_clasificacion`

| Campo | Valor |
|---|---|
| Estado | `valle` / `llano` / `punta` **según período 2.0TD** (no por percentiles) |
| Unidad | — |
| Atributos | — |
| Disponibilidad | `obtener_clasificacion_actual() is not None` |

Comportamiento:
- **Fines de semana y festivos nacionales** → `valle` todo el día.
- **Laborables**: `punta` (10–14 h y 18–22 h), `llano` (8–10 h, 14–18 h, 22–24 h), `valle` (00–08 h). Todo en hora local (CET/CEST).

#### `sensor.*_precio_estadisticas`

| Campo | Valor |
|---|---|
| Estado | **Mediana** del día (€/kWh) |
| Unidad | €/kWh |
| Atributos | `min`, `max`, `mediana`, `fuente`, `ts_actualizacion` |
| Disponibilidad | `obtener_estadisticas_actual() is not None` |

### 6.2 Entidades planificadas 🚧 F2–F5

Todas con prefijo `optimizador_bateria_omie_*` en sus `unique_id`:

| Entidad | Tipo | Descripción | Fase |
|---|---|---|---|
| `sensor.*_prediccion_consumo` | sensor | W previstos del tramo actual; atributo curva p10/p50/p90 | F3 |
| `sensor.*_plan` | sensor | acción y potencia del tramo actual; atributo plan completo | F3 |
| `sensor.*_ahorro_hoy` | sensor | € ahorrados acumulados | F5 |
| `sensor.*_forecast_quality` | diagnostic | MAPE del último backtest | F5 |
| `switch.*_activo` | switch | habilita/deshabilita la escritura (dry-run cuando off) | F4 |
| `switch.*_permitir_carga_red` | switch | arbitraje de compra | F4 |
| `switch.*_permitir_venta_red` | switch | arbitraje de venta | F4 |
| `switch.*_festivo_hoy` | switch | marca manual de festivo/vacaciones | F3 |
| `number.*_precio_max_compra` | number (€/kWh) | umbral de compra | F4 |
| `number.*_precio_min_venta` | number (€/kWh) | umbral de venta | F4 |
| `number.*_margen_arbitraje` | number (€/kWh) | margen mínimo | F4 |
| `number.*_soc_min` / `_soc_max` | number (%) | límites operativos | F4 |
| `number.*_potencia_max_carga` / `_descarga` | number (W) | límites de potencia | F4 |
| `number.*_potencia_max_red` | number (W) | potencia contratada / importación máxima | F4 |
| `number.*_eficiencia_round_trip` | number (%) | 70–95 | F4 |

### 6.3 Servicios 🚧 F3/F4

```yaml
# Regenerar plan ya (tras cambiar parámetros)
optimizador_bateria_omie.regenerate_plan: {}

# Aplicar manualmente un modo hasta una hora dada
optimizador_bateria_omie.force_mode:
  mode: carga_red | venta_red | autoconsumo | apagado
  until: "23:00"

# Volver al plan automático
optimizador_bateria_omie.release_manual: {}

# Calibración/validación del mapa de registros (F2)
optimizador_bateria_omie.probe_registers:
  register: 588
  expected: 73
```

---

## 7. Parámetros ajustables

### 7.1 Config flow actual ✅ F1

Tres claves, todas persistidas en `entry.data`:

| Parámetro | Tipo | Defecto | Rango / valores |
|---|---|---|---|
| `fuente_prioritaria` | selección | `omie` | `omie`, `redata` |
| `usar_cache` | bool | `true` | — |
| `dias_cache` | int | `7` | 1–30 |

Sin `OptionsFlow` en F1.

### 7.2 Modelo de tarifa actual ✅ F1

Hardcodeado en `tarifa.py`:

- Peajes y cargos de §5.6.
- Impuesto eléctrico 5,1127 %.
- IVA 21 %.
- `aplicar_impuestos_precio = true`.
- Potencia contratada 5,75 kW (declarada; no aplicada como restricción en F1).

### 7.3 Esquema completo planificado 🚧 (referencia, no implementado)

```yaml
optimizador_bateria_omie:
  omie:
    source: omie                       # omie | redata
    valorar_venta_como_compra: true
    precio_venta_factor: 1.0
  tarifa:
    potencia_contratada_kw: 5.75
    margen_potencia_w: 300
    impuesto_electrico_pct: 5.1127
    iva_pct: 21
    aplicar_impuestos_precio: true
    energia:
      punta: { dias: laborables, horas: ["10:00-14:00", "18:00-22:00"], peaje: 0.033261, cargo: 0.064292 }
      llano: { dias: laborables, horas: ["08:00-10:00", "14:00-18:00", "22:00-24:00"], peaje: 0.016409, cargo: 0.012858 }
      valle: { horas: ["00:00-08:00"], peaje: 0.000077, cargo: 0.003215 }
    potencia:
      p1: { dias: laborables, horas: "08:00-24:00", peaje_kw_dia: 0.063904, cargo_kw_dia: 0.011999 }
      p2: { peaje_kw_dia: 0.001216, cargo_kw_mes: 0.000772 }
  solarman:
    ip: 192.168.1.76
    port: 8899
    serial: 3178171873
    telemetry_source: solarman_modbus   # o ha_entities
    register_profile: deye-sun-6k-sg05lp1-am2
  battery:
    capacidad_kwh: 15.36
    voltaje_nominal: 51.2
    corriente_max_a: 135
    soc_min: 15
    soc_max: 95
    potencia_max_carga_w: 5000
    potencia_max_descarga_w: 5000
    eficiencia_round_trip: 88
  strategy:
    permitir_carga_red: true
    permitir_venta_red: true
    precio_max_compra: 0.09
    precio_min_venta: 0.18
    margen_arbitraje: 0.015
  forecast:
    ventana_historico_dias: 28
    usar_clima: true
    weather_entity: weather.casa
    festivos: ["01-01", "06-01", "12-10", "01-11", "06-12", "25-12"]
    vacaciones_rangos: [["2026-07-01", "2026-08-31"]]
  seguridad:
    dry_run: true
    max_escrituras_por_hora: 12
    desviacion_soc_replan_pct: 10
```

En F4+, todos los parámetros de `strategy`, `battery` y `seguridad.dry_run` se expondrán como entidades modificables en caliente (config flow + `options`).

---

## 8. Fases de implementación

| Fase | Alcance | Estado | Criterio de salida |
|---|---|---|---|
| **F1 — Precios** | `ClienteOmie` (plantilla + REData + caché) + `ModeloTarifa` + `CoordinadorDatosOmie` + 4 sensores + config flow | ✅ **completada** | Los precios del tramo actual coinciden con `sensor.omie_precios_de_es_hoy`; clasificación coherente con períodos 2.0TD (verificado en laborables, fines de semana y festivos); precio efectivo validado contra facturación esperada durante 3 días seguidos |
| **F2 — Solarman** | `SolarmanClient` (Modbus local) + `registers.yaml` + servicio `probe_registers`; telemetría en HA | 🚧 pendiente | SOC y potencias coinciden con la APP Solarman; familia A/B validada |
| **F3 — Predicción v1 + Planificador greedy** | Forecaster estadístico (perfil por tramo×tipo de día) + planner greedy en `dry_run`; sensor plan + ahorro estimado | 🚧 pendiente | Plan diario plausible revisado manualmente 1 semana en `dry_run` |
| **F4 — Control** | Executor con write-verify, límite de escrituras, fallback y watchdog; activación real gradual (solo carga desde red en valle nocturno) | 🚧 pendiente | 1 semana sin errores; verificación de escrituras en inversor |
| **F5 — ML + LP** | Forecaster ML (calendario+clima), optimización LP, banda p90, auditoría de ahorro real, reentrenamiento semanal | 🚧 pendiente | MAPE de backtest < 20 % en tramos de alto consumo; ahorro real medible |
| **F6 — Pulido** | Dashboard Lovelace completo, notificaciones de decisiones relevantes, exportación de informes mensuales | 🚧 pendiente | Uso continuado 30 días sin intervención |

---

## 9. Seguridad y resiliencia

### 9.1 Principios no negociables

- `dry_run` por defecto `true` (aplica en F4+): el plan se publica pero nunca escribe hasta validación explícita del usuario.
- **Write-verify**: tras cada escritura, relectura del registro; a los 3 fallos consecutivos → estado `fallback` (reestablecer modo autoconsumo nativo del inversor) y deshabilitar escrituras hasta intervención.
- **Nunca permitir**: SOC objetivo < límite de descarga del inversor, corrientes > límites del `registers.yaml`, escrituras cuando el inversor reporte alarma/fault.
- **Guard de potencia contratada**: la importación medida (consumo + carga batería) nunca debe superar **5,75 kW**; si supera el 95 %, el Executor reduce la corriente de carga de la batería.
- **Si no hay precios para el día siguiente** → plan solo con lo disponible; si tampoco hay datos hoy → mantener autoconsumo (no inventar planes).
- **Auditoría inmutable** (JSONL en directorio de datos) de: cada plan, cada escritura, cada error.

### 9.2 Aplicación en F1

En F1 solo aplica:

- El coordinador **nunca escribe** en ningún dispositivo: todos los sensores son de solo lectura.
- Si todas las fuentes de precios fallan, `_async_update_data` lanza `UpdateFailed` y los sensores quedan en `unavailable`/`unknown` (no se inventan valores).
- La caché se valida a 96 entradas antes de usarse; un JSON corrupto o incompleto se descarta y se vuelve a pedir a la fuente.
- Los logs de éxito incluyen `Datos actualizados. Fuente: <fuente>, tramos hoy: <n>` para auditoría manual.

---

## 10. Pruebas exigidas (CI)

### 10.1 F1 ✅ (pendiente de añadir a CI)

- `tests/test_normalizar_a_15min.py`: cubre 96 tramos nominales, día con cambio horario (92/100 tramos), formato con `end` y sin `end`, formatos inválidos.
- `tests/test_cliente_omie.py`: mock de `hass.states.get` devolviendo `precios` válidos, vacíos y ausentes; verifica la selección del sensor por fecha local.
- `tests/test_tarifa.py`:
  - `obtener_precio_efectivo` para los tres periodos.
  - `obtener_periodo` con casos de laborable/punta, laborable/llano, laborable/valle, sábado, domingo y festivo nacional.
  - `clasificar_por_periodo` sobre una lista de timestamps que cubre un día laborable completo y un domingo completo; verifica que **todos los tramos del domingo son `valle`**.
  - `clasificar_por_percentiles` (utilidad auxiliar) con distribuciones conocidas.
- `tests/test_coordinador.py`: `_async_update_data` con `ClienteOmie` mockeado; verifica `return self._datos`, el manejo de `UpdateFailed` y que `clasificaciones_hoy` proviene de `clasificar_por_periodo` (no de `clasificar_por_percentiles`).

### 10.2 F2+

- `tests/test_solarman_client.py`: fake inverter con mapa de registros para probar lectura/escritura sin hardware.
- `tests/test_planner.py`: escenarios día valle/punta clásico, precio negativo, rampa de precios, SOC inicial 0/50/100, venta deshabilitada, restricción de potencia contratada.
- `tests/test_forecaster.py`: dataset sintético + backtesting reproducible (seed fija).

### 10.3 Herramientas

- `pytest` con `pytest-homeassistant-custom-component`.
- Lint: `ruff check` + constraints de HA y Python.
- Se aceptan fixtures JSON de OMIE/REData con casos reales de días de cambio horario.

---

## 11. Estructura del repositorio (estado F1)

```
optimizador-bateria-omie/
├── custom_components/
│   └── optimizador_bateria_omie/
│       ├── __init__.py          # async_setup_entry / async_unload_entry
│       ├── manifest.json        # versión, requisitos, dependencias
│       ├── const.py             # DOMAIN, defaults, ATTR_*, FESTIVOS_ESPANA
│       ├── config_flow.py       # ConfigFlow (instancia única)
│       ├── cliente_omie.py      # ClienteOmie + Ranura + errores
│       ├── tarifa.py            # ModeloTarifa + ConfiguracionTarifa + PeriodoTarifa
│       ├── coordinador.py       # CoordinadorDatosOmie + DatosOmie
│       ├── entidades.py         # SensorBase + 4 sensores
│       ├── sensor.py            # async_setup_entry de plataforma sensor
│       ├── strings.json         # Etiquetas del config flow
│       ├── cache/               # (runtime) YYYY-MM-DD.json
│       └── tests/               # (a crear en la próxima iteración)
├── hacs.json
├── README.md
├── SPEC.md
└── docs/
    └── INSTALACION.md
```

**Convención de nombres**:

- Ficheros y clases en **español** (consistencia interna): `cliente_omie.py`, `Ranura`, `ModeloTarifa`.
- `unique_id` de sensores con prefijo del dominio: `optimizador_bateria_omie_precio_actual`.
- `entity_id` derivado del `device_info`; en la instalación de referencia resulta `sensor.inversor_optimizador_bateria_omie_*`.

**Ficheros futuros** (F2+): `solarman_client.py`, `registers.yaml`, `forecaster.py`, `planner.py`, `executor.py`, `services.yaml`, `translations/es.json`.

---

## 12. Datos pendientes y deuda técnica

### 12.1 Confirmados

- Inversor Deye SUN-6K-SG05LP1-EU-AM2-P.
- Batería Docan 51,2 V 300 Ah (15,36 kWh).
- Datalogger Solarman (serie 3178171873, IP 192.168.1.76).
- Tarifa 2.0TD con 5,75 kW contratados.
- Remuneración de excedentes al mismo precio que la compra.
- Fines de semana y festivos nacionales → **valle** todo el día (confirmado; coincide con tarifa 2.0TD estándar).

### 12.2 Pendientes de confirmar

1. **Entidad de clima** de HA a usar y lista de **festivos locales** (más allá de los nacionales ya incluidos en `FESTIVOS_ESPANA`).
2. **Previsión de producción FV** (Solcast u otro) — opcional, mejora el plan.
3. **`probe_registers`**: familia de registros A/B válida para la placa AM2 (bloquea F2/F4).

### 12.3 Deuda técnica registrada

| # | Elemento | Fase de resolución |
|---|---|---|
| 1 | `ConfiguracionOmie.dias_cache` se guarda pero **no se aplica**: no hay job de limpieza de caché vieja. Añadir tarea diaria que borre `cache/*.json` con `mtime > dias_cache * 86400`. | F2 |
| 2 | **Reintentos con backoff** en `_obtener_desde_redata` (actualmente timeout por defecto de `aiohttp`). | F2 |
| 3 | **Sustituir dependencia de la plantilla OMIE** por cliente directo contra `api.omie.es` (mantener la plantilla como acelerador opcional). | F2 |
| 4 | **`OptionsFlow`** en `config_flow.py` para editar `fuente_prioritaria`, `usar_cache` y `dias_cache` sin borrar y recriar la entrada. | F3 |
| 5 | **Mover parámetros de tarifa** (peajes, cargos, impuestos, potencia) a `entry.data` en lugar de estar hardcodeados en `tarifa.py`. | F4 |
| 6 | **Carpeta `tests/`** con los ficheros descritos en §10.1, incluyendo el caso explícito de domingo/festivo todo `valle`. | Próxima iteración |
| 7 | **Trazabilidad de ciclos**: el log actual dice `Datos actualizados. Fuente: X, tramos hoy: N`. Considerar añadir `tramos mañana: M` y `desviación` con `precios_hoy[0].fecha_hora_utc`. | F2 |
| 8 | **Limpieza puntual de entidades duplicadas `_2`** tras el cambio de `unique_id` de 0.7.0 → 0.7.1. Acción manual documentada: borrar las 8 entidades `optimizador_bateria_omie_*` desde la UI y reiniciar HA para recrear las 4 con `entity_id` limpio. **No requiere cambio de código**; es una migración única. | Manual, una vez |

---

## 13. Fuentes y referencias

- **OMIE** — Mercado Ibérico de la Electricidad: https://www.omie.es. Nota de prensa *go-live MTU15* (1 de octubre de 2025). Precios del mercado diario por periodo (H1Q1…).
- **REData** (`api.preciodelaluz.es`) — fallback de precios a 15 min.
- **ESIOS** (`api.esios.ree.es`) — pendiente para F2+ (requiere token personal).
- **pysolarmanv5** — librería de referencia para dataloggers Solarman en local: https://pysolarmanv5.readthedocs.io.
- **Datasheet Deye SUN-6K-SG05LP1-EU** — 6.000 W, PV 7.800 W, carga/descarga 0–135 A en modelo 6 kW, batería 40–60 V.
- **Docan Power LiFePO4 51,2 V 300 Ah** — corriente recomendada 140 A, máx. 200 A.
- **Foros comunitarios de registros Modbus Deye** — varían por modelo y generación; validar siempre con `probe_registers` antes de operar.
- **Home Assistant Developer Docs** — `DataUpdateCoordinator`, `CoordinatorEntity`, `SensorEntity`, `ConfigFlow`.
- **Tarifa 2.0TD (BOE)** — peajes y cargos vigentes; estructura de períodos punta/llano/valle; fines de semana y festivos nacionales = valle todo el día.