# Próximos pasos — Kairos Coach

Última revisión: 26/10/2026.

Solo lo que falta y lo descartado. Lo terminado se borra: el detalle está en `CHANGELOG.md` y en git. El estado vigente del cálculo de carga y las limitaciones aceptadas están en `CLAUDE.md`.

Bitácoras anteriores en git: hasta el 25/10, `git show 4578226:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10 (señal, lección y evidencia de cada cambio), `git show f2f5867:docs/proximos_pasos.md`.

## Dónde lo dejamos

Se reescribe al final de cada sesión.

**Sesión del 26/10/2026** (ordenador 2). Sin cambios a medias. 778 tests; batería 25/25.

- Hecho hoy: patologías y lesiones separadas con protocolo de diabetes según medicación; proveedor de IA del atleta (tabla `ia_provider`, clave en `PROVIDER_API_KEY`); `.env.example` limpio; metatooling por grupos de tools, siempre activo (sin variable); el "cuelgue" de las 14:15 era el portátil suspendido, y lo que de verdad fallaba (token de Supabase caducado al despertar, que cerraba Kairos) está arreglado; tests de las ramas de error del inicio de sesión (`agent/main.py` del 90 % al 94 %); memoria entre sesiones comprobada en este ordenador (resumen del día e historial en `session_context`, sin caracteres rotos); la batería ya no la lee ni la escribe (`KAIROS_SESSION_MEMORY=false`) y la memoria del atleta se vació (toda era de baterías); S28 nuevo (día de la semana con su fecha), S11 sin falso positivo con "Supraumbral" y batería pintada a 400 columnas (tablas sin partir para el juez); comando `/perfil umbral fc <lpm>` para el umbral de FC de TP.
- Supabase ya tiene la tabla `ia_provider` (RLS comprobado) y el perfil del usuario está migrado (patologías y lesiones separadas, medicación de la diabetes, proveedor de IA `nvidia`).
- Al empezar en casa: `git pull`, `pip install -r requirements.txt` (`google-genai` ya no hace falta; se puede desinstalar) y entrar una vez en Kairos. No preguntará nada: la clave `NVIDIA_API_KEY` del `.env` pasa sola a `PROVIDER_API_KEY`. Después, el repaso de los sensores con las baterías guardadas allí y el caso de S10 en bici (punto 1).
- **Aparcado hasta ~09/11/2026** (decisión del usuario): usar Kairos a diario un par de semanas y luego revisar juntos `agent.log` (`[SENSOR]`, `[TOOL] el LLM pide el grupo`, `[LLM] ◀`) para los puntos 1 y 2. Lo único que no espera al uso es el repaso en casa del punto 1.

## Pendiente

### A. Puesta en producción

1. **Uso real y revisión de las líneas `[SENSOR]` de `agent.log`.** Usar Kairos unos días con preguntas reales y revisar juntos cuánto salta cada sensor, falsos positivos nuevos y qué guía conviene reforzar (bucle de gobierno, `docs/Harness.md` §7). En concreto:
   - **En casa, con las baterías guardadas allí (891 respuestas)**: repasar con los sensores del 26/10 (S10 con la marca de progresivo detrás del rango, S11 sin "Supraumbral"/"Subumbral" y S28 nuevo) y confirmar que solo desaparecen falsos positivos y que S28 sigue sin ninguno (en las 220 del ordenador 2: S11 quita solo el caso conocido; S28, 5 avisos, los 5 errores reales).
   - **Posible falso positivo de S10** (24/10, 09:18, ordenador 1): marcó como fuera de zona 105–124 y 159–179 lpm, que son la Z1 y la Z5 de bici; parece que tomó una línea de ciclismo como de carrera (`_range_sport`). Reproducirlo con la respuesta completa (`agent.log` de casa) antes de tocar el sensor.
   - Si **S25** (diferencia de HRV), **S26** (etiqueta rTSS/hrTSS), **S27** ("mañana" con el día que no toca) y **S28** (día de la semana con su fecha) saltan con preguntas reales.

### B. Chat y LLM

2. **Metatooling, siguientes pasos** (grupos de tools hechos el 26/10: −22 % de tokens por llamada, mismas respuestas). Si hace falta más: (a) el prompt de sistema (≈ 7.000 tokens, la mayor parte de cada llamada) por partes según la pregunta, como los grupos; (b) fusionar tools parecidas en el servidor (`get_sleep_data`/`get_sleep_summary`, las tres de actividades) si el LLM las confunde. Vigilar en `agent.log` cuántas veces se pide un grupo (`[TOOL] el LLM pide el grupo`): más de 1 de cada 5 preguntas = reglas mal hechas.

### C. Cálculo de carga (TSS frente a TrainingPeaks)

Antes de tocar cualquier fórmula: hipótesis escrita, criterio de cierre fijado antes de ver resultados y prueba sin escribir (reglas de método de `CLAUDE.md`).

3. **Trail rápido (< 6:00/km).** Validar cuando haya más salidas con rTSS en TP. Hoy n = 1 (16/08: Kairos 128.0 frente a TP 112.4).
4. **Series cortas de running** (6x500, 6x1000, 4x400, 8x500): salen entre un 11 % y un 20 % por encima de TP; 23966766696 (13/08, 8x45" en cuesta) ×1.20. Plantear hipótesis antes de tocar el detector (p. ej. relación FC-velocidad con retraso fisiológico).
5. **Bici sin potencia de más de 2 h.** 07/08: Kairos 65.9 frente a TP 93. La tabla hrTSS de TP no tiene datos de bici por encima del 85 % del umbral.
6. **Revalidación periódica** con un export nuevo de TP: prueba sin escribir, criterio fijado antes, solo las filas esperadas.

## Descartado

- Quitar a Copilot de los contributors de GitHub: exigiría reescribir el historial; decidido no hacerlo (23/10).
- Capturar el RPE de fuerza después de la sesión: el TSS de fuerza sale de la FC (modelo B) y ya cuadra con TP (≈ 1,00); decidido quitarlo (25/10).
- Sensor que impida nombrar la hipoglucemia a un atleta con DT2 y metformina: el LLM la menciona para tranquilizar, no como aviso, y no limita el entrenamiento; aceptado sin sensor (26/10).
- Menú con los 5 proveedores de prueba (NVIDIA, Gemini, Groq, Mistral, Cerebras): sustituido por el proveedor de IA que diga cada atleta (26/10).
