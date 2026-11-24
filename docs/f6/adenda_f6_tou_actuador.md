# Adenda F6: el ToU como actuador (Programa 1)

Fecha: 2026-11-24. Adenda al diseño del parche F5.x para el Executor
(`executor.py` v0.14.x). Sustituye al "vigilante del ToU" previsto en la
adenda F5.2: **ya no se pelea por el registro 248 ni se escribe jamás**.

## A. Hechos verificados (22-24 nov, con lecturas Modbus y pruebas físicas)

1. El bit ToU selling (`248`) se auto-restaura a 1 en <15 s **por el propio
   firmware del inversor**: lo hace con el WAN del datalogger bloqueado en el
   router Y con el datalogger desenchufado (nadie más podía escribir).
   Datalogger, nube, app, portal y automatizaciones HA quedan exonerados.
   Decisión: 248 es una constante de facto (=1). No se escribe, no se vigila
   como error.
2. Con `248=1` el inversor solo mueve energía por la batería (carga desde red
   y descarga) según los **segmentos de la tabla 250-279**. Tabla vacía o con
   segmentos no activos => la batería flota a 20-77 W (lo visto el 21/22/23
   de noviembre: "float" crónico), aunque 228/229/230/232 estén armados.
3. La receta del usuario (Programa 1) funciona: ventana horaria + amperajes
   de "carga desde la red" y "descarga máxima de la batería" =>
   carga real verificada a 4-5 kW (nights 22→23 y 23→24 nov).

## B. Formato empírico de la tabla (lectura 24-nov con carga funcionando)

Registros 244-279 leídos el 24-nov 10:46, con un segmento de carga activo:

| Registros | Campo | Valor observado |
|---|---|---|
| 244 | CT externo | 2 (no tocar) |
| 245 | Cap exportación (W) | 5000 (no tocar) |
| 246-247 | (reservados) | 0 |
| 248 | ToU selling enable bit | **1, invulnerable: NO escribir** |
| 249 | Day-bits ToU | **0: NO tocar nunca** (sin días, no hay venta autónoma programada) |
| 250-255 | Puntos horarios 1-6, HHMM decimal (0 = inactivo) | 915, 1000, 0, 0, 0, 0 |
| 256-261 | Potencia por punto (W) | 6 x 6000 |
| 262-267 | Volt-stop por punto (0.01 V) | 6 x 4900 |
| 268-273 | SOC-stop por punto (%) | 100, 25, 25, 25, 25, 25 |
| 274-279 | Flags por punto | 5, 4, 4, 4, 4, 4 |

Hipótesis de trabajo (a confirmar en calibración, ver D):
- Los puntos horarios consecutivos delimitan **segmentos**; el segmento 1
  observado (09:15→10:00, flag=5, SOC-stop=100) es de **CARGA desde red**.
- Flag 5 = segmento de carga; flag 4 = segmento de venta (los 6 estaban a 4
  en la tabla original de venta del instalador). Valor 0 en el punto =
  segmento inactivo.
- El usuario además ajusta por modo los amperajes:
  "carga desde la red" (reg 230) y "descarga máxima de la batería"
  (reg 211). Valores usados: para CARGA, uno de ellos a 98 y el otro a 0
  (orden exacto por confirmar en calibración D1).
- El SOC-stop del segmento de carga (100 observado) corta la carga; para
  respetar `soc_max` de la configuración (95) se escribirá el stop
  correspondiente.

## C. Cambios en el Executor

1. **Nueva traducción acción→tabla.** `_objetivos_tramo` (o un método
   nuevo `_objetivos_tabla_tou`) traduce el tramo del plan a la tabla:
   - `CARGA_RED`: punto_hora[0]=inicio del tramo, punto_hora[1]=fin del tramo
     (HHMM), flag[0]=5, soc_stop[0]=soc objetivo (min(soc_max, objetivo)),
     potencia[0]=potencia del tramo; resto de puntos=0 (desactivados).
     Amperajes del modo según receta (tras D1). Se mantiene el saneo F5.2
     de 228/229 antes de armar (el AND de arranque sigue existiendo).
   - `VENTA_RED`: segmento con flag 4, potencia de venta (240-245 recorta:
     el cap físico reg 245=5000 sigue protegiendo), SOC-stop = soc_min.
     Amperajes del modo según receta.
   - `AUTOCONSUMO` y `MANTENER`: **tabla vacía** (puntos y flags a 0 salvo
     day-bits) y amperajes seguros. La flotación resultante es el
     comportamiento deseado (no comprar ni vender).
2. **Escritura por bloques de 8-10 registros** (`escribir_bloque`), nunca
   registro a registro (30 escrituras). Nunca escribir 244-249.
3. **Vigilante ToU v2** (sustituye al previsto en F5.2): antes del valle
   (p. ej. 22:00) leer 244-279 y verificar que el segmento nocturno esperado
   está programado. Si no lo está (p. ej. push de la nube lo pisó), reescribir
   una sola vez (cooldown 1/h por tabla, audit `tou_tabla_reparada`).
   Si no se puede, notification persistente + audit `tou_sin_segmento`.
4. **Auditoría:** toda la tabla se audita como motivo `tou_tabla` (bloque),
   con verificación de relectura igual que las escrituras actuales.
   Desaparece cualquier audit de peleas por 248.
5. **Config nuevas** (config entry + config_flow):
   - `actuador_tou` (bool, default true cuando se despliegue esta adenda):
     activa la escritura de tabla. False = comportamiento v0.14.x.
   - `soc_stop_carga_default` (default = soc_max).
   - Compatibilidad: si `actuador_tou` false, no escribir 250-279.
6. **Rate limiter:** la tabla es un bloque nuevo; no cuenta como 30
   escrituras individuales. Presupuesto: 2 bloques/hora máximo para
   no pelear re-sincronizaciones.

## D. Pruebas de calibración (antes de dar por buena la tabla)

1. **D1 amperajes:** con un segmento de carga activo, probar los dos
   combos de 230/211 (98/0 y 0/98) y medir 190/191 (signed, restar 65536).
   Fija el orden exacto de la receta del usuario.
2. **D2 semántica de segmentos:** programar un micro-segmento de carga de
   10 min en valle y verificar: arranca, para a la hora del punto siguiente,
   y el SOC-stop corta al % fijado.
3. **D3 autoconsumo:** tabla vacía durante un día completo de producción FV
   y verificar que la carga desde PV (reg 190<0 con FV>0) sigue funcionando
   con normalidad (la tabla solo gobierna la ruta red→batería y la venta
   programada, no la carga fotovoltaica).
4. **D4 intrusismo nube:** (cuando se decida desbloquear el WAN del
   datalogger) programar segmento, esperar un sync de la nube y leer la
   tabla: verificar si la plantilla remota pisa los segmentos. Hasta
   validar D4, mantener el bloqueo WAN del datalogger.

## E. Riesgos y notas

- La nube SÍ pisó la tabla una vez (borrado 250-279 el 22-nov). El vigilante
  v2 cubre ese caso; mantener WAN bloqueado hasta pasar D4.
- Los segmentos de venta programados + 248=1 son potencialmente VENTA
  AUTÓNOMA si algún día se escribieran day-bits (249). Regla dura: el
  executor jamás escribe 249; los flags de venta solo con tramo VENTA_RED
  real del plan, y siempre bajo el cap 245.
- Si en el futuro un firmware de Deye corrige el auto-reset de 248, revisar
  esta adenda (el actuador podría volver al esquema simple 210/230/232 de
  v0.14, que se conserva por compatibilidad con `actuador_tou=false`).
- Esta adenda NO cubre los bugs de plan conocidos (ficción intra-bloque
  `soc_previsto`, replan por desviación de SOC muerto): siguen en la
  adenda F5.2 / bug 2 y deben aplicarse aparte.
