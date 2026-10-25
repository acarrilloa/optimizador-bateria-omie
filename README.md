# Optimizador Batería OMIE

![Versión](https://img.shields.io/badge/versión-0.7.6-blue)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.6+-41BDF5)
![Python](https://img.shields.io/badge/Python-3.12+-3776AB)

**Custom component para Home Assistant** que optimiza la carga/descarga de baterías usando precios del mercado eláctrico español con resolución de 15 minutos (MTU15) y la tarifa eláctrica 2.0TD.

## Características

### Fase F1 (Implementada) ✅

- **Obtención de precios OMIE a 15 min** vía los sensores de plantilla `sensor.omie_precios_de_es_{ayer,hoy,manana}` (creados con la integración oficial OMIE `omie.get_prices_for_date`).
- **Fallback REData** (`api.preciodelaluz.es`) si los sensores de plantilla no tienen datos.
- **Caché en disco** por día (`custom_components/optimizador_bateria_omie/cache/YYYY-MM-DD.json`), validada a 96 tramos. Sobrevive a reinicios y evita saturar las fuentes.
- **Precio efectivo**: spot + peajes + cargos + impuestos 2.0TD (según `tarifa.py`).
- **Clasificación de tramos** por percentiles (valle / llano / punta) sobre los precios efectivos del día.
- **Cuatro sensores**:
  - `sensor.inversor_optimizador_bateria_omie_precio_actual` — precio del tramo actual en €/kWh; atributo `fuente` (`omie`, `redata`, `desconocida`).
  - `sensor.inversor_optimizador_bateria_omie_precio_efectivo` — precio con peajes, cargos e impuestos.
  - `sensor.inversor_optimizador_bateria_omie_clasificacion` — `valle` / `llano` / `punta`.
  - `sensor.inversor_optimizador_bateria_omie_estadisticas` — estado = **mediana** del día; atributos `min`, `max`, `mediana`, `fuente`, `ts_actualizacion`.

> **Nota sobre el prefijo `inversor_`**: los `entity_id` dependen del nombre del dispositivo/entrada de configuración. Si renombras el dispositivo en HA, los IDs cambiarán. Los ejemplos aquí usan el nombre que aparece por defecto en nuestra instalación de referencia.

### Fases futuras 🚧

- **F2**: Integración con Solarman para telemetría del inversor Deye
- **F3**: Predicción de consumo (estadística) + Planificador greedy
- **F4**: Control real del inversor con salvaguardas
- **F5**: Predicción ML + Optimización con programación lineal
- **F6**: Dashboard Lovelace + Notificaciones + Informes

## Requisitos

- **Home Assistant**: 2024.6 o superior (usa `async_forward_entry_setups` nativo).
- **Python**: 3.12 o superior.
- **Integración oficial OMIE** instalada y con la plantilla `precios_omie.yaml` activa (ver [Dependencias de F1](#dependencias-de-f1)).
- **Opcional**: acceso a internet para fallback REData.

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

## Instalación

### Mátodo 1: HACS (recomendado)

1. Añadir este repositorio como repositorio personalizado en HACS:
   - Ve a **HACS > Integraciones > Tres puntos (â‹®) > Repositorios personalizados**
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
   - Configura parámetros de tarifa si es necesario

### Mátodo 2: Manual

1. Copia la carpeta `custom_components/optimizador_bateria_omie/` a tu directorio `custom_components/` de Home Assistant

2. Reinicia Home Assistant

3. Añade la integración:
   - Ve a **Configuración > Dispositivos y servicios**
   - Haz clic en **AÑADIR INTEGRACIÓN**
   - Busca **"Optimizador Batería OMIE"**

## Configuración

### Parámetros disponibles

#### Fuente de precios (OMIE)
| Parámetro | Tipo | Valor por defecto | Descripción |
|---|---|---|---|
| `fuente` | selección | `omie` | Fuente de precios: `omie`, `redata`, `esios` |
| `esios_token` | texto | - | Token de API para ESIOS (opcional) |

#### Tarifa eláctrica
| Parámetro | Tipo | Valor por defecto | Descripción |
|---|---|---|---|
| `potencia_contratada_kw` | número | 5.75 | Potencia contratada en kW |
| `impuesto_electrico_pct` | número | 5.1127 | Impuesto eláctrico (%) |
| `iva_pct` | número | 21.0 | IVA (%) |
| `aplicar_impuestos_precio` | booleano | true | Aplicar impuestos al precio |
| `margen_potencia_w` | número | 300 | Margen de seguridad en W |

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

### `sensor.ob_precio_actual`
- **Estado**: Precio del tramo actual en €/kWh
- **Atributos**:
  - `curva_hoy`: Lista de precios del día actual (JSON)
  - `curva_manana`: Lista de precios del día siguiente (JSON, opcional)
  - `fuente`: Origen de los datos (`omie`, `redata`, `esios`)
  - `ts_actualizacion`: Fecha de última actualización

### `sensor.ob_precio_efectivo`
- **Estado**: Precio efectivo de compra en €/kWh (incluye spot + peajes + cargos + impuestos)
- **Atributos**:
  - `desglose`: Desglose del precio (spot, peaje, cargo, impuestos)
  - `tramo`: Clasificación del tramo (valle/llano/punta)

### `sensor.ob_precio_clasificacion`
- **Estado**: Clasificación del tramo actual (valle/llano/punta)
- **Atributos**:
  - `curva_clasificacion`: Lista de clasificaciones de todos los tramos
  - `percentiles`: Percentiles p25, p50, p75

### `sensor.ob_precio_estadisticas`
- **Estado**: Precio medio del día en €/kWh
- **Atributos**:
  - `min`: Precio mínimo
  - `max`: Precio máximo
  - `mediana`: Precio mediano

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

## Ejemplo de automations

### Notificación cuando el precio está bajo

```yaml
automation:
  - alias: "Aviso precio bajo"
    trigger:
      - platform: template
        value_template: >
          {{ state('sensor.ob_precio_efectivo') | float < 0.10 }}
    action:
      - service: notify.notify
        data:
          message: "Precio bajo: {{ state('sensor.ob_precio_efectivo') }} €/kWh"
          title: "OMIE - Precio bajo"
```

### Acciones basadas en clasificación

```yaml
automation:
  - alias: "Modo batería según precio"
    trigger:
      - platform: state
        entity_id: sensor.ob_precio_clasificacion
    action:
      - choose:
          - conditions:
              - condition: state
                entity_id: sensor.ob_precio_clasificacion
                state: "valle"
            sequence:
              - service: switch.turn_on
                entity_id: switch.carga_bateria
        default:
          - service: switch.turn_off
            entity_id: switch.carga_bateria
```

## Solución de problemas

### La integración no aparece
- Verifica que la carpeta está en `custom_components/optimizador_bateria_omie/`
- Reinicia Home Assistant
- Verifica que el archivo `manifest.json` está correcto

### Los sensores muestran "unavailable"
- Verifica que la integración OMIE está instalada (o configura REData/ESIOS)
- Verifica la conexión a internet
- Consulta los logs de Home Assistant

### Los precios no coinciden con OMIE
- El componente usa la integración OMIE existente como fuente primaria
- Los precios pueden tener un retraso de hasta 24 horas (cache)
- Verifica que la integración OMIE está configurada correctamente

## Desarrollo

### Estructura del proyecto

```
optimizador-bateria-omie/
â”œâ”€â”€ custom_components/
â”‚   â””â”€â”€ optimizador_bateria_omie/
â”‚       â”œâ”€â”€ __init__.py              # Entry point
â”‚       â”œâ”€â”€ manifest.json            # Metadatos HACS
â”‚       â”œâ”€â”€ const.py                 # Constantes
â”‚       â”œâ”€â”€ cliente_omie.py           # Cliente OMIE
â”‚       â”œâ”€â”€ tarifa.py                # Modelo de tarifa
â”‚       â”œâ”€â”€ coordinador.py           # Coordinador
â”‚       â”œâ”€â”€ entidades.py             # Sensores
â”‚       â”œâ”€â”€ flujo_configuracion.py   # Config flow
â”‚       â””â”€â”€ tests/                   # Tests
â”œâ”€â”€ hacs.json                         # Configuración HACS
â””â”€â”€ README.md                         # Documentación
```

### Ejecutar tests

```bash
# Instalar dependencias
pip install -r custom_components/optimizador_bateria_omie/requirements.txt

# Ejecutar tests
pytest custom_components/optimizador_bateria_omie/tests/ -v

# Verificar estilo
ruff check custom_components/optimizador_bateria_omie/
```

## Licencia

MIT License - Copyright (c) 2026

## Agradecimientos

- [OMIE](https://www.omie.es) - Mercado Ibérico de la Electricidad
- [Red Eláctrica de España](https://www.ree.es) - Datos del sistema eláctrico
- [Home Assistant](https://www.home-assistant.io) - Plataforma de automatización
