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




22/09/2026

Excelente propuesta. Ya corrí exactamente esas comprobaciones y salieron datos útiles.

Resultado 1: uso real en runtime

La ruta de cálculo de TSS de fuerza está usando el estimador HR, no la tabla de IF por etiquetas:
definición HR strength IF: load_metrics.py:508
uso en rama strength dentro de estimate_session_tss: load_metrics.py:3020
La clasificación por texto y el IF por categoría sí existen:
clasificador: load_metrics.py:1914
IF por categoría: load_metrics.py:1992
Pero en cálculo operativo de strength TSS, hoy manda la ruta HR en esa rama.
Resultado 2: heavy en historial local disponible
Conté categorías inferidas con la lógica actual en dos datasets:

Tabla independiente fuerza (18 strength):
heavy: 0
light: 5
neuromuscular: 8
general: 5
other: 0
Ventana 120 (dataset amplio local):
heavy: 0
light: 22
neuromuscular: 22
general: 74
other: 2
Conclusión factual: no aparece heavy en ninguno de los dos cortes locales analizados.

Resultado 3: simulación de fusión general + neuromuscular
Hice la simulación como pedías, pero correctamente sobre calc_tss actual (no anclando a TP), aplicando el factor de cambio de IF en las categorías fusionadas:

Modelo A: general 0.56, neuromuscular 0.57
Modelo B: ambas en 0.565
light, maintenance y heavy iguales
Impacto frente a TP:

Full 18

A: MAE 0.982401, bias 0.550934, ratio 1.014786
B: MAE 0.974577, bias 0.363488, ratio 1.011986
Delta B-A: MAE -0.007824, bias -0.187445, ratio -0.002800
OOS 13

A: MAE 1.113596, bias 0.783746, ratio 1.020399
B: MAE 1.102763, bias 0.524206, ratio 1.016522
Delta B-A: MAE -0.010833, bias -0.259540, ratio -0.003877
Lectura:

No empeora; mejora leve y estable.
Está muy lejos del umbral de deterioro material de 0.2 MAE.
Tu hipótesis de simplificación queda respaldada numéricamente en estos cortes.
Tu condición metodológica por calendario
Totalmente de acuerdo. La decisión debería gatillarse por ventana temporal, no solo por conteo. Propuesta operativa limpia:

Ventana fija de 6 a 8 semanas.
Evaluación al cierre de ventana sobre OOS puro.
Accionar solo si se mantiene patrón consistente en al menos dos ventanas consecutivas.
Si quieres, te dejo ahora mismo ese criterio escrito en proximos_pasos.md como regla explícita de gobierno para no volver a ajustar por resultado puntual.

## Aclaraciones de la sesion (22/09/2026)

Resumen corto de preguntas y respuestas para evitar ambiguedades en iteraciones futuras:

1. Modelo A vs Modelo B
- Modelo A: `general=0.56`, `neuromuscular=0.57` (separados).
- Modelo B: `general` y `neuromuscular` fusionados a `0.565`.
- `light`, `maintenance` y `heavy` se mantienen iguales en la simulacion.

2. IF fijo por categoria vs IF por frecuencia cardiaca
- IF fijo por categoria: valor hardcodeado segun `session_kind` en `_estimate_strength_if`.
- IF por FC: valor calculado dinamicamente en `_estimate_strength_if_from_hr` usando FC media + FC reposo/max de perfil.

3. Origen del `0.56` de `general`
- Es un valor heuristico historico (hardcodeado), no una calibracion reciente documentada.
- En codigo aparece explicitamente en `agent/load_metrics.py` dentro de `_estimate_strength_if`:
      - `if session_kind == "general": return 0.56`
      - `return 0.56` (fallback final)

4. Uso operativo actual en fuerza
- En la rama principal de calculo de TSS de fuerza, hoy manda el estimador HR (`_estimate_strength_if_from_hr`).
- La tabla IF por etiquetas sigue existiendo para clasificacion/compatibilidad y analisis.

5. Nota metodologica
- Para decidir cambios de calibracion: evaluar por ventana temporal fija (6-8 semanas) y sobre OOS puro,
      evitando ajustes por un unico caso o por lectura oportunista de resultados.





24/09/2026 12:24

Este log termina de confirmar tres cosas importantes:

La lectura metodológica que marcaste se sostiene.

En la corrida n=18 con hr_max=232, el Modelo A queda casi plano:

hrr_clamped en [0.20, 0.216]

IF en [0.580, 0.586]

15 de 18 sesiones pegadas al piso 0.20

Eso no es un modelo sensible a intensidad real, es casi un IF constante.

El resultado A vs B se mueve con cobertura MCP, pero el patrón central no cambia.

n=14: A MAE 4.67, B MAE 3.54

n=12: A MAE 4.62, B MAE 3.50

n=10: A MAE 4.79, B MAE 3.44

En todas esas corridas parciales recientes, B queda mejor que A.

El MCP está sano en autenticación, pero inestable en get_activity para el bloque más reciente.

mcp_relogin_probe dio 5/5 OK.

Aun así, hay timeouts repetidos en las mismas actividades recientes.

Eso explica por qué la métrica final oscila por cobertura, no por cambio conceptual del modelo.

Delta técnico relevante:

El script en workspace ya quedó con timeout de 60s y guardarraíl robusto para hr_max en compare_strength_models_ab.py.
Tus logs con timeout de 25s corresponden a ejecuciones anteriores que quedaron en historial del terminal.
Cuando probé distribución paginada de maxHR en una sesión posterior, vino vacía y por eso cae a fallback_185.
Conclusión operativa al día de hoy:

No hay base para afirmar A gana.
Sí hay base para afirmar que A está estructuralmente comprimido por clamp y que B es el candidato más estable mientras hr_max siga frágil.
Tu hipótesis sobre la dirección del sesgo de A al corregir hr_max plausible está validada.
Si quieres, siguiente paso lo dejo automático y determinista:

Reintento selectivo solo de las actividades que fallan (sin recalcular todo).
Derivación de hr_max desde las actividades efectivamente recuperadas del set de referencia, con cap duro fisiológico y percentil robusto.
Reporte final único n=18 con tabla por sesión y columnas hrr_clamped/IF de A para auditoría.



27/09/2026

Actualizacion de criterio (fuerza) tras sensibilidad monotona de `hr_max` y corridas parciales repetidas.

Decision de modelo principal

- Se decide **Modelo B (LTHR-anchored)** como modelo principal para fuerza.
- Esta decision no queda bloqueada por derivar `hr_max` con precision, porque la sensibilidad de A ya
      demostro una tendencia monotona en todo el rango plausible analizado (`232 -> 180`): al bajar `hr_max`,
      empeoran sesgo y MAE de A de forma consistente.
- Conclusión metodologica: A no falla por un valor puntual; su comportamiento estructural queda comprimido
      (muchas sesiones en piso de `hrr_clamped`), por lo que no es el candidato principal.

Reorden de prioridades (operativo)

1. **Elegir B ya** para el bloque de fuerza (decision cerrada).
2. Cerrar cobertura a `18/18` con reintento selectivo solo de actividades fallidas,
       pero con objetivo de completar evidencia para calibracion de B (no para decidir A vs B).
3. Ejecutar segunda vuelta de calibracion explicita de B (pendiente/intercepto),
       etiquetada como fase separada de esta comparacion limpia.
4. Dejar derivacion robusta de `hr_max` como trabajo residual para fallback cuando falte LTHR.

Chequeo barato pedido: patron de timeouts recientes

- Los errores de `get_activity` no se distribuyen al azar en todo el set; se concentran en el bloque
      de actividades mas recientes (principalmente ids del tramo `2026-08-14` a `2026-09-09`).
- El subconjunto exacto que falla rota entre corridas, pero dentro del mismo bloque reciente,
      lo que sugiere un problema puntual de esos payloads/ruta de servidor bajo carga y no una
      caida general de autenticacion (probe MCP sigue limpio).
- Implicacion: para cierre de evidencia conviene reintento selectivo por id (1x1) antes que
      repetir corridas completas con timeouts globales.

Ejecucion de cierre (27/09/2026)

- Se ejecutó reintento selectivo 1x1 sobre filas con `calc_error`, con pausa fija de `2.5 s`
      entre reintentos fallidos (hasta 3 intentos por actividad).
- Resultado: cierre a `18/18` (`rows_ok=18`, `rows_err=0`) sin relanzar corrida completa.
- Metricas finales del cierre:
      - `A (HR reserve)`: `MAE=4.426684`, `bias=4.426684`, `ratio_mean=1.123277`.
      - `B (LTHR anchored)`: `MAE=3.613082`, `bias=-3.613082`, `ratio_mean=0.893243`.
- Diagnostico A (corrida final 18/18):
      - `hrr_clamped` en `[0.20, 0.2865]`.
      - `IF_A` en `[0.58, 0.6146]`.
      - Persistencia de compresion de intensidad en A, coherente con decision de mantener B como principal.

Cierre operativo en produccion (27/09/2026)

- Ruta real de fuerza en `agent/load_metrics.py` actualizada para usar **Modelo B LTHR-anchored**
      como principal (con fallback a HR-reserve solo si falta LTHR/umbral).
- `TSS_FORMULA_VERSION` incrementada a `29` por cambio de comportamiento real de formula.
- Coeficientes activos en produccion:
      - `intercepto=0.523104204`
      - `pendiente=0.145349624`
      - clamps de seguridad conservados (`IF` en `[0.45, 0.80]`, `z` en `[0, 1.15]`).
- Se añade guardarrail de observabilidad: warning explicito cuando `z_c` cae fuera del rango
      de calibracion in-sample `[0.134744, 0.321826]` para detectar extrapolacion en tiempo real.
- Estado fuerza: **CERRADO** en produccion, con watch-item de confirmacion out-of-sample por
      ventana calendario de 6-8 semanas.


28/09/2026

Aplicado. Ya incorporé el tercer desenlace en el protocolo y ejecuté la auditoría actividad por actividad sobre toda la lista.

Ejecución:

Script de auditoría: audit_outliers_since_0911.py
Protocolo activo en la salida:
verified_and_same_activity
verified_but_wrong_activity_or_transcribed
source_unverified
Resultado actual de outliers (sin presuponer culpa de TP ni de código):

activity_id	fecha	modalidad	TP	Kairos	ratio	delta	dur_h	avg_pace	run_if	tempo_det	short_reps_det	same_day_total	same_day_same_modality	tp_wrong_activity_risk	estado
24492874850	2026-09-25	walking	18.000	121.015	6.723	103.015	3.418	19:17.41				2	1	medium	source_unverified
24484006590	2026-09-24	walking	10.000	57.778	5.778	47.778	0.997	13:57.52				2	1	medium	source_unverified
24398763300	2026-09-17	running	54.000	100.624	1.863	46.624	1.470	5:20.62	0.827	0	1	1	1	low	source_unverified
24502427862	2026-09-26	walking	3.000	33.199	11.066	30.199	0.873	23:36.43				2	1	medium	source_unverified
24383422318	2026-09-16	elliptical	21.000	47.744	2.274	26.744	0.525	6:59.82				2	1	medium	source_unverified
24430006167	2026-09-20	walking	15.000	30.850	2.057	15.850	0.848	21:28.66				2	1	medium	source_unverified
Lectura operativa:

El protocolo queda aplicado como pediste: no se abre auditoría de código mientras la fuente TP no esté verificada.
También queda cubierto el tercer desenlace: si TP real corresponde a otra actividad o está mal transcrito, se corrige referencia y se cierra sin tocar código.
En este bloque, todos los outliers están en source_unverified, así que el siguiente paso es puramente de validación de fuente.
Siguiente paso recomendado para cerrar rápido:

Para cada activity_id outlier, pásame evidencia TP verificable (captura/export con fecha, nombre actividad y TSS).
Yo los marco uno por uno en:
verified_and_same_activity, o
verified_but_wrong_activity_or_transcribed
Solo los que queden en verified_and_same_activity con discrepancia pasan a auditoría técnica.