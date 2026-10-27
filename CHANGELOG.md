# Changelog

Todos los cambios relevantes de Kairos Coach se registran en este archivo.

## 2026-10-27

### Fixed (tendencia de HRV con días que fallan, E9)
- `get_hrv_trend` (servidor MCP) pide la HRV día a día y descartaba sin rastro los días que fallaban: con un 429 a mitad, la tendencia salía con huecos y sin aviso. Ahora cada fallo queda en `garmin_mcp.log`, la respuesta trae `failed_days` y, si fallan todos, el error llega a Kairos, que avisa de que Garmin no respondió. Tests: 769.
- Descartado el "tiempo máximo en las llamadas a Garmin" (pendiente 3): la pregunta que esperó 57 min en la batería de la mañana coincidió con el portátil en espera moderna (registro System: Kernel-Power 506 a las 10:42, red cortada por política a las 10:52:54, 507 a las 11:49:15). Al despertar, la conexión estaba rota (`ConnectionResetError`) y el reintento funcionó. `garminconnect` ya pone 15 s a cada petición y reintenta 3 veces.
- Lección (la del 26/10, otra vez): antes de buscar un bloqueo, mirar si el equipo estaba despierto. Se apuntó un pendiente sin hacerlo.

### Changed (una sola fuente para la serie de carga, E8)
- Señal: la revisión del 27/10 encontró una copia de 120 días de la serie en el perfil (`perfil.load_metrics.series`, con `last`, `ranges`, `weekly` y `model`) que leían 11 sitios en lugar de `load_metrics_daily`: `/carga`, el estado de hoy, el TSS de la semana (como respaldo), las preguntas de datos, las tools `kairos_load_trends` y `kairos_correlate`, el contexto del motor de planes y del LLM, el chat y el arranque. Además, sin serie en la base de datos el arranque recalculaba el TSS en vivo (`_compute_load_fatigue_metrics`, sin el detalle de las actividades: un segundo TSS) y, si fallaba, usaba la copia del perfil.
- Ahora todo lee `load_metrics_daily` con `_load_metrics_series` / `_load_metrics_view`, y el estado de carga se deriva con `load_metrics.load_fatigue_from_series` (trasladada desde `trainer_agent`). Sin serie no hay estado de carga. El perfil guarda solo `load_metrics.formula_version`.
- El bucle día a día del PMC sale de `compute_and_persist_load_metrics` a `load_metrics.pmc_rows` (misma aritmética, CTL 42 / ATL 7, Forma con los valores de ayer). Fuera `_compute_load_fatigue_metrics`: sus tests usan ahora `pmc_rows` y `load_fatigue_from_series`, así que la regla del TSB de TP se prueba sobre el código que calcula la serie de verdad.
- `_resolve_sport_model_cfg` ya no lee el `model` del perfil: lo escribía Kairos solo con las constantes y nunca traía percentiles (comprobado en Supabase).
- Un test (`/carga` sin serie) consultaba Supabase de verdad; ahora con su parche. Comprobado con una URL de Supabase inválida: ningún test sale a la red.
- Lección: una copia "por rapidez" de un dato canónico acaba con lectores propios; mejor una función de lectura única desde el principio.
- Evidencia: tests 767; batería 25/25 antes y después, con las 21 respuestas deterministas idénticas y el bloque de datos de las 4 de coaching igual (cambia solo la redacción del LLM); arranque de la batería de 66 s a 23 s. Datos: la primera ejecución del código nuevo dejó `perfil.load_metrics` en `{"formula_version": 34}` (comprobado después: el resto del perfil intacto, 121 días en `load_metrics_daily`).

### Fixed (revisión del proyecto tras la limpieza, paso 1: sin cambio de TSS)
- El cálculo de carga pedía `get_activity_splits` a Garmin por cada trail nuevo y lo pasaba a `_estimate_trail_tss_high_precision`, que no lo usaba (lee el ritmo normalizado nativo). Fuera la llamada y el argumento `splits_raw` de toda la cadena: una llamada menos a Garmin por trail, mismo TSS.
- La ruta determinista de la FC umbral, sin umbral en el perfil, guardaba la del reloj solo en `hr_threshold_bpm`, sin la entrada de `hr_threshold_tp_history` que pone `/perfil umbral fc`: el hrTSS de trail y bici seguía sin referencia de TP. Ahora solo lee: muestra la del reloj y propone `/perfil umbral fc <lpm>` (decisión de método de `CLAUDE.md`: un solo número, cargado a mano, el mismo que en TP). Unidades en lpm (decía "bpm").
- Argumentos que la función recibía y no usaba: el ritmo umbral de carrera en toda la cadena de la marcha (`_estimate_walk_hike_tss` y sus dos modelos; el umbral de marcha es su propio anclaje), zonas y FC en `_estimate_walk_hike_tss`, `training_load_raw`/`ftp`/ritmo umbral en `_build_activity_analysis_block` (el análisis ya no pide el FTP para nada), `profile` en `_build_recent_tss_by_sport_markdown` y `_compute_effective_week_tss_from_series_and_activities`, `username` en `_handle_garmin_password_change` y dos de funciones internas.
- 20 de los 36 envoltorios de `trainer_agent.py` no los llamaba nadie (ni los tests): fuera. Quedan 16.
- `datetime.utcfromtimestamp` (obsoleto desde Python 3.12) pasa a `datetime.fromtimestamp(ts, tz=timezone.utc)`, mismo resultado.
- Tests: 767.

### Changed (limpieza: funciones duplicadas, D3a y D3b)
- Las 6 funciones de clasificación de deporte (`_is_trail_activity`…), `_percentile` y `_find_hr_zones_in_json` tenían una copia idéntica en `trainer_agent.py` (comprobado con el AST: solo cambiaban anotaciones y docstrings). Queda la de `load_metrics.py`, con los docstrings, y `trainer_agent` la importa. Tests: 766.
- `_parse_hr_zones_list` tenía dos versiones distintas: la de `load_metrics`, la del cálculo del TSS, no leía el formato real de `get_activity_hr_in_timezones` (objetos JSON concatenados, comprobado con Garmin real en la actividad 24764615547: `None` frente a 4 zonas). Queda una sola, la buena, en `load_metrics` junto con `_try_parse_json`; `trainer_agent` importa las dos. Decisión del usuario: sin subir `TSS_FORMULA_VERSION` ni recalcular, porque ninguna de las 226 actividades de `activity_tss` pasa por los respaldos que usan zonas (todas salen por su modelo principal: `running:pipeline_fisico`, `trail:tp_zones`, `cycling:power_ftp`…). Solo cambia el respaldo de una actividad nueva sin detalle de Garmin: zonas reales en vez de FC media. Test con el formato real. Tests: 767.

### Removed (limpieza: código que sobra, D2)
- Modo de contingencia del MCP (`KAIROS_MCP_ENABLE_FALLBACK`, caché en el perfil, ruta rápida local): apagado desde siempre porque la caché no caducaba. `contracts.py` se queda con los contratos y la normalización; la nota "Transparencia de datos" solo avisa de fallos de Garmin.
- Bloqueo de tools de escritura (`MCP_READ_ONLY`, `_is_write_mcp_tool`, `_build_mcp_read_only_block_message`): el servidor propio solo tiene tools `get_*`, y una tool fuera del esquema ya no llega al servidor (guardia de tools inexistentes del 24/10). El test del bucle lo comprueba con una tool de escritura.
- Interruptores que nadie activaba: `KAIROS_DETERMINISTIC_ROUTER` (`ToolRouter` sin `enabled`), `KAIROS_TRAIL_LOW_IF_DEBUG` (solo un log; el cálculo del trail no cambia) y `KAIROS_WELLNESS_REFRESH_ON_STARTUP` (los datos de hoy se piden siempre a Garmin, como ya pasaba).
- `running_session_inference`: se escribía en el perfil en cada cálculo de carga y nadie lo leía. El clasificador no modifica la actividad, así que el TSS no cambia.
- Compatibilidad con datos que ya no existen, comprobado antes en Supabase (0 planes, 0 resúmenes, ningún `hr_pct`): `hr_pct` en planes y en el prompt de sistema, `_strip_previous_reply` y el reintento sin `structured_workout` al guardar sesiones de plan.
- Sin uso: `_extract_splits_list`, `get_contract_registry`, `sensors.has_insulin_diabetes`, `_TRAIL_FAST_PACE_RAW_ZONES_SEC_PER_KM`, `ALL_ESSENTIAL_TOOLS` y el envoltorio `_classify_running_session_with_confidence` de `trainer_agent`.
- Datos: fuera del perfil `mcp_adapter_cache` (3,2 MB que se leían con el perfil en cada carga) y `running_session_inference`. Prueba sin escribir (solo esas dos claves, el resto idéntico), copia local, aplicación y lectura de vuelta: 8 → 6 claves.
- Unas 440 líneas menos en `agent/`. Tests: 766 (fuera los de lo quitado). Batería 25/25 antes y después; en la de después, la pregunta 25 esperó 57 min a Garmin hasta un corte de red (`ConnectionResetError` 10054) y repetida sola salió bien en 21 s.

### Changed (limpieza: documentación y menores, D1 y D4)
- README: protocolo de diabetes con o sin insulina según la medicación y lesiones aparte (decía "tipo 1 con insulina"); en "Estructura", `agent/diabetes.py`, `ia_provider.py`, `tool_groups.py`, `tools/check_rls.py`, `prompts/diabetes/` y `supabase/rls.sql` con su README; los pendientes están en `docs/proximos_pasos.md`, no en `CLAUDE.md`.
- `salir` decía "Cerrar sesión" (README, menú y ayuda), pero la sesión queda guardada: ahora "Salir de Kairos (la sesión queda guardada)". Sin cierre de sesión real.
- Sin recuentos de tests en README y `docs/Harness.md` (S18): se quedaban viejos.
- `docs/arquitectura.md`: sin "hooks" (quitados el 23/10) y con el metatooling en "Cada mensaje del chat".
- `CLAUDE.md`, "Pendiente": solo el recordatorio de `/perfil umbral fc`; la lista vive en `docs/proximos_pasos.md`.
- CI con Python 3.12 y 3.14 (los dos ordenadores usan 3.14).
- `.gitignore`: `agent.log*` y `garmin_mcp.log*`, para los ficheros rotados.
- Fuera el traslado de `NVIDIA_API_KEY` a `PROVIDER_API_KEY` (`main._migrate_legacy_key`, `ia_provider.legacy_key_env_var` y el respaldo en `_build_llm_client`): los dos ordenadores tienen ya solo `PROVIDER_API_KEY`. Tests: 783 (fuera el del traslado).

## 2026-10-26

### Changed (guías contra los errores de días y de HRV)
- Calendario para el LLM (`_calendar_block`, `prompts/system/calendar.md`): ayer, hoy, mañana y pasado mañana con su día y fecha, y esta semana y la que viene de lunes a domingo. Va en el bucle con tools, en la capa de coaching y en los dos rescates; "HOY" y "MAÑANA" del horizonte llevan su fecha. Antes solo recibía hoy y ayer en ISO y deducía él los días ("Mañana 26/10 (lunes)" un sábado 24/10: S27 y S28).
- HRV: la diferencia de la noche frente a su media de 7 días va ya calculada en los datos ("58.0 ms · 7d 59.0 ms · -2 % frente a su media de 7 días"), en el estado del día y en el bloque de diagnóstico. Antes el LLM hacía la cuenta y escribía "> 10 % por debajo" con 58 frente a 59 (S25).
- Los sensores S25, S27 y S28 se quedan como red de seguridad. En vivo: 4 preguntas de la exploración del 24/10 (pasado mañana, la semana que viene, estado de hoy, series mañana) y la batería base (25/25): 0 avisos de días ni de HRV en las respuestas finales; el LLM cita la cifra calculada ("69 ms (media 7d 62 ms, +11 %)"). S28 saltó una vez en "la semana que viene" ("Martes 28/10") y el reintento lo corrigió. Tests: 784.
- Lección: un error que se repite se ataca con una guía (darle el dato ya calculado), no solo con un sensor (`docs/Harness.md` §7); el sensor se queda como red.

### Fixed (repaso de sensores con las baterías de casa)
- Sensores de antes (`87e7717`) frente a los actuales sobre las 941 respuestas guardadas en casa, con las mismas llamadas que el juez y, aparte, S10–S12 sobre las 166 respuestas libres del LLM (las que en vivo también pasan por los sensores). Criterio fijado antes: solo pueden desaparecer falsos positivos y cada aviso nuevo se revisa a mano.
- Tres falsos positivos corregidos:
  - S10, "tirada" no contaba como carrera: "Tirada larga progresiva Z1→Z2 (115→145 lpm)" heredaba "bici" de la fila de encima (dos respuestas). `_RUNNING_LINE` incluye "tirada(s)" y "rodajes".
  - S10, el deporte escrito detrás del rango, dentro de su paréntesis: "(115→145 lpm running)" con una viñeta de cycling encima. Ese tramo cuenta para decidir el deporte.
  - S28, "del 26/10 al 01/11 (lunes a domingo)": el paréntesis es el rango de días, no el día del 01/11.
- El caso pendiente de S10 del 24/10 (105–124 y 159–179 en "Dame mis zonas de FC") ya no se reproduce: era la fila "Cycling" de la tabla de zonas, corregida ese mismo día.
- Resultado: siguen los avisos reales (S10: rodaje de carrera con la FC de bici 105–135; S28: "domingo 26/10"; S25 ×4; S27 ×3; G3 ×5) y 0 falsos positivos. Tests: 782.
- Lección: el deporte de un rango sale de su frase, y la frase incluye lo que va pegado detrás dentro del paréntesis; el vocabulario de carrera tiene que incluir la jerga ("tirada"). Repasar también las respuestas libres del LLM, no solo la capa de coaching: en vivo los sensores pasan por las dos.

### Investigado (bici sin potencia: no era un fallo del modelo)
- Pendiente "bici sin potencia > 2 h, 07/08: Kairos 65.9 frente a TP 93". Al mirar la sesión (23889452312, gravel) dura 96 min, no más de 2 h (FC media 111, máx. 145). Su gemela del 03/04 (22396245237, 99 min, FC media 112) daba Kairos 69.7. Verificada la fuente en TP (protocolo de outliers): el 93 se metió a mano por estimación (salida sin pedales de potencia); el hrTSS de TP es 62 (Kairos +6 %) y el del 03/04, 65 (+7 %). Referencia mal transcrita → se corrige la referencia, no el código. n = 2: sin calibrar.
- TP no tiene FC umbral de bici (solo de carrera), así que el hrTSS de bici usa la de carrera, como ya hacía Kairos. Las zonas de bici de Garmin (10 lpm por debajo de las de carrera) ya están en el perfil y solo se usan para los rangos de FC del texto (S10).
- Lección: antes de investigar un outlier, mirar en TP si el TSS es calculado o puesto a mano.

### Added (FC umbral cargada a mano: `/perfil umbral fc <lpm>`)
- En Garmin la FC umbral la detecta el reloj tras un test y no se cambia desde la web; Kairos la traía de Garmin una sola vez y no había forma de cambiarla, ni tampoco el umbral de TP (había que editar el perfil a mano). Decisión del usuario: un solo número tras el test, el mismo que se pone en TrainingPeaks. `/perfil umbral fc <lpm>` actualiza `hr_threshold_bpm` (hrTSS de fuerza, elíptica y remo; contexto del LLM) y añade la entrada de hoy a `hr_threshold_tp_history` (hrTSS de trail y bici sin potencia). Rige desde hoy (el TSS guardado no cambia, como con `/perfil umbral <mm:ss>`); las zonas siguen siendo las del reloj. Rango 120–230 lpm. En `/menu`, `/ayuda`, el README y el prompt de sistema (decía que la FC umbral se cambiaba con `/perfil umbral`, que solo cambia el ritmo).

### Added / Fixed (sensores: S28 nuevo, S11 y tablas partidas en el juez)
- **S28** (`find_wrong_weekday_date`): un día de la semana junto a una fecha ("domingo 20/10", "20/10 (domingo)") tiene que ser el día en que cae; sin año, el que deja la fecha más cerca de hoy. Reintenta en vivo y lo pasa el juez. Criterio fijado antes: 0 falsos positivos en las baterías guardadas y cada aviso revisado a mano. Sobre las 220 respuestas del ordenador 2 (173 pares día + fecha): 5 avisos, los 5 errores reales ("lunes 20/10 – domingo 26/10" para la semana del 19 al 25; "domingo 2/11", que es lunes). En casa se repite con las 891.
- **S11**: "umbral" solo cuenta como palabra suelta; en "Z5 Supraumbral" o "Z4 Subumbral" es el nombre de la zona ("Z5 Supraumbral (≥ 169 lpm) o ritmo 3:30–3:40 min/km" daba S11). En las 220 respuestas solo desaparece ese aviso.
- **Juez con tablas partidas**: `run_bateria` pinta a 400 columnas (antes 110): cada fila de tabla y cada párrafo en una línea, como el texto que ven los sensores en vivo. A 110, Rich partía la celda y "progresivo" quedaba en otra línea que su rango (batería 20261026_100523, pregunta 9).
- Batería de 25 a 400 columnas: 25/25, y la memoria del atleta sigue vacía después. `juez_bateria` pasa su salida a UTF-8 (en la consola de Windows, cp1252, los ✅/❌ del resumen la rompían).
- Corrección de la nota de pendientes: el 20/10/2026 fue martes, no lunes ("el domingo (20/10)" seguía siendo un error).

### Fixed (la batería ya no toca la memoria del atleta)
- Al comprobar la memoria entre sesiones en el ordenador 2 (funciona: resumen del día e historial en `session_context`), los resúmenes del 23 al 26/10 resultaron ser solo preguntas de batería; el LLM las habría recibido como temas del atleta. `tools/run_bateria.py` arranca Kairos con `KAIROS_SESSION_MEMORY=false`: no guarda historial ni resumen del día y no lee los anteriores, así que cada pasada tampoco depende de la anterior. La memoria del atleta se vació en Supabase (50 entradas de historial y 10 resúmenes, todo de baterías; prueba, aplicación y lectura de vuelta).

### Tests (inicio de sesión)
- Ramas de error de `_authenticate_or_register_user`: email vacío o sin @, sesión recuperada con contraseña de Garmin cifrada con otra `ENCRYPTION_KEY`, alta rechazada, contraseñas que no coinciden, alta fallida, alta sin confirmación por correo, error que no es de contraseña y fallo al crear el usuario de Kairos. También `/menu`. `agent/main.py` pasa del 90 % al 94 %. Sin test: los `except: pass` del ajuste UTF-8 de la consola de Windows (habría que recargar el módulo con `ctypes` roto para comprobar que no hace nada).

### Fixed (token de Supabase caducado tras suspender el equipo)
- El "cuelgue" de la batería de las 14:15 no lo fue: el log de Windows muestra el portátil en espera moderna de 14:06 a 15:14, con la red cortada a las 14:15:51 (14 s después de lanzar la llamada de coaching a NVIDIA). Al despertar saltó el `wait_for` (en log de nivel debug, por eso no se veía) y, al guardar el historial, Supabase respondió `JWT expired` (PGRST303): la excepción no la capturaba nadie y cerró Kairos. `run_bateria` contó 3.538 s porque su reloj incluye la suspensión.
- `storage._supabase()` comprueba la caducidad del token por la hora del reloj y lo renueva (`auth.get_session()`) si le queda menos de un minuto; el auto-refresco del cliente va con un temporizador que una suspensión deja atrás. Sin coste por consulta: solo relee la sesión cuando toca (o a los 30 s si la renovación falla).
- Un fallo al guardar el historial ya no se lleva la respuesta: se avisa en `agent.log` y sigue. El fallo de la capa de coaching (timeout incluido) pasa de debug a warning.
- Lección: antes de buscar un bloqueo del bucle de eventos, mirar si el equipo estaba despierto (eventos Kernel-Power 506/507 del registro System).

### Added (metatooling: grupos de tools según la pregunta)
- Antes, cada llamada del bucle con tools mandaba las 45 tools (≈ 6.000 tokens, de ≈ 20.000 de entrada por llamada). `agent/tool_groups.py` las reparte en 5 grupos (actividades, sueño y recuperación, día a día, rendimiento, semanal y cuerpo); reglas sobre la pregunta (sin LLM) eligen los grupos, y las `kairos_*` van siempre. Si ninguna regla encaja, van todas, como antes. El LLM puede pedir otro grupo con `kairos_pedir_herramientas`, y una tool real de otro grupo que pida por nombre se ejecuta igualmente y añade su grupo.
- Medición nueva en `agent.log`: tokens y número de tools de cada llamada (`[LLM] ◀ … tools= tokens entrada=`), resultados cortados a 3.000 caracteres y listas recortadas a 8 elementos (`[TOOL]`).
- Criterio fijado antes; 10 preguntas libres que van al bucle (`docs/bateria_tools_libres.txt`) y batería de 25. Sin grupos → con grupos: 45 → 17 tools por llamada de media; 20.024 → 15.636 tokens de entrada por llamada (−22 %; se esperaba −50 %: el prompt de sistema, ≈ 7.000, y el contexto pesan más que las tools); 480.567 → 343.990 en total (−28 %); mismas respuestas; la meta-tool no hizo falta (0/9); batería 25/25. La batería de 25 no pasa por el bucle con tools (todo es determinista o coaching sin tools). Resultados cortados: 1 de 24 llamadas en las dos pasadas.
- Lección: en un agente con rutas deterministas, el metatooling solo toca las preguntas libres, y el ahorro lo limita el resto del contexto; medir antes de estimar.

### Changed (.env.example limpio)
- Solo lo que se configura de verdad, en el orden del `.env` del ordenador 2: Garmin (con `ENCRYPTION_KEY`), `KAIROS_USERNAME`, Supabase, `PROVIDER_API_KEY` y proxy corporativo. Fuera lo arrastrado de versiones anteriores (proveedores de prueba, variables opcionales con su valor por defecto, explicaciones largas). Las opcionales siguen funcionando si se definen.
- `setup.ps1` / `setup.sh` generan `ENCRYPTION_KEY` también si la línea está vacía (la rellenan en su sitio); antes solo si faltaba la línea.

### Changed (proveedor de IA del atleta; fuera los 5 proveedores de prueba)
- Cambio de foco del usuario: los 5 proveedores (NVIDIA, Gemini, Groq, Mistral, Cerebras) eran para probar Kairos cambiando de modelo; ahora cada atleta usa su propio proveedor. Sustituye al menú de la entrada "modelo elegido por el atleta" de este mismo día.
- La primera vez Kairos pregunta "¿Cuál es tu proveedor de IA?" (texto libre, sin menú). Si lo conoce (`agent/ia_provider.py`: NVIDIA, OpenAI, Gemini, Anthropic, Groq, Mistral, Cerebras, OpenRouter, DeepSeek) rellena la URL y propone un modelo; si no, pide la URL de su API compatible con OpenAI y el modelo. Después, la clave: se prueba con 1 token y se guarda en `.env` en `PROVIDER_API_KEY`, la misma variable para cualquier proveedor, con un comentario encima que dice cuál es (petición del usuario). Un `.env` antiguo con `NVIDIA_API_KEY` (u otra `<PROVEEDOR>_API_KEY`) se traslada solo la primera vez, sin volver a pedirla. Al cambiar a otro proveedor con `/proveedor` se pide siempre su clave.
- Tabla `ia_provider` (sustituye a `gemini_usage`, que se elimina): proveedor, URL, modelo y consumo diario de tokens (90 días) por atleta, con RLS. La clave nunca va a la base de datos. Si la tabla falta, Kairos dice que hay que aplicar `schema.sql` y `rls.sql`.
- `/modelo` pasa a `/proveedor` (mismas preguntas; con el mismo proveedor, la clave solo se cambia si se pide).
- Fuera: menú de modelos, `_PROVIDER_INFO`, `_check_env`, cliente nativo de Gemini (`google-genai`; Gemini va por su API compatible con OpenAI), límites diarios por proveedor y avisos de cuota. Se mantiene la renovación de la clave caducada (`LLMAuthError`). `KAIROS_PROVIDER` desaparece; `KAIROS_MODEL` cambia el modelo en una ejecución (batería `--modelo`).

### Changed (modelo elegido por el atleta, clave en .env)
- Petición del usuario (productizar Kairos): la primera vez, el atleta elige el modelo en un menú con los cinco proveedores (ya no solo los que tienen clave), Kairos le pide la clave, la prueba con una llamada de 1 token (`probe_llm_key`) y la guarda en `.env` (`set_key`). La elección va a su perfil (`llm.provider`, decisión del usuario: le sigue en cualquier ordenador). Las siguientes veces no hay menú; en otro ordenador sin la clave, solo se pide la clave. `/modelo` cambia y guarda la elección.
- Clave caducada o revocada: cualquier llamada al LLM que el proveedor rechace marca `llm_auth_failed` y lanza `LLMAuthError` (un `RuntimeError`); la CLI pide la clave nueva, la guarda en `.env` y repite la pregunta. Antes un 401 en la capa de coaching (`openai.AuthenticationError`) no lo capturaba nadie y cerraba la sesión.
- Comprobado en vivo: NVIDIA rechaza una clave mala con 403 "Authorization failed", no 401; se trata como clave rechazada (un 403 del proxy, no).
- `KAIROS_PROVIDER` fija el proveedor sin menú ni perfil; `tools/run_bateria.py` lo usa (`--proveedor nvidia`) y ya no responde al menú.
- El arranque desglosa las herramientas: "45 (42 de Garmin + 3 de Kairos)"; las 3 son `kairos_load_trends`, `kairos_correlate` y `kairos_weekly_sport_breakdown`, que se resuelven sin el servidor MCP. Tests: 766.

### Tested (perfiles de salud en vivo)
- Kairos real con el perfil sustituido solo en memoria (sin escrituras en Supabase, memoria y base de conocimiento vaciadas) y 4 preguntas de coaching. Criterio fijado antes. Sano con periostitis: 0 menciones a la diabetes, periostitis en cada plan (cumple). DT2 + insulina: protocolo de hipoglucemia en cada sesión (cumple). DT2 + metformina: sin nota S16 ni carbohidratos preventivos, pero el LLM nombra la hipoglucemia para tranquilizar (criterio literal de 0 menciones no cumplido; aceptado por el usuario).
- `prompts/diabetes/sin_insulina.md` ya no nombra la hipoglucemia (título, entrada y viñeta de nutrición): su título la cebaba (3 de 4 respuestas → 2 de 4). Test que lo vigila.

### Added (lesiones a lo largo de la temporada)
- `coaching_rules.md`, sección "Lesiones, sobrecargas y molestias a lo largo de la temporada" (sustituye a "Otras lesiones"), por petición del usuario: una lesión del perfil condiciona cada consejo y plan, y antes de subir carga se pregunta cómo va; una molestia nueva contada en el chat se toma en serio aunque no esté en el perfil (preguntar lo justo, cruzar con la carga y la recuperación, decidir con criterio de dolor, rehacer los próximos días y pedir que se apunte con `/perfil editar salud`); señales de alarma; vuelta progresiva por escalones (≤ 3/10 que no empeore al día siguiente); sugerir quitarla del perfil cuando se supera; prevención si se repite. `/ayuda` y `/perfil` explican que ahí van las lesiones nuevas.
- En vivo ("me duele el sóleo desde ayer, 4/10…" y "ya no me duele"): pregunta por la evolución, relaciona la molestia con la tirada larga reciente, cambia la semana a bici/elíptica con escalones de vuelta, pide apuntarla en el perfil y, al decir que ya no duele, exige un test de carga antes de volver.

### Fixed (S27: una puntuación de dolor no es una fecha)
- "te duele desde ayer (4/10)" daba S27 (leía el 4 de octubre). Un "D/10" sin año, con D ≤ 10 y una palabra de dolor o escala antes en la misma línea, ya no se toma como fecha. Las tres baterías del 26/10 dan el mismo veredicto que antes. Tests: 751.

### Added (Kairos para atletas sanos, con DT1, con DT2 o con otra patología)
- Patologías y lesiones por separado (`health.pathologies` y `health.injuries`): un atleta puede tener periostitis y ser diabético, o no tener patologías y tener una lesión (sigue siendo sano). Formulario: patologías, medicación si hay diabetes, lesiones y notas. `/perfil`, el perfil del LLM ("Patologías", "Lesiones") y la base de conocimiento las muestran por separado. Los perfiles anteriores se separan solos al arrancar: lo que en lesiones menciona diabetes pasa a patologías (otra enfermedad escrita allí se corrige con `/perfil editar salud`). Así, la DT1 ya no reduce la dificultad del plan como "lesión activa".
- Diseño del usuario (26/10): la configuración inicial y `/perfil editar salud` preguntan por enfermedades o patologías. Si la respuesta (o las notas) menciona diabetes tipo 1 o tipo 2, en el mismo formulario se pregunta la medicación (lista numerada con varias opciones: insulina lenta, rápida, bomba, metformina, sulfonilureas, glinidas, iSGLT2, aGLP-1, iDPP-4; más otra a mano; Enter = solo dieta), en `health.diabetes_medication`. Sin patologías es un atleta sano: ninguna pregunta más y el perfil del LLM lo dice ("ninguna declarada"); las reglas piden no planificar pensando en patologías. Otra patología: la valora el LLM con lo que diga el perfil.
- Lógica en `agent/diabetes.py`: tipo detectado en el texto (DT1/DM1/tipo I, DT2/tipo II, LADA o "diabetes"; "prediabetes" no cuenta) y protocolo según la medicación.
- Perfiles anteriores con diabetes en sus patologías: al arrancar se pregunta la medicación una sola vez (`_ensure_health_profile`). A un perfil sin diabetes nunca se le pregunta.
- El protocolo sale de `coaching_rules.md` y va con el perfil (`_profile_context_block`, que usan el bucle con tools, el coaching y los dos rescates) solo a quien lo necesita: `prompts/diabetes/insulina.md` con riesgo de hipoglucemia (DT1, o insulina, sulfonilureas o glinidas; el de siempre, más lo que estaba repartido en las reglas: seguridad glucémica en cada sesión, HRV y control glucémico, hipoglucemia grave), `prompts/diabetes/sin_insulina.md` para DT2 u otra sin ese riesgo (protocolo nuevo), y ninguno sin diabetes. Sin protocolo no se habla de glucemia.
- `/perfil` y la base de conocimiento inicial muestran la diabetes con su medicación; `/ayuda` ya no habla de DT1 a todos. S16 (nota glucémica) sigue al protocolo: sin riesgo de hipoglucemia o sin diabetes, no se añade.

### Fixed (sensor S10: marca de progresivo detrás del rango)
- `_progressive_range_ok` solo buscaba la marca de progresivo y el "de Zx a Zy" antes del rango. Batería del 26/10 (ordenador 2), pregunta 9: "3×2000 m Z3–Z4 (146–168 lpm, progresivo de Z3 a Z4)" daba S10 aunque el rango es justo Z3 + Z4 (24/25). Ahora también cuenta lo que va detrás del rango en su misma frase o paréntesis (hasta ")", ";", ". " o el salto de línea); si ahí hay marca y "de Zx a Zy", mandan esas zonas. Una marca en otra frase no vale.
- Criterio fijado antes: pasa ese caso, los tests de S10 siguen en verde y en las respuestas guardadas solo desaparecen avisos que son falsos positivos de este tipo. En este ordenador solo hay la batería del 26/10 (la del 23/10 tiene las respuestas vacías): 25/25. El repaso de las baterías guardadas en el ordenador 1 queda pendiente. Tests: 729.

## 2026-10-25

### Added (sueño: una noche concreta y tendencias)
- Una noche concreta ("¿cómo dormí el martes?", "ayer", "el 20/10", "el 18 de octubre"): la ruta de anoche pasa a `sleep_night` y acepta el día. Fecha de Garmin = día en que te despiertas, y la respuesta lo dice ("la noche que terminó el martes 20/10/2026"); un día de la semana es el último que ha pasado y una fecha sin año nunca es futura. Sin datos de esa noche, lo dice.
- Tendencias ("¿cómo he dormido últimamente?", "las últimas 10 noches", "este mes"): ruta mixta `sleep_trend`. Python calcula las últimas N noches (7 por defecto, 14 con "últimas semanas", 30 con "mes") frente a las N anteriores: sueño y puntuación medios, % de profundo y REM, noches por debajo de 6 h, noches sin datos y diferencias; con 14 noches o menos, noche a noche. Después, siempre, la lectura del LLM con su prompt (`prompts/coaching/sleep_trend.md`: lectura, relación con el entrenamiento y la glucemia, qué cambiar) y sin recalcular cifras. `_augment_with_llm_coaching_if_needed` admite `always`, `prompt` y `horizon`.
- Las noches se piden de 4 en 4 (el servidor atiende 4 llamadas a Garmin a la vez y Kairos corta cada una a los 8 s).
- En vivo: una noche, 2–3 s; tendencia de 7 y de 30 noches, 22 s con la lectura del LLM. Cifras comprobadas contra Garmin directamente (las dos últimas semanas salen iguales de verdad: 194.685 s frente a 194.702 s). Batería 25/25. Tests: 726.

### Added (sensor S27: palabra relativa y día)
- `find_wrong_relative_day`: "mañana", "pasado mañana", "hoy", "ayer" o "anteayer" seguidos de un día de la semana o una fecha que no les toca respecto a hoy. Caso de la auditoría del 24/10 (sábado): a "¿qué hago pasado mañana?" el LLM respondía "Mañana 26/10 (lunes)". Solo se juzga si hay día o fecha junto a la palabra; "mañana" como parte del día ("por la mañana", "esta mañana") no cuenta. Reintento en vivo con la fecha de hoy y en el juez con la de la transcripción.
- Criterio fijado antes: 0 falsos positivos en las baterías guardadas y cada aviso revisado a mano. Sobre 554 respuestas con fecha: 3 avisos, los 3 el error real (24/10, "mañana" con el lunes 26/10). Tests: 722.

### Tests (cobertura de la CLI, `agent/main.py`)
- El despacho de comandos sale del bucle de `main()` sin cambiar su comportamiento: `_handle_cli_command` (/perfil, /perfil editar…, /perfil umbral, /perfil fc, /carga, /plan…, /menu, /ayuda) y `_switch_provider` (/modelo).
- `tests/test_main_cli.py` (55 tests): cada comando, pantallas de perfil, planes y carga (con la tabla real), preguntas de objetivos y salud con valores inválidos, cambio de modelo, comprobaciones de arranque (proveedor, claves, Supabase, Garmin, contraseña de Garmin cambiada, logging solo a fichero) y `main()` de principio a fin con dobles (respuesta, error del LLM, Ctrl+C, usuario nuevo, Garmin sin acceso).
- Criterio fijado antes: ≥ 70 % y un test por comando. Cobertura de `agent/main.py`: 34 % → 89 %. En vivo, los comandos por terminal responden igual. Tests: 718.

### Added (sensores S25 y S26)
- S25 `find_wrong_hrv_deviation`: una línea con la HRV y su media en ms no puede afirmar una diferencia en % que no sale de esas cifras. Caso real del 24/10: "anoche 58 ms (media 7 d 59 ms) → > 10 % por debajo" (es −1,7 %). Conservador: basta con que una pareja de cifras de la línea justifique lo afirmado, y no cuentan los umbrales de alerta (una sola cifra o línea con "si…").
- S26 `find_swapped_tss_label`: un TSS citado como hrTSS que en los datos es rTSS (o al revés). Solo cifras con decimal pegadas a la etiqueta en el texto y en los datos; "TSS" a secas no es error.
- Los dos piden reintento en vivo (`findings_to_retry`) y los pasa el juez. Criterio fijado antes de medir: detectar el caso real y 0 falsos positivos en las baterías guardadas (891 respuestas, 133 con coaching). S25: 4 avisos, los 4 el error real (dos coaching, una tabla del bucle con tools y otra respuesta de la exploración); un falso positivo en la primera versión ("60 ms vs. ~67 ms de referencia", orden invertido), corregido. S26: 0 avisos (el caso no está en las baterías guardadas; cubierto con tests). Batería 25/25 sin saltos. Tests: 663.
- Sin sensor: llamar "mañana" a pasado mañana (lo frena la guía del horizonte, `_coaching_horizon`).

### Added (rutas deterministas: periodo y sueño de anoche)
- "¿Cuántos km he corrido este mes?", "¿cuál fue mi actividad más larga de septiembre?", "¿mi tirada más larga este año?": ruta `period_activities`. Periodos: este mes, el mes pasado, un mes por su nombre (con año opcional; un mes posterior al actual sin año es el del año pasado), este año, esta semana y la semana pasada. Deporte por la pregunta (corrido/tirada/rodaje → carrera y trail; bici; nadar; caminar; trail) o todas. Kilómetros: tabla por deporte (actividades, distancia, tiempo, desnivel) y la lista; la más larga: por distancia y, si es otra, por tiempo, con las 5 primeras. Pide todas las páginas de `get_activities_by_date` (el servidor da 100 por página y un año tiene más).
- "¿Cómo dormí anoche?", "¿cuánto he dormido hoy?": ruta `sleep_last_night` (`get_sleep_summary` de los 8 últimos días en paralelo): duración, puntuación, fases y media de las noches anteriores. Si el reloj no ha sincronizado la de hoy, lo dice y muestra la última registrada con su fecha. Antes devolvía la tabla factual de todo el día. Las tendencias, otra noche (ayer, un día o una fecha) y las decisiones de entrenamiento ("dormí mal, ¿puedo entrenar?") siguen por su ruta.
- En vivo frente a Garmin (suma directa del servidor, sin Kairos): octubre 151,42 km en 11 carreras, septiembre 309,24 km en 29 actividades, 149 carreras en el año, la más larga la misma; 2–3 s por pregunta de un mes, 22 s la del año (la descarga de Garmin de 285 actividades). Antes: 144 s y la actividad equivocada (exploración del 24/10). Batería base 25/25. Tests: 656.

## 2026-10-24

### Changed (fiabilidad frente a Garmin: llamadas en paralelo)
- Servidor MCP: FastMCP ejecutaba las tools síncronas dentro de su bucle de eventos, así que una petición lenta a Garmin (hasta 30 s en `garminconnect`) paraba el servidor entero y las demás se encolaban. Ahora cada tool se ejecuta en un hilo (`anyio.to_thread`) con un máximo de 4 llamadas simultáneas a Garmin (evita ráfagas que lleven a un 429), conservando la firma de cada tool (esquema y catálogo sin cambios). El cliente y los logins de Garmin van con un cerrojo: si varias llamadas encuentran los tokens caducados, solo una rehace el login.
- Kairos: las tools que el LLM pide en un mismo mensaje se lanzan a la vez (`asyncio.gather`, resultados en orden). El cortacircuitos se aplica a los mensajes siguientes de la respuesta.
- No se baja el límite de 30 s de `garminconnect`: va fijo en cada petición y cambiarlo exigiría parchear la librería; con el paralelismo una petición lenta ya no bloquea las demás.
- En vivo: 6 tools reales, 3,14 s en serie y 1,36 s en paralelo, sin errores. Tests: 651 (4 nuevos del servidor y el cortacircuitos rehecho).

### Security (Supabase Auth y seguridad por fila)
- Las 11 tablas tenían RLS desactivado y Kairos usaba la clave `anon`: con la URL y esa clave (públicas, en cada `.env`) se podían leer y modificar los datos de todos los usuarios, incluido el hash de la contraseña y la contraseña de Garmin cifrada (`tools/check_rls.py`: 8 tablas visibles sin sesión). Kairos además leía `app_user` antes de iniciar sesión.
- Acceso con Supabase Auth (email + la misma contraseña que en Garmin): `storage.restore_session` (sesión guardada en `~/.kairos/supabase_session.json`, sin contraseña), `sign_in`, `sign_up` (con confirmación de email), `create_app_user`, `update_login_password`. Fuera la autenticación propia (`find_user_by_username`, `authenticate_app_user`, `register_app_user`, PBKDF2): nada se lee de la base de datos antes de entrar. El juez y `rebuild_tss_baseline` usan la sesión guardada.
- `schema.sql`: `app_user.auth_user_id`, `password_hash` opcional, funciones `kairos_app_user_id()` y `kairos_claim_app_user()` (vincula la cuenta con su `app_user` anterior por email, solo si está confirmado), y deja de desactivar RLS en cada ejecución. `rls.sql`: activa RLS y crea las políticas (tu fila de `app_user`; tus filas en las 8 tablas con `app_user_id`; las de tus planes en sesiones y versiones). `tools/check_rls.py` / `make check-rls`.
- Puesta en marcha en dos fases y explicación de los conceptos en `supabase/README.md`. Comprobado que el proyecto exige confirmar el email (`mailer_autoconfirm = false`), requisito del vínculo por email. Tests: 647.
- Aplicado el 24/10 en el proyecto real. Fase 1: `schema.sql`, cuenta creada y confirmada, vínculo comprobado (`kairos_app_user_id()` = el `app_user` de siempre, 225 actividades intactas); batería 25/25 entrando con la sesión guardada. Fase 2: `rls.sql`; `make check-rls`: 0 filas sin sesión en las 11 tablas (antes 8 abiertas) y con sesión solo la propia fila de `app_user`. Escrituras: sin sesión y a nombre de otro, rechazadas por la política; a nombre propio, permitidas (fila de prueba borrada). Batería 25/25 con RLS activo y ningún rechazo en `agent.log`. El segundo `app_user` sigue sin vincular hasta que entre con su cuenta.

Auditoría antes de producción: análisis estático, cobertura y tres sesiones exploratorias con Kairos real por terminal (35 preguntas fuera de la batería, sin escrituras de usuario). Batería base 25/25 al final (coaching 114 s). Tests: 638.

### Fixed (datos del usuario)
- Una respuesta genérica del LLM ("necesito más información") a una pregunta de planificación, de mañana o de valoración, con objetivo en el perfil, creaba en Supabase un plan "activo" de 0 semanas y sin sesiones, y `create_training_plan` desactivaba el plan activo real. Ahora pasa por el rescate con LLM y nunca escribe; los planes solo los crea el motor de planes cuando se piden. El rescate de sesión usa el horizonte de la pregunta (antes decía siempre "para mañana").

### Fixed (respuestas incorrectas o vacías)
- Router: "¿puedo hacer series hoy?" → estado del día (el LLM libre decía "descanso" con 0 señales alteradas y datos del 22/10); "compara mi HRV de esta semana con la pasada" → LLM (devolvía la tabla de un día); "¿cómo fue mi semana del 05/10 al 11/10?" y "¿qué día de esta semana tuve más carga?" → TSS semanal (agotaba las 15 llamadas a tools; el LLM leía mal la serie y daba un día de otra semana). La respuesta semanal añade "Día con más carga" y lleva tildes en los días.
- "¿Qué tal fue mi última salida en bici?" / "última carrera" → detalle de actividad (el LLM dijo "anterior a agosto"; fue el 12/08). La búsqueda de la última por deporte solo da por bueno un intento si hay alguna de ese deporte (antes, cualquier actividad en 30 días cortaba la búsqueda) y reconoce `road_biking`.
- Horizonte: "la semana que viene / próxima semana" ya no se trata como esta semana ni "pasado mañana" como mañana (`prompts/coaching/horizon_next_week.md`, `horizon_day_after.md`).
- Fechas imposibles (31/02, 30/02, 31/04, 29/02 de año no bisiesto): Kairos lo dice en vez de responder con los datos de hoy.
- Herramienta inexistente pedida por el LLM ("goals"): no se manda al servidor (se avisaba de que Garmin no respondía); se le dice qué hay. Nombres de tool con mayúsculas ("Kairos_weekly_...") se normalizan.
- Cortacircuitos: tras 2 timeouts de tools en una respuesta no se llama más a Garmin (garminconnect espera 30 s y aquí se corta a 8 s; las llamadas se encolaban: 5 timeouts y 144 s en una pregunta).
- `/perfil` y la base de conocimiento inicial muestran la fecha del evento en DD/MM/AAAA.

### Fixed (sensores)
- S10: el deporte se decide por la frase y, si no lo nombra, por la línea de contexto anterior (encabezado "Cycling" sobre una tabla de zonas), sin cortar en "|" ni heredar el deporte de otra viñeta; los rangos descriptivos ("tu FC se estabilizó en 159–178") no se juzgan; "Calent." abreviado cuenta como progresivo. S11: una meta cerca de "umbral" ("ritmo meta ≈ 3:55", "bajar el umbral a 3:55") no es el umbral. S16: solo con una prescripción real (estructura de sesión o propuesta), no por "rodaje" en una lista de actividades o una tabla de zonas. 0 diferencias frente al juez original en 350 casos.
- Juez: juzga cada transcripción con los datos de su día (nombre `AAAAMMDD_`); antes comparaba siempre con la última fila de la serie y una batería de ayer salía 21/25.

### Changed (logs)
- El logging se configura al arrancar Kairos, no al importar `agent.main` (los tests escribían en `agent.log` y mezclaban líneas `[SENSOR]` falsas con las reales). `KAIROS_LOG_STDOUT` es `false` por defecto (los logs y los volcados de JSON salían en la terminal); los tres volcados de JSON pasan a DEBUG. `agent.log` es rotativo (5 MB × 3). El stderr del servidor MCP va a `garmin_mcp.log` (`KAIROS_MCP_LOG_FILE`) en vez de a la terminal.
- Repo: commit-graph de git regenerado (faltaba un fichero de la cadena; los objetos estaban sanos).

## 2026-10-23

### Added (pre-commit y lint en la CI, paso 6 de `docs/Harness.md`)
- `.githooks/pre-commit`: ruff y los tests sin red antes de cada commit; se activa con `make hooks` (`git config core.hooksPath .githooks`) y lo activan `setup.ps1` y `setup.sh`. Comprobado que bloquea (estado 1 con un nombre sin definir).
- Lint obligatorio fijado en `pyproject.toml` (`[tool.ruff]`, `target-version = py311`, `select = ["F", "E9"]`): pyflakes y errores de sintaxis, hoy a cero. Antes ruff aplicaba las reglas por defecto de la versión instalada (`ruff>=0.6.9` sin techo), así que la CI podía cambiar de reglas sola; ahora `ruff>=0.16.10,<0.17`. Ruff no tiene línea base: en vez de llenar el código de `noqa`, el resto de familias (E, W, B, I, UP, SIM, DTZ, RUF) queda como informe que no bloquea (`make lint-informe`).
- CI: paso de lint antes de los tests.

### Changed (paso 5 de `docs/Harness.md`: planes en zonas de Garmin y marco de la sesión de hoy)
- Motor de planes en zonas de Garmin (decisión del usuario: zona principal, RPE complemento). Cada sesión se muestra como "Z2 Aeróbica (135–145 lpm) · RPE 3-4" (`_session_intensity_label`, calculado al mostrar con las zonas actuales, así vale para planes guardados); fuerza sigue en RPE y el descanso dice "descanso". En el bloque "Plan activo" del LLM, al crear el plan, en `/plan ver` y en el resumen de la sesión resultante ("Z2 Aeróbica" en vez de "endurance"). Los planes nuevos guardan `hr_zone` (1-5; calentamiento de Z1 a Z2) en vez de `hr_pct` (% del LTHR de Friel, que no casaba con Garmin); el validador acepta los dos. Sin cambios en Supabase.
- Fix: el ajuste diario multiplicaba el rango de intensidad por el factor de volumen (85–89 % del LTHR pasaba a 59,5–62,3 %); ahora baja una zona de Garmin y los planes antiguos conservan su rango.
- Marco de la sesión de hoy (G12, `_build_today_session_frame`, `prompts/coaching/session_frame.md`): en la ruta de estado del día y para hoy, el LLM recibe la decisión del motor, la sesión, las zonas permitidas con lpm (suave: Z1–Z2 con techo; reducir: hasta Z3; descanso: ninguna) y la duración máxima, y redacta dentro de él; S15 lo hace cumplir.
- Sensor S10, dos falsos positivos y un fallo de detección vistos en las baterías y corregidos: el deporte se decide por la frase o la celda del rango, no por la línea (una fila de tabla con el rodaje y una alternativa en bici dejó pasar un rodaje con zonas de bici, 105–135 lpm); un rango con otra unidad no es de FC aunque haya una zona cerca ("Z1–Z2 → agua 150–250 ml"); y el aviso al pie de Kairos no se juzga. Primeros saltos del sensor en vivo: un reintento corregido (11 s) y el falso positivo de la hidratación.
- Baterías: 24/25 con el rodaje en zonas de bici (error real del LLM que el sensor no vio por la detección por línea); 24/25 con el falso positivo de la hidratación; tras los ajustes, 25/25, coaching 198 s y ningún salto. Tests: 623.

### Added (sensores S14–S16, paso 4 de `docs/Harness.md`)
- S14: cada CTL, ATL o TSB que cita el LLM aparece en lo que recibió (contexto, datos deterministas, resultados de tools), con la tolerancia del redondeo; reintenta. S15: con decisión del motor diario de descanso, suave o reducir, la sesión de hoy no lleva Z4–Z5 ni FC desde el inicio de Z4 (solo respuestas para hoy, desde la ruta de estado); reintenta. S16: con diabetes tratada con insulina en el perfil, si la respuesta prescribe una sesión sin hablar de glucemia, se añade una nota fija sacada del protocolo de las reglas; corrige sin LLM.
- Falsos positivos medidos antes de conectarlos, sobre las 40 respuestas con coaching de las baterías del día: 6 avisos y los 6 eran del sensor (un umbral condicional, un "+46 %" cortado en "+4", ml de hidratación, cadencia y un reparto semanal con calidad otro día). Corregidos: 0.
- Batería 25/25; las 4 preguntas de coaching suman 141 s. Ningún sensor saltó ni hizo falta la nota glucémica (el LLM ya la incluye). Tests: 616 (13 nuevos).

### Added (autocorrección en vivo, paso 3 de `docs/Harness.md`)
- `TrainerAgent._apply_output_sensors` pasa los sensores de `agent/sensors.py` a las tres salidas del LLM (capa de coaching, respuesta final del bucle con tools y rescates) antes de enseñarlas. Las fechas ISO se corrigen sin LLM (`fix_iso_dates`). Si falla un rango de FC, el ritmo umbral, la CTL con el máximo de la ATL o el marcador vacío, una sola llamada más sin tools le devuelve al LLM las pistas (`prompts/system/sensor_correction.md`) y Kairos se queda con la versión con menos fallos; lo que siga fallando se avisa al pie ("⚠️ Comprobación de Kairos") y queda en `agent.log` como `[SENSOR]`. El reintento ve los mensajes de sistema y de usuario y su propia respuesta, nunca resultados de tools.
- Batería 25/25 y suma de las 4 preguntas de coaching 180 s (rango del día: 151–240 s). En esta pasada ningún sensor saltó (sin líneas `[SENSOR]` en el log), así que el reintento no se ejercitó en vivo: lo cubren 5 tests (se corrige en un reintento, fallo persistente con aviso, texto limpio sin llamada extra, fechas sin LLM, el reintento no ve tools). Tests: 603.

### Removed (`HookManager`)
- Decisión del usuario: fuera el `HookManager` (cinco eventos emitidos en unos 12 puntos de `chat()` sin ningún suscriptor en producción). Los sensores sobre la respuesta del LLM se llaman de forma explícita. Las 7 llamadas de aviso de error pasan a `_log_chat_error`, un `log.warning` con la etapa y el error: antes no dejaban rastro (sin suscriptores, `on_error` no hacía nada). `tests/test_hooks_router.py` pasa a `tests/test_tool_router.py` con los tests del router. Tests: 598.

### Added (`agent/sensors.py`, paso 2 de `docs/Harness.md`)
- Las comprobaciones del juez sobre el texto del LLM pasan a un módulo compartido con funciones puras que devuelven hallazgos con su mensaje y una pista para que el LLM se corrija: rangos de FC en zonas de Garmin por deporte (S10), ritmo umbral vigente (S11), CTL ≠ máximo habitual de la ATL (S12), fechas ISO (G3), marcador "datos disponibles" sin valor (G4) y turnos de usuario inventados (G5, con `strip_simulated_user_turn`, que se mueve de `trainer_agent`). El módulo no importa nada de `agent/` (test). El juez los importa; Kairos los usará en vivo en el paso 3.
- Verificación: 0 diferencias frente al juez anterior en 350 casos (las 175 respuestas de las siete baterías del 23/10 y las mismas con errores inyectados) y mismo veredicto en 175/175 preguntas.
- G4: "Sin datos disponibles para esta fecha" ya no cuenta como fallo. El G4 nació para un valor que no llegaba a mostrarse ("Body Battery: datos disponibles"); declarar que falta el dato es lo que piden las reglas y lo que indica el prompt de análisis de actividad, que chocaba con el juez.
- Textos de horizonte de `_coaching_horizon` a `prompts/coaching/horizon_*.md` (idénticos). Tests: 600 (14 de sensores).

### Fixed (enrutado que dependía del texto del LLM)
- `_is_week_tss_followup_intent` decidía si una pregunta semanal era continuación del TSS semanal buscando "tss acumulado" en las respuestas recientes, incluido el texto del LLM. En la batería del 23/10 un consejo decía "si el TSS acumulado del lunes a miércoles supera ~130…" y la pregunta siguiente ("¿qué sesión me recomiendas para esta semana dado mi estado?") acabó en la ruta de TSS semanal en vez de en la de estado (24/25). Ahora solo mira la parte determinista de cada respuesta (antes de "Interpretación de coaching"). Test con el caso real.

### Changed (prompts fuera del código)
- Todo el texto de instrucciones para el LLM que estaba escrito dentro de `agent/trainer_agent.py` pasa a `prompts/`, en 19 plantillas por familia: `system/` (fecha, memoria, base de conocimiento, preguntas de la sesión anterior), `coaching/` (capa de coaching, con las variantes de readiness y breve, y el mensaje de usuario), `rescue/` (valoración de un entreno y propuesta para mañana) y `activity_analysis/` (análisis profundo: mensaje principal, zonas reales, tres variantes de plan y las dos guías de la sección final). El código solo compone y aporta los datos.
- `_render_prompt(nombre, **valores)` sustituye marcadores `{{clave}}` y exige que coincidan con los valores. Plantillas extraídas evaluando las expresiones antiguas con valores centinela (sin transcribir a mano): las 19 reproducen el texto anterior carácter a carácter. Tests: cada prompt se resuelve entero, un marcador que falta o sobra da error y ningún fichero queda sin usar (586).

### Changed (zona de Garmin como intensidad principal)
- Decisión del usuario: la FC en zonas de Garmin es la referencia principal al prescribir y el RPE el complemento (manda solo donde la FC no sirve: fuerza, series muy cortas); en ciclismo con potencia, además % del FTP. En `prompts/coaching_rules.md` (antes: "RPE 1-10 y, si aplica, zona de Garmin"). El motor de planes sigue en RPE y guarda por dentro % del LTHR (zonas de Friel, que no casan con las de Garmin): pasa a zonas de Garmin en el paso 5 de `docs/Harness.md`.
- Juez: los rangos de FC de una línea de bici se comparan con las zonas de ciclismo de Garmin (Z1 105–124…), no con las de carrera. La batería marcaba como fallo un "Z1 (FC ciclista 105–124 lpm)" correcto. Las seis baterías del 23/10, 25/25 con el juez corregido.

### Changed (reglas de entrenador en una sola fuente)
- Las reglas de entrenador estaban escritas cuatro veces (system prompt y tres mini-prompts: coaching y dos rescates), con redacciones distintas; el de coaching pedía la intensidad "en % del umbral de FC", en contra de la regla de zonas de Garmin. Pasan literalmente a `prompts/coaching_rules.md` (sin perder ni duplicar líneas, comprobado) y las cargan las cuatro llamadas; `system_prompt.md` las inserta en `{{REGLAS_DE_COACHING}}`. Las llamadas al LLM no cambian; las de coaching mandan unos 2 700 tokens más.
- Ruta determinista de la FC umbral: decía que el LTHR es "la referencia de tus zonas de FC (% del umbral) y del hrTSS de trail y bici sin potencia". Ahora: ancla el hrTSS de fuerza, elíptica y remo; trail y bici sin potencia usan el umbral de TP; las zonas son las de Garmin.
- Batería 25/25; suma de las 4 preguntas de coaching 208 s (hoy entre 151 y 240 s con NVIDIA). Tests: 583.

### Removed (GitHub Models y prompt compacto)
- Desde la VPN se accede a NVIDIA, así que GitHub Models sobra (decisión del usuario): fuera el proveedor `vpn` (cliente, menú, `/modelo`, presupuesto de tokens, `GITHUB_TOKEN`/`GITHUB_MODEL`) y `prompts/system_prompt_compact.md`, que solo existía por su límite de tokens. La detección de Zscaler queda solo como información en el arranque. Proveedor por defecto de `TrainerAgent`: `nvidia`. Tests: 582 (fuera los 3 del prompt compacto). Batería con NVIDIA: 25/25.

### Docs (`docs/Harness.md` reescrito)
- Especificación del harness a partir de Böckeler/Fowler (guías y sensores, computacional e inferencial, bucle de gobierno) y de Harness² de Google Research (ocho componentes, Proponer–Probar–Componer, biblioteca de experiencia, guía separada de la rúbrica). Catálogo con 12 guías (G1–G12) y 24 sensores (S1–S24): dónde vive cada uno, qué hace si detecta un fallo y su estado real.
- Corrige lo que estaba mal clasificado o desfasado: reglas del prompt contadas como sensores, pasos del arranque llamados hooks, umbrales de alerta viejos (HRV 15 %, body battery 30), recuento de tests.
- Hueco principal: los controles sobre la respuesta del LLM (zonas, ritmo umbral, CTL/ATL, fechas) solo existen en el juez. Diseño de autocorrección en vivo con un módulo de sensores compartido y siguientes pasos ordenados. La rúbrica de la batería no cambia.

### Removed (limpieza de restos)
- Lanzadores `tools/garmin-mcp-frozen.cmd/.sh` y el concepto de "backend frozen" (restos del MCP de terceros): `MCP_BACKEND`, `KAIROS_MCP_FROZEN_COMMAND`, `KAIROS_MCP_BACKEND_EFFECTIVE`, `get_configured_mcp_backend`, el parámetro `backend_effective` de la caché de contingencia y las líneas "MCP backend" del arranque. El cliente lanza `python -m garmin_mcp` con el mismo intérprete que Kairos y la raíz del repo como `cwd`, como ya hacían las herramientas de `tools/`. Comprobado en vivo (sesión, 42 tools, llamada a Garmin).
- `fitparse` de `requirements.txt` (lo usaba `tools/fit_tss_probe.py`, que ya no existe); `form_zone_label` (`load_metrics`) y `_parse_iso_date` (`contracts`), sin uso; `make login`, idéntico a `make serve`; del `.gitignore`, `.garmin-mcp/`, `node_modules/`, `.venv2/`, `tmp/` y `.tmp_*`.
- `tests/test_mcp_inspector_smoke.py`: los 6 tests que siempre salían omitidos (necesitaban Node, npx, credenciales y `RUN_MCP_INSPECTOR_SMOKE=1`). Los cubren el healthcheck en vivo y el test de firmas de `garminconnect`. La suite queda en 585 passed, 0 skipped.
- `TODO.md`: desfasado y repetido con los pendientes de `CLAUDE.md`. Lo único que no estaba allí (revalidación periódica con un export de TP, RPE de fuerza, git hooks locales y la hipótesis de las series cortas) pasa a "Pendiente" de `CLAUDE.md`.
- README: sin `TODO.md`, lanzadores ni runbook; tests al día. `.env.example`: añadidos Mistral y Cerebras (Kairos ya los admitía) y el bloque de Gemini coherente con su valor.

### Changed (rodaje suave en Z1–Z2)
- Decisión del usuario, revisando el criterio de la mañana: un rodaje suave o aeróbico puede prescribirse en Z1–Z2 con su rango (115–145 lpm); las dos zonas están bajo VT1 y dan el mismo estímulo, y lo que importa es el techo de Z2. Un día de recuperación va solo en Z1. De Z3 para arriba (tempo, umbral, intervalos) sigue mandando una sola zona, salvo los tramos progresivos.
- En los dos system prompts, en el juez (`_easy_range_ok`) y en la rúbrica de `docs/Harness.md`. El juez no comprueba lo de la recuperación en Z1 (no sabe qué día lo es).
- Batería repetida en el ordenador personal: 23/25 con el criterio anterior (los dos fallos, "Z1-Z2 115–145" en rodajes suaves) y 25/25 con el nuevo prompt y el nuevo juez.

### Fixed (memoria entre sesiones sin respuestas viejas)
- El resumen de cada sesión (`generate_session_summary_checkpoint`) guardaba los 200 primeros caracteres de la última respuesta, y el bloque "Memoria de sesiones anteriores" del system prompt los enviaba al LLM con la orden de "dar continuidad": otra vía, además del historial, por la que volvían cifras caducadas. Ahora el resumen guarda solo las últimas preguntas del usuario.
- Los resúmenes ya guardados en Supabase se limpian al leerlos (`_strip_previous_reply` corta la "Última respuesta: …"); no se tocan los datos. El bloque indica que los datos actuales se piden con las herramientas.

### Changed (rangos de FC al prescribir)
- Un tramo continuo se prescribe en una sola zona de Garmin ("Z2 alto / Z3 bajo" queda prohibido). Solo un tramo progresivo (calentamiento, vuelta a la calma, rodaje progresivo) puede recorrer varias zonas, y se escribe "de Zx a Zy" con la FC de inicio y de fin (115→145 lpm). En los dos system prompts.
- Juez de la batería: acepta un rango entre zonas solo si en la misma línea se presenta como progresivo y nombra las zonas, con la FC de inicio en la más baja y la de fin en la más alta (`_progressive_range_ok`). Criterio decidido por el usuario y anotado en `docs/Harness.md`. Los dos fallos de la batería del 22/10 eran de este tipo; falta repetirla con el criterio nuevo.

### Docs (tool 42, `get_heart_rate_zones`)
- `garmin_mcp/README.md`: 42 tools en la introducción (decía 41) y nueva sección sobre las tools que consultan un endpoint de Garmin directamente porque `garminconnect` no tiene método (`get_heart_rate_zones`, `get_vo2max_trend`): endpoint, formato de respuesta y cómo se cierran las zonas.
- `docs/arquitectura.md`: nueva sección "Zonas de FC" (lectura al arrancar, guardado e historial en el perfil, respaldo, fuente única `_resolve_hr_zones` y sin efecto en el TSS), el paso en el diagrama de arranque, y corregido que Kairos calculaba las zonas.

### Docs (fuera `docs/mcp-frozen-runbook.md`)
- Lo vigente pasa a su sitio. A `garmin_mcp/README.md`, nueva sección "Problemas frecuentes": 429/403, tokens rechazados y `CERTIFICATE_VERIFY_FAILED` detrás de un proxy corporativo. A `docs/arquitectura.md`, nueva sección "Cliente MCP": arranque del servidor, `MCP_BACKEND`, catálogo esencial, contratos y modo de contingencia, que es del host y no del servidor.
- Corregido al pasarlo: la contingencia de `get_training_load_trend` ya no se resuelve contra `load_metrics_daily` (usa la caché como el resto) y la caché no caduca. Documentado por qué el modo está desactivado.
- Descartado por desfasado o nunca medido: verificación con "44 herramientas" y 4 preguntas (ahora healthcheck y batería con juez), validaciones de release (las cubren `make test` y la CI) y el criterio de p95.

## 2026-10-22

### Changed (zonas de FC: las de Garmin mandan)
- Kairos usaba dos juegos de zonas: los análisis de actividad mostraban las de Garmin (reserva de FC: Z2 135–145) y el contexto del LLM, las prescripciones y el juez, las suyas por % del LTHR (resistencia 144–150). El LLM mezclaba ambas (fallo de la pregunta 1 de la batería). Decisión del usuario: mandan las de Garmin, que son las del reloj y las de TrainingPeaks.
- Nueva tool `get_heart_rate_zones` (42): zonas configuradas en Garmin Connect por deporte (default, running, cycling), con método, FC de reposo, máxima y umbral, y las cinco zonas cerradas en lpm.
- Al arrancar, Kairos las lee y las guarda en el perfil (`performance.hr_zones_garmin`) solo si cambian; la versión anterior pasa a `hr_zones_garmin_history` con sus fechas. Si Garmin falla, usa las guardadas.
- `_resolve_hr_zones(perfil, deporte)` es la fuente única: contexto del LLM (zonas de carrera y de ciclismo con nombre y lpm), análisis de actividad (mismos nombres) y juez. Nombres de TP en español: Z1 Recuperación, Z2 Aeróbica, Z3 Tempo, Z4 Subumbral, Z5 Supraumbral (antes "Calentamiento/Suave/…" en los análisis y "recuperación/resistencia/…" en el resto).
- Respaldo sin zonas de Garmin: % del LTHR, ahora sin huecos (151 y 160 lpm no caían en ninguna zona por redondeo).
- No cambia el TSS: el hrTSS usa sus tramos de % del umbral de TP (`TP_HRTSS_RATES`). Los objetivos `hr_pct` del JSON de los planes siguen en % del LTHR (no se muestran).

### Changed (series de tendencia e instalación)
- `get_vo2max_trend` pide el rango en una sola llamada al endpoint `maxmet/daily/{inicio}/{fin}` de Garmin (el mismo que usa `get_max_metrics`, que solo lo llama con un día) y devuelve todos los días con estimación. Antes consultaba un día de cada 7 y muchas muestras caían en días sin estimación: en dos meses, 31 valores en vez de 4–6, en 0,6 s.
- Series de tendencia en el host: además de las filas, `rows_total` y `summary` por columna (n, mín, máx, media, primero y último; recuento por valor en las de texto), calculados sobre todas las filas. Los dos system prompts piden citar `summary` en vez de contar o promediar (el LLM decía "13/14 días" con 14 de 15).
- `pyproject.toml` mínimo (solo declara `agent` y `garmin_mcp`; las dependencias siguen en `requirements*.txt`) y `pip install -e . --no-deps` en `setup.ps1`/`setup.sh`: los paquetes se importan desde cualquier carpeta. Ruff toma ahora `requires-python` (3.11): desaparece un falso positivo (`BaseExceptionGroup`) y aparecen 22 sugerencias de modernización (`UP017`, `UP041`, `FURB162`).

### Fixed (historial entre sesiones, VO2máx y entorno)
- Kairos restauraba al arrancar los 6 últimos mensajes de la sesión anterior, respuestas incluidas, y el LLM repetía cifras viejas o erróneas sin consultar las tools (pasó con "0 días con datos" de HRV). Ahora solo recupera las preguntas del usuario, en una nota de sistema que dice que no traen datos (`_restore_previous_session`, `previous_session_questions`). El historial de la sesión en curso no cambia.
- `get_vo2max_trend` devolvía el payload entero de `get_max_metrics` por muestra (~600 caracteres de aclimatación al calor y la altitud cada una) y el host solo podía pasar 4 muestras al LLM. Devuelve ahora una serie plana (`date`, `running`, `cycling`) con `days_with_data`, y el host la envía completa como las otras series.
- Desinstalado del `.venv` el MCP de terceros `garmin-mcp` 0.1.0 (Taxuspt/garmin_mcp): nadie lo usaba y, con el mismo nombre de paquete, tapaba al `garmin_mcp` del repo en scripts lanzados fuera de la raíz.
- Verificado: healthcheck 41 tools (37 OK, 4 vacías, 0 errores); preguntas de tendencia de HRV y VO2máx sin vaciar el historial, con los valores reales de Garmin.

### Fixed (preguntas de tendencia de HRV)
- "¿Cómo ha evolucionado mi HRV en las últimas dos semanas?" iba a la ruta factual de un día (`mcp_factual`) y respondía con los datos de hoy. `_is_mcp_factual_query_intent` excluye ahora las preguntas de evolución o de periodo (evolución, tendencia, "ha ido", últimas semanas, este mes…), que pasan al LLM; las de un día siguen en la ruta determinista.
- `get_hrv_trend` (servidor) buscaba `lastNightAvg` en la raíz del payload de Garmin, que lo trae dentro de `hrvSummary`: devolvía 0 días con datos. Ahora lee `hrvSummary` y añade la media semanal y el estado de cada día.
- Compactación del host (`_compact_tool_result`): `get_hrv_data` tenía el mismo fallo (caía a la poda genérica y el LLM no veía la HRV; pedía días sueltos hasta agotar las 15 iteraciones). Y la poda genérica deja 4 elementos de cada lista anidada: de una serie de 15 días el LLM recibía 4 e inventaba el resto. `get_hrv_trend` y `get_training_load_trend` se envían ahora completas en formato columnas/filas; si no caben, se quitan los días más antiguos y se indica cuántos (`omitted_oldest_days`).
- Verificado con Kairos: los 14 días de la respuesta coinciden con la serie real de Garmin (antes: 15 iteraciones sin respuesta, o valores inventados). Batería 25/25 con el juez.
- Juez: el objetivo de glucemia "120–180 mg/dL" se juzgaba como rango de FC ("vo" de "objetivo" casaba con VO2). Ahora exige `VO2` como palabra y descarta rangos en mg/dL.

### Changed (tests y cliente MCP)
- Tests organizados por módulo: `tests/test_load_metrics.py` reúne los tests de TSS/PMC que estaban en `test_trainer_agent.py` y los cuatro ficheros pequeños de `load_metrics` (duración, caminar/senderismo). En `test_trainer_agent.py`, los 30 tests del motor de planes que estaban mezclados en `TestTssSourceTagging` pasan a `TestStructuredPlanEngine`, y los de la tabla `/carga` y de umbrales por fecha a `TestLoadTrendDisplay` y `TestThresholdHistoryResolvers`. Misma colección de tests (582, mismos nombres), de 13 a 10 ficheros.
- Fuera `GARMIN_ENABLED_TOOLS` y el parámetro `essential_only` del cliente MCP: era el filtro de herramientas del MCP de terceros anterior y `garmin_mcp` no lo lee. `.env.example` lo documentaba con herramientas que no existen. `tools/rebuild_tss_baseline.py` usaba `essential_only=False`; corregido (habría fallado al ejecutarse).

### Changed (prompts)
- `prompts/` deja solo lo que recibe el LLM (`system_prompt.md` y `system_prompt_compact.md`). `prompts/mcp_tool_routing_guide.md` no se inyectaba al LLM y duplicaba lo que ya cubren las descripciones de las tools, `garmin_mcp/README.md` y el "Checklist mínimo por intención" del prompt: pasa a `_archivo/docs/`. Sus reglas de ahorro de tokens (resumen antes que detalle, `limit` 1–5, no repetir llamadas, no reintentar en bucle) pasan a los dos system prompts como "Uso eficiente de herramientas".

### Added (documentación del MCP)
- `garmin_mcp/README.md`: qué es el servidor, arranque (también desde otros clientes MCP), configuración, mantenimiento y catálogo de las 41 tools por categorías, con descripción, parámetros (tipo, valor por defecto, descripción) y anotaciones. El catálogo lo genera `tools/gen_mcp_docs.py` (`make docs-mcp`) desde el propio servidor; `test_tool_catalog_readme_is_up_to_date` falla si se cambia una tool sin regenerarlo.
- `docs/mcp-tools-completo-vs-essentials.md` (catálogo del MCP de terceros de 126 tools, anterior al servidor propio) pasa a `_archivo/docs/`.

### Changed (organización)
- Arquitectura MCP con la terminología del estándar: host, cliente y servidor en su sitio. El servidor sale de `agent/` a su propio paquete `garmin_mcp/` (`agent/kairos_mcp_server.py` → `garmin_mcp/server.py`, arranque `python -m garmin_mcp`), porque no depende del host y Kairos solo lo usa por el protocolo MCP. El lado cliente se agrupa en `agent/mcp/` (`mcp_client.py` → `client.py`, `mcp_adapter.py` → `contracts.py`). `git mv` conserva el historial; sin cambios de comportamiento.
- El cliente calcula la raíz del proyecto con una constante (`PROJECT_ROOT`) adaptada a su nueva profundidad; lanzadores, `lint_mcp`, `healthcheck_mcp`, tests y documentación actualizados. El juez pide los récords por el protocolo MCP en vez de importar el servidor. La clave de perfil `mcp_adapter_cache` no cambia (datos ya guardados).
- Test de arquitectura `test_server_package_does_not_import_the_host`; `make lint` incluye `garmin_mcp`.
- Verificado: 575 tests, lint MCP 0 hallazgos, healthcheck 41 tools (37 OK, 4 vacías esperables, 0 errores), Inspector 6/6 y batería 25/25 con el juez.

### Added
- `tools/healthcheck_mcp.py` (`make health-mcp`): healthcheck en vivo de las 41 tools contra Garmin, con estado por tool (OK, VACÍA, ERROR, ENTORNO), tiempos e informe en `.tmp/healthcheck/`; sale con 1 si alguna falla. Complementa al test de contrato sin red. Primera pasada: 37 OK, 4 VACÍA (esperables), 0 ERROR.
- Test de contrato sin red `test_every_tool_calls_garminconnect_with_its_real_signature`: llama a las 41 tools con un cliente que valida los argumentos contra la firma real de `garminconnect`. Contra el código anterior señala las 8 tools rotas.
- Guía de estilo MCP de apiaddicts (`mcps-style-guide`) en el servidor: las 41 tools tienen título, descripción y descripción de cada parámetro (54), y anotaciones de solo lectura (`readOnlyHint`, `destructiveHint=false`, `idempotentHint`, `openWorldHint`). Antes 40 de 41 llegaban sin descripción, también al LLM de Kairos. Mismos nombres y parámetros.
- `tools/lint_mcp.py` / `make lint-mcp` y `tools/mcp_lint/.spectral.yaml`: ruleset anclado a un commit, sin las reglas de camelCase y `outputSchema` (no adoptadas, con su motivo). 217 hallazgos → 0. Test sin red `test_all_tools_follow_mcp_style_guide`.
- `tools/juez_bateria.py`: juez determinista de la batería. Contrasta cada respuesta con Supabase (y Garmin para los récords) con una rúbrica fijada antes de ver resultados (`docs/Harness.md`). Primera pasada: 23/25; tras corregir el fallo de la Carga Garmin y un falso positivo del propio juez (un rango de TSS leído como FC), 25/25. Verificado que detecta errores introducidos a propósito (CTL, TSS semanal, fecha ISO, rango de FC).
- `_hr_zone_bounds_bpm`: zonas de FC en lpm en una sola función; la usan el contexto del LLM y el juez.

### Changed
- Prompts: regla explícita de umbrales. El ritmo umbral y la FC umbral son los del perfil de Kairos (pedidos en el alta, guardados en Supabase, cambiados con `/perfil umbral`); un cambio rige desde su fecha y no altera el TSS ya calculado; `get_lactate_threshold` (Garmin) es solo informativo.
- Estado de carga con una sola regla en todo Kairos: zonas de Forma de Joe Friel relativas al CTL (Form % = TSB / CTL, como intervals.icu): 🔴 Riesgo alto < −30 %, 🟢 Óptimo −30 a −10 %, 🟡 Zona gris −10 a +5 %, 🔵 Fresco +5 a +20 %, ⚪ Transición > +20 %. Antes había tres reglas distintas (umbrales fijos en `/carga`, otros con −3,4/+6,2 en el resumen semanal y percentiles + suelos por deporte en el arranque) que se contradecían y llamaban "sobrecarga" a bloques normales de entrenamiento (TSB −16/−18).
- Motor diario: Riesgo alto → descanso; 7 días seguidos < −10 % → suave/descarga; resto de zonas → mantener y modular con HRV, sueño y body battery. Los percentiles personales pasan a ser informativos y se eliminan los suelos de TSB por deporte (`tsb_abs_floor`).
- `/carga`, resumen semanal, estado de arranque, estado de hoy, contexto del LLM y prompts muestran la zona y su % del CTL.

### Fixed
- Prueba de humo del Inspector MCP (`RUN_MCP_INSPECTOR_SMOKE=1`): `get_activities` fallaba porque el test leía la salida sin codificación (cp1252 en Windows) y los emojis del nombre de la actividad del 20/10 rompían la lectura (stdout vacío → "no devolvio result"); no era un fallo del servidor. Ahora lee en UTF-8, usa `--tool-arg` (`--tool-args-json` ya no existe en el Inspector) y fija la versión del Inspector (2.12.0). 6/6.
- 8 tools del MCP fallaban siempre por llamar a `garminconnect` 0.3.2 con argumentos que no casan con su firma: `get_daily_steps` (inicio y fin), `get_training_status` y `get_fitnessage_data` (fecha; nuevo parámetro `date`), `get_vo2max_trend` (`get_max_metrics` admite una fecha: muestreo cada 7 días + último día), `get_endurance_score` (nuevos `start_date`/`end_date`), `get_weekly_steps` y `get_weekly_stress` (fin y nº de semanas), `get_weekly_intensity_minutes` (inicio y fin); las semanales, por defecto 4 semanas. Detectadas al comprobar que el LLM elegía `get_vo2max_trend`/`get_daily_steps` y no obtenía datos. Las 41 tools comprobadas contra Garmin real.
- La carga de Garmin ya no puede usarse como TSS en ningún camino (CLAUDE.md: "la carga nativa de Garmin nunca es TSS"). Auditoría del 22/10:
  - `_estimate_session_tss` devolvía la carga/TSS de Garmin como TSS (`garmin:native_load`) en dos respaldos: actividad sin duración y deporte sin modelo propio (natación, esquí…). Ninguna de las 222 actividades guardadas lo usó; ahora sin duración es 0 y sin modelo se estima por FC (`generic:*`).
  - Ruta del día ("¿qué hice el …?"): "TSS del día" salía primero de `get_training_load_trend` (suma de `activityTrainingLoad` de Garmin). Ahora sale de la serie de Kairos o de `activity_tss`, y la carga de Garmin va en su propia fila "Carga Garmin del día (no TSS)".
  - Contexto del LLM en el análisis de actividad: el bloque "CARGA DE ENTRENAMIENTO" pasa a "CARGA GARMIN (… NO es TSS …)", y la tool `get_training_load_trend` del MCP tiene descripción que lo aclara y remite a `kairos_load_trends`.
  - Contingencia del adaptador MCP (desactivada por defecto): devolvía el TSS de Kairos con el nombre `trainingLoad` de Garmin; ya no.
- "Carga Garmin" en bici con potencia mostraba el TSS que calcula Garmin (`trainingStressScore`) en vez de su carga (`activityTrainingLoad`, la guardada en `activity_tss.native_load`): en "últimas N actividades" y en el análisis de una actividad.
- Ritmo umbral por fecha también en los cálculos de respaldo (análisis de actividad sin TSS guardado, adherencia al plan, verificación incremental): usaban el umbral actual y, tras un cambio, habrían calculado actividades anteriores con el nuevo. El TSS guardado ya no se recalculaba; ahora tampoco se muestra un valor provisional con el umbral equivocado.
- Estado de arranque: si Garmin devuelve el registro del día sin valores (reloj sin sincronizar), Body Battery y sueño decían "datos disponibles". Ahora dicen "sin datos" y una línea avisa de qué falta ("Garmin aún no tiene … sincroniza el reloj con la app y vuelve a arrancar Kairos").

### Removed
- Copia muerta de `_SPORT_MODEL_DEFAULTS` en `trainer_agent.py` (constantes antiguas, sin uso).

### Data
- Ritmo umbral de running 4:00/km (240 s) desde el 22/10/2026 (antes 4:12), estimado con la carrera del 18/10 (24764615547): 27 min, NGP 3:49/km del pipeline de producción, llevado a 60 min con Riegel (×1.049). FC umbral sin cambios (168 lpm de media en los últimos 20 min frente a 169). Añadido al historial del perfil; el TSS ya guardado no cambia.

### Tests
- 576 passed, 6 skipped. Los tests del PMC fijan su TSS sintético con un fixture explícito (antes dependían de que la carga de Garmin se usara como TSS). Nuevos: `test_daily_tss_comes_from_kairos_not_garmin_trend`, `test_garmin_load_is_never_the_final_tss`, zonas de Forma, `test_threshold_change_applies_only_from_its_date`, `test_power_ride_shows_garmin_load_not_garmin_tss`.

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
- `docs/arquitectura.md`: el detalle técnico que estaba en el README, revisado (fuera `_apply_trail_overrides`, que ya no existe; añadida `activity_tss`). Corrección del 22/10: `MCP_READ_ONLY` sí existe (activado por defecto) y se había omitido por error; vuelve a estar documentado.

### Changed
- README reescrito: de 1 016 a unas 120 líneas.
- El menú de proveedores muestra el modelo efectivo de cada uno (`*_MODEL` del `.env`), no un nombre fijo.
- Python mínimo 3.11 (el código usa `BaseExceptionGroup`).

### Security
- Historial de git reescrito con `git filter-repo`: fuera de todos los commits las 214 rutas que ya no forman parte del repo (payloads reales de Garmin, exports de TP, sondas, logs de baterías…) y sustituidos en todas las versiones la contraseña de Garmin (filtrada en `.tmp/bateria_p55_input_runtime*.txt` el 18/10) y el email de Garmin. Cambian todos los hashes; los citados en la documentación están traducidos al historial nuevo. Copia del historial anterior: `_archivo/backup_historial_pre_purga_2026-10-21.bundle` (local).
- Caché de GitHub limpiada (ticket de soporte 4888982): tras el force push, los 23 commits antiguos con `.tmp/bateria_p55_input_runtime.txt` (de `2633a69578f9` a la antigua cabeza de main `7b219bd26813`) seguían accesibles por su hash. Comprobado el 23/10 sin autenticar: los dos extremos dan 422 en la API y 404 en la web y en raw.githubusercontent.com, igual que un hash inventado, y los 15 hashes cortos antiguos citados en la documentación dan 404. El repo no tiene forks ni pull requests. La contraseña filtrada ya estaba cambiada. Revisión completa de lo publicado (main y las 3 etiquetas, 259 commits, 1 820 objetos): ni la contraseña filtrada ni la actual, ni las claves del `.env` (`ENCRYPTION_KEY`, Supabase, proveedores LLM) aparecen en ningún fichero ni mensaje; la `ENCRYPTION_KEY` y la `GARMIN_PASSWORD` del CI son de prueba. Queda el email de Garmin como identidad de autor en commits antiguos (aceptado).

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
