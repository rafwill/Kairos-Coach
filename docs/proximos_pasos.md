# Próximos pasos — Kairos Coach

Última revisión: 6/11/2026.

Solo lo que falta por hacer y lo descartado. Lo hecho está en `CHANGELOG.md` (cada cambio, con su señal y su resultado), en `CLAUDE.md` (estado vigente de la carga, los planes y el arranque) y en `docs/arquitectura.md`.

Texto anterior de esta lista, con los criterios de diseño que citan algunos comentarios del código ("pendiente 11 a-d", "punto 12"): `git show f112c0f:docs/proximos_pasos.md`. Bitácoras más antiguas: hasta el 25/10, `git show 1bf341f:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10, `git show 6aff019:docs/proximos_pasos.md`.

## Dónde lo dejamos

**Sesión del 5 y 6/11/2026** (ordenador 1). Último commit con cambios: `02fc73b` (Vault obligatorio), seguido del que ajusta estas notas. Release **`v3.1.0-rc.1`** publicada como pre-release (`afc87a3`). 1082 tests y `ruff` limpios. Detalle de cada cambio en el CHANGELOG del 05 y 06/11: Body Battery al despertar, motor diario guiado por la HRV y la sesión que toca, bici opcional en descanso, mensajes de sistema juntos para Gemini, mover sesiones desde el chat, límites de sesión para media maratón y objetivo frente a umbral, y secretos en Supabase Vault.

**Supabase (6/11):** funciones de Vault aplicadas (`schema.sql`); los dos secretos del usuario guardados y cerrados sin sesión (`check_rls`); el `.env` del ordenador 1 solo tiene `SUPABASE_URL` y `SUPABASE_ANON_KEY` (más `AGENT_LANGUAGE` y `LOG_LEVEL`, que no usa ningún código); borrada de su fila de `app_user` la copia antigua `garmin_password_encrypted` (prueba, solo esa clave, leída de vuelta).

**Siguientes pasos, en orden:**

1. **Replanificar el plan de Santa Pola** (7/11, desde las 9:00, cuando se renueva el cupo de Gemini 3.6 Flash). Mensaje: «Replanifica el plan. Mantén la salida a la sierra del sábado 14 y, el domingo 15, un rodaje suave después. Quiero bajar de 1:23 en Santa Pola.» (objetivo confirmado por el usuario: bajar de 1:23, `target_time` 1:22:59). Revisar con `agent.log` (`[PLAN]`) y Supabase: tiradas ≤ 130', regenerativos ≤ 45', ≥ 2 semanas de construcción, test de 5-10 km al final de la construcción, umbral desde la base, VO2 repetido, la sierra el 14 (evento `e1`) y la semana de la carrera con activación. El intento del 6/11 no guardó nada: dos reglas imposibles de cumplir (la tirada que es evento fijo frente a la estructura y el regenerativo en la semana de la carrera), arregladas en `e181102`; además el principal agotó su cupo (429) y siguió con Flash Lite.
2. **Ordenador 2:** `git pull`, `pip install -r requirements.txt` (sale `cryptography`) y dejar su `.env` solo con `SUPABASE_URL` y `SUPABASE_ANON_KEY`. Al entrar no debe pedir ni la contraseña de Garmin ni la clave de IA (las lee de Vault).
3. **Release 3.1:** batería de 25 preguntas (`tools/run_bateria.py` + `tools/juez_bateria.py`, otro día que el del replanificado por el cupo) → `v3.1.0-rc.2` (Vault y lo que salga del replanificado) → `v3.1.0` final (`pyproject.toml` a `3.1.0`, etiqueta firmada, release marcada como la última).
4. **Web, fase 0** (rama `web/fase-0-poc-garmin`, `docs/web.md`): prueba de Garmin desde un servidor en la nube con un solo atleta.
   - Servidor: Oracle Madrid sin capacidad con la cuenta gratuita (ni ARM A1 ni AMD E2.1.Micro; Madrid tiene un solo availability domain; reintentos seguidos dan "Too many requests"). GCP pide tarjeta. El usuario estaba creando una **Azure B1s** (1 vCPU, 1 GB, Ubuntu 24.04 x64, Spain Central, usuario `kairos`, solo SSH, clave `id_ed25519` de este ordenador) con su cuenta de empresa. **Confirmar antes** que es una suscripción de Visual Studio (crédito personal para desarrollo) o que tiene permiso: datos de salud de terceros y recursos de la empresa. Para producción con 5 atletas, cuenta personal: Clouding plan 2 (1 vCore, 2 GB, 30 GB; ~9,70 €/mes con IVA, se amplía por horas), Oracle con Pay As You Go (gratis dentro del Always Free; crear presupuesto con alerta) o GCP `e2-micro` (1 GB; confirmar coste de la IPv4).
   - Con la IP: instalar por SSH (zona horaria Madrid, git, venv, la rama); el usuario crea el `.env` con `GARMIN_EMAIL`/`GARMIN_PASSWORD` (excepción: el servidor de Garmin sin Kairos no tiene sesión para leer Vault; borrarlo al acabar) y hace `--primer-login`; cron cada 3 h; a las 72 h, `--informe`.
5. **Mientras corre la prueba: fase 1**, `today_view()` (el «Hoy» como datos JSON en el núcleo, con la CLI pintando desde ahí y sin cambiar su texto) en la rama `web/fase-1-hoy`, creada desde `main`.

## 1. Para comprobar con el uso diario

1. **Resumen del arranque.**
   - Reentrada en menos de 1 h: debe salir "Resumen de las HH:MM" (`[STARTUP] resumen reutilizado`). Si enseña algo viejo (el Body Battery de la mañana, una actividad sin sincronizar), añadir ese dato a la huella (`agent/startup_cache.py`).
   - Tras entrenar: "Sesión de hoy (realizada)", "Mañana", el plan vs ejecutado de hoy y la recomendación de descanso. Cualquier actividad de hoy cuenta como sesión hecha, también un paseo: ver si molesta.
   - El "1:47" que el usuario vio como duración de hoy no se encontró: el plan guardado decía 1:05:00 y el único plan activo es `19bd7f1c`. Si vuelve a aparecer, anotar dónde.

2. **Sensores `[SENSOR]` de `agent.log`.** Cuánto salta cada uno con preguntas reales, falsos positivos nuevos y qué guía conviene reforzar (`docs/Harness.md` §7).
   - **S25** (diferencia de HRV), **S26** (etiqueta rTSS/hrTSS), **S27** ("mañana" con el día que no toca) y **S28** (día de la semana con su fecha): si saltan, y si las guías G13 (calendario) y G14 (HRV calculada) bajan S25, S27 y S28.
   - **S27, falsos positivos vistos el 28/10:** "mañana" dicho desde otro día del texto ("Dom 01 · … Revisa HRV mañana lunes" en una fila de plan). S27 lo mide siempre desde hoy y el reintento no lo arregla porque no hay error. Idea: no juzgar "mañana" en una línea o fila que empieza por otro día o fecha.
   - **Cifras inventadas fuera de la carga** (28/10, `estres_2`): el LLM se inventó los minutos por día (510 min en reposo; Garmin: 1.097). S14 solo vigila cifras de carga: ver con preguntas reales si hace falta un sensor de cifras que no salen de las tools.

3. **Enrutado.** Cada pregunta real mal enrutada va al banco de frases (`PHRASE_BANK` en `tests/test_tool_router.py`) antes de tocar los detectores. Mirar también si salta `[G15]` (marco de hoy en el bucle con tools) y si S15 lo hace cumplir.

4. **Modelos de Gemini** (Flash para planes y arranque, Flash Lite para el chat y como relevo).
   - Ver el relevo cuando Flash agote su cupo de 20 peticiones al día (`[LLM] … relevo con …`). El 31/10 no se dio el 429.
   - Si Flash Lite responde peor que Flash en el chat, valorar las otras opciones: facturación en AI Studio, o NVIDIA para el chat.

5. **Planes en vivo** (fuera del proxy, que bloquea Gemini).
   - Eventos fijos (2/11): un viaje y una salida en grupo reales, contados en el chat y con `/plan evento`; ver que Kairos pregunta lo que falta, readapta esta semana y la siguiente y ofrece replanificar si cae más lejos, y que el plan cumple el día (`[PLAN] eventos guardados` en `agent.log`). Tras un viaje real, ver que el aviso de plan que ya no encaja no salta por esos días (3/11: `plan_drift` no los compara con el plan; la HRV sí cuenta durante el viaje: ver si molesta).
   - Un `/plan crear` real y un "ajusta el plan: …", revisados por el usuario; con bici o remo si los quiere dentro del plan.
   - Un «replanifica el plan» real, en tiempo y peticiones parecidos a `/plan crear` (10–15), revisado por el usuario.
   - Preferencias que duran con frases reales (`[PLAN] preferencias guardadas`). Una frase que se guarde o se pierda sin deber va a los tests antes de tocar las reglas.
   - El aviso "🔄 Tu plan ya no encaja" con un desvío real: revisar con el usuario si los umbrales avisan cuando deben (≥ 2 sesiones clave sin hacer, ≥ 3 días seguidos sin actividad, ≥ 3 noches con la HRV bajo el 90 % de su media, ≥ 2 días con otro deporte, TSS por debajo del 50 % o por encima del 130 % del previsto).
   - Un plan de trail real, cuando el usuario tenga el objetivo (`/perfil editar objetivo` con distancia y D+, y `/plan crear`). Las cifras de trail se irán revisando con planes reales.
   - Fiabilidad con 2–3 modelos más (batería de planes) antes de producción. Hecho con lo gratuito: Gemini 3.5/3.6 Flash, plan válido en 68 s; Groq gratuito imposible (8.000 tokens por minuto); nemotron, sin plan en 13 min.
   - El tiempo de hoy en el arranque necesita el lugar: `/perfil lugar <ciudad>` si no sale el bloque "🌡️ Tiempo hoy".

## 2. Cálculo de carga (TSS frente a TrainingPeaks)

Antes de tocar cualquier fórmula: hipótesis escrita, criterio de cierre fijado antes de ver resultados y prueba sin escribir (reglas de método de `CLAUDE.md`).

1. **Trail rápido (< 6:00/km).** Validar cuando haya más salidas con rTSS en TP. Hoy n = 1 (16/08: Kairos 128.0 frente a TP 112.4).
2. **Series cortas de running** (6x500, 6x1000, 4x400, 8x500): salen entre un 11 % y un 20 % por encima de TP; 23966766696 (13/08, 8x45" en cuesta) ×1.20. Plantear hipótesis antes de tocar el detector (p. ej. relación FC-velocidad con retraso fisiológico).
3. **Revalidación periódica** con un export nuevo de TP: prueba sin escribir, criterio fijado antes, solo las filas esperadas.

## 3. Solo si hace falta

1. **Metatooling** (los grupos de tools ya están): (a) el prompt de sistema (≈ 7.000 tokens, la mayor parte de cada llamada) por partes según la pregunta, como los grupos; (b) fusionar tools parecidas en el servidor (`get_sleep_data`/`get_sleep_summary`, las tres de actividades) si el LLM las confunde. Vigilar en `agent.log` cuántas veces se pide un grupo (`[TOOL] el LLM pide el grupo`): más de 1 de cada 5 preguntas = reglas mal hechas.
2. **Peso con báscula.** Kairos toma el peso de la configuración de usuario de Garmin (`get_user_profile`, `userData.weight`) y solo si falta, del registro de hoy de `get_body_composition`. Sin comprobar: si un pesaje de báscula actualiza también la configuración. Si un atleta usa báscula y `/perfil` no refleja su último pesaje, invertir el orden: primero el último registro de `get_body_composition` (rango reciente) y la configuración como respaldo.
3. **Arranque del servidor MCP** (2,6–3,8 s: otro proceso, que importa `garminconnect` y FastMCP). Solo si el arranque molesta: mirar qué importa el servidor al arrancar y si se puede lanzar antes (necesita las credenciales de Garmin, que llegan tras el inicio de sesión).

## 4. Ideas sin decidir

1. **Porcentaje de cumplimiento de los entrenamientos** (petición del usuario): un porcentaje de cumplimiento del plan con los datos de Kairos y lo que el atleta ha entrenado. Hoy solo hay la adherencia del día (arranque) y el aviso de desvíos de los últimos 7 días. Falta decidir qué mide (sesiones hechas, tiempo, TSS) y en qué ventana (semana, bloque, plan).
2. **Conectar Claude con Kairos** como proveedor de IA en vez de Gemini (el TSS lo calcula Python con cualquier proveedor). `/proveedor` → "anthropic" (ya en `agent/ia_provider.py`), clave de `platform.claude.com/settings/keys` (pago por uso), modelo `claude-opus-5-5` o `claude-sonnet-5-5`; va por la capa compatible con OpenAI de Anthropic, pensada para probar (si se adopta, cliente nativo). Aplazado por coste (29/10, decisión del usuario).
3. **API Personal de Garmin (clave) como segundo backend de `garmin_mcp/`** (revisado el 31/10). Garmin la abrió el 21/10/2026: clave que se crea en Garmin Connect, se cambia por un token de 24 h, solo lectura y solo tus datos. Es posible mantener nuestro MCP y los nombres de tool, y cambiar por dentro (`_dispatch` en `garmin_mcp/server.py`), con el cliente oficial `garmin/connect-personal-api-python` (Apache-2.0). Decisión (2/11, del usuario): no usarla hasta que Garmin añada más rutas a la API Personal; revisar entonces el Swagger y repetir `.tmp/probe_personal_paths.py` (si se ha perdido, es un GET con la clave a las rutas que hoy dan 404). El Swagger de Garmin cubre sueño, HRV, Body Battery, estrés, pasos, resumen diario, peso, actividades (lista, detalle, FIT original), zonas de FC y perfil; **no** cubre readiness, estado de entrenamiento, VO2máx, umbral de lactato, predicciones de carrera, récords ni FTP, así que haría falta mantener `garminconnect` y su contraseña para esas tools. Si se hace, como `GARMIN_BACKEND=auto` (clave en `.env`, por tool la Personal API si cubre y `garminconnect` si no), con una prueba de paridad de los dos orígenes (mismo día, mismos campos) antes de cambiar nada. Pasos previos:
   - Ver con el uso cuántas veces falla el login o salta el 429 de `garminconnect` (ya se reutilizan los tokens, así que hoy es raro).
   - **Prueba de solo lectura hecha el 31/10** con una clave de un año (en `.env`; script local `.tmp/compare_personal_api.py`): la clave funciona y devuelve datos reales. HRV, Body Battery, resumen diario, lista de actividades y detalle de actividad salen con los mismos campos y valores que `garminconnect` (los nuestros traen algunos campos extra de interfaz). Distinto: el sueño (Personal da el `dailySleepDTO` sin envoltorio y sin `avgOvernightHrv` ni `bodyBatteryChange`), las zonas de FC (crudas, por deporte, con `zone1Floor…`; nuestra tool las normaliza) y el perfil (la API da los ajustes, no `userData` con peso y fecha de nacimiento). Sin probar: FIT original y paso de la clave a un servidor con varios usuarios.
   - Si se sigue, empezar por HRV, Body Battery, resumen diario y actividades (cambio directo), y luego sueño y zonas con un adaptador; perfil y el resto, con `garminconnect`.
   - **¿Autenticar solo con la clave y no pedir usuario y contraseña?** No del todo (probado el 2/11, solo lectura): la clave abre una lista cerrada de rutas y el resto da 404: readiness, estado de entrenamiento, VO2máx, umbral de lactato, FTP, predicciones de carrera, récords, FC por zonas de una actividad y el perfil con `userData`. Eso es lo que hoy exige la contraseña. Opciones:
     - **Modo con clave y contraseña opcional:** la clave para lo que cubre y la contraseña solo si el atleta quiere lo demás (hoy se pide siempre). Lo demás queda igual.
     - **Modo solo clave:** sin readiness, estado de entrenamiento, VO2máx, récords ni predicciones, y sin la FC por zonas de actividad que usa el hrTSS del trail (se podría recalcular desde las muestras, pero cambiaría TSS ya validado: no sin validación y sin subir `TSS_FORMULA_VERSION` de forma deliberada).
   - **Qué ganaríamos:**
     - API oficial y estable, en lugar de un login no oficial que se rompe cuando Garmin lo cambia.
     - Una clave revocable que no da acceso a la cuenta, en lugar de la contraseña de Garmin (que hoy se guarda cifrada en Supabase Vault). Solo se quitaría del todo si se migraran también las tools que hoy no cubre.
     - FIT original con el registro segundo a segundo, e historial completo (con límite de rango por llamada).
     - Menos riesgo de 429 por logins repetidos, aunque hoy ya se reutilizan los tokens.
   - **Qué perderíamos o costaría:**
     - Cobertura: readiness, estado de entrenamiento, VO2máx, umbral de lactato, predicciones de carrera, récords y FTP no están. Habría que mantener `garminconnect` y la contraseña, con dos backends.
     - La clave caduca (máximo un año) y se renueva a mano; el token dura 24 h, sin refresco.
     - Una clave por atleta y términos pensados para uso propio: con varios atletas en Supabase hay que confirmarlo con Garmin.
     - Formas de datos distintas en sueño, zonas de FC y perfil: adaptadores y pruebas de paridad.
     - Límites de uso sin publicar y rangos máximos por llamada (p. ej. sueño y Body Battery, 28 días; estrés y pasos, 7).
     - Las muestras de `get_activity_details` no están en el Swagger, pero la ruta `/activity-service/activity/{id}/details` responde con la clave (31/10): sin decodificar FIT, aunque sin garantía de que siga así. Sin probar si el rTSS sale igual desde ahí.
     - API de 10 días (abierta el 21/10/2026): puede cambiar.
   - Confirmar en los términos de Garmin si guardar la clave de varios atletas en Supabase (como hoy con la contraseña) cuenta como "servicio que recoge claves de otros", que no permiten.

## 5. Fuera de nuestras manos

- **Ticket de GitHub Support 4918285** (abierto el 28/10): purga de las vistas en caché de los 80 commits viejos, de `eeee21b` a `716cd0c`. Cuando respondan, comprobar solo códigos HTTP: resuelto si dan 404 en la web y 422 en la API. Ampliación pedida el 3/11: los 13 commits subidos que se reescribieron ese día.

## Descartado

- Quitar a Copilot de los contributors de GitHub: exigiría reescribir el historial; decidido no hacerlo (23/10).
- Capturar el RPE de fuerza después de la sesión: el TSS de fuerza sale de la FC (modelo B) y ya cuadra con TP (≈ 1,00); decidido quitarlo (25/10).
- Sensor que impida nombrar la hipoglucemia a un atleta con DT2 y metformina: el LLM la menciona para tranquilizar, no como aviso, y no limita el entrenamiento; aceptado sin sensor (26/10).
- Tiempo máximo en las llamadas a Garmin fuera del bucle con tools: el cuelgue de 57 min de la batería del 27/10 fue el portátil en espera moderna (Kernel-Power 506/507), no Kairos; `garminconnect` ya limita cada petición a 15 s con 3 reintentos (27/10).
- Menú con los 5 proveedores de prueba (NVIDIA, Gemini, Groq, Mistral, Cerebras): sustituido por el proveedor de IA que diga cada atleta (26/10).
