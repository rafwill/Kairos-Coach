# TODO - Kairos Coach Roadmap

## Estado actual
- Arquitectura activa: DB-first multiusuario con Supabase obligatorio.
- RAG ligero operativo con base de conocimiento del atleta.
- Suite de tests: 360 tests (validados localmente a 2026-08-18). CI/CD con GitHub Actions activo.
- Validacion reciente de regresion focal (2026-08-21): `tests/test_trainer_agent.py` en verde (307 passed).
- Herramientas internas kairos_* operativas (tendencias, correlaciones, desglose deportivo).
- Contrato de salida unificado activo (prompt completo + prompt compacto + rutas deterministas clave).
- Essential Tools: 40 tools activas (2026-08-31); menú de selección eliminado del arranque.
- Modelo NVIDIA NIM activo: `nvidia/nemotron-3.5-lightning-30b-a3b` (sustituye llama-3.x EOL).
- Batería E2E punto 55: 2/10 confirmados (31/08), 8 pendientes de confirmar con nueva versión (01/09).

---

## ⏳ Pendiente

### Prioridad alta

#### 55) [REANUDACION-E2E] Validacion real de carga/fatiga (2026-08-15) — REALIZADO
- Objetivo: cerrar la validacion operacional real de TSS, ATL, CTL y TSB en ejecucion completa del agente.
- Checklist de ejecucion:
  - [REALIZADO 2026-08-15] Validacion automatizada de regresion: `python -m pytest -q` en verde (317 passed).
  - [REALIZADO 2026-08-15] Preparar entorno local y variables de Supabase/Garmin.
  - [REALIZADO 2026-08-15] Ejecutar arranque real del agente con usuario activo y sincronizacion MCP.
  - [REALIZADO 2026-08-15] Verificar reconstruccion/lectura de serie `load_metrics_daily` (resultado observado: 121 filas persistidas).
  - [REALIZADO 2026-08-15] Contrastar muestra de actividades y TSS por sesion contra referencias conocidas (FIT de muestra: `tools/fit_tss_probe.py`, delta +1.98 TSS entre metodos).
  - [REALIZADO 2026-08-15] Verificar coherencia dinamica de CTL (Estado físico)/ATL (Fatiga)/TSB (Forma) en el briefing proactivo de arranque (ATL=52.5, CTL=61.4, TSB=8.9, regla aplicada mostrada).
  - [REALIZADO 2026-08-15] Registrar evidencia (capturas/logs/resumen) en documentacion operativa: `docs/validation-load-fatigue-e2e-2026-08-15.md`.
- Criterio de cierre:
  - Cumplido: validacion end-to-end ejecutada con datos reales y evidencia guardada.

#### 37) Integración TrainingPeaks MCP (capa de escritura)
- Añadir `trainingpeaks-mcp` (https://github.com/JamsusMaximus/trainingpeaks-mcp) como servidor MCP secundario junto a `garmin_mcp`.
- Arquitectura resultante: `garmin_mcp` = capa de lectura. `trainingpeaks-mcp` = capa de escritura (calendario, sesiones estructuradas, notas, eventos).
- Autenticación via cookie del navegador (sin aprobación de API oficial TP). 78 tools disponibles.
- Funcionalidades prioritarias:
  - `tp_create_workout` con estructura de intervalos JSON auto-computando IF/TSS.
  - `tp_pair_workout` — empareja workout planificado con el ejecutado (modelo técnico para #31).
  - `tp_get_fitness` — CTL (Estado físico)/ATL (Fatiga)/TSB (Forma) nativo de TP para contrastar con modelo propio desde Garmin.
  - `tp_add_workout_comment` — el coach deja notas en sesiones del calendario.
  - `tp_get_atp` — Plan de Temporada Anual con TSS targets semanales por periodo.
- Requiere cuenta TrainingPeaks (no gratuita en todos los planes).
- Inspirado en `trainingpeaks-mcp` (111 stars, activo, MIT).

#### 1) Endurecimiento final post-implementación
- Al terminar implementacion, ejecutar bateria de seguridad: secretos, datos sensibles, configuraciones inseguras, dependencias y transporte.
- Aplicar remediaciones antes de declarar cierre del proyecto.

### Prioridad media

#### 18) Integración Strava
- Conectar Strava como fuente secundaria de actividades via OAuth2.
- Deduplicación cross-plataforma: misma fecha + deporte + duración/distancia con 5% tolerancia → Garmin como source of truth.
- Añadir campo `source_platform` a actividades en Supabase.
- Inspirado en la arquitectura de providers de FitMCP.

#### 35) [PROMPTING] Contextualización meteorológica en análisis de actividad
- El coach debe considerar las condiciones del día (temperatura, viento, humedad) como variable explicativa del rendimiento (±10–20% de impacto).
- Sección "Condiciones del día" en el análisis si el usuario las reporta, o pedirlas si el rendimiento parece inusualmente alto/bajo.
- Largo plazo: integración con API meteorológica por fecha y coordenadas GPS.
- Inspirado en el Feature 03 de FitMCP.

#### 39) Power PRs granulares por duración (ciclismo/triatlón)
- Power PRs por duración (5s, 1min, 5min, 20min, 60min, 90min) como estándar de rendimiento en ciclismo.
- Si el usuario tiene TP: usar `tp_get_peaks`. Si solo Garmin: usar `get_cycling_ftp` + sesiones clave.
- Inspirado en `tp_get_peaks` de `trainingpeaks-mcp`.

#### 2) Refactor por capas
- Separar claramente presentacion (CLI), negocio (coach) y datos (Garmin/LLM/storage).
- Reducir acoplamiento entre agent/main.py y agent/trainer_agent.py.

### Prioridad baja

#### 29) [PROMPTING] Regla de composición corporal como tendencia semanal
- Al analizar peso o composición corporal, no interpretar fluctuaciones diarias como señal.
- La unidad mínima de análisis es la tendencia semana a semana cruzada con tipo y volumen de entrenamiento.
- Aplicar también en system_prompt_compact.md.

#### 36) [PROMPTING] Métricas de natación y protocolo de triatlón
- Métricas de natación: SWOLF, cadencia de brazada, distancia por brazada.
- Protocolo de triatlón: análisis por disciplina + tiempos de transición T1/T2 + distribución de carga.
- Inspirado en el Feature 05 de FitMCP.

#### 40) Plan de Temporada Anual (ATP) — periodización a largo plazo
- Fase 1 (prompting): documentar periodos base/construcción/pico, TSS targets por periodo, A/B/C races.
- Fase 2 (datos): tabla en Supabase para el ATP del atleta que complementa a `training_plan`.
- Si el usuario tiene TP: `tp_get_atp` puede ser la fuente de verdad del ATP.
- Inspirado en `tp_get_atp` de `trainingpeaks-mcp`.

#### 5) Dashboard de métricas
- Explorar panel web opcional para tendencias (HRV, VO2max, sueño, estrés, carga).
- Evaluar Streamlit como primer candidato.

#### 6) Resumen diario automatizado
- Ejecutar resumen diario programado (Windows Task Scheduler).
- Salida por Telegram o email.

### Backlog abierto

#### 8) Gestión de tokens por proveedor LLM
- Evaluar tabla dedicada de tokens con campo de proveedor para soportar múltiples LLM de forma ordenada.

#### 9) Congelado del código MCP
- Evaluar vendorizar/congelar el código MCP en el repo para evitar roturas por cambios upstream.
- Analizar qué tools usamos y cuáles no para traernos al código solo las que necesitamos.
- Plan de ejecución propuesto (MCP propio basado en Essentials):
  - Fase 1 — Descubrimiento y baseline de uso real:
    - Inventariar tools MCP usadas en runtime, prompts y tests (frecuencia + criticidad).
    - Entregable: catálogo único de tools esenciales y contratos esperados (input/output).
  - Fase 2 — Estrategia de transición sin ruptura:
    - Adoptar backend dual con flag (`MCP_BACKEND=frozen|upstream`) para rollback inmediato.
    - Mantener upstream como fallback temporal mientras se estabiliza el MCP propio.
  - Fase 3 — Capa de adaptación estable en Kairos:
    - Implementar adapter interno para desacoplar `trainer_agent`/`mcp_client` de payloads volátiles.
    - Versionar contratos de tools esenciales para bloquear cambios no compatibles.
  - Fase 4 — Construcción del MCP propio (subset Essentials):
    - Implementar solo las tools críticas usadas por Kairos (no clonar todo el servidor externo).
    - Priorizar rutas deterministas y métricas de arranque (actividad, carga, HRV, sueño, body battery, PRs).
  - Fase 5 — Pruebas y hardening:
    - Añadir tests de contratos MCP (schema + campos mínimos) y regresión funcional E2E.
    - Añadir chequeo CI de drift entre catálogo esperado y tools realmente expuestas.
  - Fase 6 — Corte controlado de dependencia externa:
    - Promover `frozen` como backend por defecto al cumplir criterios de estabilidad.
    - Mantener `upstream` solo para contingencia durante una ventana de observación.
- Criterios de cierre del punto 9:
  - Contratos de tools esenciales versionados y testeados en CI.
  - Rutas críticas operativas sin dependencia funcional del MCP de terceros.
  - Rollback operativo validado mediante flag de backend.
  - Documentación técnica y runbook de mantenimiento del MCP propio publicados.

---

## ✅ Completado

### Hitos técnicos base
- Refactor a persistencia multiusuario en Supabase.
- Login/registro de usuario de aplicacion y onboarding inicial.
- Onboarding enriquecido: creacion/persistencia de athlete_knowledge inicial con perfil + datos MCP.
- Estado proactivo de arranque (48h): body battery, HRV, sueno y entrenamientos recientes.
- Deteccion de cambios de perfil Garmin al iniciar y reporte contextual.
- Prompt y prompt compacto alineados al nuevo enfoque de coach.
- Limpieza de memoria JSON legacy en runtime.
- Documentacion principal alineada con DB-first.
- Analisis profundo de actividad por fecha: pre-fetch enriquecido con zonas FC reales (Garmin, cascada 3 estrategias), body battery (compact handler), sueno (fecha exacta + fallback), HRV (compact handler + mapeo correcto de campos) y carga calculados en Python. El LLM solo interpreta. 6 secciones de salida estructuradas.
- Arquitectura de dos capas documentada en system prompt: capa datos vs capa coaching.
- Correccion de busqueda de actividades por fecha (campo start_time snake_case del MCP).
- Auto-login con contrasena cifrada Fernet: al arrancar, si el usuario existe, accede directamente sin pedir password. Flujo de recuperacion si la contrasena de Garmin Connect cambia.
- Politica de herramientas MCP implementada para runtime: guia de enrutado por intencion y referencia desde el system prompt para reducir tokens y latencia.
- Compatibilidad MCP actualizada para cambios de contrato: get_body_battery y get_body_composition con start_date/end_date.
- Compatibilidad MCP para PRs: endpoint vigente get_personal_record (singular) con alias defensivo del plural.
- Consulta de records personales mejorada: respuesta directa en tabla de running y follow-up contextual de distancias/marcas.
- Consulta de records por deporte mejorada: separacion running/ciclismo sin mezclar disciplinas.
- Categorias de records personales traducidas al espanol en la salida al usuario.
- Separacion explicita objetivo (goals) vs plan activo (training_plan) en el perfil de usuario.
- Estado proactivo condicionado por training_plan: sin plan muestra aviso; con plan propone adaptar sesion diaria.
- Ruta determinista para estado del plan en chat: responden desde training_plan real sin depender del LLM.
- Prompting reforzado: checklist MCP minimo por intencion, formato de fecha DD/MM/AAAA, respuestas maximos/minimos con valor + actividad + fecha.
- Cuantificacion de carga y fatiga (TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)) con tau ajustados por deporte y percentiles individualizados.
- Series temporales de carga/fatiga persistidas (hasta 120 dias) en Supabase por atleta.

### Ítems numerados cerrados

#### 10) MCP solo consulta para coaching
- Runtime endurecido: filtrado de tools de escritura en initialize y bloqueo en loop de tool-calls.
- Cobertura de tests para garantizar que no se ejecutan tools de escritura en modo read-only.

#### 11) Planes de entrenamiento
- Tablas dedicadas en Supabase (training_plan, training_plan_session, training_plan_version).
- Generacion estructurada en runtime, validacion previa a persistencia, versionado por edicion y resumen de cambios.
- Comandos de gestion: /plan crear, /plan listar, /plan activar, /plan ver.

#### 12) Naming del producto
- Nombre final definido: Kairos Coach. Aplicado en toda la base de codigo, prompts, README y documentacion.

#### 13) Cuantificación de carga y fatiga (TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma))
- Modelo EWMA con tau ajustados por deporte y percentiles individualizados por atleta.
- Integrado en snapshot proactivo de arranque con resumen operativo y regla aplicada de actuacion.
- Series temporales persistidas en Supabase.

#### 14) Pasos de ejecución en README.md
- Instrucciones de instalacion, configuracion, uso basico y ejecucion de tests documentadas.

#### 15) Motor de análisis histórico de métricas
- `kairos_load_trends`: serie temporal de TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma) con granularidad diaria y semanal.
- `kairos_correlate`: correlación de Pearson entre dos métricas de carga/fatiga (N, r, interpretación).
- Herramientas internas Python puro, operando sobre load_metrics.series en Supabase.

#### 17) Herramienta de consulta sobre datos históricos
- Implementado via `kairos_load_trends` sobre `load_metrics.series` en Supabase.

#### 19) Desglose semanal y por deporte
- `kairos_weekly_sport_breakdown`: consulta Garmin MCP, agrupa por deporte y devuelve sesiones, horas y km.

#### 20) Renombrar proyecto en GitHub — REALIZADO
- Renombrado del repo de `garmin-ai-coach` a `kairos-coach` en GitHub y actualización de la URL de clonación en README.

#### 21) GitHub Actions CI
- `.github/workflows/tests.yml` ejecuta la suite de tests en cada push y pull request, sin credenciales reales.

#### 22) Makefile + scripts de setup automatizado (2026-08-18) — REALIZADO
- `Makefile` añadido con targets: `setup`, `setup-win`, `login`, `test`, `serve`, `lint`.
- `setup.ps1` (Windows) y `setup.sh` (Unix) añadidos para crear `.venv`, instalar dependencias y generar `.env` desde `.env.example`.
- Ambos scripts generan `ENCRYPTION_KEY` automáticamente si no existe en `.env`.

#### 45) [VALIDACION] Batería sintética multi-atleta para carga/fatiga (2026-07-30) — REALIZADO
- Añadidos escenarios ficticios por deporte (running, trail y triatlón) para validar comportamiento específico CTL (Estado físico)/ATL (Fatiga)/TSB (Forma).
- Cobertura de reglas por perfil: tau por deporte, suelo de TSB y detección de fatiga/sobrecarga reciente.
- Ejecutada validación integral de `tests/test_trainer_agent.py` en verde tras integración.

#### 46) [DOCS] Persistencia en BBDD documentada en README (2026-07-30) — REALIZADO
- Añadida sección explícita "Qué guarda Kairos en la BBDD (Supabase)" en README.
- Documentadas tablas persistidas, tipos de dato almacenado y alcance real de persistencia.
- Aclaración añadida: no se persiste una tabla propia con actividades Garmin crudas.

#### 47) [FACTUAL-ROUTE] Consulta semanal de TSS por ruta determinista (2026-07-31) — REALIZADO
- Se añadió una ruta determinista para preguntas factuales de TSS semanal (sin generación libre del LLM).
- El rango temporal se fija a semana natural (lunes hasta hoy).
- Las actividades fuente (tipo/nombre) se devuelven desde Garmin con datos reales para evitar alucinaciones.

#### 48) [ANALISIS-HISTORICO] Bloque final por recencia en análisis de actividad (2026-07-31) — REALIZADO
- Actividad reciente: mantiene sección "Recuperación y próximas sesiones" con orientación de corto plazo.
- Actividad histórica: cambia a "Aprendizajes para futuras sesiones similares".
- Se elimina en histórico la pauta operativa de horizonte inmediato (por ejemplo, "mañana" o "en 2-3 días").

#### 49) [PLAN-ROUTING] Confirmación "sí" tras oferta de plan y fallback determinista (2026-07-31) — REALIZADO
- Se corrige el enrutado conversacional para que un follow-up corto ("sí", "ok", "vale") tras "te preparo un plan activo" active la ruta estructurada de planificación.
- Se evita caída silenciosa al flujo LLM cuando falla la ruta estructurada: ahora responde por fallback determinista basado en objetivo.
- Se añaden tests de regresión para afirmativo contextual con y sin oferta previa de plan.

#### 50) [PLAN-ENGINE] Periodización progresiva multi-semana + validadores anti-plantilla (2026-07-31) — REALIZADO
- Generador estructurado reescrito para producir semanas completas con fases base/build/peak/taper y progresión de carga con descarga/taper explícitos.
- Sesiones ahora incluyen variación determinista de calidad, mezcla multideporte según preferencias y ajuste de intensidad/volumen por lesión.
- Validación endurecida para rechazar planes planos o repetitivos (estructura semanal, separación de sesiones clave y checks de variedad).
- Añadidos tests de regresión para progresión semanal, selección por week_index y rutas trail/multisport.

#### 51) [PLAN-CONSTRAINTS] Planificador general por restricciones del atleta (2026-07-31) — REALIZADO
- El plan estructurado ahora distribuye el microciclo por disponibilidad real declarada (días entrenables/no entrenables, límites de minutos por día, descanso mínimo y tope de calidad semanal).
- Se elimina dependencia de plantilla fija por día de la semana: el motor asigna sesiones por reglas deterministas según restricciones y objetivo.
- La modulación de carga por salud pasa a modelo general por impacto funcional (`none/low/moderate/high`), sin lógica hardcodeada por patología concreta.
- El ajuste diario del plan fuerza descanso/reducción cuando el día está bloqueado o excede límites diarios.
- El validador incorpora checks de cumplimiento de disponibilidad y caps diarios, con tests de regresión en `tests/test_trainer_agent.py`.

#### 52) [GYM-TSS] Recalibración de fuerza/gimnasio alineada con TP (2026-08-14) — REALIZADO
- Nueva jerarquía para fuerza: `hrTSS por zonas (cobertura >=35%) -> TSS por IF de tipología -> TSS por RPE/minuto -> fallback FC`.
- Ajuste de IF por tipología con calibración práctica: movilidad (0.50), mantenimiento (0.55), fuerza general/hipertrofia (0.56), neuromuscular (0.57), fuerza máxima/potencia (0.80).
- Corrección de falsos positivos por matching textual en nombres de sesión (caso "Pesadoira").
- Verificación cruzada frente a sesiones TP clave y auditoría de 120 días de sesiones de fuerza para validar coherencia global.

#### 53) [WALKING-TSS] Calibración específica de caminata/senderismo (2026-08-14) — REALIZADO
- Se añade estimador dedicado para `walking/hiking` por bandas de TSS/h: suave (15-25), ritmo vivo (25-40), carga/cuestas (40-60+).
- Integración con zonas FC cuando hay cobertura suficiente mediante blend con la banda para reducir outliers.
- Se mantiene intacta la lógica de `trail_running` (hrTSS por zonas + calibración trail existente), sin cambios funcionales en ese flujo.
- Recalculo completo de 120 días y validación focalizada de tests de `walking/hiking` y regresión de fuerza.

#### 54) [LOAD-RESET] Reset one-shot de carga/fatiga para recálculo limpio (2026-08-14) — REALIZADO
- Procedimiento operativo aplicado: borrado de `load_metrics_daily` y limpieza de `load_metrics` en `user_profile` del usuario activo.
- Resultado esperado documentado: en el siguiente arranque, reconstrucción completa de 120 días con la fórmula vigente.
- Se documenta además la regla de `formula_version`: si cambia, Kairos fuerza recálculo completo automáticamente.

#### 56) [PARAM-EFFECTIVE-DATE] Congelación histórica por cambio de parámetros (2026-08-15) — REALIZADO
- Regla aplicada: cuando cambian parámetros que afectan al cálculo (umbral running, FTP ciclismo, perfil de FC), no se recalcula histórico anterior a la fecha de cambio.
- A partir de la fecha efectiva del cambio, los entrenamientos nuevos se calculan con los parámetros actualizados.
- Se añade sello global `performance_params_updated_at` para trazar el último cambio operativo de parámetros.
- Nuevo comando operativo: `/perfil fc <reposo> <max>` para actualizar FC de reposo/máxima con validación de rangos y fecha efectiva.
- Ajuste anti-falsos cambios: refrescar FTP sin cambio real de valor ya no actualiza la fecha efectiva.

#### 57) [SESSION-CLOSE-LATENCY] Checkpoint incremental y cierre rápido (2026-08-15) — REALIZADO
- Se elimina la dependencia de un resumen final con LLM en el momento de salir para evitar cierres lentos por timeout/retry del proveedor.
- Se añade checkpoint incremental de resumen tras cada respuesta de Kairos (resumen ligero local, sin red) y persistencia por día mediante upsert.
- Resultado operativo: salida de sesión inmediata o casi inmediata, manteniendo memoria de sesión en BBDD.

#### 58) [HARNESS-HOOKS] HookManager + ToolRouter determinista opcional + pruebas (2026-08-15) — REALIZADO
- Runtime con hooks explícitos en `TrainerAgent.chat()`: `before_message`, `after_message`, `before_tool_call`, `after_tool_call`, `on_error`.
- Router determinista opcional para intenciones críticas vía `KAIROS_DETERMINISTIC_ROUTER`.
- Cobertura nueva en `tests/test_hooks_router.py` y validación completa de regresión en verde.
- Documentación de harness actualizada y reubicada en `docs/Harness.md`.

#### 59) [ROUTING-LLM] Entrenamiento de mañana por LLM + rescate contextual (2026-08-15) — REALIZADO
- Se revierte la ruta determinista de "entrenamiento de mañana": la propuesta vuelve al flujo LLM principal.
- Si el LLM responde con "falta información" en una consulta de propuesta para mañana/planificación, Kairos ejecuta un segundo intento LLM con snapshot proactivo (48h) para devolver una sesión útil.
- Se mantiene determinismo solo en intenciones factuales/estructurales críticas (TSS semanal, FC umbral, estado de plan, readiness, etc.).
- Cobertura de regresión añadida para evitar reintroducir respuesta genérica vacía en esta consulta.

#### 60) [TRAIL-TSS-FAST] Excepción trail rápido + transparencia de salida (2026-08-18) — REALIZADO
- Nueva regla en `trail_running`: si el ritmo final/efectivo es `< 6:00/km`, se usa `hrTSS` bruto por zonas en lugar del factor de calibración 0.72.
- Para `trail_running` no rápido se mantiene la calibración existente (`hrTSS zonas * 0.72`).
- Mejora de UX en análisis de actividad: cuando hay zonas FC en trail, Kairos muestra ambos valores (`hrTSS bruto zonas` y `hrTSS Kairos aplicado`) para trazabilidad.
- Tests de regresión añadidos para la regla `< 6:00/km`, frontera `== 6:00/km` y visualización explícita de ambos valores.

#### 61) [CONSISTENCY-ROUTES] Factual/semanal + formato unificado (2026-08-18) — REALIZADO
- Consultas de semana por fecha explícita (`semana del ...`) resueltas con ventana histórica ISO (lunes-domingo) en lugar de semana actual implícita.
- Parsing robusto de fechas cortas `dd/mm/yy` (ej. `17/08/26`) para consultas factuales.
- Fallback factual/semanal de TSS desde actividades Garmin cuando `load_metrics_daily` aún no refleja el cierre diario.
- Refresco de carga endurecido para actividad reciente (hoy/ayer/anteayer) y paginación sin corte prematuro por orden no estricto.
- Corrección de recálculo por cambio de fórmula para no quedar bloqueado por clamp histórico de fecha efectiva.
- Corrección de crash en compactación de tools (`UnboundLocalError` sobre `act_type_raw`).
- Plantilla única obligatoria activada en prompts y normalizada en rutas deterministas:
  - `## 🧭 Resumen`
  - `## 📊 Métricas clave`
  - `## ✅ Recomendación`
  - `## 🎯 Próximo paso`

#### 62) [DOCS-ONBOARDING-WIN] README "for dummies" + troubleshooting Windows (2026-08-18) — REALIZADO
- Reescritura de instalación/configuración en README con flujo desde cero por sistema operativo (Windows y Unix/macOS).
- Se documenta ruta sin `make` para Windows (`setup.ps1` + `.venv\\Scripts\\python.exe -m agent.main`) y uso opcional de Makefile.
- Se añade sección de errores frecuentes en Windows con solución directa para:
  - `make` no reconocido,
  - bloqueo por `ExecutionPolicy`,
  - `python` no reconocido,
  - `uvx` no reconocido.

#### 63) [HYBRID-COACHING] Cierre de arquitectura mixta + fallback transparente (2026-08-21) — REALIZADO
- Rutas factuales clave en modo mixto: bloque determinista de datos + fase LLM de interpretación cuando la consulta pide recomendación (`week_tss`, `week_activities`, `load_trend`, `today_load_status`, `daily_readiness`, `activity_details`, `mcp_factual`).
- Si el LLM falla/timeout o devuelve vacío en la fase de coaching, Kairos informa explícitamente la indisponibilidad y mantiene únicamente el bloque factual como fuente de verdad (sin recomendación determinista de relleno).
- Cobertura de regresión ampliada para rutas mixtas y escenario de timeout en `today_load_status`.
- Validación local: `tests/test_trainer_agent.py` en verde (307 passed).

#### 4) Logging de producción (2026-08-15) — REALIZADO
- Configuración de logging por entorno en runtime: `KAIROS_LOG_LEVEL`, `KAIROS_LOG_FILE`, `KAIROS_LOG_STDOUT`, `KAIROS_DEBUG_CONSOLE`.
- Trazas de debug en consola interactiva ahora son opcionales y quedan desactivadas por defecto en producción.
- Se mantiene salida operativa para usuario final y se centraliza telemetría técnica en logger con formato uniforme.

#### 27) [PROMPTING] Umbral de spike semanal >20% (2026-08-15) — REALIZADO
- Sensor activo en runtime: alerta cuando la carga semanal actual supera en >20% a la semana anterior, incluso sin cruce de umbral TSB.
- Integrado en `load_fatigue.flags.weekly_spike_alert`, en `weekly.*` (current/previous/delta/threshold) y en recomendación operativa.
- El motor determinista diario considera `weekly_spike_alert` como señal de riesgo adicional para modular la sesión del día.
- Regla añadida también al prompt compacto.

#### 38) [PROMPTING] Formato de workout estructurado estándar TP (2026-08-15) — REALIZADO
- Se define contrato estructurado por sesión (`structured_workout`) en prompts completo y compacto con schema `kairos-workout-v1`.
- El motor de planes genera doble salida (humana + JSON estructurado) y persistencia backward-compatible con fallback legacy.
- El validador comprueba orden de bloques, coherencia duración/steps, targets/intensityClass y reglas específicas de sesiones de calidad.
- Ajuste diario y feedback post-sesión ahora usan la estructura: trazabilidad del ajuste aplicado y análisis plan-vs-ejecutado por bloques.

#### 23) Principio "relaciones > valores aislados"
- Regla en system_prompt y compact: nunca reportar valor aislado cuando se puede cruzar con otra metrica.

#### 24) Detección de anomalías biométricas
- Flags en system_prompt: FC reposo elevada sin carga, sueno malo >=2 noches, HRV >15% bajo media 7d, body battery <30 >=2 dias.

#### 25) Tendencia siempre junto al valor puntual
- Regla: para HRV, body battery, sueno, FC en reposo y VO2max, reportar valor hoy + media 7d + direccion.

#### 26) Transparencia de datos: calidad y tamaño de muestra
- Regla: declarar N y calidad del dato en toda afirmacion sobre tendencias.

#### 28) Framework de Race Readiness
- Protocolo en system_prompt: monitorizar progresion del largo, desnivel semanal y volumen vs. demanda de la carrera objetivo.

#### 31) Protocolo plan activo ↔ datos del día
- Antes del entreno: cruzar readiness/TSB con sesion planificada -> ejecutar/reducir/posponer/swapear.
- Despues del entreno: comparar ejecutado vs planificado -> analisis de desviacion.

#### 33) Historial profundo como base de generación de planes
- Regla: al generar plan, analizar ultimas 8-12 semanas de actividades reales para calibrar nivel de partida real.

#### 34) Protocolo de revisión post-sesión como entrenador
- Nota estructurada corta al compartir actividad sin pedir analisis profundo: que fue bien / que se desvio / un ajuste.

#### 41) Output del análisis de actividad — mejoras UX (2026-07-25)
- Zonas FC sin barras gráficas (solo texto: nombre · rango bpm · %).
- Tipo de deporte como primer bullet del resumen ejecutivo.
- Secciones Plan de recuperación + Recomendaciones fusionadas en una sola: Recuperación y próximas sesiones.
- Plan de entrenamiento activo inyectado como contexto (no restricción): si los indicadores piden descanso, se recomienda aunque haya sesión planificada.
- HRV con datos reales (fix campo lastNightAvg + compact handler + media 7d + estado).
- Estado pre-carrera con valores numéricos reales + interpretación coaching (body battery, sueño con fases, HRV).
- Instrucciones de sección reescritas en prosa para evitar que llama-70b las copie como bullets de output.

#### 42) [RUNNING-TSS] Clasificación explícita rodaje/fartlek/series + persistencia de inferencia (2026-07-30) — REALIZADO
- Clasificación determinista de running no-trail por señales combinadas (speed ratio, laps, RPE, TE label y keywords del texto).
- Nuevas clases operativas: `rodaje`, `fartlek`, `series` y `calidad` como fallback conservador.
- Cálculo TSS condicionado por tipo y confianza (`high`/`medium`/`low`) para modular uplift.
- Persistencia de trazas recientes de inferencia en perfil (`running_session_inference`) para auditoría/calibración.
- Versión de fórmula incrementada a v7 para forzar recálculo automático de la serie histórica.

#### 43) [TRAIL-TSS] Priorización por zonas FC + calibración final (2026-07-30) — REALIZADO
- Trail/hike/walk prioriza hrTSS por tiempo en zonas FC reales cuando existe payload de zonas.
- Calibración empírica v9 para alinear con TP (`_TRAIL_ZONES_HRTSS_CALIBRATION = 0.72`) aplicada solo a trail running.
- Corrección de ultras REALIZADO: el factor se aplica sobre hrTSS sin cap y se preservan valores >500 cuando corresponde.
- Test de regresión añadido para blindar la calibración por zonas en trail.

#### 44) [CYCLING-TSS] Priorización potencia+FTP con fallback a zonas FC (2026-07-30) — REALIZADO
- Ciclismo prioriza TSS por potencia usando FTP del usuario (`normalized/avg/average power + FTP`).
- Si falta potencia o falta FTP, el fallback principal es `hrTSS` por zonas FC del usuario.
- Si tampoco hay zonas FC, fallback final a `hrTSS` por FC media para no romper continuidad de la serie.
- En recálculo histórico, se consulta `get_activity_hr_in_timezones` para ciclismo cuando no aplica potencia+FTP.

---

## Notas de mantenimiento
- Mantener TODO sincronizado con decisiones de arquitectura reales.
- Evitar registrar aqui tareas ya completadas salvo resumen corto de hitos.
- Regla de equipo: documentar siempre los cambios antes de hacer commit.

