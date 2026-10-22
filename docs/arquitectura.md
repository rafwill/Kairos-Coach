# Arquitectura de Kairos Coach

Referencia técnica de cómo funciona Kairos por dentro. El estado vigente del cálculo de carga (modelos por deporte, anclajes, validación frente a TrainingPeaks y pendientes) está en `CLAUDE.md`; el harness de runtime (hooks, router, rutas deterministas) en `docs/Harness.md`.

## Capas

```
┌──────────────────────────────────────────────────────────────┐
│  CLI (agent/main.py)                                         │
│  Terminal interactivo · Rich UI · multiusuario · login       │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│  TrainerAgent (agent/trainer_agent.py)                       │
│  Rutas deterministas · capa de coaching LLM · motor de planes│
│  TSS/PMC (agent/load_metrics.py, agent/running_tss.py)       │
└──────────┬──────────────────────────────────┬────────────────┘
           │                                  │
┌──────────▼──────────────┐      ┌────────────▼────────────────┐
│ MCP propio (solo lectura)│      │ Supabase (agent/storage.py) │
│ agent/kairos_mcp_server  │      │ perfil · planes · TSS ·     │
│ 41 tools Garmin · stdio  │      │ serie de carga · wellness   │
└──────────────────────────┘      └─────────────────────────────┘
```

- **Datos (Python):** Garmin aporta las señales base; Kairos calcula en Python todo lo derivado (TSS, CTL/ATL/TSB, zonas de FC, ritmos, hidratación) antes de entregarlo al LLM.
- **Coaching (LLM):** interpreta los datos ya calculados con el perfil del atleta. Nunca hace aritmética ni inventa métricas.

## Qué guarda Kairos en Supabase

| Tabla | Contenido |
| --- | --- |
| `app_user` | Usuario de la aplicación, `password_hash` (PBKDF2) y credenciales auxiliares (contraseña de Garmin cifrada con Fernet). |
| `user_profile` | Perfil del atleta en JSON: `goals`, `performance` (umbrales e historiales por fecha), espejo del plan activo y caché de `load_metrics`. |
| `session_context` | Historial reciente de conversación y resúmenes de sesión. |
| `athlete_knowledge` | Base de conocimiento textual del atleta (RAG ligero). |
| `gemini_usage` | Consumo diario de tokens y estado de cuota. |
| `training_plan`, `training_plan_session`, `training_plan_version` | Planes, sesiones (`structured_workout`) y snapshots de cada versión. |
| `activity_tss` | Un TSS por actividad, calculado una vez: `tss`, `model`, anclajes usados y `native_load` (carga de Garmin, informativa). |
| `load_metrics_daily` | Serie diaria TSS/ATL/CTL/TSB: fuente canónica de todas las pantallas. |
| `wellness_daily` | Body Battery y HRV por día, con `pulled_at` para no consultar Garmin de más. |

Esquema completo en `supabase/schema.sql`: fichero único e idempotente (sirve para instalar desde cero y para poner al día un proyecto existente).

## Arranque de sesión

```
main.py → run_agent()
  ├─ login (usuario de la app) y selección de proveedor LLM
  ├─ MCP propio: tools/garmin-mcp-frozen.* → python -m agent.kairos_mcp_server
  │     tokens de Garmin reutilizados en ~/.garminconnect/kairos/<hash del email>
  ├─ TrainerAgent.compute_and_persist_load_metrics()
  │     incremental (normal) · incremental_refresh (actividad nueva) · full_recalc (usuario nuevo / cambio de fórmula)
  └─ TrainerAgent.build_startup_status_markdown()
        snapshot de 48 h (body battery, HRV, sueño, entrenamientos) + estado de carga + plan activo
```

## Cálculo de TSS y PMC

```
compute_and_persist_load_metrics()
  ├─ 1. Actividades de Garmin desde la última fecha guardada
  │     ├─ ya en activity_tss → usa ese TSS (nunca se recalcula)
  │     └─ nueva → _estimate_session_tss(actividad, anclajes vigentes en su fecha)
  │           · detalle por segundo (get_activity_details) para running, trail, caminar y bici sin potencia
  │           · hr_rest = media del RHR de 14 días previos; umbral de FC de TP y ritmo umbral por fecha
  │     └─ upsert en activity_tss
  ├─ 2. PMC con CTL 42 / ATL 7 para todos los deportes
  ├─ 3. Semilla: fila de la víspera en load_metrics_daily (0 al reconstruir la línea base)
  ├─ 4. Día a día:  atl = atl_prev + (tss − atl_prev)/7 · ctl = ctl_prev + (tss − ctl_prev)/42 · tsb = ctl_prev − atl_prev
  └─ 5. upsert en load_metrics_daily y caché en el perfil
```

- Running: `running:pipeline_fisico` (`agent/running_tss.py`: NGP con pendiente Minetti, rTSS, detectores de tempo y series cortas). Sin detalle de la actividad, respaldo `running:tp_like_adjusted` por agregados.
- Correcciones del histórico: `tools/rebuild_tss_baseline.py` (prueba sin escribir; `--apply` persiste).
- Si `formula_version` del perfil difiere de `TSS_FORMULA_VERSION`, se recalculan 120 días (las actividades ya guardadas conservan su TSS).

### Estado de carga: zonas de Forma

Una sola regla para todo Kairos (`load_metrics.form_zone` y `resolve_load_status`): estado de arranque, motor diario, `/carga`, resumen semanal, contexto del LLM y motor de planes. Zonas de Joe Friel con la Forma relativa al CTL, como intervals.icu (Form % = TSB / CTL · 100):

| Zona | Form % | Estado del motor diario |
| --- | --- | --- |
| 🔴 Riesgo alto | < −30 % | `overload`: descanso y descarga |
| 🟢 Óptimo | −30 a −10 % | `neutral`: seguir el bloque (se modula con HRV, sueño y body battery) |
| 🟡 Zona gris | −10 a +5 % | `ready` |
| 🔵 Fresco | +5 a +20 % | `ready` |
| ⚪ Transición | > +20 % | `ready`, avisando de pérdida de forma |

- 7 días seguidos por debajo de −10 % (sin llegar a Riesgo alto) → `fatigue_high`: suave si la recuperación no acompaña y programar descarga.
- Spike semanal > 20 % sobre la semana anterior: aviso y reducción de 15–25 % los 2–3 días siguientes.
- Los percentiles del atleta (rango habitual de Forma, ATL máximo habitual) se muestran como información y no deciden. No hay suelos de TSB por deporte.
- TrainingPeaks no publica zonas de TSB; las de Friel son una convención de entrenadores con poca evidencia formal.

## Cada mensaje del chat

`TrainerAgent.chat()` pasa por `HookManager` y `ToolRouter` (`docs/Harness.md`):

1. **Rutas deterministas** (sin LLM): estado del plan, TSS y actividades de una semana, TSS de las últimas N actividades por deporte, umbrales, récords personales, objetivo, detalle de una actividad por fecha, configuración.
2. **Rutas mixtas** (estado de hoy, "¿qué me toca?", "¿puedo entrenar fuerte mañana?", sesión de la semana): bloque factual determinista y después la capa de coaching del LLM, limitada a esos datos, con el horizonte que pide el usuario (`_coaching_horizon`). Si el LLM falla, se dice explícitamente y queda solo el bloque factual.
3. **Planificación:** motor determinista multisemana (`_generate_structured_plan_payload`) con el volumen real de las últimas 4 semanas (`_build_plan_load_context`), fases base → build → peak → taper, ninguna semana más de un 10 % sobre lo sostenido y validación antes de guardar.
4. **Resto:** LLM con tool-calling sobre el MCP (hasta 15 iteraciones, resultados compactados).

El contexto del LLM (`_build_system_prompt`) incluye el perfil, el estado de carga actual, los umbrales con zonas en lpm y el plan activo de hoy y los dos días siguientes.

## Carga de Garmin frente a TSS de TrainingPeaks

Kairos no toma CTL/ATL/TSB de Garmin: los calcula a partir de su propio TSS por actividad.

- **Training Load de Garmin:** EPOC integrado durante la sesión, escala empírica, recalibrada con el historial. Se guarda solo como `native_load` ("Carga Garmin"): frente al TSS de TP va de ×0.07 (fuerza) a ×2 (running), no es intercambiable.
- **TSS de TrainingPeaks** (Coggan): `TSS = t · NP · IF / (FTP · 3600) · 100`; 1 h en umbral = 100. En running, rTSS con ritmo normalizado con pendiente frente al ritmo umbral. En hrTSS, TP asigna TSS/h por tramo de FC respecto a su umbral, sin FC de reposo: Kairos reproduce esa tabla (`TP_HRTSS_RATES` en `agent/load_metrics.py`).

Criterio de validez: el TSS por sesión de Kairos debe coincidir con el de TP para el mismo atleta, con criterios fijados antes de mirar resultados (mediana Kairos/TP, MAPE y sesiones dentro de ±10 %).

## Servidor MCP propio

- `agent/kairos_mcp_server.py`, sobre la librería `garminconnect`, solo lectura, transporte stdio.
- Catálogo de 41 tools Garmin + 3 internas `kairos_*` (`kairos_load_trends`, `kairos_correlate`, `kairos_weekly_sport_breakdown`); detalle en `docs/mcp-tools-completo-vs-essentials.md`.
- Contratos y normalización: `agent/mcp_adapter.py`; cliente: `agent/mcp_client.py`; operación e incidentes: `docs/mcp-frozen-runbook.md`.
- Algunas listas llegan como objetos JSON concatenados (`_try_parse_json`) y `get_activity` trae las métricas en `summaryDTO` (`_flatten_garmin_activity`).

## Seguridad

- Contraseña de la app: hash PBKDF2-SHA256 (120 000 iteraciones). Contraseña de Garmin: cifrada con Fernet (`ENCRYPTION_KEY` del `.env`); `_sanitize_credentials_for_storage` impide guardarla en claro.
- Tokens de Garmin en `~/.garminconnect/kairos/` (fuera del repo).
- El identificador de cuota de Gemini usa SHA-256 de la clave.
- Datos reales del atleta (exports de TP, payloads de Garmin, salidas de la batería) solo en rutas ignoradas por git: `_archivo/`, `fixtures/real_payloads/`, `.tmp/`.
