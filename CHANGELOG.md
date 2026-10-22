# Changelog

Todos los cambios relevantes de Kairos Coach se registran en este archivo.

## 2026-10-22

### Data
- Ritmo umbral de running 4:00/km (240 s) desde el 22/10/2026 (antes 4:12), estimado con la carrera del 18/10 (24764615547): 27 min, NGP 3:49/km del pipeline de producción, llevado a 60 min con Riegel (×1.049). FC umbral sin cambios (168 lpm de media en los últimos 20 min frente a 169). Añadido al historial del perfil; el TSS ya guardado no cambia.

### Fixed
- Estado de arranque: si Garmin devuelve el registro del día sin valores (reloj sin sincronizar), Body Battery y sueño decían "datos disponibles". Ahora dicen "sin datos" y una línea avisa de qué falta ("Garmin aún no tiene … sincroniza el reloj con la app y vuelve a arrancar Kairos").

### Changed
- Estado de carga con una sola regla en todo Kairos: zonas de Forma de Joe Friel relativas al CTL (Form % = TSB / CTL, como intervals.icu): 🔴 Riesgo alto < −30 %, 🟢 Óptimo −30 a −10 %, 🟡 Zona gris −10 a +5 %, 🔵 Fresco +5 a +20 %, ⚪ Transición > +20 %. Antes había tres reglas distintas (umbrales fijos en `/carga`, otros con −3,4/+6,2 en el resumen semanal y percentiles + suelos por deporte en el arranque) que se contradecían y llamaban "sobrecarga" a bloques normales de entrenamiento (TSB −16/−18).
- Motor diario: Riesgo alto → descanso; 7 días seguidos < −10 % → suave/descarga; resto de zonas → mantener y modular con HRV, sueño y body battery. Los percentiles personales pasan a ser informativos y se eliminan los suelos de TSB por deporte (`tsb_abs_floor`).
- `/carga`, resumen semanal, estado de arranque, estado de hoy, contexto del LLM y prompts muestran la zona y su % del CTL.

### Removed
- Copia muerta de `_SPORT_MODEL_DEFAULTS` en `trainer_agent.py` (constantes antiguas, sin uso).

### Tests
- 568 passed, 6 skipped.

## 2026-10-21 (esquema de Supabase en un solo fichero)

### Changed
- `supabase/schema.sql` es el único fichero de esquema y es idempotente: instala desde cero o pone al día un proyecto existente sin tocar datos. Incluye lo de las antiguas migraciones 001–004 (ya estaba todo salvo el `add column if not exists structured_workout` para proyectos anteriores a la 002). Se borra `supabase/migrations/`.
- Comprobado contra la base de datos real: las 8 tablas existentes coinciden columna a columna con el fichero. Faltaban `training_plan`, `training_plan_session` y `training_plan_version` (los planes no se podían guardar); se crean ejecutando `schema.sql`.

## 2026-10-21 (login de Garmin tras cambiar la contraseña)

### Fixed
- MCP: si Garmin rechaza los tokens guardados dentro de `login()` (pasa al cambiar la contraseña: "Failed to retrieve social profile"), `_garmin_client` los borra y entra con la contraseña. Antes el error saltaba fuera del reintento de `_invoke_with_auth_retry` y todas las tools fallaban; la consulta semanal mostraba "0 actividades".

- Respuestas con fallos de Garmin: si alguna consulta MCP falla, la respuesta empieza con un aviso ("No se pudo consultar Garmin…") en vez de presentar "0 actividades" como dato real; la sección de transparencia dice que las consultas fallaron (antes hablaba de caché/contingencia).

### Tests
- 567 passed, 6 skipped.

## 2026-10-21 (limpieza del proyecto)

### Removed
- 131 ficheros versionados fuera del repo: logs de baterías antiguas, sondas de login, copias de código, tablas e informes de calibraciones cerradas, scripts sueltos de `.tmp/` y tools de análisis puntual (con `tests/test_fit_tss_probe.py`). Lo que puede servir (evidencias de la validación frente a TP, diseños cerrados, herramientas de captura y calibración, exports de TP) pasa a `_archivo/`, local y en `.gitignore`.
- Datos reales fuera de git: `fixtures/real_payloads/*` y los exports de TP siguen en local, ignorados.
- Código muerto (sin ninguna llamada): `_classify_running_session`, `_build_tomorrow_workout_markdown`, `_is_wellness_row_fresh`, `_apply_trail_overrides`, `_hr_zone_bar`, `_safe_get_wellness_row`, `_wellness_ttl_hours`, `get_gemini_daily_info`, `generate_session_summary`, `save_session_summary`, `_persist_session_summary`, `persist_session_summary`, `_parse_recent_tss_sport_request`, `_extract_trail_ngp_like_pace_sec_per_km`, `_split_first_float` y `_extract_split_*`.
- Variantes de rTSS `legacy` y `tp_like_v2` (solo activables con `KAIROS_RUNNING_TSS_MODEL` / `KAIROS_RUNNING_TSS_FALLBACK_MODEL`, sin uso en ninguna de las 222 actividades guardadas). Running queda en `running:pipeline_fisico` con respaldo `running:tp_like_adjusted`; ningún TSS cambia.

### Added
- `tools/run_bateria.py`: batería de preguntas con tiempo por respuesta (`--modelo` para comparar modelos; usuario en `KAIROS_USERNAME`; salida en `.tmp/bateria/`).
- `docs/arquitectura.md`: el detalle técnico que estaba en el README, revisado (fuera `MCP_READ_ONLY` y `_apply_trail_overrides`, que ya no existen; añadida `activity_tss`).

### Changed
- README reescrito: de 1 016 a unas 120 líneas.
- El menú de proveedores muestra el modelo efectivo de cada uno (`*_MODEL` del `.env`), no un nombre fijo.
- Python mínimo 3.11 (el código usa `BaseExceptionGroup`).

### Security
- Historial de git reescrito con `git filter-repo`: fuera de todos los commits las 214 rutas que ya no forman parte del repo (payloads reales de Garmin, exports de TP, sondas, logs de baterías…) y sustituidos en todas las versiones la contraseña de Garmin (filtrada en `.tmp/bateria_p55_input_runtime*.txt` el 18/10) y el email de Garmin. Cambian todos los hashes; los citados en la documentación están traducidos al historial nuevo. Copia del historial anterior: `_archivo/backup_historial_pre_purga_2026-10-21.bundle` (local).

### Tests
- 564 passed, 6 skipped.

## 2026-10-21 (modelo LLM de pruebas)

### Changed
- Modelo NVIDIA de pruebas: `nvidia/nemotron-3-ultra-550b-a55b` (550B, 55B activos) en `.env` (`NVIDIA_MODEL`) en lugar del de por defecto `nemotron-3.5-lightning-30b-a3b` (30B, 3B activos). Batería de 25 preguntas cronometrada con los dos: en las 4 preguntas con LLM, Ultra tuvo 2 fallos (turno de usuario inventado, "lunes" por martes) frente a ~8 de Lightning (confunde el máximo de ATL con CTL, toma 58 TSS parciales como semanales, umbrales inventados, frases en inglés); Ultra tardó 158 s frente a 75 s.

### Fixed
- Capa de coaching: `_strip_simulated_user_turn` quita un turno del usuario inventado por el LLM al final de la respuesta (línea con rol explícito o cola en primera persona tras la última pregunta).

### Tests
- 571 passed, 6 skipped.

## 2026-10-21 (prompts, planes y respuestas)

### Changed
- Prompts del LLM (`system_prompt.md`, `system_prompt_compact.md`) reescritos sin contradicciones: flujo de Kairos (alta, cálculo incremental al entrar, rutas deterministas frente a LLM, motor de planes), carga de Garmin nunca como TSS ni CTL/ATL, umbrales de alerta iguales a los del motor diario (HRV −10 %, body battery < 35, sueño < 6 h o < 60), zonas en % del umbral de FC, plantilla única con la excepción del análisis de actividad, rol según el deporte principal, regla de objetivo ya pasado y protocolo de diabetes con insulina (DT1, "DT 1", LADA).
- El LLM recibe en su contexto el estado de carga actual (CTL/ATL/TSB, estado y rangos), los umbrales con las zonas en lpm y el plan activo (hoy y dos días siguientes). Los prompts secundarios (coaching del estado diario, evaluación de entrenamiento, propuesta para mañana) reciben también el perfil.
- Motor de planes: parte del volumen real de las últimas 4 semanas (nunca más de un 10 % sobre lo ya sostenido), la sesión larga crece desde la más larga reciente, la primera semana se suaviza si hay fatiga, el ritmo objetivo sale de cualquier distancia (5K a maratón) y no planifica hacia un objetivo ya pasado. `hr_pct` es % del umbral de FC (zonas de TP).
- Respuestas deterministas: desglose semanal por unidad real (rTSS, hrTSS, TSS de potencia o metabólico) según el modelo guardado; recuento de actividades desde `activity_tss`; ritmo umbral con su historial; FC umbral con la referencia de TP; fechas en DD/MM/AAAA; estado de hoy sin línea interna, con sueño "anoche / noche anterior" en h y min y posición de ATL/TSB respecto a su rango.

### Fixed
- Récords personales: el MCP los entrega como objetos JSON concatenados y la ruta concluía "no hay récords"; `_try_parse_json` y `_compact_tool_result` los leen como lista (también las zonas de FC del análisis).
- Detalle de actividad casi vacío: `get_activity` trae las métricas en `summaryDTO`; ahora se leen (duración, distancia, ritmo, FC, desnivel, calorías, Training Effect).
- Body Battery de hoy salía "datos disponibles": el nivel actual está en `bodyBatteryValuesArray` y un `drained = 0` se perdía.

### Tests
- 566 passed, 6 skipped.

## 2026-10-21

### Changed
- PMC como TrainingPeaks: CTL 42 / ATL 7 para todos los deportes (antes ATL 8 en trail y CTL 45 en ciclismo/triatlón; las constantes ya no se leen del modelo guardado en el perfil) y Forma (TSB) = CTL de ayer − ATL de ayer.
- Trail normal: hrTSS como TP (`trail:tp_zones`), TSS/h por tramo de FC respecto al umbral de FC de TP (`TP_HRTSS_RATES`), sin FC de reposo. Trail rápido (< 6:00/km): rTSS con el pipeline de running (`trail:fast_rtss`).
- Running: ritmo umbral vigente en la fecha de cada actividad (`performance.running_threshold_pace_history`).
- Bici sin potencia: hrTSS como TP (`cycling:tp_zones`); `compute_load` pide el detalle por segundo de esas salidas.
- Caminar/senderismo: el modelo metabólico tiene tope en el hrTSS de TP (`walk_hike:tp_hr_cap`).
- `/perfil umbral` añade la entrada de hoy al historial de ritmo umbral.

### Added
- `performance.hr_threshold_tp_history`: umbral de FC con el que TP calcula su hrTSS, por fecha (distinto del umbral fisiológico del perfil).

### Fixed
- Bici con potencia usaba la potencia media: Garmin envía la NP como `normPower`. Con NP, MAPE frente a TP 26.9 % → 1.6 %.
- Elíptica/remo por texto: "remo"/"erg" se buscaban como subcadena también en la descripción ("podremos", "remontar"); ahora solo palabras completas del nombre.
- Respaldo `_compute_load_fatigue_metrics`: ya no usa la carga nativa de Garmin ni arranca con el CTL del último día.

### Data
- Correcciones aplicadas en Supabase tras prueba completa sin escribir: 3 actividades mal enrutadas, 26 trails, 40 running de feb–may, 30 bici/caminata y `load_metrics_daily` completa desde el 22/02/2026.

### Tests
- 556 passed, 6 skipped.

## 2026-10-20

### Added
- Tabla `activity_tss` (migración 004): un TSS por actividad con modelo, origen, carga nativa informativa y anclajes usados.
- Línea base de TSS desde el 22/02/2026 y `tools/rebuild_tss_baseline.py` (prueba sin escribir; `--apply`).

### Changed
- El TSS guardado nunca se recalcula hacia atrás; ATL/CTL/TSB se recalculan a diario desde los TSS guardados.
- Running usa siempre el detalle por segundo (pipeline físico).
- Todas las rutas del chat leen `activity_tss`; la carga de Garmin solo aparece como "Carga Garmin (no TSS)".
- hr_rest = media del RHR de los 14 días previos a cada actividad.

### Fixed
- `_fetch_activities_for_load_calc` solo leía 100 actividades.
- `activity_tss.sport` guardaba el dict de tipo en lugar del `typeKey`.

## 2026-10-05

### Changed
- Estado de fórmula actualizado hasta `TSS_FORMULA_VERSION=34`.
- Walk/hike en producción consolidado sobre ruta metabólica por muestra (ACSM/Minetti + equivalente en llano + suavizado 30 s + tope de seguridad en 350 m/min), con documentación de validación temporal y contraste TP.
- Gym cardio (`elliptical` + `rowing`) consolidado en rutas dedicadas con:
	- fase 1 HR-anchored por modalidad (LTHR),
	- fase 2 híbrida opcional (`0.75 HR + 0.25 mecánica`) cuando hay señal mecánica válida.

### Added
- Fixtures reales reutilizables para investigación walk/hike en `fixtures/real_payloads` para 8 actividades de referencia (payload de actividad, detalles y zonas HR), evitando dependencia de consultas MCP en vivo para revalidaciones.

### Fixed
- Cierre metodológico de hipótesis de ruido por aceleración en walk/hike: la medición en serie nativa de `activityDetailMetrics` no confirmó transiciones sobre umbrales `1.5`/`2.0 m/s²` en el set de referencia; se descartó activar filtro de aceleración con esa premisa.

### Docs
- `README.md` actualizado a estado vigente (v34 y cierre de bloques deportivos, incluyendo gym cardio).
- `docs/proximos_pasos.md` actualizado con estado operativo vigente y cierre del sub-hilo de aceleración walk/hike.
- Limpieza de artefactos intermedios de diagnóstico en `docs/walk_hike_temporal_profile` conservando comparativas finales (`tp_comparison_8_activities.csv`, `tp_comparison_8_summary.csv`).

### Notes
- Se retiraron scripts intermedios de diagnóstico ya narrados en documentación de cierre para reducir ruido operativo.

## 2026-09-16

### Changed
- Modelo de carga actualizado a `TSS_FORMULA_VERSION=26` por cambios en comportamiento de cálculo de running.
- `tempo_detector` de running ajustado a bloque mínimo de `400 s` (antes `540 s`) con tolerancia de huecos cortos de `20 s` para continuidad de esfuerzo sostenido.
- Cálculo de velocidad ajustada por pendiente con atenuación del descuento Minetti en bajada (`_MINETTI_NEGATIVE_DISCOUNT_STRENGTH=0.55`), manteniendo sin cambios la pendiente positiva.
- Criterio de cierre de sesgo de running ampliado y documentado de `±3` a `±3.5` como excepción explícita de tolerancia operativa.

### Fixed
- Exclusión mutua entre detectores de running: si `tempo_detector_triggered=True`, `short_reps_detector` se fuerza a `False` para evitar doble clasificación incompatible.

### Tests
- Validación de running tras cambios: `tests/test_running_tss.py` en verde (`14 passed`).
- Validación de regresión trail/running/fuerza en batería focal: `366 passed`.

### Docs
- Cierre consolidado en documento único: `docs/tss-cierre-definitivo-2026-09-16.md`.
- `README.md` actualizado al estado final v26 y a criterios de cierre vigentes.
- `docs/proximos_pasos.md` actualizado para reflejar running/fuerza/trail cerrados con límites conocidos documentados.
- `docs/trail-tss-cierre-2026-09-13.md` marcado como artefacto intermedio archivado.

### Notes
- Resultado de validación combinada junio-septiembre:
	- running global `MAE=6.178491`, `bias=-3.278240`, `ratio_mean=0.957760`.
	- segmentos running en rango de ratio `[0.88, 1.12]`.
- Limitaciones conocidas documentadas sin bug abierto:
	- hiking con muestra corta (`n=2`),
	- cobertura parcial de `short_reps_detector`,
	- caso `24027714450` (bloque sostenido corto ~`289 s`).

## 2026-09-13

### Changed
- Modelo de carga actualizado a `TSS_FORMULA_VERSION=24` con resolucion de duracion robusta para trail cuando faltan campos de resumen en payloads reales.
- Nuevo script oficial reproducible para recalculo trail y validacion de anclas: `tools/regenerate_trail_tss_table.py`.
- Tabla operativa `docs/tss_trail_metodo_desde_2026-07-01.csv` ampliada con columnas de diagnostico:
	- `ratio_recomputed_vs_tp`, `delta_recomputed_vs_tp`
	- `duration_hours_resolved`, `hr_coverage_ratio`
	- `grade_source`, `grade_p50`, `grade_steep_up_share`

### Fixed
- Investigacion trail cerrada en su causa principal: la sobreestimacion alta venia del bug de duracion de entrada (`hours`) y no de la rama `non_fast:lthr_details`.
- Eliminada ambiguedad entre ratio historico y ratio recalculado: el artefacto documenta ahora ambos de forma explicita.

### Docs
- `README.md` actualizado con estado trail 2026-09-13, flujo de regeneracion y columnas diagnosticas.
- `docs/proximos_pasos.md` actualizado al estado post-cierre de investigacion trail (residuo abierto e hipotesis en observacion).
- `docs/tss_comparativa_trail_desde_2026-06-01.md` actualizado para reflejar baseline historico vs recalculo v24.
- `docs/tss_comparativa_desde_2026-07-01.md` marcado como baseline historico y enlazado al artefacto vigente de recalculo.
- `docs/mcp-frozen-runbook.md` ampliado con protocolo de incidentes 403/429 y uso de sonda segura de re-login.
- `docs/siguiente_iteracion_playbook_2026-09-13.md` agregado como guia operativa de arranque para la siguiente iteracion.
- `docs/trail-tss-cierre-2026-09-13.md` ampliado con estado post-cierre (hiking/running abiertos) y referencias al diagnostico MCP.

## 2026-09-12

### Changed
- MCP simplificado a ruta única por defecto (single-path): `call_tool` usa invocación directa sin fast-path/caché automáticos.
- Fallback MCP convertido en opt-in con `KAIROS_MCP_ENABLE_FALLBACK=true`.
- `README.md`, runbook frozen y comparativa de tools actualizados al estado operativo real (`41 Garmin + 3 internas`).
- Modelo de TSS actualizado a `TSS_FORMULA_VERSION=23`.
- Fuerza y walk/hike pasan a clasificación estructurada-first (RPE/sensores antes que texto libre) con texto como fallback explícito.

### Fixed
- CI de tests en GitHub: dependencia MCP fijada a API compatible v1 (`mcp>=1.0.0,<2.0.0`) para mantener compatibilidad con `mcp.server.fastmcp.FastMCP`.
- CI de tests en GitHub: añadida dependencia faltante `fitparse>=1.2.0` para `tools/fit_tss_probe.py` y `tests/test_fit_tss_probe.py`.

### Tests
- Validación local tras los cambios:
	- `tests/test_mcp_client.py`: 30 passed.
	- suite completa: 455 passed.
	- suite completa tras guardrails de clasificación: 465 passed.

### Notes
- Auditoría rápida de impacto histórico en fuerza (lookback 730 días): `723` actividades, `137` sesiones de fuerza, `0` sesiones con RPE estructurado presente, `119` con keywords de texto, `0` casos con coexistencia RPE+keyword y por tanto `0` cambios de bucket observables en histórico actual.

## 2026-09-01

### Added
- Ruta determinista `db_count`: consultas "¿cuántas actividades tienes en la DB?" resueltas desde `load_metrics_daily` sin llamar a Garmin.
- Pre-fetch con filtro de deporte: "última actividad de trail/running/cycling" busca en Garmin las últimas 20 actividades y filtra por `typeKey`, obteniendo el ID exacto antes de llamar a `get_activity` para datos precisos.
- `docs/proximos_pasos.md`: documento de continuidad con estado de la batería de validación y plan de trabajo.

### Changed
- `_is_daily_readiness_intent`: ampliados markers con "puedo entrenar mañana", "puedo entrenar fuerte", "necesito recuperar", "necesito descansar".
- `_build_current_week_tss_markdown` (ruta `week_tss`): tabla principal ahora tiene columnas CTL/ATL/TSB/Estado igual que `/carga`; valores leídos de serie DB; comparación correcta (semana consultada vs semana actual, no vs semana anterior a la consultada); etiquetas dinámicas ("Semana pasada", "Hace N semanas", "Esta semana").
- `_build_load_trend_markdown` (ruta `load_trend`): vista semana-a-semana con cierre real de cada semana (último día con datos en DB, buscando desde domingo hacia atrás); fechas reales en columnas; sin "perfil.load_metrics.ranges" visible.
- `_augment_with_llm_coaching_if_needed`: `daily_readiness` siempre ejecuta LLM coaching (sin chequeo de intención); prompt enriquecido genera 4 secciones (⚡ Efecto de entrenamiento, 💧 Hidratación, 🛌 Estado pre-carrera, 🔄 Recuperación).
- `_prefetch_cache` inicializado al inicio de `chat()` (fix `UnboundLocalError` cuando `daily_readiness` se evaluaba antes del bloque de pre-fetch).
- Timeout LLM: default 300s, cap 300s; eliminado cap de 12s en `_augment_with_llm_coaching_if_needed`.
- Trazas timing `[STARTUP]`/`[SNAPSHOT]` en `build_startup_status_markdown` y `collect_startup_snapshot_48h`.
- `enable_thinking: False` para Nemotron en ambos puntos de llamada LLM (evita que el modelo devuelva razonamiento interno como respuesta).
- Arranque: spinner reemplazado por contador de segundos visible durante verificación Garmin.
- `_is_daily_readiness_intent`: marcadores ampliados para "forma física", "ctl", "atl", "tsb", "estado de forma", "mañana", "necesito recuperar/descansar".
- `_resolve_week_window`: detecta "semana pasada/anterior/previa", "hace N semanas", "N semanas atrás".
- `week_tss`/`week_activities`: display semana natural completa (Lun→Dom) con nota "(datos hasta DD/MM)".
- `prev_week_end` corregido a `week_start - 1` (era `week_end - 7`, mostraba lunes en lugar de domingo).
- Essential Tools: 37→40 (añadidas `get_activity_splits`, `get_activity_exercise_sets`, `get_activity_power_in_timezones`; eliminada `get_activity_hr_zones` fantasma).
- Menú de selección de tools eliminado del arranque (`essential_only=True` hardcodeado).

### Fixed
- `decrypt_password`: capturaba `ValueError/TypeError/UnicodeDecodeError` pero no `cryptography.fernet.InvalidToken` → crash al arrancar con clave de cifrado cambiada.
- `_build_load_trend_markdown`: "4 semanas" ignorado (usaba 14 días por defecto) por no detectar "semanas" en el regex.
- `week_tss`: semana natural mostraba solo el día de inicio (ej. 31/08→31/08) por clamp incorrecto a `today_d` cuando hoy es lunes.
- Modelo NVIDIA NIM: `llama-3.1-70b` (EOL 2026-08-26) → `llama-3.3-70b` → `nemotron-3.5-lightning-30b-a3b`; manejo de error 410 EOL con mensaje amigable.

## 2026-08-31

### Added
- Trazas de timing en llamadas LLM: logs `[LLM] ▶` (inicio) y `[LLM] ⏱` (elapsed/timeout) para depurar latencia.
- Trazas de timing en startup: `[STARTUP]` y `[SNAPSHOT]` con elapsed por fase.
- Nuevo documento de validación: `docs/testing-bateria-punto55.md` con checklist de 10 preguntas E2E para cerrar punto 55.

### Changed
- Modelo NVIDIA NIM actualizado: `meta/llama-3.1-70b-instruct` (EOL 2026-08-26) → `nvidia/nemotron-3.5-lightning-30b-a3b`.
- Manejo de error 410 (modelo EOL) en loop LLM: mensaje amigable en lugar de crash de sesión.
- `decrypt_password` ahora captura `cryptography.fernet.InvalidToken` correctamente (antes crasheaba al arrancar con clave de cifrado cambiada).
- Paralelización de `collect_startup_snapshot_48h`: 7 llamadas MCP secuenciales → `asyncio.gather` (~8-10s en lugar de ~35s).
- Paralelización del pre-fetch de actividad: body_battery, sleep×2, hrv, training_load → `asyncio.gather` simultáneo.
- Caché de pre-fetch (`_prefetch_cache`): evita re-fetch de body_battery y hrv del día actual cuando `daily_readiness` llama a `collect_startup_snapshot_48h`.
- Timeout LLM default elevado: 25s → 300s (configurable vía `KAIROS_LLM_TIMEOUT_SECONDS`).
- Cap de timeout LLM elevado: 120s → 300s.
- Eliminado cap de 12s en `_augment_with_llm_coaching_if_needed`; ahora usa `_llm_timeout_seconds()` completo.
- Menú de selección de herramientas eliminado del arranque: Kairos siempre usa Essential Tools (`essential_only=True` hardcodeado).
- Essential Tools actualizado: eliminada `get_activity_hr_zones` (tool fantasma — servidor no la expone); añadidas `get_activity_splits`, `get_activity_exercise_sets`, `get_activity_power_in_timezones`. Total: 37 → 40 tools.
- Eliminado bloque muerto de estrategia 2 (`get_activity_hr_zones`) del pre-fetch de zonas FC; estrategia 3 renombrada a estrategia 2.
- `docs/mcp-tools-completo-vs-essentials.md` actualizado: count 37 → 40, 4 tools modificadas.

## 2026-08-21

### Changed
- Cierre de arquitectura mixta en rutas deterministas de consulta factual:
	- `week_tss`, `week_activities`, `load_trend`, `today_load_status`, `daily_readiness`, `activity_details` y `mcp_factual` mantienen bloque factual determinista y aplican capa de coaching por LLM cuando la consulta pide recomendación.
- Ajuste de intención factual para separar mejor:
	- consultas mixtas (dato + recomendación),
	- consultas de recomendación pura (sin base factual), que no deben caer en factual determinista.

### Fixed
- Transparencia en fallback híbrido: si la fase LLM de coaching falla (timeout/error o respuesta vacía), Kairos ya no vuelve en silencio al bloque factual; ahora informa explícitamente que no fue posible generar la recomendación de coaching en ese intento.
- Alineación de `today_load_status` con la lógica objetivo: sin recomendación determinista "de relleno" cuando se solicita interpretación; la recomendación pertenece a la fase LLM.

### Tests
- Nuevas pruebas de regresión para capa híbrida en rutas faltantes:
	- `week_activities`, `load_trend`, `today_load_status`, `mcp_factual`, `activity_details`.
- Nueva prueba de resiliencia para `today_load_status` cuando el LLM de coaching entra en timeout, verificando mensaje explícito de indisponibilidad.
- Validación local actualizada en verde:
	- `tests/test_trainer_agent.py`: 307 passed.

## 2026-08-20

### Added
- Plan operativo del punto 9 (MCP propio / congelado Essentials) documentado en `TODO.md`:
	- fases de ejecucion (descubrimiento, backend dual, adapter, implementacion subset, pruebas, corte controlado),
	- criterios de cierre (contratos versionados, rutas criticas sin dependencia funcional de terceros, rollback validado, runbook).
- Nueva prueba de regresion para ventana de verificacion no fija en arranque de carga:
	- valida que el refresco se ancla al ultimo dia con actividad registrada y no a una ventana corta fija.

### Changed
- Reglas de deteccion de actividad nueva en arranque (`compute_and_persist_load_metrics`):
	- se elimina la logica de ventana minima fija de 2-3 dias,
	- la verificacion MCP ahora se ancla al ultimo dia con actividad en DB (o al ultimo dia de serie como fallback),
	- se recorre desde la ancla hasta hoy y, si hay desfase, se recalcula desde el primer dia afectado.
- Actualizacion integral de `README.md` al estado real del proyecto:
	- estructura de repo vigente,
	- rutas deterministas incluyendo `activity_details`,
	- flujo de carga actualizado (full_recalc / incremental_refresh / up_to_date),
	- ajustes de cifras y comportamiento operativo documentado.

### Fixed
- Ajustes de cobertura y expectativas en tests de refresco incremental para reflejar el nuevo patron de consulta MCP por rango anclado.

### Tests
- Validacion focal en verde para regresiones de carga incremental y refresco por actividad nueva:
	- casos de `up_to_date`,
	- refresco por actividad nueva del dia,
	- refresco con actividad faltante en dias previos,
	- caso de brecha larga (ancla historica),
	- bypass correcto de clamp por fecha efectiva en refrescos recientes.

## 2026-08-18

### Added
- Regla específica para trail rápido: si el ritmo final/efectivo es menor de `6:00/km`, Kairos usa `hrTSS` bruto por zonas (sin factor 0.72).
- En el análisis de actividad trail con zonas FC disponibles, se muestran ambos valores de forma explícita: `hrTSS bruto zonas` y `hrTSS Kairos aplicado`.
- Nuevo menú unificado de comandos en CLI (`/menu`) con categorías: ver datos, editar perfil, gestionar planes y sistema.
- Ruta determinista de opciones/configuración reforzada para preguntas tipo "qué opciones puedo cambiar" y para el alias `/menu`.
- Plantilla única obligatoria de salida en prompts (completo y compacto):
	- `## 🧭 Resumen`
	- `## 📊 Métricas clave`
	- `## ✅ Recomendación`
	- `## 🎯 Próximo paso`
- Ruta determinista semanal de actividades por fecha (`week_activities`) con semana natural ISO (lunes-domingo).
- Mensajes de transparencia en arranque cuando hay recálculo por cambio de fórmula (`formula_version`) o refresco incremental de carga.
- Setup automatizado de proyecto:
	- `setup.ps1` (Windows) y `setup.sh` (Unix),
	- `Makefile` con targets `setup`, `setup-win`, `login`, `serve`, `test`, `lint`,
	- generación automática de `.env` y `ENCRYPTION_KEY` si faltan.
- Onboarding "for dummies" en `README.md` con flujo desde cero por SO, comandos de arranque directos sin `make` y resolución guiada de errores comunes en Windows.

### Changed
- `trail_running` mantiene calibración por defecto (`hrTSS zonas * 0.72`) solo cuando no aplica la nueva regla de trail rápido.
- Separación explícita de dominios en FC umbral: la ruta determinista de LTHR ya no consulta herramientas de umbral de lactato.
- Extracción de FC umbral robustecida para payloads Garmin en `camelCase` (ej. `lactateThresholdHeartRate`).
- Endurecimiento de rutas factuales/semanales:
	- consultas por "semana del <fecha>" resueltas por ventana histórica explícita,
	- parsing de fechas `dd/mm/yy` (ej. `17/08/26`),
	- fallback de TSS diario/semanal desde actividades Garmin cuando `load_metrics_daily` aún no cerró el día,
	- estimación de TSS para sesiones sin `trainingLoad` (incluye fuerza/caminata según reglas activas),
	- paginación de actividades sin corte prematuro por orden no estricto.
- Política de recálculo histórico: en migración de fórmula se ignora el clamp de fecha efectiva para forzar recálculo completo coherente.
- Normalización de formato en rutas deterministas principales para alinear salida con la plantilla única.

### Fixed
- Corrección de crash en compactación de resultados de tools (`UnboundLocalError` sobre `act_type_raw`).
- Corrección de side-effect en ruta de FC umbral que podía tocar marcador global de fecha efectiva de parámetros.

### Tests
- Nuevos tests de regresión para validar:
	- uso de `hrTSS` bruto por zonas en trail rápido (`< 6:00/km`),
	- comportamiento de frontera en `6:00/km` (mantiene calibración),
	- presencia de `hrTSS bruto zonas` y `hrTSS Kairos aplicado` en el bloque de análisis.
	- detección robusta de intención de opciones/configuración (incluye `/menu`),
	- ruta de FC umbral sin llamadas a herramientas de lactato,
	- lectura de LTHR desde claves `camelCase` de Garmin.
	- ventana semanal histórica explícita, parser `dd/mm/yy`, fallback factual TSS desde actividades,
	- reemplazo de TSS diario en cero con carga de actividad,
	- formato unificado en rutas deterministas.
- Estado de validación local actualizado: `python -m pytest -q` en verde (`360 passed`).

## 2026-08-15

### Added
- Ruta determinista para consultas de ritmo umbral actual desde perfil persistido.
- Comando de perfil para FC: `/perfil fc <reposo> <max>`.
- Política de fecha efectiva para parámetros de carga (umbral, FTP, FC).
- Checkpoint incremental de resumen de sesión por día (upsert), guardado tras cada respuesta del coach.
- Contrato estructurado de sesión (`structured_workout`) reforzado en prompts (incluye ejemplos válido/inválido).
- Análisis plan-vs-ejecutado por bloques del `structured_workout` con resumen de completados/parciales/omitidos.
- HookManager explícito en runtime (`before_message`, `after_message`, `before_tool_call`, `after_tool_call`, `on_error`).
- ToolRouter determinista opcional para intenciones críticas (`KAIROS_DETERMINISTIC_ROUTER`).
- Sensor activo de spike semanal >20% (semana actual vs. semana previa) integrado en carga/fatiga y ajuste diario determinista.
- Nuevos tests dedicados de hooks/router en `tests/test_hooks_router.py`.
- Evidencia operativa de validacion E2E real de carga/fatiga en `docs/validation-load-fatigue-e2e-2026-08-15.md`.
- Configuración de logging por entorno: `KAIROS_LOG_LEVEL`, `KAIROS_LOG_FILE`, `KAIROS_LOG_STDOUT`, `KAIROS_DEBUG_CONSOLE`.
- Ruta determinista para consultas de FC umbral (LTHR) con lectura directa de perfil y fallback MCP rápido.
- Verificación de frescura al arranque para carga/fatiga: si hay actividad nueva hoy, se refresca el día en curso aunque la serie ya estuviera al día.

### Changed
- Cálculo de carga incremental: preserva histórico previo al último cambio de parámetros y aplica nuevos valores solo desde la fecha efectiva.
- Refresco de FTP: no actualiza fecha efectiva cuando el valor no cambia.
- Cierre de sesión optimizado: se elimina el resumen final dependiente de LLM en salida y se usa checkpoint ligero local para evitar bloqueos por red/timeouts.
- Ajuste diario del plan: ahora muta el JSON estructurado (intensityClass/target/rango, duración y reps) y guarda trazabilidad del ajuste.
- Documento de harness reubicado de `Harness.md` a `docs/Harness.md` y actualizado al estado real de hooks/router/sensores.
- `agent/main.py` ahora centraliza logging de producción, usa logger por módulo y evita trazas debug en consola salvo opt-in.
- Consulta semanal de TSS: prioriza serie canónica DB-first (`load_metrics_daily`) y completa días faltantes con `trainingLoad` de actividades Garmin cuando aplica.
- Diferenciación explícita de intents: `FC umbral` ya no se confunde con `ritmo umbral`.
- Consulta "entrenamiento para mañana" vuelve a flujo LLM; si la primera respuesta es genérica de falta de datos, se ejecuta rescate LLM con snapshot proactivo para proponer sesión útil.
- Storage endurecido ante tabla de planes ausente en Supabase (`training_plan`): rutas de lectura degradan con fallback seguro en lugar de abortar arranque.

### Tests
- Nuevas pruebas para fecha efectiva de parámetros.
- Nuevas pruebas para comando de FC y política de refresco de FTP.
- Nuevas pruebas para persistencia diaria de resumen y checkpoint local de sesión.
- Nuevas pruebas para mutación de `structured_workout` en ajuste diario y feedback por bloques.
- Nuevas pruebas para detectar `weekly_spike_alert` y su efecto en el motor determinista diario.
- Nuevas pruebas de routing/intención para `week_tss`, `hr_threshold` y separación de `running_threshold`.
- Nuevas pruebas de resiliencia startup (actividad nueva del día) y fallback de tabla faltante en storage.
- Nuevas pruebas de rescate LLM para propuestas de entrenamiento cuando la respuesta inicial es genérica.
- Suite completa validada en verde.

### Notes
- Commits clave del día: 6a0c3de (base), 30abe3c (cierre rápido de sesión), d8fee73 (cierre completo punto 38), 41cda8c (hooks/router + tests), 30c2085 (harness docs move/update), de751d0 (sensor spike semanal >20%).
- Estado de validación local al cierre: 317 tests passed.
- Validacion E2E real #55 cerrada con contraste de muestra FIT (avg_method=55.71 vs series_method=57.69, delta +1.98 TSS) y serie real de 120 dias documentada.
- Estado de validación actualizado: 325 tests passed.
