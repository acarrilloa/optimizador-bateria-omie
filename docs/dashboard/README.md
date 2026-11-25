# Dashboard del Optimizador Batería OMIE (F6-U2)

Tres vistas listas para copiar en un dashboard Lovelace en **modo YAML**:
`Vista principal` (precios + plan + SOC), `Ahorro` (estimado vs efectivo) y
`Calidad` (forecast ML vs perfil y predicción de consumo).

## Instalación

1. **Requisitos** (tarjetas personalizadas de HACS/Marketplace):
   - `apexcharts-card` (curvas con `data_generator`).
   - `mini-graph-card` (indicadores rápidos).
   Alternativa sin dependencias: sustituir las tarjetas apex/mini-graph
   por `type: statistics-graph` — se pierde la curva intradía del
   atributo `precios_ordenados`, pero todo lo demás funciona.
2. Crea un dashboard en modo YAML (Perfil → Paneos → Añadir panel Lovelace
   → **modo YAML**) o copia cada vista dentro de tu `ui-lovelace.yaml`.
3. Copia el contenido de `optimizador_bateria_omie.yaml` en el fichero de
   configuración del dashboard y recarga.
4. **Prefijo de entidades**: los `entity_id` usan el prefijo
   `inversor_optimizador_bateria_omie` (dispositivo `inversor`). Si tu
   prefijo es distinto, búsqueda y reemplazo global de
   `inversor_optimizador_bateria_omie`.

## Notas de diseño

- La curva de precio efectivo usa el atributo `precios_ordenados` de
  `sensor.*_precios_efectivos_hoy` (ya en €/kWh efectivos, tramos de 15
  min); no depende de la integración OMIE de plantillas.
- La tabla de próximos tramos renderiza con Jinja el atributo `plan` del
  sensor (ya compactado desde 0.10.2, solo tramos pendientes).
- Los badges de estado muestran `estado_de_control` con los estados de
  0.13.0: `gradual` ya no aparece como `dry_run`.

## Automatización de informe diario (opcional)

El servicio `optimizador_bateria_omie.resumen_diario` (0.14.1) devuelve
el balance del día. Ejemplo para enviarlo al móvil a las 23:00:

```yaml
automation:
  - alias: "Informe diario Optimizador OMIE"
    trigger:
      - platform: time
        at: "23:00:00"
    action:
      - action: optimizador_bateria_omie.resumen_diario
        response_variable: resumen
      - action: notify.mobile_app_tu_movil
        data:
          title: >-
            Ahorro hoy: {{ '%.2f' | format(resumen.ahorro_efectivo_eur) }} €
          message: >-
            Estimado: {{ '%.2f' | format(resumen.ahorro_estimado_eur) }} € ·
            SOC {{ resumen.soc_pct }} % ·
            {{ resumen.tramos_hoy | dictsort | map('join', '×') | join(', ') }} ·
            escrituras reales: {{ resumen.escrituras_reales_hoy }}
```
