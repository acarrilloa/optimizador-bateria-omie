# Optimizador Batería OMIE

![Versión](https://img.shields.io/badge/versión-0.8.1-blue)
![F2](https://img.shields.io/badge/F2-telemetría%20Solarman-en%20desarrollo-orange)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.x-41BDF5)
![Python](https://img.shields.io/badge/Python-3.13+-3776AB)

**Custom component para Home Assistant** que optimiza la carga/descarga de baterías usando precios del mercado eléctrico español con resolución de 15 minutos (OMIE MTU15) y la tarifa eléctrica 2.0TD.

> **Estado del proyecto**: **F1 (Precios) ✅ implementada y en producción** (v0.7.6). **F2 (Telemetría Solarman) 🚧 en desarrollo** — solo lectura, sin escrituras al inversor en esta fase. Diseño completo en `SPEC.md`; plan de la fase en `docs/PLAN_F2.md`.

## Características

### Fase F1 (Implementada) ✅

- **Obtención de precios OMIE a 15 min** vía los sensores de plantilla `sensor.omie_precios_de_es_{ayer,hoy,manana}` (creados con la integración oficial OMIE `omie.get_prices_for_date`).
- **Fallback REData** (`api.preciodelaluz.es`) si los sensores de plantilla no tienen datos.
- **Caché en disco** por día (`custom_components/optimizador_bateria_omie/cache/YYYY-MM-DD.json`), validada a 96 tramos. Sobrevive a reinicios y evita saturar las fuentes.
- **Precio efectivo**: spot + peajes + cargos + impuestos 2.0TD (según `tarifa.py`).
- **Clasificación de tramos por período 2.0TD** (`valle` / `llano` / `punta`) según hora local, con fines de semana y festivos nacionales = valle todo el día.
- **Cuatro sensores** (los `entity_id` de la instalación de referencia):
  - `sensor.inversor_optimizador_bateria_omie_precio_actual` — precio del tramo actual en €/kWh; atributo `fuente` (`omie`, `redata`, `desconocida`).
  - `sensor.inversor_optimizador_bateria_omie_precio_efectivo` — precio con peajes, cargos e impuestos.
  - `sensor.inversor_optimizador_bateria_omie_precio_clasificacion` — `valle` / `llano` / `punta` por período 2.0TD.
  - `sensor.inversor_optimizador_bateria_omie_precio_estadisticas` — estado = **mediana** del día; atributos `min`, `max`, `mediana`, `fuente`, `ts_actualizacion`.

> **Nota sobre el prefijo `inversor_`**: los `entity_id` dependen del nombre del dispositivo/entrada de configuración. Si renombras el dispositivo en HA, los IDs cambiarán. Los ejemplos aquí usan el nombre que aparece por defecto en nuestra instalación de referencia. Los `unique_id` son estables (`optimizador_bateria_omie_*`).

### Fase F2 (En desarrollo) 🚧

Telemetría **local, de solo lectura** del inversor Deye SUN-6K-SG05LP1-EU-AM2-P vía datalogger Solarman (Modbus TCP). **F2 no escribe en el inversor en ningún caso.**

Lo que está construyendo esta fase:

- **`ClienteSolarman`**: wrapper asíncrono sobre `pysolarmanv5` (llamadas en executor), accesos serializados con `asyncio.Lock`, peticiones troceadas a **bloques ≤ 16 registros** y reconexión con backoff acotado.
- **`registers.yaml`**: mapa de registros configurable con **dos familias candidatas** (A: placas nuevas SG04LP3/AM2/HP3; B: placas clásicas SG01/03LP1). Las direcciones son configuración, nunca constantes de código.
- **Servicio `optimizador_bateria_omie.probe_registers`**: valida la familia correcta comparando el SOC leído con el valor de la pantalla/APP (tolerancia ±2 % + comprobación de plausibilidad). Permite `reset` para deshacer una validación.
- **Escrituras bloqueadas hasta validación**: el método de escritura existe pero levanta un error mientras no haya familia validada (y nadie lo invoca en F2).
- **Cinco entidades nuevas**:
  - `sensor.*_soc_bateria` (%) — SOC de la batería
  - `sensor.*_potencia_bateria` (W, con signo: + carga / − descarga)
  - `sensor.*_consumo_casa` (W)
  - `sensor.*_produccion_fv` (W)
  - `sensor.*_familia_validada` (diagnóstico) — `ninguna` / `A` / `B`
- **Fuente de telemetría configurable**: `telemetry_source: solarman_modbus` (por defecto, conexión Modbus propia) o `ha_entities` (leer de las entidades de la integración Solarman ya instalada, reservando Modbus para el control de F4).
- **Deuda técnica de F1 resuelta en la fase**: limpieza de caché según `dias_cache` (arranque + job diario 04:30), timeout/reintentos con backoff en REData, y log de actualización con tramos de mañana.

> **Importante**: los sensores de telemetría **no publican valores hasta que `probe_registers` valide una familia** (antes quedan `unavailable`), para no exponer datos leídos del mapa de registros equivocado.

### Fases futuras 🚧

- **F3**: Predicción de consumo (estadística) + Planificador greedy (en `dry-run`)
- **F4**: Control real del inversor con salvaguardas (write-verify, límite de escrituras, watchdog)
- **F5**: Predicción ML + Optimización con programación lineal
- **F6**: Dashboard Lovelace + Notificaciones + Informes

## Requisitos

- **Home Assistant**: 2025.x o superior.
- **Python**: 3.13 o superior.
- **Integración oficial OMIE** instalada y con la plantilla `precios_omie.yaml` activa (ver [Dependencias de F1](#dependencias-de-f1-la-plantilla-de-precios)).
- **F2 (en desarrollo)**: datalogger Solarman accesible en la red local (puerto 8899) y `pysolarmanv5`.
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
   - Selecciona la fuente de precios (recomendado: **Integración OMIE**)

### Método 2: Manual

1. Copia la carpeta `custom_components/optimizador_bateria_omie/` a tu directorio `custom_components/` de Home Assistant

2. Reinicia Home Assistant

3. Añade la integración:
   - Ve a **Configuración > Dispositivos y servicios**
   - Haz clic en **AÑADIR INTEGRACIÓN**
   - Busca **"Optimizador Batería OMIE"**

## Configuración

### Parámetros disponibles (F1)

| Parámetro | Tipo | Valor por defecto | Descripción |
|---|---|---|---|
| `fuente_prioritaria` | selección | `omie` | Fuente de precios: `omie`, `redata` |
| `usar_cache` | booleano | `true` | Usar la caché en disco |
| `dias_cache` | número | `7` | Días de retención de la caché (1–30). 🚧 F2: se aplica con limpieza diaria + arranque |

### Parámetros de telemetría (F2, en desarrollo) 🚧

Disponibles al cerrarse la fase, editables sin borrar la entrada (paso *Reconfigurar*):

| Parámetro | Tipo | Valor por defecto | Descripción |
|---|---|---|---|
| `ip` | texto | `192.168.1.76` | IP del datalogger Solarman |
| `puerto` | número | `8899` | Puerto Modbus TCP del datalogger |
| `serie` | texto | `3178171873` | Nº de serie del datalogger |
| `telemetry_source` | selección | `solarman_modbus` | `solarman_modbus` (conexión propia) o `ha_entities` (entidades de la integración Solarman) |
| `intervalo_telemetria_s` | número | `30` | Intervalo de polling del inversor (10–300 s) |
| `entity_id_*` (×4) | texto | vacío | Solo con `ha_entities`: entidades de SOC, potencia batería, consumo y FV |

> **Sin `esios` ni token ESIOS en F1**: la fuente de fallback es únicamente REData. ESIOS queda pendiente para fases posteriores (requiere token personal).

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

## Sensores (F1)

Los `entity_id` de abajo son los de la instalación de referencia (`sensor.inversor_optimizador_bateria_omie_*`); en otras instalaciones varían según el nombre de la entrada.

### `sensor.*_precio_actual`
- **Estado**: Precio del tramo actual en €/kWh (4 decimales) o `unknown` si no hay tramo vigente
- **Atributos**:
  - `fuente`: origen de los datos (`omie`, `redata`, `desconocida`)

### `sensor.*_precio_efectivo`
- **Estado**: Precio efectivo de compra en €/kWh (spot + peajes + cargos + impuestos)

### `sensor.*_precio_clasificacion`
- **Estado**: Clasificación del tramo actual (`valle` / `llano` / `punta`) **según el período 2.0TD** (no por percentiles)
- Fines de semana y festivos nacionales → `valle` todo el día

### `sensor.*_precio_estadisticas`
- **Estado**: **Mediana** del día en €/kWh
- **Atributos**: `min`, `max`, `mediana`, `fuente`, `ts_actualizacion`

### Sensores de telemetría (F2, en desarrollo) 🚧

`sensor.*_soc_bateria`, `sensor.*_potencia_bateria`, `sensor.*_consumo_casa`, `sensor.*_produccion_fv` y el diagnóstico `sensor.*_familia_validada`. Mientras no se valide una familia de registros con `probe_registers`, los cuatro primeros quedan en `unavailable` por diseño.

## Servicio `probe_registers` (F2, en desarrollo) 🚧

Valida el mapa de registros del inversor contra la pantalla/APP antes de publicar telemetría (y, en fases futuras, antes de permitir escrituras):

```yaml
optimizador_bateria_omie.probe_registers:
  expected: 73        # SOC % que muestra la pantalla o la APP Solarman (obligatorio)
  # register: 588     # opcional: dirección a sondear (default: probe.addr de cada familia)
  # familia: "A"     # opcional: sondear solo una familia
  # reset: true       # opcional: borrar la validación y volver a unavailable
```

- Compara con **tolerancia ±2 %** y verifica plausibilidad de la familia.
- Si exactamente una familia acierta → se persiste y la telemetría empieza a publicarse.
- Devuelve el detalle por familia (`leído`, `esperado`, `desviacion_pct`, `resultado`) como respuesta del servicio (visible en Herramientas de desarrollador).

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

### Acciones basadas en clasificación

```yaml
automation:
  - alias: "Modo batería según precio"
    trigger:
      - platform: state
        entity_id: sensor.inversor_optimizador_bateria_omie_precio_clasificacion
    action:
      - choose:
          - conditions:
              - condition: state
                entity_id: sensor.inversor_optimizador_bateria_omie_precio_clasificacion
                state: "valle"
            sequence:
              - action: switch.turn_on
                target:
                  entity_id: switch.carga_bateria
        default:
          - action: switch.turn_off
            target:
              entity_id: switch.carga_bateria
```

> La clasificación usada en estos ejemplos es la del **período 2.0TD real** (la que expone el sensor), pensada para automatizaciones que deben respetar la franja tarifaria.

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
- La caché se valida a 96 tramos exactos; un JSON incompleto se descarta y se vuelve a pedir a la fuente
- Verifica que la integración OMIE está configurada correctamente

### Los sensores de telemetría muestran "unavailable" (F2) 🚧
- **Es el comportamiento esperado hasta validar la familia de registros**: ejecuta `optimizador_bateria_omie.probe_registers` con el SOC que muestra la APP Solarman
- Comprueba que `telemetry_source` es correcto y que el datalogger es accesible (IP/puerto 8899)
- El sensor diagnóstico `familia_validada` indica el estado (`ninguna` / `A` / `B`)

## Desarrollo

### Estructura del proyecto

```
optimizador-bateria-omie/
├── custom_components/
│   └── optimizador_bateria_omie/
│       ├── __init__.py              # Entry point (F2: registro de probe_registers, carga validación, limpieza de caché)
│       ├── manifest.json            # Metadatos HACS
│       ├── const.py                 # Constantes
│       ├── cliente_omie.py          # Cliente OMIE + caché
│       ├── tarifa.py                # Modelo de tarifa 2.0TD
│       ├── coordinador.py           # CoordinadorDatosOmie (15 min)
│       ├── entidades.py             # Sensores de precio
│       ├── sensor.py                # Setup de plataforma
│       ├── config_flow.py           # Config flow
│       ├── registers.yaml           # 🚧 F2: mapa de registros (familias A/B)
│       ├── mapa_registros.py        # 🚧 F2: MapaRegistros
│       ├── solarman_client.py       # 🚧 F2: ClienteSolarman
│       ├── telemetria.py            # 🚧 F2: CoordinadorTelemetria (30 s)
│       ├── entidades_solarman.py    # 🚧 F2: sensores de telemetría + diagnóstico
│       ├── services.py              # 🚧 F2: probe_registers
│       └── tests/                   # Suite F1 (62 tests) + tests F2
├── hacs.json
├── README.md
└── docs/
    ├── INSTALACION.md
    └── PLAN_F2.md
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
- [Home Assistant](https://www.home-assistant.io) - Plataforma de automatización