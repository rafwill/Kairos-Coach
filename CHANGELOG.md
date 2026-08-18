# Changelog

Todos los cambios relevantes de Kairos Coach se registran en este archivo.

## 2026-08-18

### Added
- Regla específica para trail rápido: si el ritmo final/efectivo es menor de `6:00/km`, Kairos usa `hrTSS` bruto por zonas (sin factor 0.72).
- En el análisis de actividad trail con zonas FC disponibles, se muestran ambos valores de forma explícita: `hrTSS bruto zonas` y `hrTSS Kairos aplicado`.
- Nuevo menú unificado de comandos en CLI (`/menu`) con categorías: ver datos, editar perfil, gestionar planes y sistema.
- Ruta determinista de opciones/configuración reforzada para preguntas tipo "qué opciones puedo cambiar" y para el alias `/menu`.

### Changed
- `trail_running` mantiene calibración por defecto (`hrTSS zonas * 0.72`) solo cuando no aplica la nueva regla de trail rápido.
- Separación explícita de dominios en FC umbral: la ruta determinista de LTHR ya no consulta herramientas de umbral de lactato.
- Extracción de FC umbral robustecida para payloads Garmin en `camelCase` (ej. `lactateThresholdHeartRate`).

### Tests
- Nuevos tests de regresión para validar:
	- uso de `hrTSS` bruto por zonas en trail rápido (`< 6:00/km`),
	- comportamiento de frontera en `6:00/km` (mantiene calibración),
	- presencia de `hrTSS bruto zonas` y `hrTSS Kairos aplicado` en el bloque de análisis.
	- detección robusta de intención de opciones/configuración (incluye `/menu`),
	- ruta de FC umbral sin llamadas a herramientas de lactato,
	- lectura de LTHR desde claves `camelCase` de Garmin.

## 2026-08-15

### Added
- Ruta determinista para consultas de ritmo umbral actual desde perfil persistido.
- Comando de perfil para FC: `/perfil fc <reposo> <max>`.
- Política de fecha efectiva para parámetros de carga (umbral, FTP, FC).
- Checkpoint incremental de resumen de sesión por día (upsert), guardado tras cada respuesta del coach.
- Contrato estructurado de sesión (`structured_workout`) reforzado en prompts (incluye ejemplos válido/inválido).
- Análisis plan-vs-ejecutado por bloques del `structured_workout` con resumen de completados/parciales/omitidos.
- HookManager explícito en runtime (`before_message`, `after_message`, `before_tool_call`, `after_tool_call`, `on_error`).
- ToolRouter determinista opcional para intenciones críticas (`KAIROS_DETERMINISTIC_ROUTER`).
- Sensor activo de spike semanal >20% (semana actual vs. semana previa) integrado en carga/fatiga y ajuste diario determinista.
- Nuevos tests dedicados de hooks/router en `tests/test_hooks_router.py`.
- Evidencia operativa de validacion E2E real de carga/fatiga en `docs/validation-load-fatigue-e2e-2026-08-15.md`.
- Configuración de logging por entorno: `KAIROS_LOG_LEVEL`, `KAIROS_LOG_FILE`, `KAIROS_LOG_STDOUT`, `KAIROS_DEBUG_CONSOLE`.
- Ruta determinista para consultas de FC umbral (LTHR) con lectura directa de perfil y fallback MCP rápido.
- Verificación de frescura al arranque para carga/fatiga: si hay actividad nueva hoy, se refresca el día en curso aunque la serie ya estuviera al día.

### Changed
- Cálculo de carga incremental: preserva histórico previo al último cambio de parámetros y aplica nuevos valores solo desde la fecha efectiva.
- Refresco de FTP: no actualiza fecha efectiva cuando el valor no cambia.
- Cierre de sesión optimizado: se elimina el resumen final dependiente de LLM en salida y se usa checkpoint ligero local para evitar bloqueos por red/timeouts.
- Ajuste diario del plan: ahora muta el JSON estructurado (intensityClass/target/rango, duración y reps) y guarda trazabilidad del ajuste.
- Documento de harness reubicado de `Harness.md` a `docs/Harness.md` y actualizado al estado real de hooks/router/sensores.
- `agent/main.py` ahora centraliza logging de producción, usa logger por módulo y evita trazas debug en consola salvo opt-in.
- Consulta semanal de TSS: prioriza serie canónica DB-first (`load_metrics_daily`) y completa días faltantes con `trainingLoad` de actividades Garmin cuando aplica.
- Diferenciación explícita de intents: `FC umbral` ya no se confunde con `ritmo umbral`.
- Consulta "entrenamiento para mañana" vuelve a flujo LLM; si la primera respuesta es genérica de falta de datos, se ejecuta rescate LLM con snapshot proactivo para proponer sesión útil.
- Storage endurecido ante tabla de planes ausente en Supabase (`training_plan`): rutas de lectura degradan con fallback seguro en lugar de abortar arranque.

### Tests
- Nuevas pruebas para fecha efectiva de parámetros.
- Nuevas pruebas para comando de FC y política de refresco de FTP.
- Nuevas pruebas para persistencia diaria de resumen y checkpoint local de sesión.
- Nuevas pruebas para mutación de `structured_workout` en ajuste diario y feedback por bloques.
- Nuevas pruebas para detectar `weekly_spike_alert` y su efecto en el motor determinista diario.
- Nuevas pruebas de routing/intención para `week_tss`, `hr_threshold` y separación de `running_threshold`.
- Nuevas pruebas de resiliencia startup (actividad nueva del día) y fallback de tabla faltante en storage.
- Nuevas pruebas de rescate LLM para propuestas de entrenamiento cuando la respuesta inicial es genérica.
- Suite completa validada en verde.

### Notes
- Commits clave del día: 55af659 (base), b9ea941 (cierre rápido de sesión), 367d770 (cierre completo punto 38), 18bfd72 (hooks/router + tests), 4b74718 (harness docs move/update), 8420dec (sensor spike semanal >20%).
- Estado de validación local al cierre: 317 tests passed.
- Validacion E2E real #55 cerrada con contraste de muestra FIT (avg_method=55.71 vs series_method=57.69, delta +1.98 TSS) y serie real de 120 dias documentada.
- Estado de validación actualizado: 325 tests passed.
