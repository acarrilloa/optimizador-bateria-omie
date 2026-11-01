# RESULTADO_F4 — Executor de control del inversor (vía servicios Solarman)

> **Estado**: F4 **implementada** (manifest **0.10.0**), `pytest` en verde (**314 tests**) y `ruff` limpio.
> **Plan ejecutado**: PLAN_F4 v1.1 (correcciones 1–8 del usuario). Commits por unidad lógica U1–U10.
> **Sin ninguna escritura real al inversor durante el desarrollo**: la primera activación real es un paso del usuario.

## Qué se implementó

1. **`registers.yaml` (U1)**: sección `control` de la familia `estandar` con los 6 registros confirmados — 210 corriente_carga, 211 corriente_descarga, 230 corriente_carga_red (0–135 A), 232 habilitar_carga_red ([0,1]), 217 soc_minimo, 218 soc_recuperacion (10–100) — y validación ligera en `mapa_registros.py`. Comentarios documentales: 169 (importación positiva), 424-425 (convención opuesta), 103-106 (fallos).
2. **Deuda #5 (U2)**: peajes/cargos/IE/IVA/potencia de la tarifa pasan a `entry.data` (`ConfiguracionTarifa.desde_entry`, defaults = valores confirmados 2.0TD; `potencia_contratada` W como única fuente). La entrada de producción no cambia de comportamiento.
3. **`executor.py` (U3)**: `EscritorSolarman` (envoltorio de `solarman.write_single_register`/`write_multiple_registers`/`read_holding_registers`, payload aislado, device configurado o resolución automática con ERROR explícito) + `EjecutorControl` con traducción del plan: **210 = corriente equivalente a `potencia_max_carga` (constante entre tramos, nunca la del tramo — corrección 1)**, 230/232 solo carga desde red, 211 solo en VENTA_RED; conversión W→A con `voltaje_bateria_nominal` (51,2 V) y recorte SIEMPRE a los límites del YAML; idempotencia por registro; **dry-run total con `control_activo` OFF**.
4. **Salvaguardas (U4)**: write-verify por relectura (OK / fallo / **parcial** si la relectura falla, no cuenta), 3 fallos consecutivos → **fallback** (valores seguros 232=0/230=0, `control_activo` OFF automático, `persistent_notification`, reactivación solo manual); rate limiter 12/h (1–48, ventana deslizante, aplaza al siguiente tramo; el fallback no está sujeto al límite); watchdog (telemetría > 2× intervalo o plan ausente → no escribir; carga activa → fallback); guard dinámico de 5,75 kW (> 95 % reduce; ≥ 100 % → 0 A; entidad 169 o cálculo derivado `consumo − FV − potencia_bateria`); bloqueo por fallo (103-106 ≠ 0; lista vacía → inactivo con aviso único).
5. **Gradual + auditoría (U5)**: `modo_activacion` gradual (default) → solo CARGA_RED en valle 2.0TD se ejecuta, el resto se simula y se audita (estado `dry_run`); auditoría JSONL append-only en **`config/optimizador_bateria_omie/auditoria_escrituras.jsonl`** con `{ts, tramo, accion, registros, ejecutada, verificado, latencia, motivo, guard}`; el guard deja constancia ("reducción"/"corte").
6. **Servicios (U6)**: `force_mode(mode, until)` (respeta TODAS las salvaguardas, expira solo en `until`; hasta ISO 8601 futuro) y `release_manual`.
7. **Entidades (U7)**: `switch.control_activo` (default OFF), 8 `number` calientes (escriben `entry.data` y recalculan el plan; `precio_max_compra` 0–1 €/kWh) y `sensor.estado_control` (inactivo/dry_run/activo/fallback_bloqueado con detalle).
8. **Reconfigure (U8)**: todos los parámetros F4 editables con defaults (entrada de producción intacta) + tarifa completa + **validación 218 > 217** (`vol.Invalid` y clamp en el executor — corrección 2).
9. **Wiring (U9)**: `__init__.py` crea el executor tras el plan (se suscribe a los replanificados), plataformas sensor+switch+number, unload limpio. SPEC actualizada (RF-03/RF-04 ✅ F4, §5.11 real, §8 F4 completada, §11 con manifest real, deuda #5 ✅).

## Qué falta / pasos del usuario (con acceso al hardware)

1. **Primera activación real en valle nocturno, observada contra la APP Solarman** (criterio de salida): copiar el componente, recargar la integración y encender `switch.optimizador_bateria_omie_control_activo` en valle.
2. **Configurar** (reconfigure): `device_id_solarman` (o dejar la resolución automática), y si se desea `entity_id_potencia_red` (registro 169) y `entity_ids_fallo` (registros 103-106) — con la lista vacía el bloqueo por fallo queda **inactivo con aviso**.
3. **Verificar el payload de los servicios solarman** (`device`/`register`/`value`) en la primera escritura real: está aislado en `EscritorSolarman`; si la integración instalada usa otro formato, se ajusta solo ahí.
4. **Revisar 1 semana** `config/optimizador_bateria_omie/auditoria_escrituras.jsonl` (reales vs simuladas, verifies) y contrastar corrientes/SOC contra la APP los primeros días; comprobar la magnitud W→A con la tensión real de la batería (default 51,2 V; si difiere, ajustar `voltaje_bateria_nominal`).
5. Criterio de salida: **1 semana sin errores con `control_activo=on` en gradual**, solo carga desde red en valle, sin corrientes fuera del YAML.

## Notas de implementación (decisiones conforme al plan v1.1)

- **210 constante** (corrección 1): test específico garantiza que 210 nunca queda por debajo de la corriente equivalente a `potencia_max_carga`, tampoco en AUTOCONSUMO.
- **Gradual interpretado literalmente**: solo CARGA_RED-en-valle se ejecuta; AUTOCONSUMO y VENTA_RED también se simulan en gradual (sus escrituras son neutras/seguras y el fallback garantiza los valores seguros si hace falta).
- **Watchdog "carga activa"** = último estado aplicado con 232=1 o 230>0 (no lectura del inversor).
- `solarman_modbus` sigue inactivo (deuda #9); AGENTS §1.2 (probe antes de escribir) aplica a esa vía; en F4 el habilitador es `control_activo` + límites YAML + salvaguardas.
- La mutación in-place de `entry.data` por los `number` es deliberada (D16): los valores también quedan persistidos al pasar por `reconfigure`.

## Archivos

- **Creados**: `executor.py`, `entidades_control.py`, `number.py`, `tests/test_executor.py`, `tests/test_force_mode.py`, `tests/test_entidades_control.py`, `tests/test_tarifa_entry_data.py`, `RESULTADO_F4.md`.
- **Modificados**: `registers.yaml` (control), `mapa_registros.py` (validación), `const.py`, `config_flow.py`, `tarifa.py` (deuda #5), `coordinador_plan.py` (params calientes), `__init__.py`, `services.py(.yaml)`, `switch.py`, `sensor.py`, `strings.json`, `translations/es.json`, `manifest.json` (**0.10.0**), `SPEC.md`.

## Verificación

```bash
.venv/Scripts/python.exe -m pytest custom_components/optimizador_bateria_omie/tests -q   # 314 passed
.venv/Scripts/python.exe -m ruff check .                                                # limpio
```

Sin llamadas de red ni hardware (servicios `solarman.*` mockeados; direcciones siempre del `registers.yaml`). **Ninguna escritura real al inversor durante el desarrollo.**
