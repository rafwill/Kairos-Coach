# Kairos Coach — TSS único por actividad

## Reglas de método (obligatorias)
- Nunca escribir en un golden un número que no haya salido de ejecutar el código real. Si falta el insumo, dejar `tss: null` y `status: "pendiente_de_calculo"`.
- Criterios de cierre fijados antes de ver resultados. No calibrar con n pequeño. Sin suelos ni constantes ocultas.
- Reutilizar funciones de producción en scripts de diagnóstico; goldens generados ejecutando, no de memoria.
- Protocolo de outliers: verificar primero la fuente del TP (3 desenlaces: misma actividad verificada → auditar código; mal transcrito → corregir referencia; sin verificar → no tocar código).
- Sin push hasta orden expresa. Commits locales.

## Objetivo
Cada actividad tiene UN TSS, calculado una vez con el mejor método, guardado en `activity_tss`, y todas las pantallas (listas, consulta por fecha, resumen) leen ese número. La carga nativa de Garmin (activityTrainingLoad) nunca es TSS: solo columna informativa `native_load`.

## Plan en 4 commits
1. Persistir TSS por actividad (`activity_tss`) SIN cambiar números. **Hecho en local: 94dc1fb + fix b20be03.**
2. Pasar `activity_details` a running en `compute_load` (único paso que cambia el CTL). **Hecho en local.** Tabla antes/después: docs/tablas/commit2_ctl_antes_despues_2026-10-20.csv. Solo cambian las 38 sesiones de running (todas `pipeline_fisico`, mediana ×0.865). TSS en 121 días -4.5 %; CTL a 20/10 de 61.1 a 58.3 (Δ entre -0.5 y -3.8 en toda la serie).
3. Migrar listas, consulta por fecha y bloque de resumen a leer de la tabla. **Hecho en local.** Rutas: últimos N (varios deportes, "entrenamientos"), semana, TSS semanal efectivo, consulta por fecha, bloque de análisis, adherencia al plan (provisional solo si no hay fila). Sin TSS guardado → "pendiente de cálculo"; nunca la carga nativa. Eliminado el reescalado de TSS por actividad en la ruta semanal.
4. Carga nativa solo como columna aparte ("Carga Garmin"), nunca como TSS. **Hecho junto con el 3** (rutas + bloque de análisis sin umbrales tipo TSS + prompts).
   Verificación 2026-10-20 con la pregunta original: 20 sesiones, mediana Kairos/TP 1.00, MAPE 5.4 %, 17/20 dentro de ±10 %.

## Estado a 2026-10-20
- `main` va 2 commits por delante de `origin/main` (94dc1fb, b20be03), sin push. 523 passed / 6 skipped.
- Commit 1 verificado: con la misma entrada, origin/main y HEAD dan el mismo TSS (comprobado offline con payloads congelados).
- `tss_source` ∈ {garmin_native, kairos_model}; rama real en `model`; `native_load` informativa; `sport` = typeKey.
- Migración `supabase/migrations/004_activity_tss.sql` NO aplicada en Supabase (se puede editar en sitio). Hasta aplicarla, el upsert a activity_tss falla y se ignora (compute_load continúa).
- Payloads de running 24398763300 y 24227364808 capturados el 2026-10-20 (`fixtures/real_payloads/*_20261020_0951*.json`, carpeta en .gitignore → `git add -f` en el commit del golden). Captura: `python -m tools.capture_running_payloads [ids]`.

## Línea base de TSS (decisión del usuario, 2026-10-20)
Un recálculo completo hoy con fórmulas y anclajes nuevos = línea base. Después, el TSS **nunca** se recalcula hacia atrás: solo se calculan actividades nuevas y ATL/CTL/TSB a diario desde los TSS guardados.
- `compute_and_persist_load_metrics`: si una actividad ya está en `activity_tss`, se usa su TSS (sin descargas ni recálculo). Si la lectura de activity_tss falla (no por tabla ausente), no toca nada. hr_rest = media del RHR de los 14 días previos (perfil si no hay datos). Umbral de marcha = anclaje del perfil (`performance.walk_hike_threshold_speed_m_min`). Semilla del EWMA = fila de la víspera en load_metrics_daily. `rebuild_from=ISO` solo para la línea base.
- `tools/rebuild_tss_baseline.py --username U --start ISO [--apply]`: ruta de producción con escrituras interceptadas, reintentos, validación (bloquea si algún running no usa detalles o hay errores de detalles) y comprobación de que la tabla existe.
- Arreglado: `_fetch_activities_for_load_calc` solo leía 100 actividades (lista simple sin has_more).
- Inicio elegido: 2026-02-22 (240 d; el arranque en 0 deja ~0.3 % de error en el CTL de hoy, frente a ~6 % con 120 d).

## Pendiente (en orden)
1. DECISIÓN DEL USUARIO (bloquea commit 2): política de anclajes. Recomendación: histórico por fecha de efecto. Afecta a hr_rest, LTHR, FTP, umbral de carrera **y al umbral de marcha (Option A)**. hr_rest "vigente" = media de los 14 días previos (probado frente a TP: no empeora nada y mejora trail un poco).
2. golden_objetivo.json: calcular offline con `procesar_actividad` (hr_rest 37.0, LTHR 169, umbral de carrera 252 s/km). Referencias solo para comparar: 24398763300 ≈ 100.6; 24227364808 ≈ 78.7.
3. Commit 2, tabla antes/después.
4. Commit 3: rutas de chat. Hoy la pregunta "últimos 20 entrenamientos de trail, fuerza y running … TSS" cae en `mcp_factual` (resumen del día): `_is_recent_tss_by_sport_intent` exige "activ" y un solo deporte. Y `_build_recent_tss_by_sport_markdown` prioriza la carga nativa sobre Kairos.

## Comparación con TrainingPeaks (2026-10-20, docs/tablas/tss_kairos_vs_tp_2026-10-20.csv)
TP de los CSV de docs (86 actividades, sin contradicciones entre ficheros; hasta 2026-09-16). Cruce: 52 + 24398763300. Ratio = Kairos/TP.
- Running (n=24): hoy sin detalles, mediana 1.15, MAPE 16.7 %, 6/24 dentro de ±10 %. Con detalles (commit 2, pipeline_fisico): mediana 1.00, MAPE 5.7 %, 21/24. Empeora: 24141099893 series 4x1000 (1.30); 24027714450 (1.13, limitación conocida).
- Trail (n=11): hr_rest 37 → mediana 1.12, MAPE 10.8 %. Con RHR vigente (media 14 d previos, 37.8–41.6) → 1.10, 9.5 % (cumple el criterio fijado de antemano, mejora pequeña). La validación de septiembre (≈1.0) se hizo con hr_rest = 50 por defecto oculto (load_metrics.py:603 y otros; perfil sin hr_rest hasta a89f6a0, 29/09). Hipótesis a probar: el hrTSS de TP solo usa LTHR; trail:lthr_details depende de hr_rest.
- Fuerza modelo_B (n=11): 1.01, MAPE 1 %. Ojo: dentro de muestra (recalibrado el 27/09 con estos mismos TP).
- Walk/hike n=2 (1.16 y 2.62), gym_cardio n=4 (1.13), ciclismo n=1: n insuficiente.
- Carga Garmin / TP: running 1.81, trail 1.13, fuerza 0.07 → la carga nativa no sirve como TSS.
- Fuera de muestra (docs/workouts.csv, export TP 17/09–18/10; 24 de 24 emparejadas por fecha+tipo+duración ±5 %; docs/tablas/tss_kairos_vs_tp_fuera_muestra_2026-10-20.csv). Criterios fijados antes:
  - Running (n=13): hoy 1.17 / MAPE 16.7 % / 2 de 13 dentro de ±10 %. Con detalles: 1.01 / 5.5 % / 11 de 13 → **commit 2 VALIDADO**. hr_rest vigente no cambia nada en running.
  - Fuerza modelo_B (n=3): MAPE 1.0 % → validado (n pequeño).
  - Trail (n=4): 1.07 (hr 37) → 1.06 (hr vigente). 24669705725 (8.7 h): Kairos 545.7 / TP 482.2 (1.13).
  - Walk/hike (n=4): sin relación con TP (0.45–4.56). Los valores de TP también son raros (IF 0.21 frente a 0.52 con FC similar). Fuera de alcance hasta revisarlo.
  - Carga Garmin / TP running: 2.14 (1.41–4.64).

## Golden de producción (docs/tablas/golden_produccion_actual.json, 2026-10-19, hr_rest 37.0 / LTHR 169)
trail 24172582627 = 228.266 · running 24398763300 = 118.471 · running 24227364808 = 91.996 · fuerza 24618238695 = 16.597 · walking 24502427862 = 17.073 · hiking 24013969366 = 102.75 · ciclismo 23950882994 = 50.825 · elíptica 24383422318 = 23.772

## Hechos del diagnóstico
- Running no-trail: modelo por defecto `tp_like`; sin detalles cae a `_estimate_running_tss_tp_like_adjusted` (`model=running:tp_like_adjusted`). Guardado: 90.02 (03/09), 115.67 (17/09). Validado con pipeline físico + detalles: 78.7 y 100.6. TP: 24398763300 = 104 rTSS.
- Trail 24172582627: validado 210.0 (TP 223); producción guarda 210.02; hoy 228.27 por hr_rest 37.0.
- `_hydrate_profile_hr_anchors_if_missing` (trainer_agent.py ~3674) solo llama a get_rhr_day si falta hr_rest → el valor depende de cuándo se hidrató. RHR 14 d (6–19 oct): mín 37.0, media 41.571, último 43.0.
- Umbral de marcha: `_derive_walk_hike_threshold_speed_m_min_from_activities` (trainer_agent.py ~10208) se recalibra con todas las sesiones walk/hike de la ventana de recálculo. TSS ∝ 1/umbral². El 2026-10-20 (force_full_recalc) walking 24502427862 = 11.714 y hiking 24013969366 = 70.498 (×0.686 frente al golden del 19/10). El umbral usado no se guarda en activity_tss.
- Carga Garmin / TSS Kairos en running: 1.2–5.1× (p. ej. 24764615547: 256.2 frente a 50.4). En fuerza, la carga nativa ≈ 0.
- Listas de TSS (trainer_agent.py ~995) priorizan nativo→kairos_formula. Ruta por fecha (~4389) y resumen (~8440) dan cifras distintas (170.1 / 364.2 / 344.9 para 24172582627). 364.2 sale de calcular sin activity_details.
- compute_load solo pide activity_details para trail/hike/walk. `call_tool` devuelve los errores como texto (no lanza): `if activity_details_raw:` aceptaría un texto de error como detalle.
- `_parse_hr_zones_list` devuelve None con el formato real de get_activity_hr_in_timezones (objetos JSON concatenados) → zonas FC probablemente sin usar en producción. Sin tocar (cambiaría números).
- TSS_FORMULA_VERSION = 34 (33/34 = cardio de gimnasio, elíptica/remo, sin validar contra TP).

## A revisar

- Trail rápido (< 6:00/km, `trail:fast_rtss`): solo 1 sesión con TP (24003202488, 16/08): Kairos 128.0 frente a TP 112.4 rTSS (+14 %). Vigilar con más sesiones.
- Series cortas (6x500, 6x1000, 4x400, 8x500) siguen en +11 % a +20 % frente a TP.
- Running 23966766696 (13/08, fartlek 8x45" cuestas) = 79.2 frente a TP 66 (1.20): series cortas, limitación conocida.

## Corregido 2026-10-21: enrutado a gym_cardio por texto

`_is_rowing_activity` buscaba "remo"/"erg" como subcadena en nombre y descripción: "podremos", "volveremos" y "remontar" mandaban running/trail a remo. Ahora solo el nombre y palabras completas. Filas corregidas en activity_tss (sin tocar el resto de la línea base; recálculo en prueba: 218/221 idénticas): 22248446621 (21/03) 110.6→144.3, 22915678572 (17/05) 127.3→106.0, 23966766696 (13/08) 57.7→79.2; load_metrics_daily reescrita del 21/03 al 20/10.

## Corregido 2026-10-21: trail con hrTSS como TP

- Fuente de verdad: `workouts.csv` (export TP 01/02–21/10, sin commitear). En TP el trail es "Run" y el usuario elige hrTSS a mano (también fuerza y elíptica); el trail rápido queda en rTSS.
- TP calcula el hrTSS con TSS/h por tramo de FC respecto a SU umbral de FC, sin FC de reposo ni zonas personalizadas. Deducido de 25 trails: <85 % 40.8, 85–95 % 72.7, 95–100 % 91.8, ≥100 % 100 (`TRAIL_TP_HRTSS_RATES`). Validación sin la sesión: MAPE 1.8 %, máx 5.7 %; el ajuste encaja mejor con umbral 163–166 que con 169.
- Umbral TP ≠ umbral fisiológico: prueba de esfuerzo VT2 = 169 (perfil, desde Garmin), TP tenía 166 desde el 30/01 y pasó a 169 el 21/10. Se guarda aparte en `performance.hr_threshold_tp_history` y se usa el vigente en cada fecha.
- Trail normal → `trail:tp_zones`; trail rápido → `trail:fast_rtss` (pipeline de running). Sin detalle de actividad, cadena anterior.
- Aplicado: 26 filas de trail en activity_tss y load_metrics_daily del 29/03 al 20/10 (recálculo en prueba: 196/222 idénticas, solo cambian trails). Trail normal frente a TP: mediana 1.106 → 1.011, MAPE 9.9 % → 1.6 %, 25/25 dentro de ±10 %.

## Corregido 2026-10-21: ritmo umbral de running por fecha

- Historial de TP (Run/Default): 3:57 desde 15/09/2025, 4:15 desde 24/01/2026, 3:50 desde 30/01/2026, 4:12 desde 09/05/2026. Prueba de esfuerzo: VT2 a 14.1 km/h = 4:15/km.
- `performance.running_threshold_pace_history` (`[{date, sec_per_km}]`) y `_resolve_running_threshold_pace_for_date`; sin historial se usa `running_threshold_pace_sec_per_km`. `threshold_pace_used` en activity_tss guarda el usado.
- Aplicado: 40 sesiones de running 22/02–08/05 (recálculo en prueba: solo cambian esas) y load_metrics_daily desde el 22/02. Running 22/02–08/05 frente a TP: mediana 1.179 → 0.983, MAPE 20.7 % → 6.6 %, 31/40 dentro de ±10 %.

## Limitaciones aceptadas (no bugs abiertos)

Maratón de Madrid 22663819817 (26/04, 3.4 h): Kairos 230.1 rTSS frente a TP 299.3 (−23 %), decidido dejarlo así el 2026-10-21. Kairos NGP 4:40/km (NGP/ritmo medio 1.031); TP implica NGP/medio 1.100, cuando en las otras 96 sesiones es 1.006 (Kairos 1.036) — anomalía de TP en esa sesión, no de la fórmula. Además, en el export de TP el TSS de running es 1.06–1.14 × h·IF²·100 (crece con la duración): no usar la columna IF de TP para validar.

Hiking con n bajo y un punto de desnivel grande (24013969366); short_reps_detector con cobertura parcial; 24027714450 sin detección (tramo corto sostenido embebido); RPE de fuerza 0/137; elíptica sin hr_max real; ciclismo e indoor_running sin auditar; watch-item fuerza OOS entre 8 y 22 de noviembre.
