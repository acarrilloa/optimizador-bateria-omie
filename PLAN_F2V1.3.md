# PLAN_F2 — Telemetría Solarman (solo lectura) · v1.3

> **Estado**: aprobado con modificaciones. Implementable por unidades con un commit por unidad.
> **Base**: `main` con F1 en producción (manifest `0.7.3`, ruff limpio, 62 tests en verde).
> **Versión de cierre prevista**: `0.8.0`.
> **Referencia**: SPEC.md §5.2, §5.3, §6.2, §6.3, §8 (F2), §12.3 (deuda #1, #2, #5, #7).
>
> **Historial**
> - v1.0 — borrador.
> - v1.1 — incorpora las M1–M10 del usuario.
> - v1.2 — incorpora C3/C4/C5 (críticos), M1–M5 (moderados) y m1–m5 (menores); resuelve los ajustes cruzados sobre M1–M10; cierra las preguntas Q5–Q7.
> - **v1.3** — corrige la API de `pysolarmanv5` (sin `connect()` en el cliente síncrono; pin `>=3.0.6,<4.0.0`); fija la **convención de signos de Deye** (`+descarga/−carga`) para no romper tarjetas existentes; completa las unidades U1–U10, los tests, los riesgos y los criterios de salida que faltaban en v1.2.

---

## 1. Objetivo y alcance

Añadir telemetría local del inversor Deye SUN-6K-SG05LP1-EU-AM2-P vía datalogger Solarman (Modbus TCP), con mapa de registros configurable (`registers.yaml`) y validación por sonda (`probe_registers`) **antes de permitir cualquier escritura futura**. F2 es **estrictamente de solo lectura**: no existe método de escritura en el cliente en esta fase (se introducirá en F4).

Además se resuelve la deuda técnica de F1 marcada para esta fase:

- **#1** — limpieza de caché por `dias_cache` (arranque + job diario).
- **#2** — backoff/timeout en REData.
- **#7** — log de éxito con `tramos mañana`.
- **#5** — mover parámetros de tarifa a `entry.data`.

La deuda **#3** (cliente directo API OMIE) **queda fuera de F2**: la API pública entrega mercado horario, no MTU15, y no se interpolará.

---

## 2. Decisiones de diseño

| # | Decisión | Motivo |
|---|---|---|
| D1 | Fichero `solarman_client.py`, clase **`ClienteSolarman`** | Convención española de F1 (SPEC §11) |
| D2 | Direcciones de registros **solo** en `registers.yaml`; el código las accede vía `MapaRegistros` | Regla dura de AGENTS.md: ninguna dirección en código |
| D3 | `registers.yaml` define **dos familias candidatas** (`A` y `B`); la familia activa se decide con `probe_registers` y se persiste | Placa AM2 puede responder a cualquiera de los dos mapas (SPEC §5.3) |
| D4 | Persistencia de `familia_validada` vía **`hass.storage.Store`** (clave `optimizador_bateria_omie/probe.json`), **no** vía `entry.data` | Actualizar `entry.data` recarga la entrada; el Store sobrevive a reinicios sin efectos colaterales |
| D5 | Sensores de telemetría no publican hasta que exista familia validada (quedan `unavailable`) | Evita publicar basura leída del mapa equivocado |
| D6 | `telemetry_source: solarman_modbus \| ha_entities` (default `solarman_modbus`). Con `ha_entities`, la telemetría se lee de entidades externas vía `hass.states` | Reserva la conexión Modbus para F4 y evita doble polling |
| D7 | Con `telemetry_source: ha_entities`, los 4 `entity_id` se piden en el config flow (**Q1**: por defecto quedan vacíos con TODO; no se inventan ids) | Los `entity_id` de la integración Solarman del usuario son desconocidos |
| D8 | `ClienteSolarman` mantiene: `asyncio.Lock` para serializar accesos; reconexión con backoff acotada (**M4**); troceado interno de lecturas en bloques ≤ `block_size` (16) | Timeouts documentados en esta generación de dataloggers (SPEC §5.3) |
| D9 | **`ClienteSolarman` NO incluye `escribir_registros` en F2** (reservado a F4) | Código muerto con guard; se añadirá junto con el Executor |
| D10 | Defaults de conexión en el flow: IP `192.168.1.76`, puerto `8899`, serie `3178171873` — editables, nunca constantes de código | Datos confirmados en SPEC §2/§7.3 |
| D11 | Entrada existente de F1 sin migración: los campos nuevos se leen con `entry.data.get(clave, default)`; `VERSION` sigue en 1 | No romper la entrada de producción |
| D12 | Coordinador independiente (`CoordinadorTelemetria`, intervalo 30 s configurable) con `TelemetriaInversor` (dataclass) | No acoplar el ciclo de 15 min de precios al polling de 30 s |
| D13 | El `Store` persiste `{"familia", "probe_addr", "probe_funcion", "ultima_validacion_ts"}` (**m1**) | Revalidar al arrancar si cambia `probe.addr` en el YAML |
| D14 | Override de usuario de `registers.yaml` en `config/optimizador_bateria_omie/registers.yaml` (**M1**) | HACS sobreescribe `custom_components/` en cada actualización |
| D15 | Pin `pysolarmanv5>=3.0.6,<4.0.0` (**corregido en v1.3**) | La serie 1.x/2.x no existe en PyPI; 3.0.6 es la última estable |
| **D16** | **Convención de signos: `potencia_bateria` = `+descarga / −carga`** (nativa de Deye) | Evitar romper tarjetas y gráficos existentes del usuario; `registers.yaml` incluye `invertir_signo: false` como escape |
| **D17** | **`ClienteSolarman` usa `PySolarmanV5` síncrono en executor; no invoca `connect()`** | La versión síncrona no expone `connect()`; la conexión se establece al instanciar y se cierra con `disconnect()` |
| **D18** | **`InversorFake` implementa `connect()`/`disconnect()` para tests** | Aunque el cliente real no llame a `connect()`, el fake debe simular la interfaz de forma flexible para cubrir ambos escenarios |

---

## 3. Ficheros a crear

### 3.1 `custom_components/optimizador_bateria_omie/registers.yaml`

```yaml
inverter: "deye-sun-6k-sg05lp1-am2"
block_size: 16

familias:
  A:                                   # placas nuevas SG04LP3 / AM2 / HP3
    probe:  { funcion: soc, addr: 588 }
    plausibility:
      soc:          [0, 100]
      potencia_bat: [-7000, 7000]
    telemetria:
      soc:            { addr: 588, scale: 0.1, unit: "%" }
      potencia_bat:   { addr: 590, scale: 1,   unit: "W", signed: true, invertir_signo: false, signo_semantico: "+descarga/-carga" }
      corriente_bat:  { addr: 591, scale: 0.1, unit: "A", signed: true }
      consumo_casa:   { addr: 670, scale: 0.1, unit: "W" }
      produccion_fv:  { addr: 633, scale: 0.1, unit: "W" }
    control:                           # reservado a F4; F2 no lo lee
      corriente_carga: { addr: 128, min: 0, max: 135 }
      carga_desde_red: { addr: 166 }
      limite_carga:    { addr: 172, min: 10, max: 100 }
      limite_descarga: { addr: 108, min: 10, max: 100 }

  B:                                   # placas clásicas SG01/03LP1
    probe:  { funcion: soc, addr: 4212 }  # TODO: confirmar offset exacto (bloque 4210-4233)
    plausibility:
      soc:          [0, 100]
      potencia_bat: [-7000, 7000]
    telemetria:
      soc:            { addr: 4212, scale: 1,   unit: "%" }               # TODO
      potencia_bat:   { addr: 4214, scale: 1,   unit: "W", signed: true, invertir_signo: false, signo_semantico: "+descarga/-carga" }  # TODO
      consumo_casa:   { addr: 4226, scale: 0.1, unit: "W" }               # TODO
      produccion_fv:  { addr: 4229, scale: 0.1, unit: "W" }               # TODO
    control:
      tou_habilitacion: { addr: 4310 }
      tou_programas:    { addr: 4315, qty: 24 }
```

**Reglas del fichero**:

- `block_size ≤ 16`. Se valida al cargar.
- `plausibility` se usa en la validación secundaria (**M2**).
- `invertir_signo` se aplica **después** de la conversión two's complement (**M8**). Por defecto `false` (convención Deye: + descarga / − carga).
- `signo_semantico` es solo documental.
- `TODO` en comentarios se tolera (warning, no error).

### 3.2 `custom_components/optimizador_bateria_omie/mapa_registros.py`

Clase `MapaRegistros`:

- **Constructor**: `MapaRegistros(hass)` — carga el YAML en executor, con override (**D14**):
  1. `Path(hass.config.config_dir) / "optimizador_bateria_omie" / "registers.yaml"` (si existe → `INFO "Usando override de usuario"`).
  2. `Path(__file__).parent / "registers.yaml"` (default empaquetado).
- **API pública**:
  - `familias() -> list[str]`
  - `direccion(familia, funcion, tipo="telemetria") -> int`
  - `parametros(familia, funcion, tipo="telemetria") -> dict` (contiene `scale`, `signed`, `invertir_signo`, `unit`)
  - `plausibility(familia) -> dict`
  - `probe(familia) -> dict` (contiene `{"funcion": str, "addr": int}`)
  - `block_size -> int`
- **Excepción** `ErrorMapaRegistros(msg)` en español.
- **Two's complement 16-bit** si `signed: true` (**M8**): `valor_signed = valor - 65536 if valor > 32767 else valor`.
- Tras two's complement, si `invertir_signo: true`, multiplicar por −1 (**D16**).

### 3.3 `custom_components/optimizador_bateria_omie/solarman_client.py`

Clase `ClienteSolarman`:

- **Constructor**: `ClienteSolarman(hass, ip, puerto, serie, mapa: MapaRegistros)`.
- **`PySolarmanV5` síncrono** se instancia **bajo demanda en executor**. No se invoca `connect()` (la versión síncrona no lo tiene). En reconexión se llama `disconnect()` sobre la instancia anterior (ignorando fallos) y se crea una nueva instancia (**D17**).
- `asyncio.Lock` interno para serializar accesos.
- **Backoff acotado (M4)**: máx. 3 intentos por ciclo; esperas 0,5 s, 2 s, 5 s (peor caso ≈ 7,5 s ≤ ciclo 30 s). Agotados → `ErrorComunicacionSolarman`. El coordinador reintenta en el siguiente tick.
- **Troceado interno**: `leer_registros(addr, qty)` divide en bloques ≤ `block_size` y concatena.
- `async leer_telemetria(familia: str) -> TelemetriaInversor` (**C4**): la familia llega por parámetro. El cliente **no** consulta el Store.
- **NO incluye `escribir_registros`** en F2 (**D9 / m5**). Dejar `TODO F4: escribir_registros()` como comentario.
- **Excepción** `ErrorComunicacionSolarman`.

### 3.4 `custom_components/optimizador_bateria_omie/telemetria.py`

**Dataclass `TelemetriaInversor`**:

- `soc: float | None`
- `potencia_bateria: float | None` (W, **`+descarga/−carga`** — **D16**)
- `corriente_bateria: float | None` (A) — **M5**
- `consumo_casa: float | None` (W)
- `produccion_fv: float | None` (W)
- `ts_actualizacion: datetime`

**Clase `CoordinadorTelemetria(DataUpdateCoordinator)`**:

- Intervalo: `entry.data.get("intervalo_telemetria_s", 30)`, rango 10–300.
- `__init__`: carga `familia_validada` del Store (**C4 / D13**). No instancia el cliente Solarman si `telemetry_source != solarman_modbus` o no hay familia validada.
- `_async_update_data()`:

  ```python
  async def _async_update_data(self) -> TelemetriaInversor:
      if self._telemetry_source == "solarman_modbus":
          if self._familia_validada is None:
              raise UpdateFailed(
                  "Sin familia validada. Ejecuta "
                  "optimizador_bateria_omie.probe_registers con el SOC actual, "
                  "o revisa sensor.optimizador_bateria_omie_familia_validada."
              )
          return await self._cliente.leer_telemetria(self._familia_validada)
      return self._leer_de_ha_entities()
  ```

  El `UpdateFailed` se lanza **antes** de tocar Modbus (**C5**).

- `_leer_de_ha_entities()`: lee los 4 `entity_id` de `entry.data`; estado no numérico o ausente → magnitud `None` (sin inventar).

### 3.5 `custom_components/optimizador_bateria_omie/entidades_solarman.py`

**Seis sensores** (5 de telemetría + 1 diagnóstico **M10**), todos con `_attr_has_entity_name = True`, `device_info` compartido con F1:

| Clase | `unique_id` | Unidad | `device_class` | `state_class` | `entity_category` |
|---|---|---|---|---|---|
| `SensorSocBateria` | `optimizador_bateria_omie_soc_bateria` | % | `battery` | `measurement` | — |
| `SensorPotenciaBateria` | `optimizador_bateria_omie_potencia_bateria` | W | `power` | `measurement` | — (**+descarga/−carga**) |
| `SensorCorrienteBateria` | `optimizador_bateria_omie_corriente_bateria` | A | `current` | `measurement` | — (**M5**) |
| `SensorConsumoCasa` | `optimizador_bateria_omie_consumo_casa` | W | `power` | `measurement` | — |
| `SensorProduccionFv` | `optimizador_bateria_omie_produccion_fv` | W | `power` | `measurement` | — |
| `SensorFamiliaValidada` | `optimizador_bateria_omie_familia_validada` | — | — | — | `EntityCategory.DIAGNOSTIC` (**M10**) |

**`SensorFamiliaValidada`**:

- Estado: `"ninguna"` / `"A"` / `"B"` (leído del Store).
- Icono: `mdi:shield-check`.
- Atributos: `probe_addr`, `probe_funcion`, `ultima_validacion_ts` (leídos del Store).
- Se actualiza al recibir respuesta del servicio `probe_registers` o al arrancar.

### 3.6 `custom_components/optimizador_bateria_omie/services.py` + `services.yaml`

**Servicio `optimizador_bateria_omie.probe_registers`** (**M1, M2, M3, M7, M9**):

```yaml
optimizador_bateria_omie.probe_registers:
  fields:
    expected:
      description: "SOC % actual que muestra la pantalla o APP Solarman"
      example: 73
      required: true
      selector: number
    register:
      description: "Dirección a leer (por defecto: probe.addr de cada familia)"
      example: 588
      required: false
      selector:
        number: { min: 1, max: 9999 }
    familia:
      description: "Sondear solo esta familia (por defecto: ambas)"
      required: false
      selector:
        select: { options: ["A", "B"] }
    reset:
      description: "Con reset=true, borra familia_validada del Store y NO sondea (se ignoran expected/register/familia)"
      default: false
      required: false
      selector: boolean
```

- **Decorador**: `@callback` no; es `async`.
- **Registro** con `supports_response=SupportsResponse.OPTIONAL` (**M9**). No usar `return_response` en el registro.
- **Con `reset: true`** → borra Store, telemetría a `unavailable`, `INFO` con resultado `{"reset": true}`.
- **Con `reset: false`** → para cada familia (o la indicada):
  1. Lee el registro SOC (`register` si se indica, si no `probe.addr`).
  2. `desviacion_pct = abs(leido − expected) / expected * 100`. **Tolerancia ±2 %** (**M1**).
  3. Si `desviacion_pct ≤ 2.0` → **plausibilidad (M2)** con los umbrales del `registers.yaml`:
     - SOC en `[soc_min, soc_max]` (0–100).
     - `potencia_bat` en `[pbat_min, pbat_max]` (|W| ≤ 7000).
     - Si falla cualquier plausibilidad → WARNING por familia, sin validar.
  4. Si ambas tolerancia y plausibilidad OK → candidata.
  5. Si **exactamente una** familia candidata → persistir en Store `{familia, probe_addr, probe_funcion, ultima_validacion_ts}`, recargar telemetría, `INFO "Familia X validada"`.
  6. Si 0 o 2 candidatas → WARNING, sin persistir.
- **Respuesta** (con `return_response: true` en la llamada):

  ```json
  {
    "reset": false,
    "familias": {
      "A": {"leido": 73.0, "esperado": 73, "desviacion_pct": 0.0, "plausible": true, "resultado": "VALIDADA"},
      "B": {"leido": null, "esperado": 73, "desviacion_pct": null, "plausible": false, "resultado": "ERROR_COMUNICACION"}
    },
    "familia_activa": "A"
  }
  ```

### 3.7 Tests

- **`tests/fake_inversor.py`** — `InversorFake` con:
  - `connect()` (no-op, para compatibilidad), `disconnect()` (**D18**).
  - `read_holding_registers(addr, qty)`, `write_multiple_holding_registers(addr, values)`.
  - Contador de llamadas, registro de tamaños de bloque, inyección de timeouts/desconexiones.
- **`tests/test_mapa_registros.py`** — YAML válido, YAML inválido, `TODO` como warning, override de usuario (**M1**), `block_size` ≤ 16, two's complement (**M8**), `invertir_signo` (**D16**).
- **`tests/test_solarman_client.py`** — lectura por familia A y B, troceado > 16, semáforo, backoff acotado (**M4**: 3 intentos exactos, espera total ≤ 10 s con `asyncio.sleep` mockeado), `leer_telemetria(familia)` con **C4** (familia por parámetro), sin `escribir_registros`, y **verificación de que no se invoca `connect()` en el cliente real**.
- **`tests/test_telemetria.py`** — `ha_entities` con `hass.states` mockeados; `solarman_modbus` sin familia → `UpdateFailed` sin tocar el cliente (**C5**); con familia → delegación correcta.
- **`tests/test_probe_registers.py`** — tolerancia ±2 % (**M1**), plausibilidad fallida → sin validar (**M2**), reset (**M3**), persistencia en Store, `familia` explícita (**M7**), revalidación al arranque si cambia `probe_addr` (**m1**).
- **`tests/test_mantenimiento.py`** — `limpiar_cache_antigua` idempotente (job 04:30 + arranque, **M5** user), backoff de REData (**deuda #2**).
- **`tests/test_config_flow.py`** — campos nuevos con defaults, `reconfigure` (**M6**).

---

## 4. Ficheros a modificar

| Fichero | Cambio |
|---|---|
| `config_flow.py` | Añadir `async_step_reconfigure` (**M6**). Campos: `ip`, `puerto`, `serie`, `telemetry_source`, `intervalo_telemetria_s`, y condicionales si `ha_entities`: 4 `entity_id` vacíos. Los 3 campos de F1 (`fuente_prioritaria`, `usar_cache`, `dias_cache`) **no** son editables vía reconfigure en F2. `VERSION` sigue en 1 |
| `__init__.py` | Registrar servicio `probe_registers`; cargar Store en setup; **revalidar al arrancar** si `probe_addr` cambió (**m1**); job diario 04:30 + pasada al arranque de `limpiar_cache_antigua` (**M5** user) |
| `sensor.py` | Instanciar `MapaRegistros` + `CoordinadorTelemetria` y añadir las 6 entidades. `ClienteSolarman` solo si `telemetry_source == solarman_modbus` y familia validada |
| `cliente_omie.py` | `limpiar_cache_antigua(dias)` (executor, idempotente, **M5** user); `_obtener_desde_redata` con `ClientTimeout(total=10)` + 2 reintentos con backoff 0,5 s / 2 s (**deuda #2**). Comentario de versión → 0.8.0 |
| `coordinador.py` | Log de éxito: `Datos actualizados. Fuente: X, tramos hoy: N, tramos mañana: M` (**deuda #7**) |
| `tarifa.py` / `coordinador.py` | **Deuda #5**: mover `ConfiguracionTarifa` a `entry.data` (con defaults); leer en `sensor.py` y pasarla al coordinador |
| `manifest.json` | `requirements`: `pysolarmanv5>=3.0.6,<4.0.0` + `PyYAML`; `version` → `0.8.0` |
| `const.py` | Constantes nuevas: `CONF_IP`, `CONF_PUERTO`, `CONF_SERIE`, `CONF_TELEMETRY_SOURCE`, `CONF_INTERVALO_TELEMETRIA`, `CONF_ENTITY_ID_SOC`, `CONF_ENTITY_ID_PBAT`, `CONF_ENTITY_ID_CONSUMO`, `CONF_ENTITY_ID_FV`, `SERVICE_PROBE_REGISTERS`, `STORE_KEY_PROBE`, unique_ids de telemetría |
| `translations/es.json` + `strings.json` | Etiquetas de campos del flow (reconfigure) y del servicio |
| `SPEC.md` | §5.2 (`ClienteSolarman` sin escritura, `leer_telemetria(familia)`, sin `connect()`), §5.3 (convención `+descarga/−carga`, `invertir_signo`, `plausibility`, override de YAML), §12.3 (deuda #1, #2, #5, #7 resueltas), §8 (F2 completada), §6.2 (6 sensores) |
| `README.md` | Instrucciones: `probe_registers`, sensor diagnóstico, override de `registers.yaml`, **convención de signo Deye** |

Los 4 sensores de F1 no cambian de comportamiento salvo el log de éxito (**deuda #7**), que no afecta a estados.

---

## 5. Estrategia de tests

- **Sin hardware ni red**: `PySolarmanV5` nunca se usa directamente en tests; se inyecta `InversorFake` en `ClienteSolarman` vía un atributo interno (patrón de F1). El fake implementa `connect()`/`disconnect()` aunque el cliente real no llame a `connect()`, para cubrir ambos escenarios.
- **Sin red**: REData sigue parcheando `async_get_clientsession`.
- **Ejecución**: `pytest` con `pytest-homeassistant-custom-component` en el venv (HA 2026.10.1) + `ruff check` limpio. Si `pytest-homeassistant-custom-component` no tiene build compatible con HA 2026.10.1, se mantiene el enfoque actual (hass mockeado con `MagicMock`) y se documenta en `RESULTADO_F2.md`.
- **Cobertura mínima**: §3.7 + regresión de los 62 tests de F1.

---

## 6. Riesgos

| Riesgo | Mitigación |
|---|---|
| Direcciones de registros equivocadas (ambas familias son documentación comunitaria) | `probe_registers` contra la APP antes de publicar telemetría (D5); escrituras bloqueadas hasta validación |
| Offsets exactos de familia B desconocidos dentro de 4210–4233 | `TODO` explícito en `registers.yaml`; el mapa no rompe el arranque por un TODO |
| Timeouts del datalogger con bloques grandes | Troceado interno a ≤ 16; semáforo; backoff |
| `pysolarmanv5` bloquea el event loop | Todas las llamadas vía `async_add_executor_job` |
| Entrada de config de producción sin campos nuevos | Defaults vía `entry.data.get` (D11); sin migración |
| `ha_entities` con entity_ids desconocidos | Campos configurables + TODO; no se inventan ids |
| REData con dominio muerto: los reintentos no lo arreglan | La deuda pide backoff/timeout, se implementa; retirar la fuente queda decisión de F3 |
| **Signo de potencia invertido respecto a tarjetas existentes** | **Convención Deye `+descarga/−carga` (D16)**; `invertir_signo: false` por defecto; escape en YAML si fuera necesario |
| **`connect()` no existe en `PySolarmanV5` síncrono** | **D17**: no se invoca `connect()` en el cliente real; el fake lo implementa solo para tests |

---

## 7. Paquetes nuevos

| Paquete | Destino | Estado |
|---|---|---|
| `pysolarmanv5>=3.0.6,<4.0.0` | `manifest.json` requirements + venv | Pre-autorizado (AGENTS.md); pin corregido en v1.3 |
| `PyYAML` | `manifest.json` requirements | OK (Q3) |
| `pytest-homeassistant-custom-component` + `pysolarmanv5` | venv de tests | OK (Q3) |

---

## 8. Respuestas a preguntas abiertas

- **Q1** — `entity_id` de la integración Solarman: por defecto se usa `solarman_modbus`; los campos quedan vacíos con TODO. No se inventan ids.
- **Q2** — `expected` es siempre el SOC % de la APP; tolerancia ±2 % (**M1**) + plausibilidad (**M2**).
- **Q3** — OK a `PyYAML` en manifest y `pytest-homeassistant-custom-component` (+ `pysolarmanv5`) en el venv.
- **Q4** — OK a bump `0.8.0` y job a las 04:30 (más pasada al arranque, **M5** user).
- **Q5** — Convención **`+descarga/−carga`** adoptada (nativa de Deye); `invertir_signo: false` por defecto. SPEC se corrige al cierre (**D16**).
- **Q6** — Exponer `SensorCorrienteBateria` como 5º sensor de telemetría (**M5**).
- **Q7** — **No** incluir `escribir_registros` en F2 (**D9**).

---

## 9. Criterios de salida

- SOC y potencias leídas coinciden con la APP Solarman (verificación hardware por el usuario).
- Familia A/B validada con tolerancia ±2 % + plausibilidad parametrizada; reseteable.
- `SensorFamiliaValidada` muestra `A`/`B`/`ninguna` coherentemente con el Store.
- **Ninguna escritura** al inversor; método de escritura inexistente en F2.
- **Signo de `potencia_bateria` coherente con Deye** (`+descarga/−carga`) verificado en hardware.
- `pytest` en verde (62 tests F1 + nuevos) + `ruff check` limpio.
- `SPEC.md` actualizada al cierre.
- `README.md` actualizado con instrucciones de validación.

---

## 10. Unidades lógicas (orden de implementación)

| Unidad | Alcance | Dependencias |
|---|---|---|
| **U1** | `registers.yaml` + `mapa_registros.py` + `test_mapa_registros.py` | — |
| **U2** | `solarman_client.py` (sin `escribir_registros`) + `InversorFake` + `test_solarman_client.py` | U1 |
| **U3** | `telemetria.py` + `test_telemetria.py` + `SensorFamiliaValidada` | U1, U2 |
| **U4** | `services.py` + `services.yaml` + `test_probe_registers.py` | U1, U2, U3 |
| **U5** | `entidades_solarman.py` (5 sensores de telemetría + 1 diagnóstico) | U3 |
| **U6** | Deuda F1 (#1, #2, #5, #7) + `test_mantenimiento.py` | — |
| **U7** | `config_flow.py` con `reconfigure` + traducciones | — |
| **U8** | `__init__.py` (servicio, Store, revalidación al arranque, job 04:30) | U4, U7 |
| **U9** | `SPEC.md`, `README.md`, `docs/INSTALACION.md` | U1–U8 |
| **U10** | Cierre: bump `0.8.0`, regresión completa, `RESULTADO_F2.md` | U1–U9 |

### Criterios de aceptación y commits por unidad

**U1 — Mapa de registros**

- **Ficheros**: `registers.yaml`, `mapa_registros.py`, `tests/test_mapa_registros.py`, `const.py` (constante `STORE_KEY_PROBE`).
- **Criterio**: override de usuario, `block_size ≤ 16`, `TODO` como warning, two's complement, `invertir_signo`, `plausibility`, `probe`.
- **Commit**: `feat(F2-U1): mapa de registros configurable con override, two's complement e invertir_signo`

**U2 — Cliente Solarman**

- **Ficheros**: `solarman_client.py`, `tests/fake_inversor.py`, `tests/test_solarman_client.py`.
- **Criterio**: `leer_telemetria(familia)`, sin `escribir_registros`, sin `connect()`, backoff acotado, troceado ≤ 16, `InversorFake` con `connect()`/`disconnect()`.
- **Commit**: `feat(F2-U2): cliente Solarman con familia por parámetro, signo Deye y backoff acotado`

**U3 — Coordinador de telemetría + sensor diagnóstico**

- **Ficheros**: `telemetria.py`, `tests/test_telemetria.py`, `sensor.py` (instanciación condicional), `__init__.py` (carga de Store).
- **Criterio**: `UpdateFailed` antes de Modbus, `SensorFamiliaValidada` con atributos, revalidación al arranque.
- **Commit**: `feat(F2-U3): coordinador de telemetría y sensor diagnóstico con guard antes de Modbus`

**U4 — Servicio `probe_registers`**

- **Ficheros**: `services.py`, `services.yaml`, `tests/test_probe_registers.py`, `__init__.py` (registro), `translations/es.json`.
- **Criterio**: `supports_response`, tolerancia ±2 %, plausibilidad, reset, `familia` opcional, persistencia en Store.
- **Commit**: `feat(F2-U4): servicio probe_registers con tolerancia, plausibilidad y reset`

**U5 — Entidades de telemetría**

- **Ficheros**: `entidades_solarman.py`, `sensor.py` (añadir las 6 entidades).
- **Criterio**: 6 sensores, `device_class`/`state_class`, `SensorPotenciaBateria` con `+descarga/−carga`.
- **Commit**: `feat(F2-U5): seis entidades de telemetría con device_class y sensor diagnóstico`

**U6 — Deuda F1**

- **Ficheros**: `cliente_omie.py`, `coordinador.py`, `sensor.py`, `tarifa.py`, `const.py`, `tests/test_mantenimiento.py`.
- **Criterio**: `limpiar_cache_antigua`, backoff REData, log mejorado, tarifa en `entry.data`.
- **Commit**: `chore(F2-U6): deuda F1 (#1, #2, #5, #7)`

**U7 — `reconfigure`**

- **Ficheros**: `config_flow.py`, `const.py`, `translations/es.json`, `strings.json`, `tests/test_config_flow.py`.
- **Criterio**: edita solo campos de telemetría, `VERSION` en 1.
- **Commit**: `feat(F2-U7): reconfigure del config flow para parámetros de telemetría`

**U8 — `__init__.py` completo**

- **Ficheros**: `__init__.py`.
- **Criterio**: registro de servicio, Store, revalidación, job 04:30, unload limpio.
- **Commit**: `feat(F2-U8): registro de servicio, Store, revalidación al arranque y job de limpieza`

**U9 — Documentación**

- **Ficheros**: `SPEC.md`, `README.md`, `docs/INSTALACION.md`.
- **Criterio**: SPEC, README y `INSTALACION.md` actualizados con la convención de signos Deye, `probe_registers`, override de YAML.
- **Commit**: `docs(F2-U9): actualización de SPEC, README y guía de instalación`

**U10 — Cierre**

- **Ficheros**: `manifest.json`, `docs/RESULTADO_F2.md`.
- **Criterio**: `manifest.json` con `0.8.0`, regresión completa, `RESULTADO_F2.md`.
- **Commit**: `chore(F2-U10): cierre F2 — bump 0.8.0, regresión completa y resultado`

---

## 11. Reglas de parada

Detente y pregunta si:

- Encuentras una inconsistencia entre `SPEC.md` y `PLAN_F2.md`.
- Una unidad requiere una decisión de diseño no prevista.
- Los tests de F1 empiezan a fallar y no está claro si es regresión tuya o del entorno.
- Una dependencia externa no está disponible (`pysolarmanv5`, `pytest-homeassistant-custom-component`).
- La librería `pysolarmanv5` no expone los métodos esperados (ya corregido en D17: no se usa `connect()`).

**En cada parada**: documenta la decisión propuesta en `docs/PLAN_F2.md` (sección "Notas de implementación") antes de tocar código. Después reanuda.

---

## 12. Reglas de commit

- Un commit por unidad, **exactamente** el mensaje indicado en §10.
- El commit **solo** incluye los ficheros de esa unidad. Nada de "chore: varios cambios".
- Antes de cada commit: `pytest tests/ -q` y `ruff check custom_components/optimizador_bateria_omie/` en verde.
- Si una unidad rompe tests anteriores, **no se commitea**. Se arregla primero.

---

## 13. Verificación por unidad (checklist)

Tras cada unidad, antes del commit, verifica:

- [ ] `pytest tests/ -q` en verde (incluye regresión de F1).
- [ ] `ruff check custom_components/optimizador_bateria_omie/` limpio.
- [ ] `git diff` limitado a los ficheros de la unidad.
- [ ] No hay `print()` ni `TODO` sin comentario explicativo.
- [ ] Docstrings actualizados en clases/métodos nuevos.
- [ ] Logs sin datos sensibles.
- [ ] Mensaje de commit según §10.

---

## 14. Fin de F2

Cuando U10 esté commiteada:

1. Ejecuta una última regresión completa: `pytest tests/ -q` y `ruff check`.
2. Pega en `docs/RESULTADO_F2.md` el resumen de la suite (número de tests por fichero, tiempo total).
3. Informa al usuario con:
   - Nº de tests (F1 + nuevos).
   - Nº de commits de la fase.
   - Estado del sensor `optimizador_bateria_omie_familia_validada` (debe ser `ninguna` hasta que el usuario ejecute `probe_registers` contra hardware real).
   - Lista de verificaciones manuales que el usuario debe hacer:
     - Validación de familia con la APP (`probe_registers` con SOC real).
     - Comprobación de valores de SOC, potencia y corriente contra la pantalla/APP.
     - Verificación del **signo de potencia** (+descarga/−carga) en un ciclo real de carga y descarga.
     - Override de `registers.yaml` si necesita personalizar direcciones.
4. **No** ejecutes `probe_registers` contra el hardware real. Es una acción del usuario.
---

## Notas de implementacion

### NI-1 (U1, corregido por v1.3) - Pin de pysolarmanv5

El pin `pysolarmanv5>=1.1.0,<2.0.0` de v1.2 no existia en PyPI (solo 2.3.0-3.0.6).
v1.3 corrige a `>=3.0.6,<4.0.0` (D15) y fija D17 (sin `connect()` en el cliente real)
y D18 (`InversorFake` con `connect()`/`disconnect()` para tests). Instalada la 3.0.6
en el venv de desarrollo para verificacion.

### NI-2 (U1) - Ajustes de U1 tras la publicacion de v1.3

U1 se commiteo antes de existir v1.3 con el mensaje de la directiva original
("...override de usuario...") y `signo_semantico: "+carga/-descarga"`. Ajustes
aplicados en amend del commit local (aun sin push):
- `registers.yaml`: `signo_semantico` a `"+descarga/-carga"` (D16), ambas familias.
- `mapa_registros.py`: INFO "Usando override de usuario" al cargar override (3.2).
- Mensaje de commit al de v1.3 10: `feat(F2-U1): mapa de registros configurable con
  override, two's complement e invertir_signo`.

### NI-3 (U2) - Ubicacion de la dataclass TelemetriaInversor

U2 exige `leer_telemetria(familia) -> TelemetriaInversor` y la dataclass se
documenta en telemetria.py (3.4, creado en U3). Decision: crear `telemetria.py`
en U2 conteniendo SOLO la dataclass `TelemetriaInversor`; U3 anade a ese mismo
fichero `CoordinadorTelemetria`. Respeta la ubicacion del plan y la necesidad
de U2; el commit de U2 incluira `telemetria.py`.

### NI-4 (U3) - Decisiones de ubicacion no previstas en la lista de U3

1. `const.py` necesita en U3 (no en U7) las claves que `telemetria.py` lee de
   `entry.data` y la señal de actualizacion: `CONF_TELEMETRY_SOURCE`,
   `CONF_INTERVALO_TELEMETRIA`, `CONF_ENTITY_ID_SOC/PBAT/CONSUMO/FV`,
   `SENSOR_FAMILIA_VALIDADA`, `SIGNAL_VALIDACION_CAMBIADA`. Se anaden a const.py
   en el commit de U3 (U7 anadira el resto de CONF_*).
2. `entidades_solarman.py` se CREA en U3 con solo `SensorFamiliaValidada`
   (criterio M10 de U3); U5 anade a ese mismo fichero los 5 sensores de
   telemetria.
3. La gestion del Store de validacion vive en `telemetria.py` como clase
   `GestorValidacion` (cargar/guardar/borrar + revalidacion m1 + senal
   dispatcher al cambiar). Es la unica pieza nueva sin nombre en el plan;
   la alternativa (duplicarla entre services/__init__/sensor) es peor.
4. `__init__.py` (alcance U3 "carga de Store") crea `MapaRegistros` +
   `GestorValidacion`, ejecuta la revalidacion m1 y los deja en
   `hass.data[DOMAIN]`; `sensor.py` los consume desde ahi. El registro del
   servicio y el job 04:30 siguen siendo de U4/U8.
5. `sensor.py` instancia el WRAPPER `ClienteSolarman` siempre que la fuente sea
   `solarman_modbus`; la CONEXION Modbus sigue siendo perezosa (se crea en la
   primera lectura) y el guard C5 impide toda lectura sin familia validada.
   Asi no hace falta recrear el cliente al validar familia via servicio; el
   efecto neto ("sin trafico Modbus sin validacion") es el del plan.

### NI-5 (U4) - Decisiones no previstas en la lista de U4

1. `const.py` necesita en U4 (no en U7): `SERVICE_PROBE_REGISTERS` y
   `TOLERANCIA_PROBE_PCT = 2.0` (M1 parametrizada como constante de modulo).
2. `telemetria.py`: propiedad publica `cliente` en `CoordinadorTelemetria`;
   el servicio obtiene el cliente Modbus de `hass.data` via el coordinador.
   Si la fuente es `ha_entities` (sin cliente), el servicio crea un
   `ClienteSolarman` temporal solo para sondear (conexion perezosa).
3. Guard de division por cero: con `expected == 0` la desviacion solo es 0
   si `leido == 0` (en otro caso FUERA_TOLERANCIA).
4. El nucleo del servicio es `ejecutar_probe(hass, entry, datos_llamada)`
   (funcion pura testeable); el handler registrado en HA es un envoltorio.
5. Tras validar o resetear se llama `coordinador.async_request_refresh()`
   (recarga de telemetria del plan; con C5 el reset la deja unavailable).
