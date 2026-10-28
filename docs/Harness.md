# Harness de Kairos

Especificación de todo lo que rodea al LLM de Kairos para que sus respuestas sean deterministas donde pueden serlo y comprobables donde no. Cada guía y cada sensor tiene un identificador, el sitio del código donde vive, cómo actúa y su estado real. Revisado el 23/10/2026.

Estados: ✓ funciona · ◐ existe pero incompleto o incoherente · ✗ propuesto, no existe.

## 1. Marco

Dos referencias, adaptadas a un coach (no a un agente de código):

- **Guías y sensores** (Birgitta Böckeler, [Harness engineering for coding agent users](https://martinfowler.com/articles/harness-engineering.html)):
  - Una **guía** (feedforward) dirige al agente *antes* de actuar para que acierte a la primera. Un **sensor** (feedback) observa *después* y le ayuda a corregirse; funciona mejor si su mensaje está escrito para que el LLM pueda actuar con él.
  - Solo guías: las reglas nunca se comprueban. Solo sensores: el agente repite los mismos errores.
  - Cada control es **computacional** (código determinista: rápido, barato, fiable) o **inferencial** (otro modelo que juzga: más rico, más caro, no determinista).
  - **Calidad a la izquierda:** cuanto antes se detecta un fallo, más barato es corregirlo.
  - **Bucle de gobierno:** cuando un fallo se repite, se mejora la guía o el sensor para que deje de ocurrir; el humano dirige el harness, no cada respuesta.
- **Harness²** de Google Research ([repositorio](https://github.com/google-research/harness2), [artículo](https://arxiv.org/abs/2610.14224)):
  - Un harness es todo lo que rodea al modelo, con ocho componentes editables: system prompt, guardrails, memoria y skills (construyen el contexto); tool guidance y scripts (interfaz de herramientas); subagentes (delegación); plugins (middleware).
  - Mejora el harness sin tocar el modelo con **Proponer–Probar–Componer**: proponer cambios, probarlos sobre la misma tarea y quedarse con lo que la evidencia sostiene comparando ejecuciones.
  - Guarda una **biblioteca de experiencia**: por cada cambio adoptado, la señal que lo motivó, la lección y la evidencia.
  - Separa cambios **invariantes** (valen para cualquier tarea) de cambios **de dominio**.
  - Las guías describen *el trabajo*, nunca *cómo se mide*: el texto del juez no entra en el prompt.

**Qué adoptamos.** El vocabulario de guías y sensores con su eje computacional/inferencial; el bucle de gobierno; Proponer–Probar–Componer hecho a mano con la batería como prueba (cada cambio de prompt o de sensor se contrasta con una batería antes y después); la biblioteca de experiencia como entradas de la bitácora con señal, lección y evidencia; y la separación entre guía y rúbrica.

**Qué no adoptamos.** La optimización automática y recursiva de Harness² (un modelo que reescribe el harness): Kairos tiene un solo dominio, un usuario y una batería de 25 preguntas, y las reglas de método de `CLAUDE.md` piden criterios fijados antes de ver resultados y cambios revisados por una persona.

## 2. Principio de control

Los números los calcula Python; el LLM solo interpreta. Por orden de preferencia, para cada cosa que Kairos debe hacer bien:

1. **Que no la haga el LLM.** Ruta determinista sin LLM (router) o cálculo en Python que el LLM recibe hecho.
2. **Guía.** Si la tiene que hacer el LLM, darle el dato ya calculado y la regla explícita.
3. **Sensor en vivo con autocorrección.** Comprobar la respuesta antes de enseñarla; si falla, devolverle el fallo concreto una vez.
4. **Sensor fuera de línea.** Tests, batería y juez: detectan lo que se escapó y alimentan el bucle de gobierno.

## 3. Componentes de Harness² en Kairos

| Componente | En Kairos |
| --- | --- |
| System prompt | G1, G2 |
| Guardrails | S4, S5, S7, S9 |
| Memoria | G9 (historial y resúmenes), G11 (base de conocimiento) |
| Skills | G7, G8 (motores de decisión y de planes: conocimiento procedimental en Python) |
| Tool guidance | G6, G10 |
| Scripts | `tools/` (batería, juez, healthcheck, reconstrucción del TSS) |
| Subagentes | No hay (un solo agente; la capa de coaching es una llamada aparte con contexto propio) |
| Plugins / middleware | G5 (router) y la cadena explícita de sensores sobre la respuesta del LLM (§6) |

## 4. Guías

| ID | Guía | Tipo | Dónde | Estado |
| --- | --- | --- | --- | --- |
| G1 | Reglas de entrenador, fuente única: salud y protocolo de diabetes, principios, Forma y reglas de actuación, señales de recuperación y anomalías (umbrales del motor diario: HRV < 90 % de su media semanal, body battery < 35, sueño < 6 h o < 60), estructura de la sesión y zonas de Garmin, calidad del dato, fechas, límites éticos. System prompt del bucle con tools: rol, dos capas, tools, análisis de actividad, planes, formato de salida | Texto | `prompts/coaching_rules.md` (lo cargan las cuatro llamadas al LLM) y `prompts/system_prompt.md` (lo inserta en `{{REGLAS_DE_COACHING}}`) | ✓ |
| G2 | Instrucciones propias de las capas de coaching y de rescate (interpretar solo el bloque factual, secciones de readiness, horizonte); las reglas, de G1 | Texto | `prompts/coaching/`, `prompts/rescue/`, `prompts/activity_analysis/` y `prompts/system/`, compuestos por `_render_prompt` | ✓ desde el 23/10 (antes repetía reglas y pedía "% del umbral de FC") |
| G3 | Contexto del atleta: perfil, "Estado de carga actual", "Umbrales" con zonas en lpm, "Plan activo" (hoy y dos días) | Computacional (datos) | `_profile_context_block`, `_load_and_anchors_context_block`, `_plan_context_block`; zonas de `_resolve_hr_zones` | ✓ |
| G4 | Horizonte de la respuesta (hoy, mañana o lo que queda de semana) | Computacional | `_coaching_horizon` | ✓ |
| G5 | Router determinista: 18 intenciones (TSS, actividades, totales de un periodo (km, horas, actividades, desnivel, días con y sin actividad) y la más larga, sueño de una noche y tendencia de sueño (mixta), umbrales, plan, objetivo, récords (también de una distancia: "¿mi récord de 10k?"), readiness…) se responden sin LLM. Banco de frases (`TestToolRouter.PHRASE_BANK`, 28/10): cada frase con su ruta; se amplía con preguntas reales | Computacional | `ToolRouter` | ✓ |
| G6 | Descripción de cada tool y de sus parámetros, la que lee el LLM para elegirla | Texto | `garmin_mcp/server.py`; catálogo en `garmin_mcp/README.md` | ✓ |
| G7 | Decisión diaria: mantener, reducir, suavizar o descansar según carga, HRV, sueño y body battery; al bajar intensidad baja una zona de Garmin (antes reescalaba el % del LTHR con el factor de volumen) | Computacional | `_compute_daily_plan_adjustment`, `_apply_structured_workout_adjustment` | ✓ (el LLM la recibe como marco: G12) |
| G8 | Motor de planes: parte del volumen real de 4 semanas, ninguna semana > 10 % sobre lo sostenido, plan genérico si el objetivo ya pasó; intensidad en zonas de Garmin (`hr_zone`) con el RPE como complemento, mostrada como "Z2 Aeróbica (135–145 lpm) · RPE 3-4" también en planes guardados antes del 23/10 | Computacional | `_generate_structured_plan_payload`, `_build_plan_load_context`, `_session_intensity_label` | ✓ |
| G9 | Memoria entre sesiones: solo las preguntas del usuario, nunca respuestas con cifras viejas | Computacional | `_restore_previous_session`, `generate_session_summary_checkpoint` | ✓ |
| G10 | Resultados de tools compactados (3 000 caracteres; series diarias completas en columnas) | Computacional | `_compact_tool_result`, `_DAILY_SERIES_TOOLS` | ✓ |
| G11 | Base de conocimiento del atleta (fragmentos relevantes de sus notas) | Computacional | `_build_athlete_knowledge_context`, `ATHLETE_KB_PATHS` | ✓ |
| G12 | Marco de la sesión de hoy: decisión, sesión, zonas de Garmin permitidas (con lpm) y duración máxima, calculados con G7; el LLM los recibe junto a los datos deterministas y redacta dentro de ellos; S15 lo hace cumplir | Computacional | `_build_today_session_frame`, `prompts/coaching/session_frame.md` (ruta de estado del día, solo para hoy) | ✓ |
| G13 | Calendario: ayer, hoy, mañana y pasado mañana con su día y fecha, y las dos semanas de lunes a domingo; "HOY" y "MAÑANA" del horizonte con su fecha. Antes el LLM solo tenía hoy y ayer en ISO y deducía los días (errores que veían S27 y S28) | Computacional | `_calendar_block`, `prompts/system/calendar.md` (bucle con tools, coaching y rescates) | ✓ |
| G14 | Diferencia de la HRV de la noche frente a su media de 7 días ya calculada en los datos ("-2 % frente a su media de 7 días"); antes el LLM hacía la cuenta (error que veía S25) | Computacional | `_hrv_deviation_text` en `_format_hrv_day` y en el bloque de diagnóstico | ✓ |
| G15 | Qué entrenar hoy en el bucle con tools: si la pregunta habla de hoy y de entrenar (y no de una sesión ya hecha), el LLM libre recibe la decisión del motor diario y el marco de la sesión (G12) y S15 la comprueba. Antes "¿Qué hago hoy?" seguía al motor y "¿Qué entreno hago hoy?" no (28/10) | Computacional | `_is_today_training_question`, `TrainerAgent._today_frame_for_loop` | ✓ |

## 5. Sensores

"Acción" es lo que hace el sensor cuando detecta un fallo: **bloquea** (no deja pasar), **corrige** (arregla sin LLM), **reintenta** (devuelve el fallo al LLM una vez), **avisa** (lo muestra al usuario) o **registra** (solo log o informe).

### En vivo, al arrancar

| ID | Comprueba | Tipo | Acción | Dónde | Estado |
| --- | --- | --- | --- | --- | --- |
| S1 | Acceso a Garmin (contraseña cambiada, 401) | Computacional | Bloquea y pide credenciales | `get_user_profile` en `agent/main.py` | ✓ |
| S2 | Zonas de FC de Garmin al día | Computacional | Corrige el perfil (con historial) | `_refresh_hr_zones_from_garmin` | ✓ |
| S3 | Pico de carga semanal > 20 % | Computacional | Avisa | `_compute_weekly_spike_signal` (`load_metrics.py`) | ✓ |

### En vivo, en cada llamada a tools

| ID | Comprueba | Tipo | Acción | Dónde | Estado |
| --- | --- | --- | --- | --- | --- |
| S4 | Ninguna tool de escritura | Computacional | Bloquea: el servidor solo tiene tools `get_*` y una tool fuera del esquema no llega al servidor | `garmin_mcp/server.py`, guardia de tools inexistentes en `chat()` | ✓ |
| S5 | Argumentos obligatorios de cada tool | Computacional | Bloquea con error de contrato, sin llamar a Garmin | `validate_min_input_contract` (`agent/mcp/contracts.py`) | ✓ |
| S6 | Errores de Garmin (tipo, HTTP, `retry_after`) | Computacional | Avisa (bloque "Transparencia de datos") | `_classify_mcp_error_text` (`agent/mcp/client.py`) | ✓ |
| S7 | Bucle de tools acotado: 15 iteraciones como máximo; tras 2 timeouts en una respuesta no se llama más a Garmin (cortacircuitos, 24/10); las tools de un mismo mensaje van en paralelo y el servidor las atiende en hilos con 4 llamadas simultáneas como máximo (24/10) | Computacional | Bloquea | `_MAX_TOOL_ITER`, `_MAX_TOOL_TIMEOUTS` (`agent/trainer_agent.py`), `GARMIN_MAX_CONCURRENT_CALLS` (`garmin_mcp/server.py`) | ✓ |

### En vivo, sobre la respuesta del LLM

| ID | Comprueba | Tipo | Acción | Dónde | Estado |
| --- | --- | --- | --- | --- | --- |
| S8 | Turnos de usuario inventados | Computacional | Corrige (los recorta) | `strip_simulated_user_turn` (`agent/sensors.py`) | ✓ |
| S9 | Plan generado válido (sesiones, duraciones, progresión) | Computacional | Bloquea el plan | `_validate_structured_plan` | ✓ |
| S10 | Cada rango de FC cabe en una zona de Garmin (Z1–Z2 en rodaje suave; progresivos "de Zx a Zy", con la marca antes del rango o detrás en su misma frase) | Computacional | Reintenta | `find_hr_ranges_outside_zones` (`agent/sensors.py`); juez y Kairos en vivo | ✓ |
| S11 | Un ritmo citado como umbral es el vigente en la fecha | Computacional | Reintenta | `find_wrong_threshold_pace` (`agent/sensors.py`); juez y Kairos en vivo | ✓ |
| S12 | No atribuye a la CTL el máximo habitual de la ATL | Computacional | Reintenta | `find_ctl_with_atl_max` (`agent/sensors.py`); juez y Kairos en vivo | ✓ |
| S13 | Fechas DD/MM/AAAA, nunca ISO; tampoco el marcador "datos disponibles" sin valor (G4) | Computacional | Corrige | `find_iso_dates`, `find_empty_data_lines` (`agent/sensors.py`); juez (G3, G4) y Kairos en vivo (fechas: corrige; marcador vacío: reintenta) | ✓ |
| S14 | Cifras de carga citadas (CTL, ATL, TSB) = alguna de las que recibió el LLM (contexto, datos deterministas, resultados de tools), con la tolerancia del redondeo; no cuentan umbrales condicionales ("si la ATL supera 55") | Computacional | Reintenta | `find_unsupported_load_figures` (`agent/sensors.py`) | ✓ |
| S15 | Con decisión del motor diario de descanso, suave o reducir, la sesión de hoy no lleva Z4–Z5 ni FC desde el inicio de Z4 (solo respuestas para hoy; no cuentan líneas condicionales ni rangos que no son de FC) | Computacional | Reintenta | `find_intensity_against_decision` (`agent/sensors.py`), decisión desde la ruta de estado del día y desde el bucle con tools (G15) | ✓ |
| S16 | Nota de seguridad glucémica si el perfil tiene diabetes con riesgo de hipoglucemia (DT1, insulina, sulfonilureas o glinidas; `agent/diabetes.py`) y la respuesta prescribe una sesión sin hablar de glucemia | Computacional | Corrige (añade la nota fija `GLUCOSE_NOTE`, del protocolo `prompts/diabetes/insulina.md`) | `add_glucose_note` (`agent/sensors.py`) | ✓ |
| S17 | Responde al horizonte preguntado | Inferencial (no hay forma fiable de comprobarlo con código) | Registra | — | ✗ (solo si S10–S16 no bastan) |
| S25 | La diferencia de HRV que afirma una línea ("> 10 % por debajo") sale de las cifras en ms de esa línea (medida y media); conservador: basta con que una pareja la justifique; no cuentan los umbrales de alerta (una sola cifra o "si…") | Computacional | Reintenta | `find_wrong_hrv_deviation` (`agent/sensors.py`); juez y Kairos en vivo | ✓ |
| S26 | La etiqueta de un TSS citado (rTSS / hrTSS) es la que trae en los datos; solo cifras con decimal pegadas a la etiqueta en los dos textos; "TSS" a secas no es error | Computacional | Reintenta | `find_swapped_tss_label` (`agent/sensors.py`); juez y Kairos en vivo | ✓ |
| S27 | Una palabra relativa ("mañana", "pasado mañana", "hoy", "ayer") va con el día de la semana o la fecha que le toca respecto a hoy; "mañana" a secas o como parte del día ("por la mañana") no se juzga | Computacional | Reintenta | `find_wrong_relative_day` (`agent/sensors.py`); juez (con la fecha de la transcripción) y Kairos en vivo | ✓ |
| S28 | Un día de la semana junto a una fecha ("domingo 20/10", "20/10 (domingo)") es el día en que cae esa fecha; sin año, el que la deja más cerca de hoy | Computacional | Reintenta | `find_wrong_weekday_date` (`agent/sensors.py`); juez (con la fecha de la transcripción) y Kairos en vivo. Criterio: 0 falsos positivos en las baterías guardadas; 26/10, 220 respuestas del ordenador 2: 5 avisos, los 5 errores reales | ✓ |

### Fuera de línea

| ID | Comprueba | Tipo | Cuándo | Dónde | Estado |
| --- | --- | --- | --- | --- | --- |
| S18 | Suite de tests sin red | Computacional | Cada push (CI) y antes de commitear | `tests/`, `.github/workflows/tests.yml` | ✓ |
| S19 | Batería de 25 preguntas contra datos reales y juez con rúbrica fija | Computacional | Tras cambios en rutas, prompts o modelo | `tools/run_bateria.py`, `tools/juez_bateria.py` (§8) | ✓ |
| S20 | Guía de estilo MCP sobre `tools/list` | Computacional | CI (test) y al cambiar tools | `tools/lint_mcp.py`, `test_all_tools_follow_mcp_style_guide` | ✓ |
| S21 | Cada tool llama a `garminconnect` con su firma real; healthcheck en vivo de las 42 | Computacional | CI (test) y bajo demanda | `test_every_tool_calls_garminconnect_with_its_real_signature`, `tools/healthcheck_mcp.py` | ✓ |
| S22 | TSS frente a TrainingPeaks (mediana, MAPE, ±10 %) | Computacional | Al cambiar una fórmula | `tools/rebuild_tss_baseline.py` y el export de TP | ✓ |
| S23 | Lint obligatorio y tests antes de cada commit | Computacional | Pre-commit local | `.githooks/pre-commit` (`make hooks`; los setup lo activan) | ✓ |
| S24 | Lint en la CI: pyflakes y sintaxis (F, E9), hoy a cero; el resto de familias de ruff como informe que no bloquea (`make lint-informe`) | Computacional | CI | `[tool.ruff]` en `pyproject.toml`, paso de lint en `.github/workflows/tests.yml` | ✓ |

No hay sensores inferenciales (LLM como juez) a propósito: son caros, no deterministas y, para un consejo de entrenamiento, el criterio final es del usuario. La calidad del consejo queda en revisión humana.

## 6. Autocorrección (en vivo desde el 23/10: S8, S10–S16; S25–S27 desde el 25/10; S28 desde el 26/10)

Los sensores de la respuesta del LLM viven en un único módulo (`agent/sensors.py`) que usan a la vez Kairos en vivo y el juez de la batería, para que haya una sola definición de cada regla.

1. El LLM responde.
2. Se pasan S8, S13 y S16, que corrigen sin LLM.
3. Se pasan S10, S11, S12, S14, S15, S25, S26, S27 y S28. Si alguno falla, se hace **una** llamada más con el fallo redactado para que el LLM actúe, por ejemplo: "El rango 115–150 lpm no cabe en ninguna zona. Zonas: Z1 115–134, Z2 135–145, Z3 146–157. Reescribe esa línea con una sola zona, o Z1–Z2 si es un rodaje suave."
4. Si el segundo intento también falla, Kairos se queda con la versión con menos fallos, añade al pie "⚠️ Comprobación de Kairos" con lo que no cuadra y lo registra en `agent.log` con el ID del sensor.

En el código: `TrainerAgent._apply_output_sensors(texto, mensajes, tipo)` sobre las tres salidas del LLM (capa de coaching, respuesta final del bucle con tools y rescates). El reintento recibe los mensajes de sistema y de usuario y su propia respuesta, sin tools ni resultados de tools, con `prompts/system/sensor_correction.md`. Cada salto queda en `agent.log` como `[SENSOR] <tipo>: <ID> <mensaje>`.

El reintento solo cuesta latencia cuando hay un fallo. Cada sensor registra cuántas veces salta, para el bucle de gobierno.

## 7. Bucle de gobierno

- **Fallo que se repite → control nuevo.** Si un fallo aparece en dos baterías, se añade o se corrige una guía (si el LLM no tenía el dato o la regla) o un sensor (si la tenía y no la cumplió).
- **Proponer–Probar–Componer a mano.** Cada cambio de guía o de sensor se prueba con una batería antes y otra después, con los criterios fijados antes de mirar. Un sensor nuevo se pasa primero sobre transcripciones ya guardadas en `.tmp/bateria/` y debe dar 0 falsos positivos antes de conectarlo a Kairos.
- **Biblioteca de experiencia.** Cada cambio adoptado deja en su entrada del `CHANGELOG.md` la **señal** (qué falló y dónde), la **lección** (qué regla general se saca) y la **evidencia** (baterías antes y después). Las entradas del 23 al 26/10 están en git: `git show 6aff019:docs/proximos_pasos.md`.
- **Guía ≠ rúbrica.** Los prompts describen el trabajo de entrenador; nunca copian el texto del juez ni mencionan la batería.
- **Sin hooks genéricos.** El `HookManager` (cinco eventos en unos 12 puntos de `chat()`, sin ningún suscriptor) se quitó el 23/10: los sensores de §6 se llaman de forma explícita, que se lee y se prueba mejor. Sus avisos de error pasaron a `_log_chat_error` (`log.warning` con la etapa), que antes no dejaban rastro.

## 8. Rúbrica de la batería (S19)

`run_bateria.py` lanza las 25 preguntas de `docs/bateria_p55_preguntas_base.txt` contra Kairos real y guarda la transcripción con tiempos (`.tmp/bateria/`). La pinta a 400 columnas, para que cada fila de tabla y cada párrafo queden en una línea como en el texto que ven los sensores en vivo (110 hasta el 26/10: Rich partía las celdas), y sin leer ni escribir la memoria del atleta (`KAIROS_SESSION_MEMORY=false`, desde el 26/10). `juez_bateria.py <transcripción>.md` la contrasta con Supabase (`load_metrics_daily`, `activity_tss`, perfil, planes) y, para los récords, con Garmin en solo lectura; reutiliza funciones de producción (`form_zone`, umbral por fecha, `_resolve_hr_zones`, familia de deporte). Rúbrica fijada antes de ver resultados (22/10/2026):

- Globales (G1–G5 del juez): hay respuesta y coaching; Garmin accesible (si no, fallo de entorno); fechas DD/MM/AAAA; nada de "datos disponibles" sin cifras (declarar que falta el dato, "sin datos disponibles", es correcto desde el 23/10); sin turnos de usuario inventados.
- Por pregunta: CTL/ATL/TSB y zona de Forma = serie del día; TSS semanal, diario y por actividad = `load_metrics_daily` / `activity_tss`; actividades de un rango ⊇ `activity_tss`; análisis de la actividad correcta con su TSS y su Carga Garmin (`native_load`); recuento y desglose por deporte; umbrales y su historial = perfil; plan activo = `training_plan`; récords = Garmin; objetivo = `goals`; últimas 20 por deporte = `activity_tss` (orden, TSS, carga, media y máximo).
- En el texto del LLM: no atribuye a la CTL el máximo habitual de la ATL; cada rango de FC en lpm cabe en una zona (zonas de Garmin guardadas en el perfil, las del reloj y TP; desde el 22/10, antes % del LTHR; las de ciclismo si la línea habla de bici o ciclismo y las de carrera en el resto, desde el 23/10), salvo un rango dentro de Z1–Z2 (rodaje suave: las dos zonas están bajo VT1 y dan el mismo estímulo; desde el 23/10, decisión del usuario; que un día de recuperación vaya solo en Z1 lo pide el prompt, el juez no lo comprueba) y salvo un tramo progresivo (desde el 23/10, decisión del usuario): en la misma línea, marcado como calentamiento, vuelta a la calma, enfriamiento o progresivo y con "de Zx a Zy" / "Zx–Zy", con la FC de inicio en la zona más baja nombrada y la de fin en la más alta; un tramo continuo entre dos zonas de Z2/Z3 para arriba ("Z2 alto / Z3 bajo", "Z4–Z5") sigue siendo fallo; un ritmo citado como umbral es el vigente ("Supraumbral" y "Subumbral" son nombres de zona, no el umbral; desde el 26/10); la diferencia de HRV sale de sus cifras en ms (S25), la etiqueta rTSS/hrTSS es la de los datos (S26), "mañana", "hoy"… van con su día (S27) y un día de la semana con su fecha (S28, desde el 26/10).
- Tolerancias: ±0,1 en TSS/CTL/ATL/TSB, ±1 lpm, ±2 s/km. No juzga la calidad del consejo (revisión humana).

Genera `<transcripción>.juez.md/.json` y sale con 1 si algo suspende. Primera pasada (22/10): detectó que la "Carga Garmin" de las salidas en bici con potencia mostraba el TSS de Garmin. 23/10: 25/25 tras el criterio de Z1–Z2 y tras la limpieza de restos.

## 9. Siguientes pasos

Los pendientes del harness (revisión de las líneas `[SENSOR]` con el uso real, metatooling) están en `docs/proximos_pasos.md`; lo hecho, con su señal, lección y evidencia, en `CHANGELOG.md`.
