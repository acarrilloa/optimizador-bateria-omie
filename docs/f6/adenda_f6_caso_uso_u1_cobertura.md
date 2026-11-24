# Adenda F6-F: caso de uso U1 (test de aceptación) y cobertura

Fecha: 2026-11-24. Complemento de `adenda_f6_tou_actuador.md` (la edición
directa de ficheros existentes en `docs/` bajo OneDrive no es posible desde
el asistente de diseño; este fichero es la continuación autorizada).

## F. Caso de uso U1 (test de aceptación): cobertura hasta el próximo tramo barato

Contexto real (24-nov, mañana): SOC muy bajo, día frío con consumo alto
(aerotermia) y siguiente tramo barato a las 14:00. El usuario tuvo que
programar a mano un segmento de carga 09:15-10:00 para que la batería
cubriera la casa hasta las 14:00. **El sistema debe resolver esto solo.**

Requisitos derivados:

1. **Check de cobertura periódico** (cada ~30 min, en el coordinador):
   si `SOC_real + previsión de consumo hasta el inicio del próximo tramo
   CARGA_RED/valle < margen`, disparar `regenerar_plan` inmediato aunque el
   bloque en curso esté congelado (motivo `cobertura_insuficiente`),
   valorando cargar en el momento aunque el precio no sea valle (comprar
   caro por adelantado es preferible a quedar sin batería... salvo que
   comprar directo a la red en cada hora salga más barato que cargar:
   decide el optimizador por precio marginal).
2. El replan por desviación de SOC (bug 2, umbral
   `desviacion_soc_replan_pct`) debe funcionar de verdad (listener vivo,
   verificación del entity_id de SOC en el arranque, log si falta) y ser el
   detector principal; el check de cobertura es la segunda barrera.
3. **Consumo previsto sensible a temperatura**: en días fríos el consumo
   supera la media histórica (carga base + aerotermia). Dos vías
   complementarias: (a) multiplicador/estacionalidad por temperatura si
   hay sensor de exterior, o (b) replan temprano cuando el ritmo de
   descarga real supere el previsto por un factor (p. ej. descarga un 30%
   más rápida de lo previsto → replan, sin esperar al umbral absoluto de
   SOC).
4. **Prueba de aceptación emulada**: SOC 25 % a las 09:00 de un día frío,
   previsión FV baja, consumo 2x del histórico, premio tramo barato a las
   14:00: el plan regenerado debe proponer carga antes de las 14:00 y el
   segmento ToU correspondiente debe armarse sin intervención manual
   (auditoría `tou_tabla` con el segmento de la mañana visible).
