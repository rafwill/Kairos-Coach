# Changelog

Todos los cambios relevantes de Kairos Coach se registran en este archivo.

## 2026-08-21

### Changed
- Cierre de arquitectura mixta en rutas deterministas de consulta factual:
	- `week_tss`, `week_activities`, `load_trend`, `today_load_status`, `daily_readiness`, `activity_details` y `mcp_factual` mantienen bloque factual determinista y aplican capa de coaching por LLM cuando la consulta pide recomendación.
- Ajuste de intención factual para separar mejor:
	- consultas mixtas (dato + recomendación),
	- consultas de recomendación pura (sin base factual), que no deben caer en factual determinista.

### Fixed
- Transparencia en fallback híbrido: si la fase LLM de coaching falla (timeout/error o respuesta vacía), Kairos ya no vuelve en silencio al bloque factual; ahora informa explícitamente que no fue posible generar la recomendación de coaching en ese intento.
- Alineación de `today_load_status` con la lógica objetivo: sin recomendación determinista "de relleno" cuando se solicita interpretación; la recomendación pertenece a la fase LLM.

### Tests
- Nuevas pruebas de regresión para capa híbrida en rutas faltantes:
	- `week_activities`, `load_trend`, `today_load_status`, `mcp_factual`, `activity_details`.
- Nueva prueba de resiliencia para `today_load_status` cuando el LLM de coaching entra en timeout, verificando mensaje explícito de indisponibilidad.
- Validación local actualizada en verde:
	- `tests/test_trainer_agent.py`: 307 passed.

## 2026-08-20

### Added
- Plan operativo del punto 9 (MCP propio / congelado Essentials) documentado en `TODO.md`:
	- fases de ejecucion (descubrimiento, backend dual, adapter, implementacion subset, pruebas, corte controlado),
	- criterios de cierre (contratos versionados, rutas criticas sin dependencia funcional de terceros, rollback validado, runbook).
- Nueva prueba de regresion para ventana de verificacion no fija en arranque de carga:
	- valida que el refresco se ancla al ultimo dia con actividad registrada y no a una ventana corta fija.

### Changed
- Reglas de deteccion de actividad nueva en arranque (`compute_and_persist_load_metrics`):
	- se elimina la logica de ventana minima fija de 2-3 dias,
	- la verificacion MCP ahora se ancla al ultimo dia con actividad en DB (o al ultimo dia de serie como fallback),
	- se recorre desde la ancla hasta hoy y, si hay desfase, se recalcula desde el primer dia afectado.
- Actualizacion integral de `README.md` al estado real del proyecto:
	- estructura de repo vigente,
	- rutas deterministas incluyendo `activity_details`,
	- flujo de carga actualizado (full_recalc / incremental_refresh / up_to_date),
	- ajustes de cifras y comportamiento operativo documentado.

### Fixed
- Ajustes de cobertura y expectativas en tests de refresco incremental para reflejar el nuevo patron de consulta MCP por rango anclado.

### Tests
- Validacion focal en verde para regresiones de carga incremental y refresco por actividad nueva:
	- casos de `up_to_date`,
	- refresco por actividad nueva del dia,
	- refresco con actividad faltante en dias previos,
	- caso de brecha larga (ancla historica),
	- bypass correcto de clamp por fecha efectiva en refrescos recientes.

## 2026-08-18

### Added
- Regla específica para trail rápido: si el ritmo final/efectivo es menor de `6:00/km`, Kairos usa `hrTSS` bruto por zonas (sin factor 0.72).
- En el análisis de actividad trail con zonas FC disponibles, se muestran ambos valores de forma explícita: `hrTSS bruto zonas` y `hrTSS Kairos aplicado`.
- Nuevo menú unificado de comandos en CLI (`/menu`) con categorías: ver datos, editar perfil, gestionar planes y sistema.
- Ruta determinista de opciones/configuración reforzada para preguntas tipo "qué opciones puedo cambiar" y para el alias `/menu`.
- Plantilla única obligatoria de salida en prompts (completo y compacto):
	- `## 🧭 Resumen`
	- `## 📊 Métricas clave`
	- `## ✅ Recomendación`
	- `## 🎯 Próximo paso`
- Ruta determinista semanal de actividades por fecha (`week_activities`) con semana natural ISO (lunes-domingo).
- Mensajes de transparencia en arranque cuando hay recálculo por cambio de fórmula (`formula_version`) o refresco incremental de carga.
- Setup automatizado de proyecto:
	- `setup.ps1` (Windows) y `setup.sh` (Unix),
	- `Makefile` con targets `setup`, `setup-win`, `login`, `serve`, `test`, `lint`,
	- generación automática de `.env` y `ENCRYPTION_KEY` si faltan.
- Onboarding "for dummies" en `README.md` con flujo desde cero por SO, comandos de arranque directos sin `make` y resolución guiada de errores comunes en Windows.

### Changed
- `trail_running` mantiene calibración por defecto (`hrTSS zonas * 0.72`) solo cuando no aplica la nueva regla de trail rápido.
- Separación explícita de dominios en FC umbral: la ruta determinista de LTHR ya no consulta herramientas de umbral de lactato.
- Extracción de FC umbral robustecida para payloads Garmin en `camelCase` (ej. `lactateThresholdHeartRate`).
- Endurecimiento de rutas factuales/semanales:
	- consultas por "semana del <fecha>" resueltas por ventana histórica explícita,
	- parsing de fechas `dd/mm/yy` (ej. `17/08/26`),
	- fallback de TSS diario/semanal desde actividades Garmin cuando `load_metrics_daily` aún no cerró el día,
	- estimación de TSS para sesiones sin `trainingLoad` (incluye fuerza/caminata según reglas activas),
	- paginación de actividades sin corte prematuro por orden no estricto.
- Política de recálculo histórico: en migración de fórmula se ignora el clamp de fecha efectiva para forzar recálculo completo coherente.
- Normalización de formato en rutas deterministas principales para alinear salida con la plantilla única.

### Fixed
- Corrección de crash en compactación de resultados de tools (`UnboundLocalError` sobre `act_type_raw`).
- Corrección de side-effect en ruta de FC umbral que podía tocar marcador global de fecha efectiva de parámetros.

### Tests
- Nuevos tests de regresión para validar:
	- uso de `hrTSS` bruto por zonas en trail rápido (`< 6:00/km`),
	- comportamiento de frontera en `6:00/km` (mantiene calibración),
	- presencia de `hrTSS bruto zonas` y `hrTSS Kairos aplicado` en el bloque de análisis.
	- detección robusta de intención de opciones/configuración (incluye `/menu`),
	- ruta de FC umbral sin llamadas a herramientas de lactato,
	- lectura de LTHR desde claves `camelCase` de Garmin.
	- ventana semanal histórica explícita, parser `dd/mm/yy`, fallback factual TSS desde actividades,
	- reemplazo de TSS diario en cero con carga de actividad,
	- formato unificado en rutas deterministas.
- Estado de validación local actualizado: `python -m pytest -q` en verde (`360 passed`).

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
