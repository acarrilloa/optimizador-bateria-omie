# Adenda (21-nov-2026, tarde) al parche F5.2 — añadir a la nota_apply.md antes de aplicar

**Léeme junto con `nota_parche_f5_2.md`**: los hechos de esta tarde en producción modifican el diseño del saneo.

## Hechos nuevos verificados en producción

1. **El registro 229 rebota**: escribido 229=100 (llamada aislada de un registro), el valor reaparece en **90** poco después. En cambio el 228=6100 SÍ persiste desde esta mañana. Fuente sin identificar: candidata principal, alguna entidad de la integración Solarman que restaura configuración al reiniciar/recargar (la misma firma del incidente de la mañana: ToU reactivado + 228→4900/229→90 tras reiniciar HA), o un push del datalogger/nube.
2. La carga desde red cortaría en ~SOC 90 con 229=90 (semántica de punto de arranque: la condición deja de cumplirse al alcanzarlo). Pérdida menor frente a soc_max=95; aceptable mientras se investiga.

## Cambios requeridos en el parche F5.2 (además de lo ya especificado)

1. **Objetivo del 229 por configuración**: mantener `arranque_carga_red_soc` como parámetro (ya estaba), pero documentar en `strings.json`/README que si el registro rebota en la instalación, se configifique **90** para que el saneo nunca escriba (cero churn de cuota). No dejar hardcode 100 sin cooldown.
2. **Cooldown de rearme por registro** (imprescindible): el saneo NO debe reescribir el mismo registro más de **1 vez por hora** aunque siga derivando; si el cooldown está activo, registrar en auditoría `verificado: "rearme_enfriamiento"` y seguir con el tramo (la carga funciona igual con 229=90 mientras SOC < 90). Implementación simple: `self._ts_rearme[addr]` dict addr→datetime; comprobar antes de escribir.
3. **El 228 no rebota**: puede dejarse objetivo 6100 fijo (sin cooldown en principio), pero el cooldown puede cubrir ambos registros por uniformidad.
4. Test adicional: deriva persistente de 229 (fake siempre devuelve 90) → el saneo escribe 1 vez, y en el siguiente tramo audita "rearme_enfriamiento" SIN escribir de nuevo; el tramo NO se aborta (la carga con 229=90 es válida).

## Nota operativa (no código)

- Pendiente investigar la entidad Solarman fuente del 90 (buscar entre las entidades number/select de la integración Solarman algo tipo "grid/utility charge SOC"). Si aparece y se pone a 100 desde HA, el rebote desaparecería de raíz y el objetivo del saneo puede volver a 100.
- ICP: los tramos CARGA_RED se dimensionan con el **consumo previsto** (contratada − margen − pronóstico). Si el consumo real supera al previsto (hoy: 3,16 kW reales vs ~0,9 previstos), la importación puede superar la contrata y el guard D9 (que solo se evalúa al aplicar el tramo) no lo evita. Mitigación física recomendada al usuario: activar en el inversor "Reducción de picos de la red" ≈ 5.400 W. Propuesta F5.3: reevaluación periódica del guard (cada ~60 s mientras haya carga activa).
