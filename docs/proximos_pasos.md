# Próximos pasos — Kairos Coach

Última revisión: 28/10/2026.

Solo lo que falta y lo descartado. Lo terminado se borra: el detalle está en `CHANGELOG.md` y en git. El estado vigente del cálculo de carga y las limitaciones aceptadas están en `CLAUDE.md`.

Bitácoras anteriores en git: hasta el 25/10, `git show 1bf341f:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10 (señal, lección y evidencia de cada cambio), `git show 6aff019:docs/proximos_pasos.md`.

## Dónde lo dejamos

Se reescribe al final de cada sesión.

**Sesión del 28/10/2026** (ordenador 1). 807 tests; batería 25/25; batería del bucle 10/10.

- Hecho: ordenador 1 alineado con el historial reescrito; ticket de GitHub Support **4918285** abierto (purga de los 80 commits viejos, de `eeee21b` a `716cd0c`; cuando respondan, comprobar solo códigos HTTP: resuelto si dan 404 en la web y 422 en la API); revisión completa del proyecto y sus arreglos: errores del proveedor que cerraban Kairos, G15, totales del periodo y récords, tests de los lectores y del cliente MCP (11), avisos de Garmin (9), precarga de "mi último trail" (10) rendimiento (8b caché de Supabase, 8c arranque en paralelo, 8a bucle con tools con `get_rhr_trend` y `kairos_load_trends` sin poda) y `trainer_agent.py` dividido en módulos (7; de 12.363 a 200 líneas, mapa en `docs/arquitectura.md`). Último commit: el del punto 7 ("refactor: _answer en tabla de rutas…"); lo del 8a y anteriores ya está en GitHub, lo del 7 sin subir hasta orden expresa. En el ordenador 2, al empezar: `git pull`.
- **Siguiente paso:** **aparcado hasta ~09/11/2026** (decisión del usuario): usar Kairos y revisar juntos `agent.log` (`[SENSOR]`, `[G15]`, `[TOOL] iter=N pide`, `[TURNO]`, `[LLM] ◀`) para los puntos 1, 2 y 8, con las preguntas mal enrutadas al banco de frases. Los puntos 3 a 5 (TSS frente a TP), cuando haya sesiones nuevas con su TSS en TP.

## Pendiente

### A. Puesta en producción

1. **Uso real y revisión de las líneas `[SENSOR]` de `agent.log`.** Usar Kairos unos días con preguntas reales y revisar juntos cuánto salta cada sensor, falsos positivos nuevos y qué guía conviene reforzar (bucle de gobierno, `docs/Harness.md` §7). En concreto:
   - Si **S25** (diferencia de HRV), **S26** (etiqueta rTSS/hrTSS), **S27** ("mañana" con el día que no toca) y **S28** (día de la semana con su fecha) saltan con preguntas reales, y si con las guías G13 (calendario) y G14 (HRV calculada) del 26/10 bajan los saltos de S25, S27 y S28.
   - **S27, falsos positivos vistos el 28/10** (batería 20261028_141326, preguntas 8 y 9): "mañana" dicho desde otro día del texto ("Dom 01 · … Revisa HRV mañana lunes" en una fila de plan; "Mañana (viernes 30)" después de la sesión del jueves 29). S27 lo mide siempre desde hoy y el reintento no lo arregla porque no hay error. Idea: no juzgar "mañana" en una línea o fila que empieza por otro día o fecha.
   - **Cifras inventadas fuera de la carga** (28/10, `estres_2`, pregunta 2): para comparar el estrés de dos semanas el LLM no pidió el detalle de esta semana y se inventó los minutos por día (510 min en reposo el 26/10; Garmin: 1.097). S14 solo vigila cifras de carga: mirar con preguntas reales si hace falta un sensor de cifras que no salen de las tools.
   - Enrutado: cada pregunta real mal enrutada va al banco de frases (`PHRASE_BANK` en `tests/test_tool_router.py`) antes de tocar los detectores. Mirar también si salta `[G15]` (marco de hoy en el bucle con tools) y si S15 lo hace cumplir.

### B. Chat y LLM

2. **Metatooling, siguientes pasos** (grupos de tools hechos el 26/10: −22 % de tokens por llamada, mismas respuestas). Si hace falta más: (a) el prompt de sistema (≈ 7.000 tokens, la mayor parte de cada llamada) por partes según la pregunta, como los grupos; (b) fusionar tools parecidas en el servidor (`get_sleep_data`/`get_sleep_summary`, las tres de actividades) si el LLM las confunde. Vigilar en `agent.log` cuántas veces se pide un grupo (`[TOOL] el LLM pide el grupo`): más de 1 de cada 5 preguntas = reglas mal hechas.

### C. Cálculo de carga (TSS frente a TrainingPeaks)

Antes de tocar cualquier fórmula: hipótesis escrita, criterio de cierre fijado antes de ver resultados y prueba sin escribir (reglas de método de `CLAUDE.md`).

3. **Trail rápido (< 6:00/km).** Validar cuando haya más salidas con rTSS en TP. Hoy n = 1 (16/08: Kairos 128.0 frente a TP 112.4).
4. **Series cortas de running** (6x500, 6x1000, 4x400, 8x500): salen entre un 11 % y un 20 % por encima de TP; 23966766696 (13/08, 8x45" en cuesta) ×1.20. Plantear hipótesis antes de tocar el detector (p. ej. relación FC-velocidad con retraso fisiológico).
5. **Revalidación periódica** con un export nuevo de TP: prueba sin escribir, criterio fijado antes, solo las filas esperadas.

### D. Futuro (sin prioridad, cuando haga falta)

6. **Peso con báscula.** Kairos toma el peso al arrancar de la configuración de usuario de Garmin (`get_user_profile`, `userData.weight`) y solo si falta, del registro de hoy de `get_body_composition` (`fetch_garmin_personal_data`). Comprobado el 28/10 en vivo: al cambiarlo en la configuración, Garmin guarda también un registro con fecha de hoy y las dos fuentes coinciden (65,0 kg). Sin comprobar: si un pesaje de báscula actualiza también la configuración. Si un atleta usa báscula y `/perfil` no refleja su último pesaje, invertir el orden: primero el último registro de `get_body_composition` (rango reciente, no solo hoy) y la configuración como respaldo. El peso no se edita en `/perfil editar salud`: Garmin manda y el arranque lo sobrescribiría.

### E. Revisión del 28/10 (estructura y rendimiento)

De la revisión completa del proyecto del 28/10; ya hechos los fallos del proveedor que cerraban Kairos, la G15, los totales del periodo, los puntos 9 a 11 y el rendimiento (CHANGELOG).

7. **Dividir `tests/test_trainer_agent.py`** (≈ 7.000 líneas) como se dividió `agent/trainer_agent.py` el 28/10 (un fichero de tests por módulo), cuando haga falta tocarlo; sin prisa: los tests ya importan cada función de su módulo.
8. **Arranque, lo que queda** (8c, en curso el 28/10): importar `openai` y `mcp` (≈ 1,5 s) y arrancar el servidor MCP (≈ 1,6 s). Hechos el 28/10: estrés de un rango en una llamada (`get_stress_trend`) y la regla del juez para la respuesta de tope del bucle (CHANGELOG).

## Descartado

- Quitar a Copilot de los contributors de GitHub: exigiría reescribir el historial; decidido no hacerlo (23/10).
- Capturar el RPE de fuerza después de la sesión: el TSS de fuerza sale de la FC (modelo B) y ya cuadra con TP (≈ 1,00); decidido quitarlo (25/10).
- Sensor que impida nombrar la hipoglucemia a un atleta con DT2 y metformina: el LLM la menciona para tranquilizar, no como aviso, y no limita el entrenamiento; aceptado sin sensor (26/10).
- Tiempo máximo en las llamadas a Garmin fuera del bucle con tools: el cuelgue de 57 min de la batería del 27/10 fue el portátil en espera moderna (Kernel-Power 506/507), no Kairos; `garminconnect` ya limita cada petición a 15 s con 3 reintentos (27/10).
- Menú con los 5 proveedores de prueba (NVIDIA, Gemini, Groq, Mistral, Cerebras): sustituido por el proveedor de IA que diga cada atleta (26/10).
