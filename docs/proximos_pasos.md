# Próximos pasos — Kairos Coach

Última revisión: 27/10/2026.

Solo lo que falta y lo descartado. Lo terminado se borra: el detalle está en `CHANGELOG.md` y en git. El estado vigente del cálculo de carga y las limitaciones aceptadas están en `CLAUDE.md`.

Bitácoras anteriores en git: hasta el 25/10, `git show 4578226:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10 (señal, lección y evidencia de cada cambio), `git show f2f5867:docs/proximos_pasos.md`.

## Dónde lo dejamos

Se reescribe al final de cada sesión.

**Sesión del 27/10/2026** (ordenador 2). Todo subido a GitHub, sin cambios a medias. 767 tests; batería 25/25.

- En el ordenador 1, al empezar: `git pull`. No hay dependencias ni variables nuevas; la clave tiene que estar ya en `PROVIDER_API_KEY` (el traslado desde `NVIDIA_API_KEY` se quitó).
- **Siguiente paso: la revisión del 27/10 (bloque E), en el orden acordado:** E8 (una sola fuente para la serie de carga), después el punto 3 con E9, luego E10, y E11 cuando se toquen esos ficheros.
- **Aparcado hasta ~09/11/2026** (decisión del usuario): el resto de pendientes se verifica con el uso diario de Kairos. Después, revisar juntos `agent.log` (`[SENSOR]`, `[TOOL] el LLM pide el grupo`, `[LLM] ◀`) para los puntos 1 y 2; los puntos 4 a 6 (TSS frente a TP), cuando haya sesiones nuevas con su TSS en TP. Los envoltorios (punto 7), cuando se toque `trainer_agent.py` por otro motivo.

## Pendiente

### A. Puesta en producción

1. **Uso real y revisión de las líneas `[SENSOR]` de `agent.log`.** Usar Kairos unos días con preguntas reales y revisar juntos cuánto salta cada sensor, falsos positivos nuevos y qué guía conviene reforzar (bucle de gobierno, `docs/Harness.md` §7). En concreto:
   - Si **S25** (diferencia de HRV), **S26** (etiqueta rTSS/hrTSS), **S27** ("mañana" con el día que no toca) y **S28** (día de la semana con su fecha) saltan con preguntas reales, y si con las guías G13 (calendario) y G14 (HRV calculada) del 26/10 bajan los saltos de S25, S27 y S28.

### B. Chat y LLM

2. **Metatooling, siguientes pasos** (grupos de tools hechos el 26/10: −22 % de tokens por llamada, mismas respuestas). Si hace falta más: (a) el prompt de sistema (≈ 7.000 tokens, la mayor parte de cada llamada) por partes según la pregunta, como los grupos; (b) fusionar tools parecidas en el servidor (`get_sleep_data`/`get_sleep_summary`, las tres de actividades) si el LLM las confunde. Vigilar en `agent.log` cuántas veces se pide un grupo (`[TOOL] el LLM pide el grupo`): más de 1 de cada 5 preguntas = reglas mal hechas.

3. **Tiempo máximo en las llamadas a Garmin fuera del bucle con tools.** En la batería del 27/10 la ruta determinista de "TSS de mis 20 últimas actividades de ciclismo" esperó 57 min a `get_activities_by_date` hasta un corte de red (`ConnectionResetError` 10054); el bucle con tools tiene 8 s por tool, pero las rutas deterministas, el arranque y el cálculo de carga llaman a `call_tool` sin límite. Ver dónde ponerlo (en `call_tool` o en el servidor, con la sesión HTTP de `garminconnect`) sin cortar llamadas largas legítimas.

### C. Cálculo de carga (TSS frente a TrainingPeaks)

Antes de tocar cualquier fórmula: hipótesis escrita, criterio de cierre fijado antes de ver resultados y prueba sin escribir (reglas de método de `CLAUDE.md`).

4. **Trail rápido (< 6:00/km).** Validar cuando haya más salidas con rTSS en TP. Hoy n = 1 (16/08: Kairos 128.0 frente a TP 112.4).
5. **Series cortas de running** (6x500, 6x1000, 4x400, 8x500): salen entre un 11 % y un 20 % por encima de TP; 23966766696 (13/08, 8x45" en cuesta) ×1.20. Plantear hipótesis antes de tocar el detector (p. ej. relación FC-velocidad con retraso fisiológico).
6. **Revalidación periódica** con un export nuevo de TP: prueba sin escribir, criterio fijado antes, solo las filas esperadas.

### D. Limpieza del proyecto

7. **Envoltorios de `trainer_agent.py`:** quedan 16 funciones de una línea que solo llaman a `load_metrics` (las otras 20 no las usaba nadie y se quitaron el 27/10). Los tests las usan para sustituir funciones: dejarlo para cuando se toque `trainer_agent.py` por otro motivo. Va con la decisión sobre la "API pública" de `load_metrics` (E11).

### E. Revisión del proyecto del 27/10 (el paso 1, hecho, está en el CHANGELOG)

8. **Una sola fuente para la serie de carga** (con tests y batería antes y después). El perfil guarda una copia de 120 días (`perfil.load_metrics.series`) que leen 11 sitios en lugar de `load_metrics_daily` (en `trainer_agent.py`: `_build_load_trend_markdown`, `_build_today_load_status_markdown`, `_build_current_week_tss_markdown` como respaldo, `_build_mcp_factual_query_markdown`, las tools `_kairos_load_trends` y `_kairos_correlate`, `_build_plan_load_context`, `_build_load_and_anchors_context_block` (contexto del LLM) y `chat`; en `main.py`: `_show_load_trend_cli` (`/carga`) y el arranque), la fuente canónica según `CLAUDE.md`. Además, si al arrancar no hay serie en la base de datos, `collect_startup_snapshot_48h` recalcula el TSS en vivo con `_compute_load_fatigue_metrics` (sin detalle de actividades: un segundo TSS) y, si falla, usa la copia del perfil. Propuesta: todo lee `load_metrics_daily`; fuera el recálculo en vivo y la copia (el perfil se queda con `formula_version` y los percentiles); una de las dos funciones que construyen el dict de carga (`_compute_load_fatigue_metrics` / `_build_load_fatigue_dict_from_series`).
9. **Con el punto 3:** `_hrv_trend` del servidor (`garmin_mcp/server.py`) descarta sin rastro los días que fallan; una tendencia con un 429 sale con huecos sin aviso.
10. **Dependencias:** `garminconnect`, `supabase` y `openai` sin tope superior (una instalación nueva puede traer una versión que rompa); `openai>=1.30` muy por debajo de la instalada (3.37); el README dice Python 3.11+ y la CI no prueba la 3.11.
11. **Cuando se toquen esos ficheros:** duplicados pequeños (`_moving_average` en `load_metrics` y `running_tss`; cuatro `_coerce` de velocidad casi iguales; parejas internas `_first_float`, `_key_candidates`, `_first`; "completar con `activity_tss` los días a cero", repetido en `trainer_agent.py`); la frase de tools de escritura de `prompts/system_prompt.md` (con batería); y decidir si se mantiene la "API pública" de `load_metrics` (unas 30 funciones de una línea "para reutilizar en otros proyectos" que no usa nadie).

## Descartado

- Quitar a Copilot de los contributors de GitHub: exigiría reescribir el historial; decidido no hacerlo (23/10).
- Capturar el RPE de fuerza después de la sesión: el TSS de fuerza sale de la FC (modelo B) y ya cuadra con TP (≈ 1,00); decidido quitarlo (25/10).
- Sensor que impida nombrar la hipoglucemia a un atleta con DT2 y metformina: el LLM la menciona para tranquilizar, no como aviso, y no limita el entrenamiento; aceptado sin sensor (26/10).
- Menú con los 5 proveedores de prueba (NVIDIA, Gemini, Groq, Mistral, Cerebras): sustituido por el proveedor de IA que diga cada atleta (26/10).
