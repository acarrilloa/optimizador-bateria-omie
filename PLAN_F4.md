# PLAN_F4 — Executor de control del inversor (vía servicios Solarman)

> **Estado**: **v1.1 — CONFIRMADO por el usuario**. Implementable por unidades con un commit por unidad (pytest + ruff en verde antes de cada commit).
> **Base**: `main` con F1+F2+F3 en producción (manifest **0.9.1**, 227 tests en verde, `ruff` limpio). HA 2026.10.1, Python 3.14.
> **Versión de cierre prevista**: `0.10.0`.
> **Referencia**: SPEC.md **v0.9.4** — RF-03, RF-04, §5.11 (Executor), §4.4 (máquina de estados), §9 (seguridad), §12.3 (deuda #5).
> **Patrón**: PLAN_F2V1.3.md (unidades + reglas de commit) y PLAN_F3.md (decisiones D + criterios de salida).
>
> **Cambios v1.0 → v1.1 (confirmación del usuario)**:
> 1. **D3 corregida**: `corriente_carga` (210) **NO** se escribe con la corriente del tramo — limita la carga de batería desde **cualquier fuente** (PV + red) y estrangularía la carga solar nocturna. 210 = `potencia_max_carga` del plan convertida a A (valor constante entre tramos, idempotente); 230/232 gobiernan solo la carga desde red. AUTOCONSUMO deja 210 en ese mismo valor. Test: 210 nunca por debajo de la corriente equivalente a `potencia_max_carga`.
> 2. **Validación 218 > 217**: `soc_recuperacion` siempre > `soc_minimo` (semántica Deye: recuperación > corte). `vol.Invalid` en el config flow + clamp en el executor.
> 3. **Q1 aceptada**: `device_id_solarman` vacío → resolución automática del primer device de la integración Solarman, ERROR explícito si falla; payload aislado en `EscritorSolarman`.
> 4. **Q2 aceptada**: `voltaje_bateria_nominal` default 51,2 V, editable 40–60.
> 5. **Q3 resuelta a la alternativa**: la auditoría JSONL va en **`config/optimizador_bateria_omie/auditoria_escrituras.jsonl`** (sobrevive a HACS, consistente con el override de registers.yaml); se crea el directorio si no existe.
> 6. **Q4 aceptada**: estados del sensor tal como se definen (inactivo/dry_run/activo/fallback_bloqueado).
> 7. **Rango de `precio_max_compra` ampliado a 0–1 €/kWh** en la entidad `number` (el precio efectivo con peajes e impuestos puede acercarse a 0,35).
> 8. **Manifest**: bump directo 0.9.1 → 0.10.0 al cierre; la SPEC §11 se corrige al cierre para reflejar la versión real.
>
> **Incoherencias detectadas y cómo se tratan**:
> - El `registers.yaml` **actual no tiene la sección `control`** que muestra SPEC §5.3 → la añade la U1 con los 6 registros confirmados (210, 211, 230, 232, 217, 218).
> - El manifest real está en **0.9.1** (SPEC §11 dice 0.9.4) → bump directo a **0.10.0** al cierre.
> - AGENTS §1.2 exige "mapa validado con `probe_registers` antes de escrituras": esa regla aplica a la vía `solarman_modbus` (inactiva). En F4 el habilitador es **`control_activo=on`** + límites del YAML + resto de salvaguardas (ver D2).

---

## 1. Objetivo y alcance

Implementar el **Executor** que aplica el plan de F3 al inversor Deye **escribiendo vía los servicios de la integración oficial Solarman** (`solarman.write_single_register` / `solarman.write_multiple_registers`; el `write_multiple_holding_registers` está deprecado y no se usa). Verificación por relectura con `solarman.read_holding_registers`. **Sin gateway RS485, sin pysolarmanv5 operativo.**

Con control cerrado, todas las escrituras quedan bajo salvaguardas: habilitador manual, límites del YAML, write-verify, rate limiter, watchdog, guard de potencia contratada, bloqueo por fallo del inversor, activación gradual y auditoría JSONL.

**Unidades del alcance (las 10 confirmadas por el usuario)**:

1. `executor.py`: `EjecutorControl` + envoltorio `EscritorSolarman` (`hass.services.async_call("solarman", ...)`).
2. Write-verify con relectura a los `latencia_verify_s` (default 3 s) + verify soft (`verificado: parcial`) + fallback tras 3 fallos consecutivos.
3. Rate limiter: máx 12 escrituras/hora reales (configurable 1–48); superado → aplazar al siguiente tramo con WARNING.
4. Watchdog: telemetría obsoleta (> 2× intervalo) o plan ausente en cambio de tramo → no escribir; con carga activa → fallback.
5. Guard dinámico de potencia contratada (5,75 kW): > 95 % → reducir corriente de carga; ≥ 100 % → 0 A inmediato.
6. Bloqueo por fallo del inversor (registros Deye 103–106) → no escribir; con carga activa → fallback; lista vacía → bloqueo inactivo con aviso único.
7. Entidades F4: `switch.control_activo` (default OFF) + 8 `number` calientes + sensor diagnóstico de estado de control.
8. Servicios `force_mode(mode, until)` y `release_manual`.
9. `modo_activacion` gradual|completo (default **gradual**): solo CARGA_RED en valle 2.0TD se escribe de verdad; el resto se simula y loguea.
10. **Deuda #5**: peajes/cargos/IE/IVA/potencia de la tarifa pasan a `entry.data` (defaults = valores actuales confirmados).
11. Auditoría JSONL append-only: `data/auditoria_escrituras.jsonl` con `{ts, tramo, accion, registros, ejecutada, verificado, latencia, motivo, guard}`.

**Fuera de alcance**: `solarman_modbus` operativo (deuda #9), LP/ML (F5), auditoría de ahorro real (F5), dashboard (F6).

---

## 2. Decisiones de diseño

| # | Decisión | Motivo |
|---|---|---|
| D1 | **`EscritorSolarman`** aísla todos los detalles del servicio: `escribir(addr, valor)`, `escribir_bloque(direcciones, valores)` y `leer(addr, qty)` envuelven `hass.services.async_call("solarman", ...)` con `blocking=True` (+ `return_response=True` en la lectura). **Ningún otro módulo llama a `hass.services` del dominio solarman** | Si el payload del servicio varía entre versiones de la integración Solarman, se ajusta en un solo sitio (riesgo R1) |
| D2 | **Habilitador**: `switch.optimizador_bateria_omie_control_activo`, **default OFF**. Con OFF el executor **simula** (traduce, loguea y audita con `ejecutada: false`) pero **nunca escribe**. El guard de familia validada de AGENTS §1.2 queda circunscrito a la vía `solarman_modbus` (inactiva); en F4 el control de acceso es el switch + límites YAML + salvaguardas | El prompt confirma este modelo; `probe_registers` no aplica a la vía servicios |
| D3 | **Traducción acción → registros** (direcciones SOLO del YAML, tipo `control`). **Corrección v1.1**: `corriente_carga` (210) limita la carga de batería desde **cualquier fuente** (PV + red) → se escribe SIEMPRE con la corriente equivalente a `potencia_max_carga` del plan (valor constante entre tramos, idempotente), **nunca** con la corriente de un tramo. MANTENER → no escribe. AUTOCONSUMO → `habilitar_carga_red=0`, `corriente_carga_red=0` (valores seguros; 210 queda en su valor constante). CARGA_RED → `habilitar_carga_red=1`, `corriente_carga_red=I_tramo` (230), 210 en su valor constante. VENTA_RED → `habilitar_carga_red=0`, `corriente_carga_red=0`, `corriente_descarga=I_tramo` (211). `soc_minimo`(217) y `soc_recuperacion`(218) se escriben **solo al activar el control o al cambiar `soc_min`/`soc_recuperacion`**, no cada tramo; **218 > 217 siempre** (validado en config flow y con clamp en el executor) | 210/230/232 tienen semánticas distintas: 230/232 gobiernan la carga desde red; dejar 210 en I nocturno estrangularía la carga solar diurna |
| D4 | **Conversión W → A**: `corriente = potencia_w / voltaje_bateria_nominal` con **nuevo parámetro `voltaje_bateria_nominal` (default 51,2 V, Docan 51,2 V 300 Ah)**, editable por reconfigure. Se recorta SIEMPRE a `[min, max]` del YAML (0–135 A) y se redondea a entero (los registros son enteros) | El plan trabaja en W y los registros de control en A; sin tensión no hay traducción. Default = dato de SPEC §2 |
| D5 | **Write-verify**: tras cada escritura real, `await asyncio.sleep(latencia_verify_s)` (default 3, rango 1–15) y relectura vía `solarman.read_holding_registers`. **Coincidencia exacta** del valor entero (tolerancia 0; parametrizable `verify_tolerancia`, default 0). **Si la relectura falla (error del servicio) → `verificado: "parcial"` y NO cuenta como fallo** (verify soft). Si el valor leído difiere → fallo. **3 fallos consecutivos → fallback**: escritura de valores seguros (232=0, 230=0), `control_activo` a OFF automático + `persistent_notification.create`. Reactivación solo manual (encender el switch) | Prompt del usuario; el "parcial" evita falsos fallbacks por fallos de lectura |
| D6 | **Rate limiter**: ventana deslizante de 1 h; cuenta **cada llamada real a un servicio de escritura** (1 registro = 1 llamada; un tramo CARGA_RED son 3 llamadas). Máx `max_escrituras_hora` (default 12, rango 1–48). Superado → **no escribe** en ese tramo, WARNING y aplica en el siguiente cambio de tramo. **Las escrituras de fallback y de activación (217/218) no cuentan** pero se auditan | Seguridad primero; la idempotencia (D8) limita las escrituras reales |
| D7 | **Watchdog**: antes de escribir se comprueba (a) telemetría con `ts_actualizacion` más reciente que `2 × intervalo_telemetria_s`, (b) plan presente y tramo actual disponible, (c) SOC real disponible. Falta cualquiera → no escribe; si el último estado aplicado tenía **carga desde red activa** (232=1 o 230>0) → fallback inmediato (valores seguros) | RF-06/SPEC §9; no escribir a ciegas |
| D8 | **Idempotencia**: si los valores objetivo del tramo son idéntimos a los últimos aplicados (por registro), no se reescriben ni cuentan en el rate limiter | Reduce escrituras reales y respetan el límite de 12/h |
| D9 | **Guard dinámico de potencia contratada**: importación actual = `entity_id_potencia_red` (registro Deye **169**, signed W, **>0 = importación**) si está configurado; si no, **cálculo derivado** `consumo_casa − produccion_fv − potencia_bateria` (convención Deye +descarga/−carga). **Normalización interna: importación positiva**. Si la entidad configurada fuera de los registros 424-425 (medidor externo, convención OPUESTA: compra negativa), se normaliza también a importación positiva y se documenta. `importación > 95 % de potencia_contratada` → corriente de carga red proporcional al margen restante; `≥ 100 %` → 0 A inmediato (solo 232=0/230=0) | RF-04; el prompt confirma ambas fuentes y la normalización |
| D10 | **Bloqueo por fallo**: `entity_ids_fallo` (lista de entity_ids, default vacía) — si **alguna** es ≠ 0 → no escribir; con carga activa → fallback. Lista vacía → bloqueo **inactivo con WARNING una sola vez** por arranque | Registros Deye 103-106 (F07-F64); 101-102/117-118 opcionales si el usuario los añade a la lista |
| D11 | **`modo_activacion`** (`gradual`\|`completo`, default **gradual**): en gradual solo se ejecutan de verdad las **CARGA_RED en tramo valle 2.0TD** (período del tramo actual vía `ModeloTarifa.obtener_periodo`, reutilizando el `modelo_tarifa` del `CoordinadorDatosOmie`); VENTA_RED y CARGA_RED fuera de valle se **simulan** (traducidas, logueadas y auditadas con `ejecutada: false, motivo: "gradual"`). En completo se ejecuta todo lo que pase las salvaguardas | Activación progresiva confirmada; el valle es el escenario más seguro |
| D12 | **Estados del control** (sensor diagnóstico): `inactivo` (control_activo OFF, todo simulado) · `dry_run` (control_activo ON pero la acción actual se simula por gradual) · `activo` (escribiendo de verdad en el tramo actual) · `fallback_bloqueado` (3 fallos de verify; requiere intervención manual). Atributos: `motivo`, `fallos_consecutivos`, `escrituras_ultima_hora`, `ultimo_verify`, `modo_manual` | El prompt pide exactamente estos 4 estados |
| D13 | **Modo manual** (`force_mode(mode, until)`): `mode` ∈ AUTOCONSUMO/CARGA_RED/VENTA_RED/MANTENER con `until` (datetime, obligatorio, debe ser futuro). El executor aplica el modo manual **respetando TODAS las salvaguardas** (límites YAML, guard, fallo, watchdog, rate limiter, gradual). Expira **solo** al llegar `until` (comprobado en cada cambio de tramo; un tramo manual puede cruzar varios tramos). `release_manual()` lo libera inmediatamente. Estado en **memoria** (un reinicio de HA lo limpia → vuelve a modo plan, valor seguro). `until` en el pasado → `vol.Invalid` | Prompt del usuario; en memoria es la opción segura ante reinicios |
| D14 | **Auditoría JSONL append-only** (v1.1: Q3 resuelta a la alternativa): **`config/optimizador_bateria_omie/auditoria_escrituras.jsonl`** (junto al override de `registers.yaml`; sobrevive a actualizaciones de HACS; el directorio se crea si no existe), una línea por escritura real o simulada: `{ts, tramo, accion, registros: {addr: valor}, ejecutada, verificado, latencia, motivo, guard}`. Escritura en executor con `async_add_executor_job`, `mode="a"`, `encoding="utf-8"`. Sin rotación en F4 (F6 la valora) | Prompt + decisión del usuario; coherente con la zona de datos persistente del usuario |
| D15 | **Deuda #5 — tarifa en `entry.data`**: `ConfiguracionTarifa.desde_entry(entry)` lee `peaje_acceso_punta/llano/valle`, `cargo_sistema_punta/llano/valle`, `impuesto_electrico_pct`, `iva_pct`, `aplicar_impuestos_precio` con defaults = **valores actuales confirmados** de `tarifa.py` (0,033261/0,016409/0,000077 y 0,064292/0,012858/0,003215, IE 0,051127, IVA 0,21). La **potencia contratada** se unifica en el parámetro F3 existente `potencia_contratada` (W, default 5750): `ConfiguracionTarifa` la deriva (÷1000) y la usa solo a efectos de guard/plan, eliminando la duplicación 5,75 kW ↔ 5750 W. `ModeloTarifa` se instancia con esa configuración en `__init__.py` | `entry.data.get` → la entrada de producción no se rompe; un solo origen para la potencia |
| D16 | **Numbers calientes**: los 8 `number` escriben **directamente en `entry.data`** (`entry.data[clave] = valor`) y notifican al `CoordinadorPlan` (recálculo inmediato del plan, ≤ 60 s) y al `EjecutorControl` (217/218 si toca). No pasa por `reconfigure` (que recarga la entrada); mutación in-place documentada como decisión pragmática: HA no ofrece API oficial para parámetros en caliente y la entrada no se recrea | Prompt del usuario ("escriben en entry.data y disparan recálculo"); coherente con M3 de F3 |
| D17 | **Disparo del executor**: se registra como listener del `CoordinadorPlan` (`async_registrar_callback`); cada recálculo (tramo cada 15 min, precios, desviación SOC, festivo, numbers, `regenerate_plan`) → `async_aplicar_tramo()` con el `tramo_actual()`. Nunca escribe fuera de ese ciclo ni desde el event loop sin asyncio.create_task | Reutiliza la maquinaria de replanificación de F3 sin duplicar timers |
| D18 | **`control_activo` OFF → fallback_bloqueado no aplica**: el switch es la única vía de escritura; OFF por el usuario o automático cierra todo. Al encenderlo de nuevo: reset de `fallos_consecutivos`, escritura de 217/218 y aplicación inmediata del tramo (bajo salvaguardas) | Intervención manual del prompt |
| D19 | Sin paquetes nuevos; `pysolarmanv5` permanece en requirements (vía inactiva). Plataforma nueva `number` en `PLATFORMS` (`["sensor", "switch", "number"]`) | AGENTS: confirmación previa para paquetes; no hace falta ninguno |

---

## 3. Ficheros a crear

### 3.1 `custom_components/optimizador_bateria_omie/executor.py`

- **`EscritorSolarman`** (envoltorio, D1):
  - `async escribir(addr: int, valor: int) -> None` → `solarman.write_single_register`.
  - `async escribir_bloque(addr: int, valores: list[int]) -> None` → `solarman.write_multiple_registers` (bloques contiguos ≤ 16).
  - `async leer(addr: int, qty: int = 1) -> list[int]` → `solarman.read_holding_registers` con `return_response=True`.
  - Payload con el dispositivo configurado (`device_id_solarman`; ver §9-Q1). `ErrorEscrituraSolarman` si el servicio falla.
- **`EjecutorControl`**:
  - `__init__(hass, entry, mapa, coordinador_plan, coordinador_telemetria, coordinador_precio, escritor=None)` — `escritor` inyectable para tests.
  - `async async_setup()` — registra listener en el `CoordinadorPlan` (D17) y aplica el tramo (simulado si OFF).
  - `async async_aplicar_tramo(motivo: str)` — pipeline: control_activo → modo manual → watchdog (D7) → fallo (D10) → traducción (D3-D4) → gradual (D11) → guard (D9) → idempotencia (D8) → rate limiter (D6) → escrituras → verify (D5) → auditoría (D14) → estado (D12).
  - `async async_set_control_activo(on: bool)` (D18), `async force_mode(mode, until)`, `async release_manual()` (D13).
  - Estado interno: `estado`, `fallos_consecutivos`, `escrituras` (timestamps), `modo_manual`, `ultimo_aplicado` (por registro), `aviso_fallo_emitido` (D10, una vez).
  - `async _async_fallback(motivo: str)` — valores seguros (232=0, 230=0 del YAML), switch a OFF, `persistent_notification.create`, auditoría; **no** sujeto a rate limiter (D6).
  - Propiedades para entidades: `estado`, `estado_detalle` (dict de atributos del sensor diagnóstico).

### 3.2 `custom_components/optimizador_bateria_omie/entidades_control.py`

| Clase | unique_id | Tipo | Detalle |
|---|---|---|---|
| `SwitchControlActivo` | `optimizador_bateria_omie_control_activo` | switch | default **OFF**; `async_turn_on/off` → `executor.async_set_control_activo` |
| `NumberParametroPlanner` (base) | — | number | 8 instancias: `precio_max_compra` (**0–1 €/kWh**, v1.1), `precio_min_venta` (0–1), `margen_arbitraje` (0–0,1), `soc_min` (5–50 %), `soc_max` (50–100 %), `potencia_max_carga` (0–6900 W), `potencia_max_descarga` (0–6900 W), `eficiencia_round_trip` (70–95 %). `mode: BOX`; escriben `entry.data` + recálculo inmediato (D16) |
| `SensorEstadoControl` | `optimizador_bateria_omie_estado_control` | sensor (diagnóstico) | `inactivo`/`dry_run`/`activo`/`fallback_bloqueado` + atributos de D12 |

Todas con `device_info` compartido (F1/F2/F3) y repintado por callback del executor.

### 3.3 `custom_components/optimizador_bateria_omie/number.py`

- Plataforma `number`: crea el switch ya vive en `switch.py`; aquí solo las 8 `NumberParametroPlanner`.

### 3.4 Tests

- **`tests/test_executor.py`** (el más extenso): con `hass.services.async_call` mockeado:
  - traducción acción → registros (direcciones del YAML, nunca hardcodeadas en el test);
  - **dry-run**: control_activo OFF → ninguna llamada de escritura, auditoría con `ejecutada: false`;
  - **gradual**: CARGA_RED en valle se ejecuta, en llano/punta se simula; VENTA_RED simulada; `completo` ejecuta todo;
  - **verify OK** (relectura coincide) / **verify con valor distinto** (cuenta fallo; 3 consecutivos → fallback + notification + switch OFF) / **relectura con error de servicio** → `verificado: "parcial"`, sin contar fallo;
  - **rate limiter**: superado el límite no escribe y audita el aplazamiento;
  - **watchdog**: telemetría obsoleta y plan ausente → no escribe; con carga activa → fallback;
  - **guard 95 %**: reduce corriente; **100 %**: 0 A inmediato; con y sin `entity_id_potencia_red` (derivado);
  - **bloqueo por fallo**: entidad ≠ 0 → no escribe; carga activa → fallback; lista vacía → aviso único;
  - **idempotencia**: mismos valores objetivo → sin escrituras;
  - **límites**: nunca escribe corrientes fuera de `[min, max]` del YAML;
  - **217/218**: se escriben al activar, con valores dentro de `[10, 100]` (AGENTS §1.3) y **218 > 217 siempre** (clamp del executor);
  - **210 nunca por debajo de la corriente equivalente a `potencia_max_carga`** (corrección 1: valor constante entre tramos, también en AUTOCONSUMO).
- **`tests/test_force_mode.py`**: modo manual aplicado con salvaguardas; expira solo en `until` (tramos posteriores lo respetan); `release_manual` inmediato; `until` pasado → Invalid; reinicio (nuevo executor) → sin modo manual.
- **`tests/test_entidades_control.py`**: switch default OFF y encendido/apagado; 8 numbers escriben `entry.data` y disparan recálculo (mock del `CoordinadorPlan`); sensor de estado con los 4 valores y atributos.
- **`tests/test_tarifa_entry_data.py`** (deuda #5): `ConfiguracionTarifa.desde_entry` con defaults de la entrada de producción (sin claves) = valores actuales; claves presentes los sobreescriben; `ModeloTarifa` con configuración custom produce precios efectivos coherentes; `potencia_contratada` (W) única fuente.

---

## 4. Ficheros a modificar

| Fichero | Cambio |
|---|---|
| `registers.yaml` | **Sección `control` de la familia `estandar`** (SPEC §5.3, confirmada contra manuales Deye): `corriente_carga {addr: 210, min: 0, max: 135, unit: A, scale: 1}`, `corriente_descarga {addr: 211, ...}`, `corriente_carga_red {addr: 230, ...}`, `habilitar_carga_red {addr: 232, valores: [0, 1]}`, `soc_minimo {addr: 217, min: 10, max: 100}`, `soc_recuperacion {addr: 218, min: 10, max: 100}` + comentarios documentales (169 convención importación positiva; 424-425 opuesta; 103-106 fallos) |
| `mapa_registros.py` | Sin cambios funcionales: `parametros(familia, funcion, tipo="control")` ya funciona. Se añade validación ligera en `_validar`: si existe `control`, cada entrada debe tener `addr` (y `min/max` o `valores`); nunca rompe la carga de la telemetría |
| `const.py` | `CONF_MODO_ACTIVACION`, `CONF_LATENCIA_VERIFY_S`, `CONF_VERIFY_TOLERANCIA`, `CONF_MAX_ESCRITURAS_HORA`, `CONF_ENTITY_ID_POTENCIA_RED`, `CONF_ENTITY_IDS_FALLO`, `CONF_VOLTAJE_BATERIA_NOMINAL`, `CONF_SOC_RECUPERACION`, `CONF_DEVICE_ID_SOLARMAN`, `CONF_*` de tarifa (peajes/cargos/IE/IVA), `SERVICE_FORCE_MODE`, `SERVICE_RELEASE_MANUAL`, unique_ids F4 (`SWITCH_CONTROL_ACTIVO`, `SENSOR_ESTADO_CONTROL`, prefijo `NUMBER_*`), estados de control, `MODO_ACTUAL_GRADUAL/COMPLETO` + sus defaults |
| `tarifa.py` | `ConfiguracionTarifa.desde_entry(entry)` (D15); los defaults de clase siguen siendo los valores confirmados |
| `coordinador_plan.py` | Pequeño ajuste: reconstruir `ParametrosPlanner.desde_entry(entry)` en cada `async_recalcular` (los numbers calientes mutan `entry.data`; D16) — hoy el planificador se instancia una vez con parámetros congelados |
| `services.py` + `services.yaml` | `force_mode` (`mode` select + `until` datetime, `vol.Invalid` si es pasado) y `release_manual` (sin campos), núcleo testeable `ejecutar_force_mode`/`ejecutar_release_manual` |
| `config_flow.py` | Campos nuevos en `CONFIG_SCHEMA` (user y reconfigure, todos `vol.Optional` con default → entrada de producción intacta): §5. `entity_ids_fallo` como texto separado por comas (reutiliza el patrón `festivos_locales`). **Validación 218 > 217** (`vol.Invalid` si `soc_recuperacion <= soc_min`, corrección 2) |
| `__init__.py` | Plataformas `["sensor", "switch", "number"]`; construir `EjecutorControl` tras el `CoordinadorPlan` y exponerlo en `hass.data[DOMAIN][entry.entry_id]["executor"]`; `ConfiguracionTarifa.desde_entry` para el `ModeloTarifa` (D15); unload descarga el executor |
| `switch.py` | Añade `SwitchControlActivo` (la plataforma switch ya existe para `festivo_hoy`) |
| `sensor.py` | Añade `SensorEstadoControl` (desde `hass.data[...]["executor"]`) |
| `strings.json` + `translations/es.json` | Etiquetas de los campos nuevos y de las entidades/servicios |
| `manifest.json` | `version` → **0.10.0** (al cierre). Sin requisitos ni dependencias nuevas |
| `SPEC.md` | Al cierre: RF-03/RF-04 ✅ F4, §5.11 implementado, §6.3/§6.4 entidades y servicios, §12.3 deuda #5 ✅, §8 F4 completada, **§11 corregido al manifest real (0.10.0 + dependencies reales)** |

Los sensores y switches de F1–F3 no cambian de comportamiento.

---

## 5. Parámetros nuevos del reconfigure (F4)

| Parámetro | Default | Rango | Uso |
|---|---|---|---|
| `modo_activacion` | `gradual` | gradual\|completo | D11 |
| `max_escrituras_hora` | 12 | 1–48 | D6 |
| `latencia_verify_s` | 3 | 1–15 | D5 |
| `verify_tolerancia` | 0 | 0–5 | D5 (0 = coincidencia exacta) |
| `voltaje_bateria_nominal` | 51.2 | 40–60 V | D4 (W → A) |
| `soc_recuperacion` | 20 | 10–100 % | registro 218 (D3) |
| `entity_id_potencia_red` | `""` | entity_id | D9; vacío → cálculo derivado |
| `entity_ids_fallo` | `""` (lista vacía) | texto "a, b, c" | D10; vacío → bloqueo inactivo con aviso |
| `device_id_solarman` | `""` | device_id | D1; vacío → resolución automática (§9-Q1) |
| `peaje_acceso_punta/llano/valle` | 0.033261 / 0.016409 / 0.000077 | €/kWh | deuda #5 |
| `cargo_sistema_punta/llano/valle` | 0.064292 / 0.012858 / 0.003215 | €/kWh | deuda #5 |
| `impuesto_electrico_pct` | 0.051127 | 0–0.25 | deuda #5 |
| `iva_pct` | 0.21 | 0–0.30 | deuda #5 |
| `aplicar_impuestos_precio` | true | bool | deuda #5 |

`potencia_contratada` ya existe desde F3 (default 5750 W) y pasa a ser la única fuente de potencia (D15).

---

## 6. Estrategia de tests

- **`hass.services.async_call` mockeado** (`AsyncMock`): nunca se llama a la integración Solarman real ni a hardware. El `EscritorSolarman` es inyectable en `EjecutorControl` para simular latencia/errores sin `asyncio.sleep` real (mock de `asyncio.sleep` para el verify).
- `registers.yaml` real (empaquetado) cargado por `MapaRegistros`: los tests de traducción verifican que las direcciones salen del YAML.
- Cobertura obligatoria (prompt): dry-run, gradual, traducción de registros, verify OK/fallo con fallback, verify soft parcial, rate limiter, watchdog, guard 95 %/100 %, bloqueo por fallo, `force_mode`, entidades, tarifa en `entry.data`.
- Regresión F1+F2+F3 (227 tests) en verde en cada unidad + `ruff check` limpio.

---

## 7. Paquetes nuevos

**Ninguno.** Sin cambios en `requirements` (pysolarmanv5 permanece por la vía inactiva) ni en `dependencies`.

---

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| **Payload/selector de los servicios `solarman.*` distinto al asumido** (p. ej. `device` vs `device_id`, nombres de campos de respuesta) | `EscritorSolarman` aísla el payload (D1); `device_id_solarman` configurable; se verifica contra la integración instalada en la **primera activación real en valle nocturno observada contra la APP** (criterio de salida); los tests no dependen del payload real |
| Valores escritos no aplicados por el inversor aunque el registro refleje el valor (semántica distinta) | Write-verify contra el registro + revisión humana contra la APP los primeros días (criterio de salida) |
| Conversión W→A con tensión real ≠ nominal (rango 40–60 V) | Parámetro editable `voltaje_bateria_nominal`; el recorte a límites YAML protege en el peor caso; verificar magnitudes en la primera semana |
| Latencia real del servicio > 3 s → verify prematuro | `latencia_verify_s` configurable; verify soft "parcial" no provoca fallback espurio |
| Rate limiter demasiado estricto con 3 escrituras por tramo (12/h ≈ 4 tramos) | Idempotencia (D8): solo se escriben cambios reales; el límite es configurable 1–48 |
| `entity_ids_fallo` apuntando a entidades con unidades/formato no numérico | Se parsea como float; no numérico → se ignora con WARNING (no bloquea por un artefacto) |
| Auditoría borrada por actualización HACS | **Resuelto (v1.1)**: vive en `config/optimizador_bateria_omie/`, fuera de `custom_components/` |
| Mutación in-place de `entry.data` por los numbers (no oficialmente recomendada por HA) | Decisión pragmada documentada (D16); los valores también son editables por reconfigure (fuente de verdad persistida en `.storage`) |
| Escrituras en ráfaga al reconectarse el plan | Debounce natural: solo cambia el tramo aplicado si difiere del último (D8) |

---

## 9. Decisiones confirmadas por el usuario (v1.1)

- **Q1 — Dispositivo de los servicios solarman** ✅: `device_id_solarman` vacío → el `EscritorSolarman` resuelve el **primer device de la integración solarman**; si no se puede resolver, ERROR explícito (nunca se inventa). Payload aislado en `EscritorSolarman`.
- **Q2 — Tensión nominal para W→A** ✅: `voltaje_bateria_nominal` default **51,2 V**, editable 40–60.
- **Q3 — Ubicación de la auditoría** ✅: **`config/optimizador_bateria_omie/auditoria_escrituras.jsonl`** (sobrevive a HACS; el directorio se crea si no existe).
- **Q4 — Estados del sensor de control** ✅: `inactivo` = switch OFF (todo simulado); `dry_run` = switch ON pero acción actual simulada por gradual; `activo` = escribiendo de verdad; `fallback_bloqueado` = requiere intervención.

---

## 10. Criterios de salida

- **1 semana sin errores con `control_activo=on` en `modo_activacion=gradual`**.
- **Solo carga desde red en tramos valle 2.0TD** (gradual); VENTA_RED y no-valle simuladas en auditoría.
- **Escrituras verificadas**: relectura correcta; los primeros días contrastadas contra la **APP Solarman** (revisión manual del usuario).
- **Ninguna escritura posible sin `control_activo=on`**, ninguna corriente fuera de los límites del YAML, ninguna escritura con fallo activo (tests + auditoría).
- **Primera activación real en valle nocturno observada contra la APP** (paso del usuario).
- **Deuda #5 cerrada**: tarifa desde `entry.data` con defaults actuales; entrada de producción sin cambios de comportamiento en precios.
- **Auditoría JSONL completa** en `config/optimizador_bateria_omie/auditoria_escrituras.jsonl`: cada escritura real o simulada registrada con `{ts, tramo, accion, registros, ejecutada, verificado, latencia, motivo, guard}`.
- `pytest` en verde (regresión F1+F2+F3 + nuevos: executor, force_mode, entidades_control, tarifa_entry_data) + `ruff check` limpio.
- Manifest **0.10.0**; SPEC actualizada al cierre.

---

## 11. Unidades lógicas (un commit por unidad)

| Unidad | Alcance | Commit |
|---|---|---|
| **U1** | `registers.yaml` (sección control) + validación ligera en `mapa_registros.py` + tests del mapa de control | `feat(F4-U1): sección control en registers.yaml con límites 0-135 A y validación` |
| **U2** | `tarifa.py` `desde_entry` (deuda #5) + `test_tarifa_entry_data.py` + wiring del `ModeloTarifa` en `__init__.py` | `feat(F4-U2): deuda #5 — tarifa 2.0TD desde entry.data con defaults actuales` |
| **U3** | `executor.py`: `EscritorSolarman` + `EjecutorControl` con traducción, límites, idempotencia y dry-run (control OFF) + `test_executor.py` (traducción/dry-run/límites) | `feat(F4-U3): EjecutorControl con EscritorSolarman, traducción del plan y dry-run` |
| **U4** | Salvaguardas: write-verify (OK/fallo/parcial), fallback + notificación, rate limiter, watchdog, guard 95/100 %, bloqueo por fallo + tests | `feat(F4-U4): write-verify, fallback, rate limiter, watchdog, guard y bloqueo por fallo` |
| **U5** | `modo_activacion` gradual/completo + auditoría JSONL + tests | `feat(F4-U5): activación gradual y auditoría JSONL de escrituras` |
| **U6** | `force_mode` / `release_manual` (services.py + services.yaml) + `test_force_mode.py` | `feat(F4-U6): servicios force_mode y release_manual con salvaguardas` |
| **U7** | `entidades_control.py` + `number.py` + `switch.py`/`sensor.py` + ajuste de `coordinador_plan.py` (params en caliente) + `test_entidades_control.py` | `feat(F4-U7): switch control_activo, 8 numbers calientes y sensor de estado de control` |
| **U8** | `config_flow.py` + `const.py` (campos F4) + `strings.json` + `translations/es.json` + tests del reconfigure ampliado | `feat(F4-U8): reconfigure con parámetros F4 (guard, verify, gradual, fallo, tarifa)` |
| **U9** | `__init__.py` (plataformas, wiring del executor, unload) + `SPEC.md` | `feat(F4-U9): wiring del executor y SPEC actualizada` |
| **U10** | Cierre: manifest **0.10.0**, regresión completa, `RESULTADO_F4.md` | `chore(F4-U10): cierre F4 — bump 0.10.0, regresión y resultado` |

Orden elegido para tener deuda #5 (U2) pronto (los precios efectivos alimentan el plan) y el executor en U3–U5 con tests antes de exponer entidades y servicios.

---

## 12. Reglas de parada y de commit

- Un commit por unidad con el mensaje exacto de §11; `pytest -q` + `ruff check custom_components/optimizador_bateria_omie/` en verde **antes** de cada commit; `git diff` limitado a los ficheros de la unidad.
- Detente y documenta en "Notas de implementación" de este plan antes de tocar código si: el payload real de los servicios `solarman.*` difiere del asumido (Q1), una unidad exige una decisión no prevista, o la regresión F1–F3 falla sin causa clara.
- **Nunca** se ejecuta una escritura real contra el inversor desde el desarrollo: la primera activación real (en valle nocturno, observada contra la APP) es un paso del usuario.

---

## 13. Verificación manual que requerirá el usuario (al cierre de F4)

1. Configurar `device_id_solarman` (o dejar la resolución automática) y, si se desea, `entity_id_potencia_red` y `entity_ids_fallo` con las entidades que reflejen 169 y 103–106.
2. Encender `switch.optimizador_bateria_omie_control_activo` en **valle nocturno** y contrastar en la APP Solarman: corriente de carga desde red y SOC siguiendo el plan.
3. Revisar `config/optimizador_bateria_omie/auditoria_escrituras.jsonl` (escrituras reales vs simuladas, verifies) durante la primera semana.
4. Probar `force_mode`/`release_manual` y un fallo simulado (p. ej. cambiar un límite del YAML fuera de rango) para ver el `fallback_bloqueado` y la notificación.

## 14. Notas a adicinar a la SPEC.md y a README.md

1. Registros 228/229 ("Grid charging start voltage/capacity point"): el firmware puede condicionar cuándo la carga desde red (232=1) está realmente activa según tensión/SOC de arranque. No cambia el diseño, pero en la primera activación real conviene leer su valor (APP Solarman) para no interpretar un "no carga" como fallo de escritura. Que U3 los deje documentados.
2. VENTA_RED en modo completo: escribir 211 (límite de descarga) solo permite/limita la descarga; no fuerza la venta. Forzar venta real requeriría los registros TOU Selling (248 enable + 250–262 puntos de tiempo/potencia), que están fuera de alcance — coherente con la decisión de aplazar la venta (Q3 confirmada). Que el plan lo diga explícitamente para que nadie espere venta real en completo en F4.
