# Próximos pasos — Kairos Coach

Última revisión: 30/10/2026.

Solo lo que falta y lo descartado. Lo terminado se borra: el detalle está en `CHANGELOG.md` y en git. El estado vigente del cálculo de carga y las limitaciones aceptadas están en `CLAUDE.md`.

Bitácoras anteriores en git: hasta el 25/10, `git show 1bf341f:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10 (señal, lección y evidencia de cada cambio), `git show 6aff019:docs/proximos_pasos.md`.

## Dónde lo dejamos

Se reescribe al final de cada sesión.

**Sesión del 30/10/2026** (ordenador 2, el del trabajo). `git pull` desde `22e5595` hasta `aa27605`. Decidido el pendiente 10: Gemini 3.6 Flash para planes y arranque, y Gemini 3.5 Flash Lite para el chat y como relevo (columna `ia_provider.chat_model`; CHANGELOG del 30/10). Hecho en Supabase: columna `ia_provider.chat_model` añadida y guardado `gemini-3.5-flash-lite` con `/proveedor`, comprobado al leerlo. Para cambiarlo con Gemini ya guardado, `/proveedor` propone el modelo del chat que hay, no Flash Lite: hay que escribirlo.
- Después, en la misma sesión:
  - Resumen de cambios del plan en h:mm y TSS.
  - Pendiente 11 d1: aviso en el arranque si el plan ya no encaja.
  - Pendiente 11 d2: «replanifica el plan» rehace hasta la carrera.
  - Pendiente 11 c: lo que el atleta dice para siempre se guarda para sus planes.
  - Pendiente 11 b: otros deportes dentro del plan (bici, remo, elíptica, natación).
  - Pendiente 11 a: dónde entrena y el desnivel de sus salidas (asfalto 7,6 m/km, trail 48,3 m/km con sus datos).
  - El tiempo de hoy en el arranque (Open-Meteo; `/perfil lugar <ciudad>`).
  - Queda del punto 11: las pruebas en vivo con Gemini, fuera del proxy.
  - Arreglado el fallo que rehízo el plan de Gemini con el motor determinista ("plan ver 19bd7f1c" sin barra; `plan_data` perdido al normalizar). Plan de Gemini recuperado como versión 4 y comprobado.
  - Detalle en el CHANGELOG del 30/10. Tests: 929.
- **Siguiente paso, fuera del proxy (en casa):**
  - Punto 10: Flash Lite en el chat y el relevo.
  - Pendiente 11 d2 en vivo: un «replanifica el plan» real, revisado por el usuario.

**Sesión del 28-29/10/2026** (ordenador 1). Release **v3.0.0 — Kairos Coach Funcional** (29/10). 861 tests; batería 25/25 con Gemini. Proveedor del usuario: Gemini `gemini-3.6-flash` (la clave de NVIDIA, en `NVIDIA_API_KEY`). Plan activo: el de Gemini hacia la media de Santa Pola (`19bd7f1c`). Lo hecho, en `CHANGELOG.md` (28-29/10); el mapa de módulos, en `docs/arquitectura.md`. En el ordenador 2, al empezar: `git pull` y poner en su `.env` la clave de Gemini en `PROVIDER_API_KEY`.

- **Siguiente paso (decisión del usuario):** usar Kairos unas semanas con el plan y ajustar lo que salga. Revisar juntos `agent.log` (`[SENSOR]`, `[G15]`, `[TOOL] iter=N pide`, `[TURNO]`, `[LLM] ◀`, `[PLAN]`, reintentos `[LLM] gemini 503`) para los puntos 1 y 2 y la revisión del plan (B2.9). Los puntos 3 a 5 (TSS frente a TP), cuando haya sesiones nuevas con su TSS en TP.

## Pendiente

### A. Puesta en producción

1. **Uso real y revisión de las líneas `[SENSOR]` de `agent.log`.** Usar Kairos unos días con preguntas reales y revisar juntos cuánto salta cada sensor, falsos positivos nuevos y qué guía conviene reforzar (bucle de gobierno, `docs/Harness.md` §7). En concreto:
   - Si **S25** (diferencia de HRV), **S26** (etiqueta rTSS/hrTSS), **S27** ("mañana" con el día que no toca) y **S28** (día de la semana con su fecha) saltan con preguntas reales, y si con las guías G13 (calendario) y G14 (HRV calculada) del 26/10 bajan los saltos de S25, S27 y S28.
   - **S27, falsos positivos vistos el 28/10** (batería 20261028_141326, preguntas 8 y 9): "mañana" dicho desde otro día del texto ("Dom 01 · … Revisa HRV mañana lunes" en una fila de plan; "Mañana (viernes 30)" después de la sesión del jueves 29). S27 lo mide siempre desde hoy y el reintento no lo arregla porque no hay error. Idea: no juzgar "mañana" en una línea o fila que empieza por otro día o fecha.
   - **Cifras inventadas fuera de la carga** (28/10, `estres_2`, pregunta 2): para comparar el estrés de dos semanas el LLM no pidió el detalle de esta semana y se inventó los minutos por día (510 min en reposo el 26/10; Garmin: 1.097). S14 solo vigila cifras de carga: mirar con preguntas reales si hace falta un sensor de cifras que no salen de las tools.
   - Enrutado: cada pregunta real mal enrutada va al banco de frases (`PHRASE_BANK` en `tests/test_tool_router.py`) antes de tocar los detectores. Mirar también si salta `[G15]` (marco de hoy en el bucle con tools) y si S15 lo hace cumplir.

### B. Chat y LLM

2. **Metatooling, si hace falta más** (los grupos de tools ya están; CHANGELOG del 26/10): (a) el prompt de sistema (≈ 7.000 tokens, la mayor parte de cada llamada) por partes según la pregunta, como los grupos; (b) fusionar tools parecidas en el servidor (`get_sleep_data`/`get_sleep_summary`, las tres de actividades) si el LLM las confunde. Vigilar en `agent.log` cuántas veces se pide un grupo (`[TOOL] el LLM pide el grupo`): más de 1 de cada 5 preguntas = reglas mal hechas.

### B2. Planes

9. **Plan construido por el LLM** (28-29/10; CHANGELOG). Con nemotron, 3 de 5 planes válidos el 28/10 y 2 de 2 con preferencias el 29/10 (3 a 5,5 min); si falla, no se guarda ningún plan (29/10). El 29/10, en `/plan crear` real, falló por la semana 9 sin descanso y se guardó el plan genérico de respaldo, ya quitado. Pendiente:
   - **Revisión del usuario** de un plan real (`/plan crear`) y de una revisión en el chat ("ajusta el plan: …").
   - **Fiabilidad con otros modelos:** medir con 2-3 modelos más (batería de planes) antes de producción. Hecho el 29/10 con lo gratuito: Gemini 3.5/3.6 Flash, plan válido en 68 s; Groq gratuito imposible (8.000 tokens por minuto); nemotron, sin plan en 13 min.

10. **Dos modelos de Gemini (decidido el 30/10; CHANGELOG): comprobarlo en vivo.** Flash (20 peticiones al día) para planes y arranque, y Flash Lite (500) para el chat y como relevo. Desde el ordenador del trabajo no se pudo probar porque el proxy bloquea Gemini. Fuera del proxy, comprobar:
    - Que `gemini-3.5-flash-lite` responde con ese nombre (Google sirvió el 3.5 Flash como 3.6).
    - Batería 25/25 con Flash Lite en el chat (`tools/run_bateria.py --modelo gemini-3.5-flash-lite`).
    - Que el relevo salta al agotarse Flash (`[LLM] … relevo con …` en `agent.log`).
    - Si Flash Lite responde peor que Flash en el chat, valorar las otras opciones: facturación en AI Studio, o NVIDIA para el chat.

### C. Cálculo de carga (TSS frente a TrainingPeaks)

Antes de tocar cualquier fórmula: hipótesis escrita, criterio de cierre fijado antes de ver resultados y prueba sin escribir (reglas de método de `CLAUDE.md`).

3. **Trail rápido (< 6:00/km).** Validar cuando haya más salidas con rTSS en TP. Hoy n = 1 (16/08: Kairos 128.0 frente a TP 112.4).
4. **Series cortas de running** (6x500, 6x1000, 4x400, 8x500): salen entre un 11 % y un 20 % por encima de TP; 23966766696 (13/08, 8x45" en cuesta) ×1.20. Plantear hipótesis antes de tocar el detector (p. ej. relación FC-velocidad con retraso fisiológico).
5. **Revalidación periódica** con un export nuevo de TP: prueba sin escribir, criterio fijado antes, solo las filas esperadas.

### D. Futuro (sin prioridad, cuando haga falta)

6. **Peso con báscula.** Kairos toma el peso al arrancar de la configuración de usuario de Garmin (`get_user_profile`, `userData.weight`) y solo si falta, del registro de hoy de `get_body_composition` (`fetch_garmin_personal_data`). Comprobado el 28/10 en vivo: al cambiarlo en la configuración, Garmin guarda también un registro con fecha de hoy y las dos fuentes coinciden (65,0 kg). Sin comprobar: si un pesaje de báscula actualiza también la configuración. Si un atleta usa báscula y `/perfil` no refleja su último pesaje, invertir el orden: primero el último registro de `get_body_composition` (rango reciente, no solo hoy) y la configuración como respaldo. El peso no se edita en `/perfil editar salud`: Garmin manda y el arranque lo sobrescribiría.

7. **Arranque del servidor MCP** (2,6–3,8 s: es otro proceso, que importa `garminconnect` y FastMCP, la API de servidor del SDK oficial `mcp`, no el paquete aparte `fastmcp`). Solo si el arranque molesta: mirar qué importa el servidor al arrancar y si se puede lanzar antes (necesita las credenciales de Garmin, que llegan tras el inicio de sesión).

### E. Fuera de nuestras manos

8. **Ticket de GitHub Support 4918285** (abierto el 28/10): purga de las vistas en caché de los 80 commits viejos, de `eeee21b` a `716cd0c`. Cuando respondan, comprobar solo códigos HTTP: resuelto si dan 404 en la web y 422 en la API.

### F. Notas adicionales

9. Revisar como conectar Claude con Kairos para la sincronización de datos y la generación de TSS en vez de Nvidia. Cómo: `/proveedor` → "anthropic" (ya en `agent/ia_provider.py`), clave de `platform.claude.com/settings/keys` (pago por uso), modelo `claude-opus-5-5` o `claude-sonnet-5-5`; va por la capa compatible con OpenAI de Anthropic, pensada para probar (si se adopta, cliente nativo). Aplazado por coste (29/10, decisión del usuario).

10. Revisar garmin-personal-mcp. MCP que se puede conectar a Claude para preguntar cosas a Calude o ChatGPT

11. **Planes que se adaptan al atleta en cada momento** (revisado el 30/10 frente a lo hecho el 28-29/10). Es lo que dará la fuerza y el valor a Kairos.
    - **Ya hecho** (CHANGELOG del 28 y 29/10):
      - El plan lo construye el LLM del atleta y el código lo comprueba.
      - `/plan crear` pregunta como un entrenador: días disponibles, día fijo de descanso, tiempo máximo por día, dobles, distribución de la intensidad y progresión. Las respuestas se guardan en el perfil.
      - Lo que el atleta escribe al pedir el plan llega al LLM como "Lo que pide el atleta".
      - En el chat, "ajusta el plan: …" (`_route_plan_revision`) revisa los días que quedan, con lo hecho los últimos 7 días (Garmin + `activity_tss`).
      - El motor diario ajusta la sesión de hoy con HRV, sueño, Body Battery y carga.
    - **Falta:**
      - (a) **Entorno: hecho el 30/10, con la temperatura del día (Open-Meteo)** (CHANGELOG). Situación de partida: el LLM no recibe ni dónde vive el atleta, ni la orografía (el desnivel de sus rutas habituales se podría sacar de Garmin), ni la temperatura (con calor, ajustar ritmos o ir por FC). `/plan crear` no lo pregunta.
        - **Diseño y criterios fijados antes de programarlo (30/10):**
          - **Orografía, de Garmin y sin umbrales inventados.** Con las mismas actividades de las últimas 6 semanas que ya lee el contexto de carga, se calculan metros de desnivel positivo por km, por separado para carrera en asfalto y para trail, con los km de cada uno ("asfalto: 8 m/km en 120 km; trail: 45 m/km en 30 km"). Sin etiquetas llano/ondulado: el LLM interpreta el número. Si un grupo no tiene km, no sale.
          - **Lugar.** `/plan crear` pregunta "¿Dónde entrenas normalmente? (ciudad o zona; Enter para no decirlo)" y lo guarda en `goals.training_location`. El LLM lo recibe con los datos.
          - **Instrucción en `prompts/plan/system.md`**, no en el código: tener en cuenta el terreno habitual (cuestas donde las hay, o sustitutos si es llano) y el clima de ese lugar en las fechas del plan (con calor, sesiones por FC o a primera hora).
          - **Temperatura real del día: fuera de este paso.** Necesita un servicio de tiempo externo que recibe la ubicación. Decide el usuario.
          - **Temperatura real: hecho el 30/10** (`agent/weather.py`; CHANGELOG). Para verlo hace falta `/perfil lugar <ciudad>`: hoy el perfil del usuario no tiene lugar. Diseño y criterios fijados antes de programarlo:
            - **Fuente:** Open-Meteo, gratuito y sin clave; comprobado desde la red del trabajo (200).
              - El nombre del lugar (`goals.training_location`) se geocodifica una vez y las coordenadas se guardan con el nombre del que salen (`goals.training_location_coords`). Si cambia el lugar, se vuelven a buscar.
              - Cada día solo se envían las coordenadas. Nada más del atleta sale hacia Open-Meteo.
            - **Datos de hoy, en la hora local del lugar:** temperatura mínima y máxima, sensación térmica máxima y la temperatura a las 7:00, 12:00 y 19:00.
            - **Dónde:** bloque "🌡️ Tiempo hoy en …" en el arranque y en la ruta de estado del día, tras la sesión de hoy. La capa de coaching lo recibe con los datos.
            - **Sin umbrales de calor en el código.** El motor diario no cambia. La guía para el LLM va en `prompts/coaching_rules.md`: con calor, lo exigente por FC y a la hora más fresca del día. El LLM decide con los datos.
            - **Si falla** (sin lugar, sin red, más de 4 s o respuesta rara): sin bloque, el arranque sigue igual y queda una línea en el log.
            - **`/perfil lugar <ciudad>`** guarda el lugar sin pasar por `/plan crear`.
            - **Cierre:** tests de la lectura de la respuesta, del fallo, de la caché de coordenadas, del bloque y del comando. En vivo, la función de producción con un lugar real.
          - **Cierre:** tests del cálculo (asfalto, trail, sin km, sin desnivel), de la pregunta y de que llega al LLM. Además, el cálculo con los datos reales del usuario (solo lectura), comparable con Garmin Connect.
      - (b) **Deportes cruzados: hecho el 30/10** (CHANGELOG). Queda verlo con un `/plan crear` real con bici o remo, fuera del proxy. Situación de partida: el plan admite `cross` y `strength`, pero `/plan crear` no pregunta qué hace el atleta (bici, remo, gimnasio) ni cuánto.
        - Un `cross` sin tramos sale titulado "Fuerza" y con el TSS de la fuerza (35/h, `plan_builder.py`), no con el del deporte que sea.
        - La regla de volumen (≤ 10 %) mira el volumen sostenido sin separar deportes.
        - **Diseño y criterios fijados antes de programarlo (30/10):**
          - **Pregunta en `/plan crear`:** "¿Qué otros deportes quieres dentro del plan? (bici, remo, elíptica, natación; «no» si ninguno; Enter para que decida el entrenador)" y, si da alguno, "¿Cuántas sesiones por semana como mucho?". Se guarda en `plan_preferences.cross` = {sports, max_per_week}; Enter = sin regla.
          - **Formato del día `cross`:** lleva `"sport"` (cycling, rowing, elliptical o swimming) y tramos por FC (FC1–FC5) con warmup y cooldown. El título por defecto sale del deporte ("Bici", "Remo", "Elíptica", "Natación").
            - Sin tramos, sin "sport" o con ritmo (zonas Z, RP o "pace") → fallo con su texto para el LLM.
            - El TSS sale de los tramos por FC con la tabla hrTSS de TP, como la bici sin potencia, en vez de 35/h de la fuerza. Sin km.
          - **Reglas por semana** (si el atleta respondió): solo sus deportes y como mucho `max_per_week` sesiones `cross`; con «no», ninguna.
          - **El LLM recibe** sus deportes y el máximo en "Preferencias del atleta", y el formato del `cross` en el prompt de semanas.
          - **Volumen sostenido** (`recent_weekly_minutes`): sin caminatas ni senderismo, que el plan nunca pone (inflaban el punto de partida). Con los datos del usuario no cambia nada: 0 caminatas en las últimas 4 semanas.
          - Planes ya guardados: no se tocan (el plan actual no tiene `cross`).
          - **Cierre:** tests de cada regla a favor y en contra, del TSS por FC frente a los 35/h, de la pregunta de `/plan crear` y del volumen sin caminatas.
      - (c) **Preferencias que duran: hecho el 30/10** (`agent/plan_prefs.py`; CHANGELOG). Queda verlo con frases reales en `agent.log` (`[PLAN] preferencias guardadas`). Una frase que se guarde o se pierda sin deber va a los tests antes de tocar las reglas.
        - **Diseño y criterios fijados antes de programarlo (30/10).** Sin LLM: `agent/plan_prefs.py` lee el mensaje de planes (crear, ajustar o replanificar) antes de llamar al LLM.
          - **Días que nunca puede.** Cuentan si se dicen en plural ("los martes no puedo", "no puedo entrenar los martes ni los jueves") o con "nunca", "ningún", "siempre" o "a partir de ahora". El día sale de `availability.training_days` y entra en `rest_days`, así que las reglas lo exigen como descanso desde esa misma petición.
          - **Lo que no dura:** "este martes", "el martes que viene", "mañana" o un día en singular sin esas palabras. Eso es para una vez y no se guarda.
          - **Preferencias de texto.** La frase que lleva "prefiero", "siempre", "nunca", "a partir de ahora" o "de ahora en adelante" se guarda tal cual en `plan_preferences.notes`: sin duplicados, como mucho 8 (se va la más antigua) y 200 caracteres cada una.
          - **Las preferencias llegan al LLM** en "Preferencias del atleta" de cada plan, revisión o replanificado.
          - **La respuesta dice qué se ha guardado.** `/plan crear` las enseña y pregunta si se mantienen. Responder que ya no borra las notas; los días se cambian con su pregunta de días.
          - `/plan crear` ya no borra las notas al guardar sus respuestas.
          - **Cierre:** un test por tipo de frase, a favor y en contra, con frases reales del estilo del usuario. Más uno de que llegan al LLM, uno de que las reglas exigen el día y uno de `/plan crear`.
      - (d) **Replanificación por los datos y no solo a petición.**
        - Hoy Kairos solo replanifica si el atleta lo pide, y solo hasta el final de la semana siguiente. El motor diario solo toca la sesión de hoy.
        - Falta que Kairos detecte en Garmin cuándo el plan ya no vale y proponga rehacerlo: sesiones clave sin hacer, varios días sin actividad, HRV baja varios días seguidos, una actividad distinta de la prevista.
        - Falta poder rehacer el bloque entero cuando el parón es largo (viaje, una semana enfermo), con la carrera en su fecha y la progresión desde lo que el atleta sostiene de verdad.
    - Orden propuesto: (d) primero, que es el valor diferencial; después (c), (b) y (a).
    - **(d1) Detector de desvíos: hecho el 30/10** (`agent/plan_drift.py`; criterios y resultado en el CHANGELOG). Queda verlo saltar con un desvío real y revisar con el usuario si los umbrales avisan cuando deben.
    - **(d2) Replanificar el bloque entero tras un parón: hecho el 30/10** (`_route_plan_rebuild`; CHANGELOG). **Falta la prueba en vivo con Gemini fuera del proxy:** un «replanifica el plan» real, en tiempo y peticiones parecidos a `/plan crear`, revisado por el usuario. Diseño y criterios fijados antes de programarlo:
      - **Cuándo:** con un plan del LLM activo y "replanific…" en el mensaje (lo que propone el bloque de d1). "Ajusta el plan" sigue siendo la revisión corta, hasta el final de la semana siguiente.
      - **Qué rehace:** la estructura y los días desde el lunes que viene hasta la carrera, con el mismo constructor que `/plan crear` (`_build_plan_with_llm`) y todas sus reglas. Si hoy es lunes, desde hoy.
        - La progresión parte de lo que el atleta sostiene de verdad: la media de las últimas 4 semanas, que ya recoge el parón.
        - La carrera, en su fecha. Objetivo y tiempo, los del plan activo.
        - Lo que el atleta escribe ("he estado enfermo una semana") llega al LLM como "Lo que pide el atleta".
      - **Esta semana:** se quedan las sesiones que tenía. Para cambiarlas, "ajusta el plan".
      - **Cómo se guarda:** versión nueva del mismo plan, que empieza el lunes de esta semana. La semana 1 es la actual, con sus sesiones; desde la 2, las nuevas. Lleva `revised_on` = hoy. La versión anterior queda en `training_plan_version`.
      - **Si el LLM no consigue un plan válido:** no cambia nada y la respuesta dice qué no cumplía, como en `/plan crear`.
      - **Si no queda al menos una semana entera antes de la de la carrera:** no rehace nada y propone "ajusta el plan".
      - **Cierre:** tests con el LLM simulado del enrutado (replanificar frente a ajustar), las fechas, la unión de semanas y `plan_data`, el fallo que no toca nada y el caso sin semanas. En vivo con Gemini, fuera del proxy (desde el trabajo no llega): un replanificado válido en tiempo y peticiones parecidos a `/plan crear`, revisado por el usuario.

12. Añadir un porcentaje de cumplimiento de los entrenamientos en Kairos basado en los datos de kairos y los entrenamientos del atleta

## Descartado

- Quitar a Copilot de los contributors de GitHub: exigiría reescribir el historial; decidido no hacerlo (23/10).
- Capturar el RPE de fuerza después de la sesión: el TSS de fuerza sale de la FC (modelo B) y ya cuadra con TP (≈ 1,00); decidido quitarlo (25/10).
- Sensor que impida nombrar la hipoglucemia a un atleta con DT2 y metformina: el LLM la menciona para tranquilizar, no como aviso, y no limita el entrenamiento; aceptado sin sensor (26/10).
- Tiempo máximo en las llamadas a Garmin fuera del bucle con tools: el cuelgue de 57 min de la batería del 27/10 fue el portátil en espera moderna (Kernel-Power 506/507), no Kairos; `garminconnect` ya limita cada petición a 15 s con 3 reintentos (27/10).
- Menú con los 5 proveedores de prueba (NVIDIA, Gemini, Groq, Mistral, Cerebras): sustituido por el proveedor de IA que diga cada atleta (26/10).
