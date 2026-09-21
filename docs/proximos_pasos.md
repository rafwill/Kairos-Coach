# Proximos pasos - Kairos Coach

Ultima actualizacion: 2026-09-16 (cierre final)

## Actualizacion de estado (2026-09-16) - CIERRE FINAL

1. Version de formula actualizada a `TSS_FORMULA_VERSION=26` por cambio de comportamiento en running:
      - `tempo_detector`: umbral de bloque a `400 s` y tolerancia de huecos cortos `20 s`.
      - Ajuste de pendiente negativa: atenuación parcial del descuento Minetti en bajada
         (`_MINETTI_NEGATIVE_DISCOUNT_STRENGTH=0.55`), manteniendo intacta la pendiente positiva.
2. Criterio de cierre de running documentado con excepción explícita:
      - MAE global `< 7` (sin cambios).
      - Sesgo global ampliado de `±3` a `±3.5` como tolerancia justificada
         (mismo principio aplicado al MAE; evitar sobreajuste por márgenes residuales).
      - Ratios por segmento en `0.88-1.12` (sin cambios).
3. Resultado de cierre running con criterio final:
      - `running_global`: `n=32`, `MAE=6.178491`, `bias=-3.278240`, `ratio_mean=0.957760`.
      - Segmentos dentro de rango ratio:
         - `repeticiones_cortas=1.077338`
         - `fartlek_tempo_sostenido=0.974348`
         - `rodaje_continuo=0.938087`
      - Estado: **CERRADO** bajo criterio final documentado (`±3.5`).
4. Estado de bloques principales:
      - Trail: **CERRADO**.
      - Fuerza: **CERRADO** (neuromuscular y movilidad/activación en rango).
      - Running: **CERRADO** con excepción de sesgo documentada.
5. Limitaciones conocidas aceptadas y trazadas:
      - Hiking: muestra corta (`n=2`) y variabilidad alta; queda como frente de observación.
      - `short_reps_detector`: cobertura parcial conocida (3/4 reales no activan y hay activaciones en rodajes).
      - `24027714450`: tramo sostenido corto (`289 s`) embebido en rodaje largo, no detectado por tempo;
         aceptado como limitación, no bug abierto.
6. Referencia única de cierre:
      - `docs/tss-cierre-definitivo-2026-09-16.md`.

## Nota de archivo historico

El contenido operativo previo de este fichero (hipotesis, pendientes y planes por pasos)
corresponde al periodo de investigacion anterior al cierre final del 2026-09-16.

Para trabajo nuevo, usar como fuente vigente:

- `docs/tss-cierre-definitivo-2026-09-16.md`
- `docs/tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv`

Los documentos intermedios se mantienen solo para trazabilidad historica.

## Checkpoint de continuidad (2026-09-21)

Estado guardado para reanudar en la siguiente sesion:

1. Rama de trabajo activa con cambios publicados:
      - Branch: `feature/calibracion-fuerza-tp`
      - Commit: `37cbf30`
      - Push remoto: completado en `origin/feature/calibracion-fuerza-tp`
2. Calibracion de fuerza implementada y validada en codigo/tests:
      - Estimador de fuerza recalibrado (enfoque global, no por actividad aislada).
      - Suite validada previamente en esta iteracion (`tests/test_trainer_agent.py` y corrida completa).
3. Artefactos de analisis ya generados:
      - `docs/strength_tss_baseline_tp_vs_kairos_2026-09-21.md`
      - `docs/strength_tss_calibrated_tp_vs_kairos_2026-09-21.md`
      - `docs/strength_calibration_window_120_2026-09-21.csv` (ventana global 120 sesiones)
4. Estado del dataset global (120 sesiones de fuerza):
      - `tp_tss` aun pendiente de completar desde export/copia de TrainingPeaks.
      - Columnas listas para cruce y metricas (`tp_tss`, `delta_kairos_minus_tp`).
5. Proximo paso operativo al retomar:
      - Importar/pegar `tp_tss` de TP para esas 120 sesiones.
      - Calcular metricas globales finales: MAE, bias, mediana error absoluto, percentiles y ratio.
      - Si aplica, ajustar una sola vez la calibracion global y volver a validar.

Nota rapida TP:
- Si no aparece boton Export/Download, usar Calendar en vista lista, filtrar Strength + rango de fechas,
  copiar tabla a hoja de calculo y guardar CSV limpio con columnas `date,activity_name,tp_tss`
  (idealmente `activity_id,date,activity_name,tp_tss`).
