# Kairos Coach — notas de trabajo (TSS y carga)

## Reglas de método (obligatorias)

- Nunca escribir en un golden un número que no haya salido de ejecutar el código real. Si falta el insumo, dejar `tss: null` y `status: "pendiente_de_calculo"`.
- Criterios de cierre fijados antes de ver resultados. No calibrar con n pequeño. Sin suelos ni constantes ocultas.
- Reutilizar funciones de producción en scripts de diagnóstico; goldens generados ejecutando, no de memoria.
- Protocolo de outliers: verificar primero la fuente del TP (misma actividad verificada → auditar código; mal transcrito → corregir referencia; sin verificar → no tocar código).
- Cambios en datos de Supabase: siempre en prueba primero (recálculo completo sin escribir), comprobar que solo cambia lo esperado, aplicar solo esas filas y verificar leyendo de vuelta.
- Sin push hasta orden expresa. Commits firmados con la clave GPG personal (rafwill2@gmail.com, la elige `~/.gitconfig-personal` por el remoto `github.com/rafwill/...`); `gpg-agent` recuerda la contraseña 8 h. Si la firma falla por falta de contraseña, pedir al usuario que haga él el commit (la escribe y queda recordada); nunca `--no-gpg-sign`.

## Objetivo

Cuantificar cada sesión con TSS (TSS/hrTSS/rTSS) como lo haría TrainingPeaks, para que ATL/CTL/TSB se parezcan lo más posible a los de TP. Cada actividad tiene UN TSS, calculado una vez, guardado en `activity_tss`, y todas las pantallas leen ese número. La carga nativa de Garmin nunca es TSS (solo columna `native_load` / "Carga Garmin").

## Modelo vigente (2026-10-21)

PMC (`agent/trainer_agent.py` `compute_and_persist_load_metrics`, `agent/load_metrics.py`):

- CTL 42 / ATL 7 para todos los deportes (`PMC_CTL_TAU_DAYS`, `PMC_ATL_TAU_DAYS`; config del PMC de TP del usuario). No se leen de `load_metrics.model` del perfil.
- `ATL_t = ATL_{t-1} + (TSS_t − ATL_{t-1})/7`, ídem CTL con 42. **TSB_t = CTL_{t-1} − ATL_{t-1}** (convención de TP).
- Semilla = fila de la víspera en `load_metrics_daily`; la línea base arranca en 0 el 2026-02-22.
- Estado de carga: zonas de Forma de Friel relativas al CTL (TSB/CTL: Riesgo alto < −30 %, Óptimo −30 a −10 %, Zona gris −10 a +5 %, Fresco +5 a +20 %, Transición > +20 %), regla única en `load_metrics.form_zone` / `resolve_load_status` (decisión del usuario, 22/10). Los percentiles personales solo informan; no hay suelos de TSB por deporte.

TSS por deporte (`_estimate_session_tss`):

| Deporte | Modelo (`model`) | Referencia |
| --- | --- | --- |
| Running (no trail) | `running:pipeline_fisico` (rTSS: NGP con pendiente Minetti / ritmo umbral vigente en la fecha) | feb–oct frente a TP: mediana 0.986, MAPE 6.3 % (n=97) |
| Trail normal | `trail:tp_zones` (hrTSS de TP: TSS/h por tramo de FC/umbral TP, sin FC de reposo) | MAPE 1.6 %, 25/25 dentro de ±10 % |
| Trail rápido (< 6:00/km) | `trail:fast_rtss` (pipeline de running) | n=1 (+14 %) |
| Bici con potencia | `cycling:power_ftp` (NP = `normPower` / FTP) | MAPE 1.6 %, 17/17 |
| Bici sin potencia | `cycling:tp_zones` | 5 de 7 mejor que antes |
| Caminar / senderismo | `walk_hike:metabolico` (ACSM + Minetti; intermedio entre rTSS y hrTSS de TP, por decisión del usuario), con tope `walk_hike:tp_hr_cap` | ×1.37 frente a TP (TP mezcla rTSS y hrTSS a mano) |
| Fuerza | `strength:modelo_B` (LTHR) | ≈ 1.00 |
| Elíptica / remo | `gym_cardio:*` (solo por tipo o por palabra completa en el nombre) | ≈ 1.1 ("Other" en TP) |

Tabla hrTSS de TP (`TP_HRTSS_RATES`): FC/umbral TP < 85 % → 40.8 TSS/h; 85–95 % → 72.7; 95–100 % → 91.8; ≥ 100 % → 100. Deducida de 25 trails (validación sin la sesión MAPE 1.8 %).

Anclajes por fecha (perfil `performance`):

- `hr_threshold_tp_history`: umbral de FC con el que TP calcula su hrTSS (166 desde 30/01/2026, 169 desde 21/10/2026). Distinto del umbral fisiológico `hr_threshold_bpm` = 169 (Garmin; VT2 de la prueba de esfuerzo).
- `running_threshold_pace_history`: 3:50/km (230 s) desde 30/01/2026, 4:12 (252 s) desde 09/05/2026, 4:00 (240 s) desde 22/10/2026 (test: carrera del 18/10, actividad 24764615547, 27 min, NGP 3:49 llevado a 60 min con Riegel ×1.049; el usuario lo cambia también en TP y Connect). VT2 de la prueba de esfuerzo: 4:15.
- hr_rest = media del RHR de los 14 días previos. FTP bici 205 (igual que TP). Umbral de marcha = anclaje del perfil.

## Prompts y planes (2026-10-21)

- `prompts/system_prompt.md`: flujo de Kairos, rutas deterministas frente a LLM, umbrales de alerta = los del motor diario (`_compute_daily_plan_adjustment`: HRV < 90 % de su media semanal, body battery < 35, sueño < 6 h o < 60). Los tests `TestSystemPrompt*` exigen ciertos encabezados de reglas: no renombrarlos.
- Contexto del LLM (`_build_system_prompt`): perfil + "Estado de carga actual" + "Umbrales" (con zonas en lpm) + "Plan activo" (hoy y dos días; caché `_active_plan_cache`, se invalida al crear/ajustar plan). Los prompts secundarios reciben el mismo perfil.
- Motor de planes (`_generate_structured_plan_payload(..., load_context=)`): `_build_plan_load_context` aporta minutos/semana de las últimas 4 semanas, sesión larga de 6 semanas y CTL/ATL/TSB/estado. Ninguna semana > 10 % sobre lo sostenido; objetivo pasado → plan genérico de 8 semanas; `hr_pct` = % LTHR.
- `prompts/` contiene solo lo que recibe el LLM: `coaching_rules.md` (reglas de entrenador, fuente única que cargan el bucle con tools y las capas de coaching y de rescate; una regla se cambia solo aquí) y `system_prompt.md` (bucle con tools; inserta las reglas en `{{REGLAS_DE_COACHING}}`; todos los proveedores; GitHub Models y su prompt compacto se quitaron el 23/10: desde la VPN se usa NVIDIA). Qué hace cada tool: descripciones del servidor y `garmin_mcp/README.md`.
- El MCP devuelve algunas listas como objetos JSON concatenados (récords, zonas): usar `_try_parse_json`. `get_activity` trae las métricas en `summaryDTO`: usar `_flatten_garmin_activity`.
- Batería de preguntas: `docs/bateria_p55_preguntas_base.txt` (25 preguntas). Se lanza con `tools/run_bateria.py [--modelo M]` (usuario de `KAIROS_USERNAME`; salida con tiempos en `.tmp/bateria/`, ignorada por git) y se juzga con `tools/juez_bateria.py <salida>.md` (rúbrica fija en `docs/Harness.md`; 25/25 el 22/10).
- Login de Garmin: el MCP reutiliza tokens en `~/.garminconnect/kairos/<hash del email>` (o `GARMINTOKENS`); solo hace login real si faltan o no valen (si Garmin los rechaza al cargarlos, p. ej. tras cambiar la contraseña, se borran y entra con la del perfil). Antes cada arranque hacía login completo y Garmin bloqueaba la IP (429) tras varias pasadas.
- Arquitectura MCP: Kairos (`agent/`) es el host; `agent/mcp/client.py` y `agent/mcp/contracts.py` son su cliente; `garmin_mcp/server.py` es el servidor (independiente: no importa `agent/`, lo vigila `test_server_package_does_not_import_the_host`). Arranque del servidor: `python -m garmin_mcp`. Catálogo de las 42 tools en `garmin_mcp/README.md`, generado con `tools/gen_mcp_docs.py` (`make docs-mcp`; un test falla si está desfasado). La clave de perfil `mcp_adapter_cache` se conserva.
- MCP: las 42 tools llaman a `garminconnect` con su firma real; `test_every_tool_calls_garminconnect_with_its_real_signature` lo comprueba sin red (8 estaban rotas hasta el 22/10). Healthcheck en vivo: `tools/healthcheck_mcp.py` / `make health-mcp` (al actualizar `garminconnect`, antes de una batería o si algo falla; no en el arranque). `get_vo2max_trend` pide el rango en una sola llamada (todos los días con estimación); las semanales, por defecto 4 semanas.
- Zonas de FC: mandan las de Garmin (reloj y TP; reserva de FC, carrera 115/135/146/158/169, máx 179). `get_heart_rate_zones` → perfil `performance.hr_zones_garmin` al arrancar, solo si cambian (`hr_zones_garmin_history`). Fuente única `_resolve_hr_zones(perfil, deporte)` para contexto del LLM, análisis y juez; nombres Z1 Recuperación, Z2 Aeróbica, Z3 Tempo, Z4 Subumbral, Z5 Supraumbral. Respaldo: % LTHR. El hrTSS no depende de las zonas.
- Historial: de la sesión anterior solo llegan al LLM las preguntas del usuario (`_restore_previous_session`), nunca las respuestas. Lo mismo en "Memoria de sesiones anteriores": el resumen guarda solo las preguntas y `_strip_previous_reply` limpia la "Última respuesta" de los resúmenes antiguos. Series de tendencia (`get_hrv_trend`, `get_training_load_trend`, `get_vo2max_trend`): el host las envía completas en columnas/filas (`_DAILY_SERIES_TOOLS`); la poda genérica recorta las listas anidadas a 4 elementos.
- Capa de coaching: `_coaching_horizon` fija el horizonte (hoy, mañana o los días que quedan de la semana); `_strip_simulated_user_turn` limpia turnos de usuario inventados por el LLM.
- LLM de pruebas: NVIDIA `nemotron-3-ultra-550b-a55b` vía `NVIDIA_MODEL` en `.env` (el defecto del código sigue siendo `nemotron-3.5-lightning-30b-a3b`). Con la batería del 21/10 fue más coherente y más lento (158 s frente a 75 s en las 4 preguntas con LLM). Varios modelos del catálogo de NVIDIA dan 404 con esta clave (Kimi K2.6, Nemotron 3 Super, Mistral Large 2).

## Datos y herramientas

- `activity_tss` (en `supabase/schema.sql`, el único fichero de esquema, idempotente): una fila por actividad con `tss`, `model`, `tss_source`, `native_load`, anclajes usados (`hr_rest_used`, `lthr_used`, `threshold_pace_used`, `ftp_used`, `walk_threshold_used`).
- `load_metrics_daily`: serie diaria TSS/ATL/CTL/TSB (fuente canónica de las pantallas).
- Línea base: desde 2026-02-22. El TSS guardado nunca se recalcula; solo se calculan actividades nuevas. Correcciones puntuales = recálculo en prueba + escritura de las filas afectadas + serie desde la primera fecha afectada.
- `tools/rebuild_tss_baseline.py --username U --start ISO [--apply]`: ruta de producción con escrituras interceptadas; sin `--apply` es la prueba.
- TP de referencia: `_archivo/datos_tp/workouts.csv` (export TP 01/02–21/10/2026; `_archivo/` es local y está en `.gitignore`). El export omite algunas sesiones (7 de fuerza de agosto) y la columna IF no sirve para validar (TSS de running ≈ 1.06–1.14 × h·IF²·100).
- `TSS_FORMULA_VERSION` = 34: no subirla sin decidirlo (dispara recálculo completo de 120 días).

## Pendiente

- Trail rápido: vigilar con más sesiones (16/08: Kairos 128.0 frente a TP 112.4 rTSS).
- Series cortas de running (6x500, 6x1000, 4x400, 8x500) en +11 % a +20 % frente a TP; 23966766696 (13/08, 8x45" cuestas) ×1.20. Plantear hipótesis antes de tocar umbrales (p. ej. relación FC-velocidad con retraso fisiológico).
- Bici sin potencia > 2 h: 07/08 Kairos 65.9 frente a TP 93 (la tabla no tiene datos de bici por encima del 85 %).
- Si cambia el umbral de FC de TP, añadir la entrada con fecha a `hr_threshold_tp_history` el mismo día (no hay comando; el ritmo umbral sí: `/perfil umbral` añade la entrada de hoy al historial).
- Revalidación periódica con un export nuevo de TP (prueba sin escribir, criterio fijado antes, solo filas esperadas).
- Capturar el RPE de fuerza después de la sesión (persistido por `activity_id`).
- Git hooks locales (pre-commit con los tests).

## Limitaciones aceptadas (no bugs abiertos)

- Maratón de Madrid 22663819817 (26/04, 3.4 h): Kairos 230.1 rTSS frente a TP 299.3 (−23 %), decidido dejarlo así. NGP/ritmo medio en TP 1.100 frente a 1.006 en las otras 96 sesiones: anomalía de TP en esa sesión.
- Caminatas: no cuadran con TP por diseño (modelo intermedio).
- Hiking con n bajo; short_reps_detector con cobertura parcial; 24027714450 sin detección (tramo corto sostenido embebido); RPE de fuerza 0/137; elíptica sin hr_max real; `_parse_hr_zones_list` no lee el formato real de zonas de Garmin (solo afecta a respaldos).

## Historia

Detalle de cada cambio en `docs/proximos_pasos.md` (bitácora) y `CHANGELOG.md`. Hitos: `activity_tss` y línea base (086b5f6…70275f8, 20/10); enrutado gym_cardio por palabras completas (089462c); trail como TP (c664f46); ritmo umbral por fecha (2cea97c); auditoría PMC, bici NP, caminatas (d8dfc45). Evidencias de calibración (tablas frente a TP, goldens del 19/10, diseños cerrados) y herramientas de diagnóstico: `_archivo/` (local, en `.gitignore`; no se sube). Datos reales de Garmin para reproducir offline: `fixtures/real_payloads/` (local, ignorado).
