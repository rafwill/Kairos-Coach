# Próximos pasos — Kairos Coach

Última revisión: 27/10/2026.

Solo lo que falta y lo descartado. Lo terminado se borra: el detalle está en `CHANGELOG.md` y en git. El estado vigente del cálculo de carga y las limitaciones aceptadas están en `CLAUDE.md`.

Bitácoras anteriores en git: hasta el 25/10, `git show 4578226:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10 (señal, lección y evidencia de cada cambio), `git show f2f5867:docs/proximos_pasos.md`.

## Dónde lo dejamos

Se reescribe al final de cada sesión.

**Sesión del 27/10/2026** (ordenador 2). Todo subido a GitHub, sin cambios a medias. 767 tests; batería 25/25.

- Hecho: la limpieza del proyecto (D1, D2, D3a, D3b y D4): documentación corregida; fuera el modo de contingencia del MCP, el bloqueo de tools de escritura, los interruptores sin uso y la compatibilidad con datos viejos (unas 440 líneas en `agent/`); perfil de Supabase sin `mcp_adapter_cache` (3,2 MB) ni `running_session_inference`; una sola copia de las funciones duplicadas en `load_metrics.py`, con `_parse_hr_zones_list` leyendo ya el formato real de Garmin (sin recálculo: ninguna actividad guardada pasa por esos respaldos). Detalle en CHANGELOG.
- En el ordenador 1, al empezar: `git pull`. No hay dependencias ni variables nuevas; la clave tiene que estar ya en `PROVIDER_API_KEY` (el traslado desde `NVIDIA_API_KEY` se quitó).
- **Siguiente paso: el tiempo máximo de las llamadas a Garmin (punto 3)**, que hoy colgó la batería 57 min.
- **Aparcado hasta ~09/11/2026** (decisión del usuario): el resto de pendientes se verifica con el uso diario de Kairos. Después, revisar juntos `agent.log` (`[SENSOR]`, `[TOOL] el LLM pide el grupo`, `[LLM] ◀`) para los puntos 1 y 2; los puntos 4 a 6 (TSS frente a TP), cuando haya sesiones nuevas con su TSS en TP. Los envoltorios (7c), cuando se toque `trainer_agent.py` por otro motivo.

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

### D. Limpieza del proyecto (revisión del 26/10)

Revisión completa de código y documentación del 26/10: incoherencias, código que sobra y duplicados. Hechos el 27/10: D1 (documentación), D4 (menores), D2 (código que sobra, con su limpieza del perfil en Supabase) y D3a/D3b (una sola copia de las funciones duplicadas). Cada bloque, su commit.

**Recomendación (por aquí se sigue):**

1. **Dejar para otro momento:** los envoltorios (7c), porque tocan mucho para ganar poco.

7. **D3. Código duplicado entre `trainer_agent.py` y `load_metrics.py`**

   | # | Qué | Riesgo |
   | --- | --- | --- |
   | c | 36 envoltorios de una línea en `trainer_agent.py` que solo llaman a `load_metrics` | Bajo (estético); los tests los usan para sustituir funciones. Dejar para otro momento. |

## Descartado

- Quitar a Copilot de los contributors de GitHub: exigiría reescribir el historial; decidido no hacerlo (23/10).
- Capturar el RPE de fuerza después de la sesión: el TSS de fuerza sale de la FC (modelo B) y ya cuadra con TP (≈ 1,00); decidido quitarlo (25/10).
- Sensor que impida nombrar la hipoglucemia a un atleta con DT2 y metformina: el LLM la menciona para tranquilizar, no como aviso, y no limita el entrenamiento; aceptado sin sensor (26/10).
- Menú con los 5 proveedores de prueba (NVIDIA, Gemini, Groq, Mistral, Cerebras): sustituido por el proveedor de IA que diga cada atleta (26/10).

### 26/10/2026 — Repaso de sensores con las baterías de casa

- Señal: S10 marcaba fuera de zona rangos correctos de carrera (115→145) por heredar "bici" de la línea de encima; S28 leía "(lunes a domingo)" como el día de una fecha.
- Lección: el deporte de un rango sale de su frase, y la frase incluye lo que va pegado detrás dentro del paréntesis; el vocabulario de carrera tiene que incluir la jerga ("tirada"). Repasar también las respuestas libres del LLM, no solo la capa de coaching: en vivo los sensores pasan por las dos.
- Evidencia: 941 respuestas (166 libres del LLM); 3 falsos positivos corregidos, 0 nuevos, todos los avisos reales se mantienen. Detalle en CHANGELOG.

### 26/10/2026 — Guías contra los errores de días y de HRV

- Señal: los avisos reales que quedaban en las baterías de casa eran de días (S27 ×3, S28) y de HRV (S25 ×4), todos del mismo tipo: el LLM calculaba fechas o porcentajes a partir de datos sueltos.
- Lección: un error que se repite se ataca con una guía (darle el dato ya calculado), no solo con un sensor (`docs/Harness.md` §7). El sensor se queda como red: con el calendario, S28 aún saltó una vez y el reintento lo corrigió.
- Evidencia: G13 (calendario) y G14 (diferencia de HRV calculada); 4 preguntas de la exploración y batería 25/25 sin avisos en las respuestas finales.
