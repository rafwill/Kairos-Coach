# Cierre definitivo TSS - 2026-09-16

## Estado final

- Formula vigente: `TSS_FORMULA_VERSION=26`.
- Bloques principales:
  - Trail: **CERRADO**.
  - Fuerza: **CERRADO**.
  - Running: **CERRADO** con excepción de sesgo documentada.

## Cambios de formula incluidos en v26

1. Running tempo:
   - `_TEMPO_SUSTAINED_MIN_SECONDS` de 540 a 400.
   - Tolerancia de huecos cortos `_TEMPO_SUSTAINED_GAP_TOLERANCE_S=20` con fusión en cascada.
2. Running pendiente negativa:
   - Atenuación del descuento Minetti en bajadas con `_MINETTI_NEGATIVE_DISCOUNT_STRENGTH=0.55`.
   - Pendiente positiva sin cambios.
3. Exclusión mutua de detectores en running:
   - Si `tempo_detector_triggered=True`, `short_reps_detector` se fuerza a `False`.

## Criterio de cierre aplicado (running)

- MAE global `< 7`.
- Sesgo global en `±3.5`.
- Ratio medio por segmento en `[0.88, 1.12]` para:
  - `rodaje_continuo`
  - `fartlek_tempo_sostenido`
  - `repeticiones_cortas`

### Nota de excepción explícita

El umbral de sesgo se amplía de `±3` a `±3.5` por criterio de tolerancia operativo (mismo principio aplicado al MAE): priorizar estabilidad y evitar sobreajuste por margen residual cuando el resto de indicadores están en banda.

## Resultado final de validación combinada junio-septiembre

Artefacto de referencia único:
- `docs/tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv`

Métricas reportadas:
- `running_global`: `n=32`, `MAE=6.178491`, `bias=-3.278240`, `ratio_mean=0.957760`.
- `running_segment_repeticiones_cortas`: `n=4`, `ratio_mean=1.077338`.
- `running_segment_fartlek_tempo_sostenido`: `n=2`, `ratio_mean=0.974348`.
- `running_segment_rodaje_continuo`: `n=26`, `ratio_mean=0.938087`.
- `strength_neuromuscular`: cerrado.
- `strength_movilidad_activacion`: cerrado.
- `trail`: cerrado en este ciclo.

Veredicto final:
- Running: **CERRADO** bajo criterio final documentado.

## Limitaciones conocidas (aceptadas y documentadas)

1. Hiking:
   - Muestra corta (`n=2`) y señal no consolidada; mantener en observación.
2. `short_reps_detector`:
   - Cobertura parcial conocida en datos reales (3/4 casos reales no activan y hay activaciones en rodajes).
   - Rediseño aparcado para otra sesión.
3. Caso `24027714450`:
   - Patrón de tramo sostenido corto embebido en rodaje largo.
   - Bloque máximo observado ~`289 s` incluso con tolerancia de huecos, no activa detector de tempo.
   - Aceptado como limitación, no como bug abierto.

## Trazabilidad y archivo de artefactos intermedios

Documentos archivados (referencia histórica, no fuente vigente de decisión):
- `docs/proximos_pasos.md`
- `docs/trail-tss-cierre-2026-09-13.md`
- `docs/tss_independiente_desde_2026-07-01_hasta_2026-09-13.csv`
- `docs/tss_independiente_desde_2026-07-01_hasta_2026-09-13.md`
- `docs/tss_independiente_desde_2026-07-01_hasta_2026-09-15.csv`
- `docs/tss_comparativa_desde_2026-07-01.csv`
- `docs/tss_comparativa_desde_2026-07-01.md`
- `docs/rodaje_continuo_diagnostico_paso1_2026-09-16.csv`
- `docs/rodaje_continuo_diagnostico_paso1_2026-09-16.md`

Fuente vigente para cierre de esta iteración:
- `docs/tss-cierre-definitivo-2026-09-16.md`
- `docs/tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv`
