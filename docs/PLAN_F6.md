# PLAN_F6 — ToU como actuador + cobertura U1

Fecha: 2026-11-24. Entradas de diseño del usuario: `docs/f6/adenda_f6_tou_actuador.md`
(F6/A, actuador) y `docs/f6/adenda_f6_caso_uso_u1_cobertura.md` (F6/B, cobertura).
Estado: **CONFIRMADO E IMPLEMENTADO EN 0.15.0** (usuario confirmó D1 como
default con `descarga_minima_backup_a` opcional, margen 20 % y entrega única).

## Evaluación de las adendas

Ambas son consistentes con lo verificado en producción (F5.2) y con el código
actual. Puntos clave asumidos del diseño:

- **248 nunca se escribe ni se vigila** (auto-restauración por firmware:
  constante de facto). **249 tampoco se escribe jamás** (regla dura:
  day-bits = venta autónoma). El executor solo escribe **250-279**.
- La tabla es la palanca real de energía con 248=1; 210/230/232 solos
  producen el "float" crónico observado. `actuador_tou` (bool) mantiene el
  esquema v0.14.x por compatibilidad.
- El flujo U1 (SOC bajo, día frío, tramo barato a las 14:00) exige que
  replanifique el **coordinador**; el actuador solo materializa lo decidido.

## Entregas propuestas (2 versiones)

### 0.15.0 — F6/A: tabla ToU como actuador (executor)

Archivo a crear/modificar:

| Archivo | Cambio |
|---|---|
| `registers.yaml` | 24 entradas `control`: `tou_hora_1..6` (250-255, HHMM), `tou_potencia_1..6` (256-261, W), `tou_vstop_1..6` (262-267, 0.01 V), `tou_soc_stop_1..6` (268-273, %), `tou_flag_1..6` (274-279, 0/4/5). Direcciones SOLO aquí. |
| `const.py` | `CONF_ACTUADOR_TOU` (default **true**), `CONF_SOC_STOP_CARGA_DEFAULT` (default = `soc_max`), `CONF_MAX_TABLAS_TOU_HORA` (default 2), constantes de flag. |
| `executor.py` | (1) `_objetivos_tabla_tou(tramo)`: segmento del tramo → hora inicio/fin HHMM, flag 5/4, potencia, soc_stop (carga: `min(soc_stop_default, soc_max)`; venta: `soc_min`), vstop 4900 (valor observado funcionando); resto de puntos a 0. AUTOCONSUMO/MANTENER → tabla vacía. (2) amperajes del modo (receta D1, ver dudas). (3) `_escribir_tabla`: bloques contiguos de 6 registros (5 bloques de `escribir_bloque`, ≤16 satisfecho), idempotencia contra la última tabla aplicada, presupuesto propio de 2 tablas/hora, auditoría UNA línea `tou_tabla` por tabla con verify de relectura. (4) **Vigilante v2**: en cada cambio de tramo (:00/:15/:30/:45) con `actuador_tou` on, releer 250-279 en bloques ≤16 y comparar con la última tabla aplicada; si difiere → reescribir 1 vez/hora (audit `tou_tabla_reparada`); si no se puede → notificación persistente + audit `tou_sin_segmento`. Las lecturas no consumen cuota. (5)never 244-249: aserción defensiva en `_escribir_tabla`. (6) Se conserva el saneo F5.2 de 228/229 antes de armar CARGA_RED. |
| `config_flow.py` + `translations/es.json` | `actuador_tou` (booleano), `soc_stop_carga_default` (% 10-100), `max_tablas_tou_hora` (1-6). Labels ES. |
| `tests/test_executor.py` | Tabla de CARGA_RED (hora/flag/soc-stop/potencia correctos, puntos restantes 0), VENTA_RED (flag 4, cap 245), AUTOCONSUMO/MANTENER (tabla vacía), idempotencia (misma tabla → no reescritura), presupuesto 2/h, vigilante repara (audit `tou_tabla_reparada`), vigilante que no puede (notificación + `tou_sin_segmento`), nunca 244-249, `actuador_tou=false` no toca 250-279. |
| `tests/test_reconfigure_f3.py` | Defaults y rangos de los campos nuevos. |
| README/SPEC/FASES/manifest | Versión 0.15.0 + bullet F6/A. |

Detalle D11: en modo **gradual** el criterio de ejecutable no cambia (solo
CARGA_RED en valle); la tabla se escribe solo con acciones ejecutables, igual
que las escrituras actuales.

### 0.15.1 — F6/B: cobertura U1 + bug 2 (coordinador)

| Archivo | Cambio |
|---|---|
| `const.py` | `CONF_MARGEN_COBERTURA_WH` (default 20 % de `capacidad_bateria_kwh`), `CONF_INTERVALO_CHECK_COBERTURA_S` (default 1800), `CONF_FACTOR_RITMO` (default 1.3). |
| `coordinador_plan.py` | (1) **Bug 2**: al `async_setup`, verificar `entity_id_soc` con `hass.states.get()`; si es None o no numérico → WARNING destacado + atributo en el sensor plan (`replan_soc_activo: false`). Log de confirmación cuando el listener queda vivo. (2) **Check de cobertura** cada intervalo: SOC real (Wh) vs consumo previsto acumulado (p50 predictor, tramo a tramo hasta el inicio del próximo tramo CARGA_RED del plan o valle del día) − previsión FV; si SOC − consumo + FV **< margen** → `async_recalcular("cobertura_insuficiente")`, metido en `MOTIVOS_REPLAN_INMEDIATO` (bypassa el congelado del bloque). El **planner** ya decidirá cargar o no según `precio_max_compra` (compra anticipada solo si el precio lo permite; NO se fuerza compra caro). (3) **Detector de ritmo**: consumo real del/los último(s) tramo(s) vs previsto; ritmo > factor (real − FV > previsto × 1.3) sostenido → mismo replan `cobertura_insuficiente` (segunda barrera del requisito 3b). Sensibilidad a temperatura (3a) ya cubierta por el ML con clima AEMET si está reconfigurado; no se añade multiplicador ad-hoc. |
| `tests/test_coordinador_plan.py` | Caso U1 emulado (requisito 4 de la adenda F6-F): SOC 25 % a las 09:00, consumo 2× histórico, FV baja, valle 14:00 → replan inmediato antes de fin de bloque; ritmo 1.4× → replan; margen OK → sin replan; entity_id SOC inexistente → WARNING + atributo. |
| README/SPEC/FASES/manifest | Versión 0.15.1 + bullet F6/B. |

## Riesgos

1. **Semántica de segmentos no validada (D2 de la adenda)**: los defaults
   (flag 5=carga/4=venta/0=inactivo, vstop 4900, HHMM punto→punto) son los
   observados; el micro-segmento de calibración puede refinar. La escritura
   queda confinada a 250-279 y es reversible (tabla vacía = float seguro).
2. **WAN del datalogger debe seguir bloqueado hasta D4** (la nube pisó la
   tabla una vez). El vigilante v2 cubre el caso con cooldown de 1 h.
3. **Presupuesto tablas/hora**: con planes nerviosos la idempotencia evita
   reescrituras; 2/h es el techo y las reparaciones del vigilante comparten
   el mismo presupuesto.
4. El check de cobertura usa p50 del predictor; con MAPE alto (perfil actual
   en adopción) el margen del 20 % absorbe el error. Configurable.

## Pasos que requieren el usuario (calibración física, tras desplegar)

- **D1 amperajes**: con segmento de carga activo, probar 230=98/211=0 y
  230=0/211=98, medir 190/191 → confirmar el orden de la receta (defaults
  propuestos en dudas).
- **D2 micro-segmento de 10 min** en valle: arranque, corte por hora y por
  SOC-stop.
- **D3 tabla vacía en día FV**: carga fotovoltaica normal.
- **D4** intrusismo nube (cuando se decida desbloquear WAN).

## Dudas resueltas por el usuario (24-nov)

1. **D1 amperajes**: confirmado el default (CARGA → 230 = corriente +
   211 = 0; VENTA → 211 = corriente + 230 = 0), **preservando D1 como
   verificación empírica**. Se añade config opcional
   **`descarga_minima_backup_a`** (default 0 = receta pura): si D1
   demuestra que la carga a plena potencia funciona igual con 211≠0,
   subir el valor da respaldo ante corte nocturno de red (en modo
   CARGA, 211 = este valor). 232 sigue siendo la llave maestra de la
   carga desde red y 210 se mantiene constante en todos los modos.
2. **Margen de cobertura**: 20 % de la capacidad (configurable).
3. **Una sola versión 0.15.0** con F6/A y F6/B juntos.

## Unidades implementadas (ambas en la 0.15.0)
