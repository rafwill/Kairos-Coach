# Próximos pasos — Kairos Coach

Última revisión: 26/10/2026.

Solo lo que falta y lo descartado. Lo terminado se borra: el detalle está en `CHANGELOG.md` y en git. El estado vigente del cálculo de carga y las limitaciones aceptadas están en `CLAUDE.md`.

Bitácoras anteriores en git: hasta el 25/10, `git show 4578226:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10 (señal, lección y evidencia de cada cambio), `git show f2f5867:docs/proximos_pasos.md`.

## Dónde lo dejamos

Se reescribe al final de cada sesión.

**Sesión del 26/10/2026** (ordenador 2). Sin cambios a medias. 766 tests; batería 25/25.

- Hecho hoy: patologías y lesiones separadas con protocolo de diabetes según medicación; proveedor de IA del atleta (tabla `ia_provider`, clave en `PROVIDER_API_KEY`); `.env.example` limpio; metatooling por grupos de tools, siempre activo (sin variable); el "cuelgue" de las 14:15 era el portátil suspendido, y lo que de verdad fallaba (token de Supabase caducado al despertar, que cerraba Kairos) está arreglado.
- Supabase ya tiene la tabla `ia_provider` (RLS comprobado) y el perfil del usuario está migrado (patologías y lesiones separadas, medicación de la diabetes, proveedor de IA `nvidia`).
- Al empezar en casa: `git pull`, `pip install -r requirements.txt` (`google-genai` ya no hace falta; se puede desinstalar) y entrar una vez en Kairos. No preguntará nada: la clave `NVIDIA_API_KEY` del `.env` pasa sola a `PROVIDER_API_KEY`. Después, el repaso de S10 con las baterías guardadas allí (punto 1).
- Siguiente paso propuesto: los sensores pendientes del punto 1 (mejor en casa, con las 891 respuestas guardadas).

## Pendiente

### A. Puesta en producción

1. **Uso real y revisión de las líneas `[SENSOR]` de `agent.log`.** Usar Kairos unos días con preguntas reales y revisar juntos cuánto salta cada sensor, falsos positivos nuevos y qué guía conviene reforzar (bucle de gobierno, `docs/Harness.md` §7). En concreto:
   - **S10 con la marca de progresivo detrás del rango** (arreglado el 26/10 en el ordenador 2): repasar en casa las baterías guardadas (891 respuestas) con el sensor nuevo y confirmar que solo desaparecen falsos positivos de este tipo.
   - **Posible falso positivo de S10** (24/10, 09:18): marcó como fuera de zona 105–124 y 159–179 lpm, que son la Z1 y la Z5 de bici; parece que tomó una línea de ciclismo como de carrera (`_range_sport`). Reproducirlo con la respuesta completa antes de tocar el sensor.
   - **S10 en el juez con tablas partidas**: el juez lee la transcripción pintada a 110 columnas y una celda larga se parte en varias líneas (batería 20261026_100523, pregunta 9: "progresivo" quedó en la línea anterior a "Z2→Z3 (135→157 lpm)"; en vivo no saltó). Decidir si `run_bateria` guarda también el texto original y el juez lo usa, o si se acepta como ruido conocido.
   - **Día de la semana que no cuadra con su fecha** (26/10): el LLM escribió "el domingo (20/10)" y el 20/10/2026 fue lunes. S27 solo comprueba fechas junto a hoy/ayer/mañana. Posible sensor nuevo, con el criterio de siempre: 0 falsos positivos en las baterías guardadas y cada aviso revisado a mano (mejor en casa, con las 891 respuestas).
   - **S11 con "Supraumbral"**: "Z5 Supraumbral (≥ 169 lpm) o ritmo 3:30–3:40 min/km" dio S11 (ritmo umbral 3:30 ≠ 4:00); la palabra "umbral" dentro de "Supraumbral" no es el ritmo umbral. Falso positivo del sensor.
   - Si **S25** (diferencia de HRV), **S26** (etiqueta rTSS/hrTSS) y **S27** ("mañana" con el día que no toca) saltan con preguntas reales.
2. **Memoria entre sesiones en el ordenador 2**: comprobar que funciona allí (lo demás de ese ordenador quedó hecho el 26/10).

### B. Chat y LLM

3. **Metatooling, siguientes pasos** (grupos de tools hechos el 26/10: −22 % de tokens por llamada, mismas respuestas). Si hace falta más: (a) el prompt de sistema (≈ 7.000 tokens, la mayor parte de cada llamada) por partes según la pregunta, como los grupos; (b) fusionar tools parecidas en el servidor (`get_sleep_data`/`get_sleep_summary`, las tres de actividades) si el LLM las confunde. Vigilar en `agent.log` cuántas veces se pide un grupo (`[TOOL] el LLM pide el grupo`): más de 1 de cada 5 preguntas = reglas mal hechas.
4. **Cobertura restante de `agent/main.py`.** Quedan sin tests las ramas de error del inicio de sesión y el ajuste de la consola de Windows. Prioridad baja.

### C. Cálculo de carga (TSS frente a TrainingPeaks)

Antes de tocar cualquier fórmula: hipótesis escrita, criterio de cierre fijado antes de ver resultados y prueba sin escribir (reglas de método de `CLAUDE.md`).

5. **Trail rápido (< 6:00/km).** Validar cuando haya más salidas con rTSS en TP. Hoy n = 1 (16/08: Kairos 128.0 frente a TP 112.4).
6. **Series cortas de running** (6x500, 6x1000, 4x400, 8x500): salen entre un 11 % y un 20 % por encima de TP; 23966766696 (13/08, 8x45" en cuesta) ×1.20. Plantear hipótesis antes de tocar el detector (p. ej. relación FC-velocidad con retraso fisiológico).
7. **Bici sin potencia de más de 2 h.** 07/08: Kairos 65.9 frente a TP 93. La tabla hrTSS de TP no tiene datos de bici por encima del 85 % del umbral.
8. **Revalidación periódica** con un export nuevo de TP: prueba sin escribir, criterio fijado antes, solo las filas esperadas.
9. **Mantenimiento de umbrales.** Si cambia el umbral de FC en TP, añadir ese mismo día la entrada con fecha a `hr_threshold_tp_history` (no hay comando). El ritmo umbral se registra con `/perfil umbral`.

## Descartado

- Quitar a Copilot de los contributors de GitHub: exigiría reescribir el historial; decidido no hacerlo (23/10).
- Capturar el RPE de fuerza después de la sesión: el TSS de fuerza sale de la FC (modelo B) y ya cuadra con TP (≈ 1,00); decidido quitarlo (25/10).
- Sensor que impida nombrar la hipoglucemia a un atleta con DT2 y metformina: el LLM la menciona para tranquilizar, no como aviso, y no limita el entrenamiento; aceptado sin sensor (26/10).
- Menú con los 5 proveedores de prueba (NVIDIA, Gemini, Groq, Mistral, Cerebras): sustituido por el proveedor de IA que diga cada atleta (26/10).
