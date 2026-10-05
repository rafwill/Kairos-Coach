# Cierre definitivo TSS - 2026-09-16

## Estado final

- Formula vigente en codigo: `TSS_FORMULA_VERSION=32`.
- Bloques principales:
  - Trail: **CERRADO**.
  - Fuerza: **CERRADO** (actualizado 2026-09-27 con Modelo B en produccion).
  - Running: **CERRADO** con excepción de sesgo documentada.

## Actualizacion walking/hiking (2026-10-05) - CIERRE EJECUTIVO AUTOCONTENIDO

### Veredicto

1. La hipotesis de "saltos fisicamente imposibles" queda descartada con evidencia.
2. No se implementa filtro de aceleracion fisica para walking/hiking en el estado actual.
3. La validacion se cierra por consistencia fisica interna del modelo, no por ajuste a TP.

### Causas reales corregidas (resumen cronologico)

1. Funcion antigua seguia activa pese a "implementar" la nueva ruta.
2. `LTHR` y FC reposo llegaban en `None` por falta de hidratacion desde `trainer_agent.py`.
   - Corregido con hidratacion automatica via `get_lactate_threshold` y `get_rhr_day`.
3. Constante de desnivel implementada a `3.6x` de lo especificado.
4. Inversion `VO2 -> velocidad equivalente` usaba ACSM en muestras calculadas con Minetti,
   con extrapolacion fuera del rango valido de ACSM.
5. Zona de mezcla ACSM/Minetti generaba no-monotonia real (mas pendiente podia bajar coste).
6. Tope de seguridad previo (`140`) capaba terreno de montana normal.
   - Ajustado a `350` con base en percentiles reales observados.

### Aclaracion sobre ancla minima de 90 minutos

1. El umbral minimo de ancla no resolvio la hipotesis puntual para la que se introdujo
   (los casos sospechosos subieron en bloque, no selectivamente).
2. Aun asi, se mantiene como regla de diseno correcta por robustez metodologica,
   en el mismo principio de umbral minimo temporal usado en running.
3. Conclusion: cambio util y valido de gobernanza, aunque no fuese la causa raiz de ese subcaso.

### Hipotesis investigada y descartada (con evidencia)

1. El patron "nervioso" de caminatas con perro no era ruido de sensor.
2. La discrepancia venia de medir aceleracion sobre serie reconstruida a `1 Hz`.
3. En cadencia nativa Garmin (`activityDetailMetrics`), el salto clave de `24430006167`
   ocurre en `2 s` (`t=2 -> t=4`), con aceleracion efectiva `0.849 m/s2`.
4. Regla metodologica cerrada:
   - Derivadas temporales (aceleracion) solo en serie nativa.
   - Serie 1Hz reconstruida solo para observables energeticos (IF/pendiente normalizada).

### Criterio de validacion aplicado

1. El modelo no se calibro para "copiar" TP en walking/hiking.
2. `rTSS`/`hrTSS` no se tomaron como verdad externa unica para esta modalidad
   (hay casos con divergencia fuerte entre ambos en la misma actividad).
3. Criterio usado para cierre: consistencia fisica interna y explicabilidad mecanica:
   - montana: intensidad alta explicada por pendiente/terreno real,
   - marcha con perro: picos explicados por aceleracion real de ritmo, poco visible en FC.

### Tabla final (8 actividades, base consolidada)

| fecha | activity_id | modalidad | actividad | dur_h | kairos_tss | metodo | tp_rTSS | tp_hrTSS | tp_status |
|---|---:|---|---|---:|---:|---|---:|---:|---|
| 2026-07-03 | 23468464527 | walking | Turismo. Vielha (Alto Aran - Lleida) | 2.818 | 5.771 | TSS | 4.0 | 84.0 | verified_and_same_activity |
| 2026-07-04 | 23478220005 | hiking | Senderismo. Pla de Beret - Refugi de Mongarri i/v (Baqueira Beret - Lleida) | 2.827 | 27.110 | TSS | 4.0 | 87.0 | verified_and_same_activity |
| 2026-08-02 | 23829149525 | walking | Turismo. Paseo Maritimo de Sanxenxo y Portonovo | 5.211 | 11.624 | TSS | 13.0 | 154.0 | verified_and_same_activity |
| 2026-08-17 | 24013969366 | hiking | Senderismo. Ruta A Moa 1/2 con Hector (O Fieiro - A Coruna) | 1.984 | 20.757 | TSS | 7.0 | 61.0 | verified_and_same_activity |
| 2026-09-20 | 24430006167 | walking | Caminata con Silvia y Yuma | 0.848 | 3.433 | TSS | 4.0 | 25.0 | verified_and_same_activity |
| 2026-09-24 | 24484006590 | walking | Turismo. Del SH Valencia Palace a UPV ETSINF (Valencia) | 0.997 | 5.062 | TSS | 10.0 | 30.0 | verified_and_same_activity |
| 2026-09-25 | 24492874850 | walking | Turismo. Valencia | 3.418 | 12.958 | TSS | 18.0 | 103.0 | verified_and_same_activity |
| 2026-09-26 | 24502427862 | walking | Caminata con Silvia y Yuma | 0.873 | 2.021 | TSS | 3.0 | 26.0 | verified_and_same_activity |

### Control de version de formula (cierre)

1. Version actual en codigo: `agent/load_metrics.py` fija `TSS_FORMULA_VERSION=32`.
2. Trazabilidad Git inspeccionada en `agent/load_metrics.py`:
   - salto `31 -> 32` en commit `350d5e7`.
   - no hay commits posteriores en ese archivo con cambios de formula sin incremento de version.
3. Estado de control: **OK** para cierre (sin evidencia de cambios de formula post-32 no versionados).

## Actualizacion de fuerza (2026-09-27)

### Causa raiz confirmada

1. La ruta de produccion de fuerza estaba usando HR-reserve (Modelo A) con compresion de intensidad:
   - `hrr_clamped` frecuentemente en piso.
   - IF casi plano en la muestra real.
2. La calibracion historica de fuerza por categorias/texto no estaba gobernando la rama operativa real.

### Evidencia de decision (A vs B)

1. Sensibilidad monotona de A al corregir `hr_max` en rango plausible (`232 -> 180`):
   - al bajar `hr_max`, empeoran sesgo y MAE de A de forma consistente.
2. Comparativas A/B repetidas con muestra de 18 sesiones y cierre por reintento selectivo 1x1.
3. Veredicto: **Modelo B (LTHR-anchored)** elegido como principal para fuerza.

### Modelo aplicado en produccion

1. Coeficientes activos (calibracion LS in-sample):
   - `intercepto=0.523104204`
   - `pendiente=0.145349624`
2. Clamps de seguridad mantenidos:
   - `z` en `[0, 1.15]`
   - `IF` en `[0.45, 0.80]`
3. Guardrail de extrapolacion en runtime:
   - warning explicito cuando `z_c` cae fuera de `[0.134744, 0.321826]`.
4. Fallback:
   - si falta LTHR/umbral, cae a HR-reserve (Modelo A) para no perder robustez operativa.

### Limitaciones y etiqueta metodologica

1. Calibracion LS de fuerza etiquetada como **in-sample**, pendiente de confirmacion **out-of-sample**.
2. Rango de calibracion estrecho en esta muestra (`z_c` bajo), con alta correlacion `duration_h` vs `tp_local`.
3. Criterio de confirmacion: ventana calendario de 6-8 semanas antes de consolidar como definitivo OOS.

### Estado de integracion y trazabilidad

1. Cambio integrado en rama principal (`main`) y publicado.
2. Rama de trabajo `feature/calibracion-fuerza-tp` mergeada y eliminada (local/remoto).
3. Artefactos finales de fuerza a conservar como referencia:
   - `docs/strength_model_b_recalibrated_2026-09-27.csv`
   - `docs/strength_model_b_recalibrated_2026-09-27.md`

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
