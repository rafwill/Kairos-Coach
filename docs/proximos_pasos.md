# Próximos pasos — Kairos Coach

Última revisión: 27/10/2026.

Solo lo que falta y lo descartado. Lo terminado se borra: el detalle está en `CHANGELOG.md` y en git. El estado vigente del cálculo de carga y las limitaciones aceptadas están en `CLAUDE.md`.

Bitácoras anteriores en git: hasta el 25/10, `git show 1bf341f:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10 (señal, lección y evidencia de cada cambio), `git show 6aff019:docs/proximos_pasos.md`.

## Dónde lo dejamos

Se reescribe al final de cada sesión.

**Sesión del 27/10/2026** (ordenador 2). Todo subido a GitHub, sin cambios a medias. 769 tests; batería 25/25.

**En el ordenador 1, al empezar (en este orden).** El historial de GitHub se reescribió el 27/10 (80 commits desde el 23/10; ver CHANGELOG): un `git pull` normal mezclaría el historial viejo con el nuevo y un `git push` desde allí lo devolvería a GitHub. Hasta el paso 3, ese ordenador tiene la versión vieja de este fichero: estos pasos se leen aquí o en GitHub.

1. `git status`: tiene que decir que no hay nada que confirmar. Si hay cambios o commits sin subir, parar y guardarlos aparte (copiar los ficheros) antes de seguir: el paso 3 los borra.
2. `git fetch origin`.
3. `git reset --hard origin/main`.
4. Comprobar: `git log --oneline -1` muestra este commit o uno posterior y `git status` dice "up to date with 'origin/main'".
5. Opcional, para que no queden en local los commits viejos: `git reflog expire --expire=now --all` y `git gc --prune=now`.
6. `.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt`: hay topes de versión nuevos (`garminconnect<0.4`, `supabase<3`, `openai>=3,<4`). Python tiene que ser 3.12 o superior.
7. Entrar una vez en Kairos para comprobar que arranca. La clave tiene que estar ya en `PROVIDER_API_KEY` (el traslado desde `NVIDIA_API_KEY` se quitó).

- Hecho hoy: limpieza del proyecto (D1–D4), revisión del proyecto (código sobrante, una sola fuente para la serie de carga, tendencia de HRV con días fallidos, dependencias con tope y Python 3.12+, duplicados, "API pública" de `load_metrics` fuera) y reescritura del historial. Detalle en CHANGELOG.
- **Queda fuera de nuestro alcance:** GitHub conserva los commits viejos accesibles por su enlace directo hasta que los purga, y la actividad pública reciente muestra los mensajes de los pushes. Para eliminarlos, pedir a GitHub Support que purgue las vistas en caché y los commits sin referencia del repositorio `rafwill/Kairos-Coach` (procedimiento de eliminación de datos sensibles).
- **Siguiente paso:** nada pendiente de la revisión del 27/10. **Aparcado hasta ~09/11/2026** (decisión del usuario): el resto se verifica con el uso diario de Kairos. Después, revisar juntos `agent.log` (`[SENSOR]`, `[TOOL] el LLM pide el grupo`, `[LLM] ◀`) para los puntos 1 y 2; los puntos 3 a 5 (TSS frente a TP), cuando haya sesiones nuevas con su TSS en TP.

## Pendiente

### A. Puesta en producción

1. **Uso real y revisión de las líneas `[SENSOR]` de `agent.log`.** Usar Kairos unos días con preguntas reales y revisar juntos cuánto salta cada sensor, falsos positivos nuevos y qué guía conviene reforzar (bucle de gobierno, `docs/Harness.md` §7). En concreto:
   - Si **S25** (diferencia de HRV), **S26** (etiqueta rTSS/hrTSS), **S27** ("mañana" con el día que no toca) y **S28** (día de la semana con su fecha) saltan con preguntas reales, y si con las guías G13 (calendario) y G14 (HRV calculada) del 26/10 bajan los saltos de S25, S27 y S28.

### B. Chat y LLM

2. **Metatooling, siguientes pasos** (grupos de tools hechos el 26/10: −22 % de tokens por llamada, mismas respuestas). Si hace falta más: (a) el prompt de sistema (≈ 7.000 tokens, la mayor parte de cada llamada) por partes según la pregunta, como los grupos; (b) fusionar tools parecidas en el servidor (`get_sleep_data`/`get_sleep_summary`, las tres de actividades) si el LLM las confunde. Vigilar en `agent.log` cuántas veces se pide un grupo (`[TOOL] el LLM pide el grupo`): más de 1 de cada 5 preguntas = reglas mal hechas.

### C. Cálculo de carga (TSS frente a TrainingPeaks)

Antes de tocar cualquier fórmula: hipótesis escrita, criterio de cierre fijado antes de ver resultados y prueba sin escribir (reglas de método de `CLAUDE.md`).

3. **Trail rápido (< 6:00/km).** Validar cuando haya más salidas con rTSS en TP. Hoy n = 1 (16/08: Kairos 128.0 frente a TP 112.4).
4. **Series cortas de running** (6x500, 6x1000, 4x400, 8x500): salen entre un 11 % y un 20 % por encima de TP; 23966766696 (13/08, 8x45" en cuesta) ×1.20. Plantear hipótesis antes de tocar el detector (p. ej. relación FC-velocidad con retraso fisiológico).
5. **Revalidación periódica** con un export nuevo de TP: prueba sin escribir, criterio fijado antes, solo las filas esperadas.

## Descartado

- Quitar a Copilot de los contributors de GitHub: exigiría reescribir el historial; decidido no hacerlo (23/10).
- Capturar el RPE de fuerza después de la sesión: el TSS de fuerza sale de la FC (modelo B) y ya cuadra con TP (≈ 1,00); decidido quitarlo (25/10).
- Sensor que impida nombrar la hipoglucemia a un atleta con DT2 y metformina: el LLM la menciona para tranquilizar, no como aviso, y no limita el entrenamiento; aceptado sin sensor (26/10).
- Tiempo máximo en las llamadas a Garmin fuera del bucle con tools: el cuelgue de 57 min de la batería del 27/10 fue el portátil en espera moderna (Kernel-Power 506/507), no Kairos; `garminconnect` ya limita cada petición a 15 s con 3 reintentos (27/10).
- Menú con los 5 proveedores de prueba (NVIDIA, Gemini, Groq, Mistral, Cerebras): sustituido por el proveedor de IA que diga cada atleta (26/10).
