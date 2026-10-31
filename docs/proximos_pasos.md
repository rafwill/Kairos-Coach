# Próximos pasos — Kairos Coach

Última revisión: 31/10/2026.

Solo lo que falta por hacer y lo descartado. Lo hecho está en `CHANGELOG.md` (cada cambio, con su señal y su resultado), en `CLAUDE.md` (estado vigente de la carga, los planes y el arranque) y en `docs/arquitectura.md`.

Texto anterior de esta lista, con los criterios de diseño que citan algunos comentarios del código ("pendiente 11 a-d", "punto 12"): `git show 59d16e9:docs/proximos_pasos.md`. Bitácoras más antiguas: hasta el 25/10, `git show 1bf341f:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10, `git show 6aff019:docs/proximos_pasos.md`.

## Dónde lo dejamos

**Sesión del 31/10/2026** (ordenador 1). Último commit: `59d16e9`, subido a `origin/main`; 983 tests y `ruff` limpios. En el otro ordenador, al empezar: `git pull`. La tabla `startup_summary` ya existe en Supabase.

**Siguiente paso (decisión del usuario): usar Kairos a diario unas semanas y decidir con lo que salga.** Nada de código nuevo hasta entonces salvo lo que rompa. Revisar juntos `agent.log` (`[SENSOR]`, `[G15]`, `[TURNO]`, `[STARTUP]`, `[PLAN]`, `[LLM] … relevo`).

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
2. **Conectar Claude con Kairos** para la sincronización de datos y la generación de TSS en vez de NVIDIA. `/proveedor` → "anthropic" (ya en `agent/ia_provider.py`), clave de `platform.claude.com/settings/keys` (pago por uso), modelo `claude-opus-5-5` o `claude-sonnet-5-5`; va por la capa compatible con OpenAI de Anthropic, pensada para probar (si se adopta, cliente nativo). Aplazado por coste (29/10, decisión del usuario).
3. **Revisar garmin-personal-mcp:** un MCP que se puede conectar a Claude o ChatGPT para preguntar por los datos de Garmin.

## 5. Fuera de nuestras manos

- **Ticket de GitHub Support 4918285** (abierto el 28/10): purga de las vistas en caché de los 80 commits viejos, de `eeee21b` a `716cd0c`. Cuando respondan, comprobar solo códigos HTTP: resuelto si dan 404 en la web y 422 en la API.

## Descartado

- Quitar a Copilot de los contributors de GitHub: exigiría reescribir el historial; decidido no hacerlo (23/10).
- Capturar el RPE de fuerza después de la sesión: el TSS de fuerza sale de la FC (modelo B) y ya cuadra con TP (≈ 1,00); decidido quitarlo (25/10).
- Sensor que impida nombrar la hipoglucemia a un atleta con DT2 y metformina: el LLM la menciona para tranquilizar, no como aviso, y no limita el entrenamiento; aceptado sin sensor (26/10).
- Tiempo máximo en las llamadas a Garmin fuera del bucle con tools: el cuelgue de 57 min de la batería del 27/10 fue el portátil en espera moderna (Kernel-Power 506/507), no Kairos; `garminconnect` ya limita cada petición a 15 s con 3 reintentos (27/10).
- Menú con los 5 proveedores de prueba (NVIDIA, Gemini, Groq, Mistral, Cerebras): sustituido por el proveedor de IA que diga cada atleta (26/10).
