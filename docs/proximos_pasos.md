# Próximos pasos — Kairos Coach

Última revisión: 26/10/2026.

Solo lo que falta y lo descartado. Lo terminado se borra: el detalle está en `CHANGELOG.md` y en git. El estado vigente del cálculo de carga y las limitaciones aceptadas están en `CLAUDE.md`.

Bitácoras anteriores en git: hasta el 25/10, `git show 4578226:docs/proximos_pasos.md`; biblioteca de experiencia del 23 al 26/10 (señal, lección y evidencia de cada cambio), `git show f2f5867:docs/proximos_pasos.md`.

## Dónde lo dejamos

Se reescribe al final de cada sesión.

**Sesión del 26/10/2026** (ordenador 2). Sin cambios a medias. 778 tests; batería 25/25.

- Hecho hoy: patologías y lesiones separadas con protocolo de diabetes según medicación; proveedor de IA del atleta (tabla `ia_provider`, clave en `PROVIDER_API_KEY`); `.env.example` limpio; metatooling por grupos de tools, siempre activo (sin variable); el "cuelgue" de las 14:15 era el portátil suspendido, y lo que de verdad fallaba (token de Supabase caducado al despertar, que cerraba Kairos) está arreglado; tests de las ramas de error del inicio de sesión (`agent/main.py` del 90 % al 94 %); memoria entre sesiones comprobada en este ordenador (resumen del día e historial en `session_context`, sin caracteres rotos); la batería ya no la lee ni la escribe (`KAIROS_SESSION_MEMORY=false`) y la memoria del atleta se vació (toda era de baterías); S28 nuevo (día de la semana con su fecha), S11 sin falso positivo con "Supraumbral" y batería pintada a 400 columnas (tablas sin partir para el juez); comando `/perfil umbral fc <lpm>` para la FC umbral tras un test; la bici sin potencia del 07/08 no era un fallo (el 93 de TP estaba metido a mano; su hrTSS es 62, Kairos 65.9).
- Supabase ya tiene la tabla `ia_provider` (RLS comprobado) y el perfil del usuario está migrado (patologías y lesiones separadas, medicación de la diabetes, proveedor de IA `nvidia`).
- Al empezar en casa: `git pull`, `pip install -r requirements.txt` (`google-genai` ya no hace falta; se puede desinstalar) y entrar una vez en Kairos. No preguntará nada: la clave `NVIDIA_API_KEY` del `.env` pasa sola a `PROVIDER_API_KEY`. Después, el repaso de los sensores con las baterías guardadas allí y el caso de S10 en bici (punto 1).
- **Siguiente paso: la limpieza del proyecto (Pendiente D, puntos 6 a 9), siguiendo su recomendación**: primero la documentación y lo menor (D1 y D4), después el código que sobra (D2) y los duplicados (D3).
- **Aparcado hasta ~09/11/2026** (decisión del usuario): todos los pendientes se verifican con el uso diario de Kairos en las próximas semanas. Después, revisar juntos `agent.log` (`[SENSOR]`, `[TOOL] el LLM pide el grupo`, `[LLM] ◀`) para los puntos 1 y 2; los puntos 3 a 5 (TSS frente a TP), cuando haya sesiones nuevas con su TSS en TP. Lo único que no espera al uso es el repaso en casa del punto 1 (891 respuestas y el caso de S10 en bici) y la limpieza del proyecto (D, puntos 6 a 9), que se puede hacer cuando se quiera.

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
