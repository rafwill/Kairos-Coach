# TODO - Kairos Coach Roadmap

## Estado actual
- Arquitectura activa: DB-first multiusuario con Supabase obligatorio.
- RAG ligero operativo con base de conocimiento del atleta.
- Suite de tests: 401 tests en verde (validado localmente a 2026-09-02). CI/CD con GitHub Actions activo.
- Validacion reciente de regresion focal (2026-09-02): `tests/test_trainer_agent.py` en verde (326 passed).
- Herramientas internas kairos_* operativas (tendencias, correlaciones, desglose deportivo).
- Contrato de salida unificado activo (prompt completo + prompt compacto + rutas deterministas clave).
- Essential Tools: 43 tools activas en runtime (2026-09-03).
- Modelo NVIDIA NIM activo: `nvidia/nemotron-3.5-lightning-30b-a3b` (sustituye llama-3.x EOL).
- Batería E2E punto 55: validación E2E re-ejecutada y cerrada (11/11 consultas verificadas) con correcciones aplicadas en runtime (2026-09-01).
- Bloque cálculo hrTSS trail (sin ponderado + regla trail rápido) validado contra TP: REALIZADO (2026-09-01).
- Refactor de load metrics completado (Fase 1 + Fase 2, deduplicación + API pública) con validación total en verde (2026-09-02).

---

## ⏳ Pendiente

### Prioridad alta

#### 9) Congelado del código MCP — REALIZADO (2026-09-03)
- Objetivo (10/10): eliminar dependencia funcional del MCP de terceros en rutas críticas de Kairos, manteniendo rollback inmediato y reduciendo latencia de consultas.
- Alcance:
  - Catálogo Essentials objetivo: 43 tools identificadas en runtime actual.
  - MVP inicial (Ola 1): tools críticas para rutas deterministas y métricas de arranque.
  - Cobertura completa (Ola 2): completar las 43 tools del catálogo Essentials.
- Definiciones operativas:
  - Tool esencial: aparece en runtime, prompts o tests y su contrato impacta respuesta al usuario.
  - Tool crítica: esencial con impacto directo en rutas deterministas o en snapshot proactivo de arranque.
  - Contrato versionado: schema de entrada/salida con campos mínimos obligatorios y compatibilidad backward.
- Métricas de éxito:
  - Latencia p95 en 4 consultas sentinela: mejora >= 30% frente a backend upstream.
  - Estabilidad: 0 regresiones en suite `pytest -q` y smoke E2E sentinela en verde.
  - Confiabilidad de contratos: 100% de tools del catálogo con tests de contrato en CI.
  - Operabilidad: rollback `MCP_BACKEND=frozen|upstream` validado en < 5 min.
- Plan de ejecución (MCP propio basado en Essentials):
  - Fase 1 — Descubrimiento y baseline de uso real.
    - Inventariar tools MCP usadas en runtime, prompts y tests (frecuencia + criticidad).
    - Entregable: catálogo único (43) con clasificación esencial/crítica + contratos esperados.
    - Criterio de salida: catálogo aprobado y trazable a código/tests.
  - Fase 2 — Estrategia de transición sin ruptura.
    - Activar backend dual con flag `MCP_BACKEND=frozen|upstream`.
    - Mantener upstream como fallback temporal durante estabilización.
    - Criterio de salida: conmutación ida/vuelta validada en local y smoke.
  - Fase 3 — Capa de adaptación estable en Kairos.
    - Implementar adapter interno para desacoplar `trainer_agent`/`mcp_client` de payloads volátiles.
    - Versionar contratos por tool y añadir validación defensiva.
    - Criterio de salida: rutas críticas consumen solo contratos versionados del adapter.
  - Fase 4 — Construcción del MCP propio.
    - Ola 1: implementar primero tools críticas (rutas deterministas + startup metrics + PRs).
    - Ola 2: completar resto de tools hasta cubrir las 43 Essentials.
    - Criterio de salida: paridad funcional del catálogo objetivo.
  - Fase 5 — Pruebas y hardening.
    - Añadir tests de contratos MCP (schema + campos mínimos) y regresión funcional E2E.
    - Añadir chequeo CI de drift entre catálogo esperado y tools realmente expuestas.
    - Criterio de salida: CI en verde con drift-check obligatorio.
  - Fase 6 — Corte controlado de dependencia externa.
    - Promover `frozen` como backend por defecto al cumplir métricas.
    - Mantener `upstream` como contingencia por ventana de observación definida (14 días).
    - Criterio de salida: sin incidencias P1/P2 en ventana de observación.
- Criterios de cierre del punto 9:
  - 43/43 tools Essentials implementadas o explicitamente descartadas con justificación.
  - Contratos de tools versionados y testeados en CI.
  - Rutas críticas operativas sin dependencia funcional del MCP de terceros.
  - Rollback operativo validado mediante flag de backend.
  - Documentación técnica y runbook de mantenimiento del MCP propio publicados.

- Estado de avance (Fase 1 + Fase 2 en progreso — 2026-09-03):
  - Inventario preliminar completado en código/prompts/tests.
  - Baseline confirmado: 43 Essentials objetivo = 40 tools Garmin (`GARMIN_ESSENTIAL_TOOLS`) + 3 tools internas (`kairos_load_trends`, `kairos_correlate`, `kairos_weekly_sport_breakdown`).
  - Set crítico inicial identificado para Ola 1 (MVP):
    - `get_user_profile`
    - `get_activities`
    - `get_activities_by_date`
    - `get_activity`
    - `get_activity_hr_in_timezones`
    - `get_body_battery`
    - `get_hrv_data`
    - `get_sleep_summary`
    - `get_training_load_trend`
    - `get_training_readiness` / `get_morning_training_readiness`
    - `get_personal_record`
    - `kairos_load_trends`
  - Siguiente entregable inmediato:
    - [REALIZADO 2026-09-03] Catálogo único versionado (43) con clasificación `crítica/esencial` por tool y contrato mínimo de entrada/salida.
  - Fase 2 aplicada en código (backend dual sin ruptura):
    - Selector `MCP_BACKEND=frozen|upstream` implementado en `agent/mcp_client.py`.
    - Fallback automático `frozen -> upstream` controlado por `MCP_BACKEND_FALLBACK_UPSTREAM` (default: true).
    - Soporte de comando local congelado por `KAIROS_MCP_FROZEN_COMMAND` (o binario `garmin-mcp-frozen`).
    - Señal de backend efectivo expuesta vía `KAIROS_MCP_BACKEND_EFFECTIVE` y mostrada al arrancar en `agent/main.py`.
  - Validación Fase 2:
    - Suite de regresión focal en verde: `pytest -q tests/test_main.py tests/test_trainer_agent.py` (383 passed).
    - Cobertura nueva de conmutación backend en CI: `tests/test_mcp_client.py`.
      - Selector `MCP_BACKEND` (`frozen|upstream|inválido`).
      - Fallback `frozen -> upstream` cuando `MCP_BACKEND_FALLBACK_UPSTREAM=true`.
      - Comportamiento fail-fast cuando fallback está desactivado.
    - Validación conjunta actualizada: `pytest -q tests/test_mcp_client.py tests/test_main.py tests/test_trainer_agent.py` (391 passed).
  - Fase 3 iniciada (adapter estable en Kairos):
    - Capa adapter extraída a módulo dedicado `agent/mcp_adapter.py`.
    - Normalización de invocaciones (`get_personal_records` -> `get_personal_record`).
    - Normalización de argumentos para contratos de fecha (`get_body_battery`/`get_body_composition`: `date` -> `start_date/end_date`).
    - Guardas de contrato mínimo en frontera adapter->MCP (error explícito con versión `mcp-adapter-v1`).
    - Catálogo Essentials centralizado en `agent/mcp_client.py`:
      - `GARMIN_ESSENTIAL_TOOLS` (40)
      - `KAIROS_INTERNAL_ESSENTIAL_TOOLS` (3)
      - `ALL_ESSENTIAL_TOOLS` (43)
    - Cobertura nueva en `tests/test_mcp_client.py` para alias, normalización y fail-fast de contrato.
    - Drift-check base de catálogo añadido en CI (conteo/uniqueness/must-have críticos).
  - Validación tras inicio Fase 3:
    - `pytest -q tests/test_mcp_client.py` (15 passed).
    - `pytest -q tests/test_main.py tests/test_trainer_agent.py tests/test_mcp_client.py` (398 passed).
  - Fase 4 (Ola 1) iniciada — bootstrap frozen local:
    - Launcher local añadido: `tools/garmin-mcp-frozen.cmd` (Windows) y `tools/garmin-mcp-frozen.sh` (Unix).
    - Resolución de backend frozen prioriza wrapper local del repo en `agent/mcp_client.py`.
    - Hardening de fallback: si frozen falla durante `session.initialize()`, cambia automáticamente a upstream cuando `MCP_BACKEND_FALLBACK_UPSTREAM=true`.
    - Validación runtime no interactiva: `configured=frozen`, `effective=frozen`, `tools=40`.
    - Fast-path local añadido para `get_training_load_trend` en backend `frozen` (usa `load_metrics_daily` vía adapter, con fallback transparente a MCP).
  - Validación tras bootstrap Fase 4:
    - `pytest -q tests/test_mcp_client.py` (16 passed).
    - `pytest -q tests/test_main.py tests/test_trainer_agent.py tests/test_mcp_client.py` (399 passed).
    - Benchmark no interactivo de latencia (`upstream` vs `frozen`) en tools críticas (3 iteraciones por tool):
      - upstream: `avg=848.3 ms`, `p95=1660.3 ms`.
      - frozen: `avg=839.7 ms`, `p95=1623.3 ms`.
      - mejora p95 observada: `+2.2%` (aún por debajo del objetivo >=30%).
    - Conclusión provisional Fase 4:
      - Paridad funcional validada en Ola 1 (`effective=frozen`, `tools=40`).
      - Objetivo de latencia no cumplido todavía; pendiente optimización estructural del backend frozen (más allá de wrapper local).
  - Validación tras optimización local Fase 4 (2026-09-03):
    - Benchmark no interactivo con usuario activo (`rafwill1@hotmail.com`), 3 iteraciones por tool crítica:
      - upstream: `avg=869.7 ms`, `p95=1602.5 ms`.
      - frozen: `avg=602.1 ms`, `p95=792.0 ms`.
      - mejora p95 observada: `+50.6%` (objetivo >=30% cumplido).
      - impacto principal: `get_training_load_trend` (`p95 1653.3 ms -> 203.2 ms`).
    - Regresión conjunta tras el cambio: `pytest -q tests/test_main.py tests/test_trainer_agent.py tests/test_mcp_client.py` (402 passed).
    - Smoke E2E runtime real en `MCP_BACKEND=frozen` (usuario `rafwill1@hotmail.com`, NVIDIA NIM opción 4):
      - `¿Cuál es mi tendencia de carga de las últimas 4 semanas?` -> OK (tabla semanal ATL/CTL/TSB coherente).
      - `¿Cuánto TSS hice esta semana?` -> OK (total, semanal comparativa, desglose diario y por tipo).
      - `¿Puedo entrenar fuerte mañana o necesito recuperar?` -> OK (ruta readiness determinista con recomendación concreta).
      - `¿Cuáles son mis récords personales running?` -> OK (tabla factual 1K/1mi/5K/10K/MM/Maratón/larga).
      - Señal operativa validada en runtime: `MCP backend efectivo: frozen`, `43 herramientas disponibles`.
  - Estado de criterio Fase 4 (latencia):
    - Cumplido para baseline de herramientas críticas medidas.
    - Batería sentinela E2E interactiva completada en verde; cierre formal de Fase 4 habilitado.
  - Fase 6 aplicada (corte controlado de dependencia externa):
    - `MCP_BACKEND=frozen` promovido como default en runtime.
    - Dependencia de runtime en backend `upstream` eliminada (Kairos solo arranca MCP propio local).
    - Contratos v1 ampliados para 43/43 tools Essentials y cache fallback crítico en backend frozen.
    - Runbook técnico publicado en `docs/mcp-frozen-runbook.md`.
    - Regresión final tras cierre técnico: `pytest -q tests/test_mcp_client.py tests/test_main.py tests/test_trainer_agent.py` (406 passed).
    - Smoke E2E de regresión sobre MCP propio (`effective=frozen`):
      - `get_training_load_trend`, `get_activities_by_date`, `get_sleep_summary`, `get_hrv_data`, `get_personal_record` -> OK.

- Cierre del punto 9 (verificación final 2026-09-03):
  - 43/43 tools Essentials cubiertas en catálogo y contratos v1 (sin gaps).
  - Rutas críticas con fallback local/caché en frozen (sin dependencia dura en error transitorio de tercero).
  - Regresión unitaria/funcional en verde y smoke E2E sentinela completado.
  - Operación local-only validada: Kairos solo depende de su MCP propio y de Garmin Connect API.
  - Documentación técnica y runbook de mantenimiento publicados.

- Catálogo versionado de tools (v1 - 2026-09-03):
  - Convención:
    - Clase `C`: crítica (rutas deterministas/snapshot arranque).
    - Clase `E`: esencial no crítica (necesaria para cobertura funcional completa).
  - Contrato mínimo por tool (`input -> output esperado por Kairos`):
    - `get_user_profile` | C | `{}` -> perfil con sexo/edad/peso/altura o equivalente usable en perfil interno.
    - `get_activities` | C | `{limit,page}` -> lista con `activityId`, fecha, tipo, distancia, duración.
    - `get_activity` | C | `{activity_id}` -> detalle de actividad con métricas base y metadatos de esfuerzo.
    - `get_activity_hr_in_timezones` | C | `{activity_id}` -> zonas FC con tiempos/porcentajes por zona.
    - `get_activities_by_date` | C | `{start_date,end_date}` -> lista de actividades del rango.
    - `get_activities_fordate` | E | `{startdate,enddate}` -> alias/fallback de actividades por rango.
    - `get_activity_splits` | E | `{activity_id}` -> segmentos/laps con distancia y tiempo.
    - `get_activity_exercise_sets` | E | `{activity_id}` -> bloques/series de fuerza si existen.
    - `get_activity_power_in_timezones` | E | `{activity_id}` -> zonas de potencia por tiempo.
    - `get_stats` | E | `{date?}` -> resumen diario agregado (pasos/energía/FC u homólogos).
    - `get_sleep_summary` | C | `{date}` -> horas de sueño totales y score.
    - `get_sleep_data` | E | `{date}` -> detalle por fases/eventos de sueño.
    - `get_heart_rates_summary` | E | `{date}` -> resumen FC diaria (reposo/media/máxima según disponibilidad).
    - `get_stress_summary` | E | `{date}` -> estrés diario agregado.
    - `get_respiration_summary` | E | `{date}` -> respiración diaria agregada.
    - `get_all_day_stress` | E | `{date}` -> serie intradía de estrés.
    - `get_all_day_events` | E | `{date}` -> eventos intradía relevantes.
    - `get_body_battery` | C | `{start_date,end_date}` -> carga/descarga Body Battery del día/rango.
    - `get_rhr_day` | E | `{date}` -> FC en reposo diaria.
    - `get_spo2_data` | E | `{date}` -> SpO2 diario (agregado o serie corta).
    - `get_hrv_data` | C | `{date}` -> HRV nocturno + baseline/estado si existe.
    - `get_daily_steps` | E | `{date}` -> pasos diarios.
    - `get_hydration_data` | E | `{date}` -> hidratación diaria y objetivo si existe.
    - `get_body_composition` | E | `{start_date,end_date}` -> peso/composición en rango.
    - `get_training_readiness` | C | `{date?}` -> readiness score y factores disponibles.
    - `get_morning_training_readiness` | C | `{date?}` -> readiness matinal para check-in.
    - `get_training_status` | E | `{}` -> estado global de entrenamiento.
    - `get_training_load_trend` | C | `{start_date,end_date}` -> serie de carga para contextualización factual.
    - `get_training_effect` | E | `{activity_id|date?}` -> efecto aeróbico/anaeróbico cuando aplique.
    - `get_hrv_trend` | E | `{start_date,end_date}` -> tendencia HRV por rango.
    - `get_vo2max_trend` | E | `{start_date,end_date}` -> tendencia VO2max.
    - `get_endurance_score` | E | `{}` -> score de resistencia/endurance.
    - `get_fitnessage_data` | E | `{}` -> fitness age/edad de forma.
    - `get_lactate_threshold` | E | `{}` -> umbral (ritmo/FC/potencia según datos).
    - `get_cycling_ftp` | E | `{}` -> FTP ciclismo vigente.
    - `get_race_predictions` | E | `{}` -> predicciones de carrera por distancia.
    - `get_personal_record` | C | `{}` -> PRs por tipo (running/ciclismo), con marca y fecha si existe.
    - `get_weekly_steps` | E | `{start_date?,end_date?}` -> pasos semanales.
    - `get_weekly_intensity_minutes` | E | `{start_date?,end_date?}` -> minutos de intensidad semanales.
    - `get_weekly_stress` | E | `{start_date?,end_date?}` -> estrés semanal.
    - `kairos_load_trends` | C | `{metric,weeks_back}` -> `daily` y `weekly` para TSS/ATL/CTL/TSB.
    - `kairos_correlate` | E | `{metric_x,metric_y,weeks_back}` -> correlación (`n`,`r`,`interpretación`).
    - `kairos_weekly_sport_breakdown` | E | `{weeks_back}` -> desglose por deporte (sesiones/horas/km).
  - Notas de compatibilidad v1:
    - Alias defensivo de PRs: aceptar `get_personal_records` y resolver a `get_personal_record`.
    - Para `get_body_battery` y `get_body_composition`, contratos v1 exigen `start_date/end_date`.

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

#### 55) [REANUDACION-E2E] Validacion real de carga/fatiga (2026-08-15) — REALIZADO
- Validación E2E operativa cerrada con datos reales (TSS/ATL/CTL/TSB) en runtime completo del agente.
- Evidencia registrada en `docs/validation-load-fatigue-e2e-2026-08-15.md` y revalidaciones posteriores en `docs/testing-bateria-punto55.md`.

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

#### 64) [DATE-HARDENING] Interpretación robusta y proactiva de fechas (2026-09-01) — REALIZADO
- Parser central de rangos naturales añadido: `del ... al ...` y `entre ... y ...` con formatos `DD/MM`, `DD/MM/AA`, `DD/MM/AAAA` y `YYYY-MM-DD`.
- Prioridad explícita al rango literal del usuario frente a interpretaciones relativas (`esta semana`, `semana pasada`).
- Normalización de argumentos de fecha en tools MCP para convertir literales a ISO de forma consistente.
- Corrección de fallback semanal para evitar rangos invertidos en consultas históricas (evita respuestas vacías/TSS=0.0 falsos).
- Prompting reforzado en `system_prompt.md` y `system_prompt_compact.md` con regla obligatoria de rangos explícitos.
- Cobertura de regresión añadida para parseo de fechas, rangos y cruce anual; validación focal en verde (23 tests).

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

