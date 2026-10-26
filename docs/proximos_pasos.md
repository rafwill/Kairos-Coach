# Próximos pasos — Kairos Coach

Última revisión: 26/10/2026.

Solo lo que falta y lo descartado. Lo terminado se borra: el detalle está en `CHANGELOG.md` y en git. El estado vigente del cálculo de carga y las limitaciones aceptadas están en `CLAUDE.md`.

Bitácoras anteriores en git: hasta el 25/10, `git show 4578226:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10 (señal, lección y evidencia de cada cambio), `git show f2f5867:docs/proximos_pasos.md`.

## Dónde lo dejamos

Se reescribe al final de cada sesión.

**Sesión del 26/10/2026, noche** (ordenador 1). Todo subido a GitHub, sin cambios a medias. 784 tests; batería 25/25.

- Hecho: traídos los 22 commits del ordenador 2 (`pip install -r requirements.txt` y la clave ya migrada a `PROVIDER_API_KEY`); repaso de sensores con las 941 respuestas de casa, con tres falsos positivos corregidos (S10 con "tirada" y con el deporte detrás del rango; S28 con "(lunes a domingo)") y el caso de S10 en bici del 24/10 cerrado (ya no se reproduce); guías nuevas G13 (calendario para el LLM) y G14 (diferencia de HRV ya calculada), probadas en vivo sin avisos en las respuestas finales.
- En el ordenador 2, al empezar: `git pull`. No hay dependencias ni variables nuevas.
- **Siguiente paso: la limpieza del proyecto (Pendiente D, puntos 6 a 9)**: primero la documentación y lo menor (D1 y D4), después el código que sobra (D2) y los duplicados (D3).
- **Aparcado hasta ~09/11/2026** (decisión del usuario): el resto de pendientes se verifica con el uso diario de Kairos. Después, revisar juntos `agent.log` (`[SENSOR]`, `[TOOL] el LLM pide el grupo`, `[LLM] ◀`) para los puntos 1 y 2; los puntos 3 a 5 (TSS frente a TP), cuando haya sesiones nuevas con su TSS en TP.

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

### D. Limpieza del proyecto (revisión del 26/10)

Revisión completa de código y documentación: incoherencias, código que sobra y duplicados. Nada cambiado aún. Cada bloque, su commit.

**Recomendación (por aquí se empieza la próxima sesión):**

1. **Arreglar ya, porque es sencillo y seguro:** la documentación (6, D1) y lo menor (9, D4, salvo el traslado de la clave). Cambiar también lo que dicen el menú y `salir`, para que no prometan cerrar la sesión (6c).
2. **Quitar con tests y una batería antes y después:** el código que sobra (7, D2), unas 300–400 líneas. Borrar la caché `mcp_adapter_cache` del perfil es un cambio de datos en Supabase: prueba previa, aplicación y lectura de vuelta, como la memoria.
3. **Unificar en `load_metrics.py`:** los duplicados 8a y 8b. Las zonas (8a) primero, comprobando qué versión es la correcta.
4. **Dejar para otro momento:** los envoltorios (8c), porque tocan mucho para ganar poco.
5. **Al volver a casa:** el traslado de la clave (último punto de D4). Basta con haber entrado una vez en Kairos allí para que la clave quede trasladada.

6. **D1. Documentación que dice algo falso** (arreglar ya)

   | # | Dónde | Problema |
   | --- | --- | --- |
   | a | README, "Qué hace" | "Protocolo de diabetes tipo 1 con insulina": hoy es DT1 o DT2 según la medicación, con lesiones aparte. |
   | b | README, "Estructura" | Faltan `agent/diabetes.py`, `ia_provider.py`, `tool_groups.py`, `tools/check_rls.py` y `prompts/diabetes/`; `supabase/` solo nombra `schema.sql` (faltan `rls.sql` y su README). |
   | c | README y menú (`agent/main.py`), `salir` | Dicen "Cerrar sesión", pero salir deja la sesión guardada (`storage.sign_out` existe y nadie la llama). Corregir el texto ("Salir de Kairos; la sesión queda guardada") o añadir un cierre de sesión real. |
   | d | README, `docs/Harness.md` (S18) | Recuentos de tests viejos ("623 passed (23/10)", "(585)"; hoy 778). Quitar el número. |
   | e | README, tabla de documentos | Dice que los pendientes están en `CLAUDE.md`; están aquí. |
   | f | `CLAUDE.md`, "## Pendiente" | Repite los puntos 3, 4 y 5 de este fichero. Dejar aquí la lista y en `CLAUDE.md` solo el recordatorio de `/perfil umbral fc`. |
   | g | `docs/arquitectura.md` | La primera línea habla de "hooks" (se quitaron el 23/10); "Cada mensaje del chat" no menciona el metatooling (`agent/tool_groups.py`). |
   | h | README, Requisitos; CI | Recomienda Python 3.12 y la CI prueba solo 3.12; los dos ordenadores usan 3.14. Añadir 3.14 a la matriz de la CI. |

7. **D2. Código que sobra** (≈ 300–400 líneas; con tests y batería antes y después)

   | # | Qué | Por qué sobra |
   | --- | --- | --- |
   | a | Modo de contingencia del MCP: `KAIROS_MCP_ENABLE_FALLBACK`, caché en el perfil, ruta rápida local (`agent/mcp/client.py`, `contracts.py`) | Apagado desde siempre; la caché no caduca y por eso no se activa (`docs/arquitectura.md`). El perfil aún guarda `mcp_adapter_cache` sin que nadie la lea: borrarla es un cambio de datos en Supabase (prueba antes, como la memoria). |
   | b | Bloqueo de tools de escritura: `MCP_READ_ONLY`, `_is_write_mcp_tool`, `_build_mcp_read_only_block_message` | El servidor propio solo tiene las 42 tools `get_*`. |
   | c | Interruptores que nadie activa: `KAIROS_DETERMINISTIC_ROUTER`, `KAIROS_TRAIL_LOW_IF_DEBUG`, `KAIROS_WELLNESS_REFRESH_ON_STARTUP` | Mismo caso que `KAIROS_TOOL_GROUPS` (quitado el 26/10). |
   | d | `running_session_inference` (`compute_and_persist_load_metrics`) | Se escribe en el perfil en cada cálculo de carga y nadie lo lee. |
   | e | Compatibilidad con datos que ya no existen | Planes con `hr_pct` (no hay ningún plan guardado), `_strip_previous_reply` (memoria vaciada el 26/10), inserción de sesiones de plan sin `structured_workout` (`schema.sql` ya la crea). |
   | f | Sin uso | `load_metrics._extract_splits_list`, `contracts.get_contract_registry`, `sensors.has_insulin_diabetes` (sustituida por `agent/diabetes.py`), `_TRAIL_FAST_PACE_RAW_ZONES_SEC_PER_KM`; `ALL_ESSENTIAL_TOOLS` solo en tests. |

8. **D3. Código duplicado entre `trainer_agent.py` y `load_metrics.py`**

   | # | Qué | Riesgo |
   | --- | --- | --- |
   | a | `_parse_hr_zones_list` y `_find_hr_zones_in_json`, con versiones **distintas** en cada módulo | Alto: la misma entrada puede dar zonas distintas. Primero comprobar cuál es la correcta (`CLAUDE.md`: `_parse_hr_zones_list` no lee el formato real de Garmin). |
   | b | `_is_trail_activity` y otras 6 de clasificación de deporte, y `_percentile`, copiadas idénticas | Medio: un cambio en una copia no llega a la otra. Dejar las de `load_metrics.py`. |
   | c | 37 envoltorios de una línea en `trainer_agent.py` que solo llaman a `load_metrics` | Bajo (estético); los tests los usan para sustituir funciones. Dejar para otro momento. |

9. **D4. Menores**
   - `.gitignore` solo ignora `agent.log`: al rotar, `agent.log.1`… saldrían como ficheros nuevos. Cambiar a `agent.log*` (y `garmin_mcp.log*`).
   - Encabezado viejo `# ─── /modelo` en `tests/test_main_cli.py`.
   - Cuando el ordenador 1 haya entrado una vez en Kairos (la clave ya trasladada), quitar el traslado de `NVIDIA_API_KEY` a `PROVIDER_API_KEY` (`main._migrate_legacy_key`, `ia_provider.legacy_key_env_var`, y el respaldo en `trainer_agent._build_llm_client`).

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
