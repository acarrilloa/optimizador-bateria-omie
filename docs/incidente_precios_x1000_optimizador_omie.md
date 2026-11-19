# Incidente: precios ×1000 en Optimizador Batería OMIE (2026-11-19)

## Síntoma

`sensor.inversor_optimizador_bateria_omie_precio_efectivo` mostraba
**37,3179 €/kWh** (escala €/MWh). La curva `precios_ordenados` de hoy estaba
en 29,6–39,6 «€/kWh». Confirmado por el usuario contra el portal de OMIE.

## Causa raíz

1. El 18-nov a las 13:23 CET, al publicarse el día D+1 (2026-11-19), la
   integración OMIE de HA aún no tenía datos y el **fallback `omie_web`**
   (`cliente_omie.py::_obtener_desde_omie_web`) scrapeó la página
   `omie.es/…/day-ahead-price`.
2. La serie Highcharts real «Precio marginal español» de hoy es
   `[10.01, 6.03, 5.83, …]` **€/MWh con 16 valores negativos**.
   El regex del cliente `r'\\?"data\\?"\s*:\s*\[([0-9][0-9.,\s]*)\]'`
   **no admite números negativos**: descarta la serie real de precios y
   captura el **siguiente array puramente numérico** de la página
   (`[31164.7, 30991.0, …]`, otra gráfica, otra unidad).
3. Con `/1000.0` ese array queda en 23,3–36,6 **€/MWh**, que se almacena en
   la caché como `precio_eur_kwh` (`cache/2026-11-19.json`, fuente
   `omie_web`) y el coordinador lo republica todo el día.

Versión afectada observada: 0.13.0 (`cliente_omie.py` «versión 0.10.2»).
La integración OMIE de HACS (`omie_precios_de_es_*`) da los valores
correctos en €/kWh (00:00 = 0,01 €/kWh = 10,01 €/MWh) — única vía sana.

## Impacto

- Todo el día 19-nov hasta la corrección: precio efectivo, curva, ahorro
  estimado y **planes del LP** calculados con precios ~1000× y con un
  perfil intradía ERRÓNEO (el array capturado no es la serie de precios).
- Recurrencia esperable: cualquier día con precios negativos marginales
  en el que la integración OMIE no haya refrescado aún el sensor D+1
  (típico ~13:20–13:45 CET). No es un fallo puntual.

## Mitigación aplicada (sin reiniciar HA)

- Generada caché corregida `cache/2026-11-19.json` (96 ranuras, €/kWh
  reales desde `sensor.omie_precios_de_es_hoy`, fuente `omie`).
- Copia forense: `cache/2026-11-19.json.envenenado.bak`.
  Eliminarla cuando el desarrollador confirme el análisis.
- El coordinador releyó la caché en su ciclo de 15 min: precio efectivo
  0,0085 €/kWh, curva correcta (min 0,0055 a las 04:00, máx ~0,30 a las
  20:45). Sin reinicio de HA.

## Corrección propuesta para el desarrollador

En `_obtener_desde_omie_web`, reemplazar el regex por uno que admita
negativos y nulos y que solo capture la serie pegada al marcador
«Precio marginal español», p. ej.:

```python
m = re.search(
    r'\\?"data\\?"\s*:\s*\[((?:-?[0-9]+(?:\.[0-9]+)?|null)\s*(?:,'
    r'\s*(?:-?[0-9]+(?:\.[0-9]+)?|null)\s*)*)\]',
    html[idx : idx + 3000],
)
```

con `idx = html.find("Precio marginal espa")`, y filtrar `null`.
Recomendable además un sanity check: si la media está fuera de
[-100, 400] €/MWh tras el /1000, descartar la fuente.

## Vigilancia hasta que haya versión corregida

A partir de ~13:45 CET, verificar que `cache/AAAA-MM-DD.json` del día
siguiente tiene `precio_eur_kwh` en escala €/kWh (valores ~0–0,4, no
20–40). Si vuelve a envenenarse: sustituir el fichero por los datos del
sensor `omie_precios_de_es_*` (script `.local/repara_cache_omie.py`) y
esperar el ciclo de 15 min del coordinador.

## Consecuencia operativa: la batería no cargó en la madrugada (19-nov)

Análisis de `auditoria_escrituras.jsonl` (4.708 líneas) y telemetría:

1. **El plan nocturno ordenó AUTOCONSUMO de 01:00 a 07:30** (nada de
   CARGA_RED en el valle) porque los replans previos a las 01:38 usaban
   los precios envenenados: la serie basura era casi plana (23–36), sin
   valle aprovechable. Con datos sanos tras la corrección tampoco hubo
   carga nocturna planificada.
2. **Modo gradual = simulación**: TODO lo registrado lleva
   `ejecutada: false, motivo: "plan: simulada por modo gradual"` — 0
   escrituras reales en toda la auditoría. El gradual solo ejecutaría
   CARGA_RED en valle 2.0TD real (L–V 00:00–08:00) y la CARGA_RED
   planificada a las 09:15 (llano) queda siempre simulada.
3. Resultado: SOC cayó 68% → ~30% durante la noche.
4. **La carga que arrancó a las 07:50 (5,5 kW desde red) NO es del
   Optimizador**: es la lógica nativa del Deye — switch
   `Batería - carga desde red` ON con *SOC de inicio* 30% — que
   arrancó al cruzar el 30%. Coincidió con spot ~0,003 €/kWh, pero con
   peajes llano (efectivo 0,041).

**Conclusiones para la puesta en marcha**:
- Mientras el modo sea gradual, el plan afternoon/nocturno fuera de
  valle 2.0TD nunca actuará sobre el inversor.
- El SOC-floor del Deye (30%) funciona como red de seguridad y es quien
  realmente garantizó la carga de hoy.
