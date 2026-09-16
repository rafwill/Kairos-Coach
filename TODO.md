# TODO - Kairos Coach Roadmap

## Estado actual (2026-09-16)

- Formula activa: TSS_FORMULA_VERSION=26.
- Cierre de bloques principales:
  - Trail: CERRADO.
  - Fuerza: CERRADO.
  - Running: CERRADO con excepcion documentada de sesgo (criterio final +-3.5).
- Artefacto unico de cierre:
  - docs/tss-cierre-definitivo-2026-09-16.md
  - docs/tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv
- Validacion focal en verde tras cambios de cierre: 366 tests passed.

## Cambios consolidados en v26

1. Tempo detector de running
- Umbral minimo de bloque sostenido: 400 s.
- Tolerancia de huecos cortos: 20 s (fusion en cascada).

2. Minetti en pendiente negativa
- Atenuacion del descuento en bajada (55% de la magnitud del descuento original).
- Pendiente positiva sin cambios.

3. Exclusión mutua de detectores
- Si tempo activa, short_reps se fuerza a False.

## Limitaciones conocidas (aceptadas)

1. Hiking
- Muestra corta (n=2), mantener en observacion.

2. Short reps detector
- Cobertura parcial conocida en datos reales.
- Hay casos reales que no activa y algunos rodajes donde puede activar.
- Rediseño aparcado para sesion futura.

3. Caso 24027714450
- Tramo sostenido corto embebido en rodaje largo.
- Bloque maximo observado ~289 s, no activa tempo con el esquema actual.
- Aceptado como limitacion, no bug abierto.

## Pendiente real para proximas sesiones

### Prioridad alta

1. Reforzar muestra y criterios de hiking
- Ampliar muestra con TP para confirmar si hay sesgo estable.
- Mantener trazabilidad por actividad (delta, ratio, metodo, fuentes).

2. Instrumentar captura de RPE en fuerza
- Captura post-sesion con retardo (20-30 min).
- Persistencia por activity_id.
- Medir cobertura en ventana reciente y definir umbral minimo operativo.

3. Cobertura futura de short reps
- Diseñar hipotesis alternativa (por ejemplo relacion FC-velocidad con retraso fisiologico) antes de tocar umbrales.

### Prioridad media

1. Higiene de artefactos de validacion
- Mantener un unico artefacto de cierre por iteracion.
- Archivar tablas y notas intermedias sin usarlas como fuente vigente.

2. Automatizacion de chequeos de cierre
- Script unico de cierre que calcule y verifique:
  - MAE global running
  - sesgo global running
  - ratio por segmentos
  - estado final cerrado/pendiente

## Referencia de documentos vigentes

- docs/tss-cierre-definitivo-2026-09-16.md
- docs/proximos_pasos.md
- CHANGELOG.md
- README.md

## Nota

Este archivo queda reiniciado al estado post-cierre v26. Los detalles historicos de investigacion previa se conservan en los documentos de docs archivados.
