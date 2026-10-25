# Arquitectura de Kairos Coach

Referencia técnica de cómo funciona Kairos por dentro. El estado vigente del cálculo de carga (modelos por deporte, anclajes, validación frente a TrainingPeaks y pendientes) está en `CLAUDE.md`; el harness de runtime (hooks, router, rutas deterministas) en `docs/Harness.md`.

## Capas

Con la terminología del estándar MCP: Kairos es el **host** (la aplicación con el LLM), `agent/mcp/` es su **cliente MCP** y `garmin_mcp/` es el **servidor MCP**. Un cliente por servidor (1:1): si Kairos usara otro servidor MCP, tendría otro cliente.

```
┌─────────────────────── HOST: Kairos (agent/) ────────────────────────┐
│  CLI (main.py) · TrainerAgent (trainer_agent.py)                     │
│  Rutas deterministas · coaching LLM · motor de planes                │
│  TSS/PMC (load_metrics.py, running_tss.py) · Supabase (storage.py)   │
│                                                                      │
│  ┌── cliente MCP (agent/mcp/) ──┐                                    │
│  │ client.py     sesión stdio, call_tool, catálogo                  │
│  │ contracts.py  contratos de argumentos, normalización, caché      │
│  └───────────────┬──────────────┘                                    │
└──────────────────┼───────────────────────────────────────────────────┘
                   │ protocolo MCP (stdio, subproceso)
┌──────────────────▼───────────────────────────────────────────────────┐
│  SERVIDOR MCP: garmin_mcp/ (server.py) — independiente del host      │
│  42 tools de Garmin Connect · solo lectura · garminconnect           │
└──────────────────────────────────────────────────────────────────────┘
```

El servidor no importa nada de `agent/` (lo comprueba `test_server_package_does_not_import_the_host`): cualquier cliente MCP puede usarlo (Kairos, el Inspector MCP, `tools/healthcheck_mcp.py`, el juez de la batería).

- **Datos (Python):** Garmin aporta las señales base y las zonas de FC del reloj; Kairos calcula en Python todo lo derivado (TSS, CTL/ATL/TSB, ritmos, hidratación) antes de entregarlo al LLM.
- **Coaching (LLM):** interpreta los datos ya calculados con el perfil del atleta. Nunca hace aritmética ni inventa métricas.

## Qué guarda Kairos en Supabase

| Tabla | Contenido |
| --- | --- |
| `app_user` | Usuario de la aplicación, vinculado a su cuenta de Supabase Auth (`auth_user_id`), y credenciales auxiliares (contraseña de Garmin cifrada con Fernet). `password_hash` (PBKDF2) solo en cuentas anteriores al 24/10 sin migrar. |
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
  ├─ login con Supabase Auth (sesión guardada o email + contraseña) y selección de proveedor LLM
  ├─ MCP propio: python -m garmin_mcp (mismo Python que Kairos)
  │     tokens de Garmin reutilizados en ~/.garminconnect/kairos/<hash del email>
  ├─ TrainerAgent.initialize() → _refresh_hr_zones_from_garmin()
  │     get_heart_rate_zones; escribe en el perfil solo si las zonas cambian
  ├─ TrainerAgent.compute_and_persist_load_metrics()
  │     incremental (normal) · incremental_refresh (actividad nueva) · full_recalc (usuario nuevo / cambio de fórmula)
  └─ TrainerAgent.build_startup_status_markdown()
        snapshot de 48 h (body battery, HRV, sueño, entrenamientos) + estado de carga + plan activo
```

## Zonas de FC

Mandan las zonas configuradas en Garmin Connect, que son las del reloj y las de TrainingPeaks (decisión del 22/10). Kairos no las calcula.

- **Lectura:** al arrancar, `_refresh_hr_zones_from_garmin()` llama una vez a `get_heart_rate_zones` (formato en [`garmin_mcp/README.md`](../garmin_mcp/README.md#tools-que-no-son-un-método-de-garminconnect)).
- **Guardado:** en el perfil, `performance.hr_zones_garmin` (`updated_at` y las zonas por deporte). Solo se escribe si las zonas cambian. La versión anterior pasa a `performance.hr_zones_garmin_history` con `valid_from` y `valid_until`.
- **Si Garmin falla** o no devuelve zonas válidas, se usan las guardadas.
- **Fuente única:** `_resolve_hr_zones(perfil, deporte)` da las zonas del deporte pedido (o las de `default`) en lpm. La usan el contexto del LLM (bloque "Umbrales"), los análisis de actividad y el juez de la batería. Nombres de TP en español: Z1 Recuperación, Z2 Aeróbica, Z3 Tempo, Z4 Subumbral, Z5 Supraumbral.
- **Respaldo:** sin zonas de Garmin guardadas, se calculan por % del umbral de FC del perfil (`_hr_zone_bounds_bpm`), sin huecos entre zonas.
- **Sin efecto en la carga:** el hrTSS usa los tramos de % del umbral de TP (`TP_HRTSS_RATES`), no estas zonas. Los objetivos `hr_pct` de los planes siguen en % del umbral, como formato interno.

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

`TrainerAgent.chat()` pasa por `ToolRouter` (`docs/Harness.md`):

1. **Rutas deterministas** (sin LLM): estado del plan, TSS y actividades de una semana, kilómetros y actividad más larga de un periodo (`period_activities`), sueño de anoche (`sleep_last_night`), TSS de las últimas N actividades por deporte, umbrales, récords personales, objetivo, detalle de una actividad por fecha, configuración.
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

- `garmin_mcp/server.py` (arranque: `python -m garmin_mcp`), sobre la librería `garminconnect`, solo lectura, transporte stdio. Paquete independiente del host.
- Catálogo de las 42 tools (parámetros, tipos y anotaciones): [`garmin_mcp/README.md`](../garmin_mcp/README.md), generado desde el servidor con `tools/gen_mcp_docs.py`. Además, el host añade 3 tools internas `kairos_*` (`kairos_load_trends`, `kairos_correlate`, `kairos_weekly_sport_breakdown`) que no pasan por el MCP.
- Lado cliente en el host: `agent/mcp/client.py` (sesión y llamadas) y `agent/mcp/contracts.py` (contratos, normalización y caché). Detalle en "Cliente MCP" más abajo. Problemas frecuentes (429/403, certificados detrás de un proxy): [`garmin_mcp/README.md`](../garmin_mcp/README.md#problemas-frecuentes).
- Algunas listas llegan como objetos JSON concatenados (`_try_parse_json`) y `get_activity` trae las métricas en `summaryDTO` (`_flatten_garmin_activity`).
- Guía de estilo MCP de apiaddicts ([mcps-style-guide](https://github.com/apiaddicts/mcps-style-guide)): cada tool tiene título, descripción (la que ve el LLM de Kairos para elegirla), descripción de cada parámetro y anotaciones de solo lectura (`readOnlyHint`, `idempotentHint`, `openWorldHint`). `tools/lint_mcp.py` (o `make lint-mcp`) pide `tools/list` por stdio y lo pasa por Spectral con `tools/mcp_lint/.spectral.yaml`: el ruleset anclado a un commit, sin las dos reglas no adoptadas (camelCase y `outputSchema`, con su motivo). Resultado el 22/10: 0 hallazgos (antes 217). `tests/test_garmin_mcp_server.py` lo garantiza sin red.
- Contrato con `garminconnect`: cada tool llama a la librería con su firma real (verificado contra la versión instalada por un test sin red). El 22/10 se corrigieron 8 tools que fallaban siempre (`get_daily_steps`, `get_training_status`, `get_vo2max_trend`, `get_endurance_score`, `get_fitnessage_data`, `get_weekly_steps`, `get_weekly_stress`, `get_weekly_intensity_minutes`); comprobadas las 41 contra Garmin real, y las 42 tras añadir `get_heart_rate_zones` (22/10 y 23/10: 0 errores). Healthcheck en vivo bajo demanda: `tools/healthcheck_mcp.py` (`make health-mcp`).

### Cliente MCP (`agent/mcp/`)

- **Arranque del servidor:** `_get_server_params()` lanza `python -m garmin_mcp` con el mismo intérprete que Kairos (`sys.executable`, el del `.venv`) y la raíz del repo como `cwd`, igual que las herramientas de `tools/`. El subproceso recibe el entorno de Kairos más las credenciales de Garmin del usuario. (Hasta el 23/10 lo hacía a través de los lanzadores `tools/garmin-mcp-frozen.*` y `MCP_BACKEND=frozen`, restos del MCP de terceros.)
- **Catálogo esencial:** 45 tools, las 42 de Garmin (`GARMIN_ESSENTIAL_TOOLS`) y las 3 internas `kairos_*`. Los tests fallan si cambia el número o si a una le falta su contrato.
- **Antes de cada llamada:** `normalize_tool_invocation` corrige alias y formas de los argumentos (por ejemplo, `date` → `start_date`/`end_date` en `get_body_battery`), y `validate_min_input_contract` comprueba los argumentos obligatorios de `TOOL_CONTRACTS_V1`. Si faltan, devuelve un error de contrato (`mcp-adapter-v1`) sin llamar a Garmin.
- **Ruta única por defecto:** cada llamada va al servidor, sin caché ni rutas alternativas. Los errores de Garmin se clasifican (tipo, código HTTP, `retry_after`) y se registran.
- **En paralelo:** las tools que el LLM pide en un mismo mensaje se lanzan a la vez (`asyncio.gather`, en orden) y el servidor las atiende en hilos con un tope de 4 llamadas simultáneas a Garmin. Cada llamada se corta a los 8 s (`KAIROS_TOOL_TIMEOUT_SECONDS`); tras 2 timeouts en una respuesta, las siguientes no se mandan a Garmin (cortacircuitos).
- **Modo de contingencia** (desactivado; se activa con `KAIROS_MCP_ENABLE_FALLBACK=true`):
  - Caché por usuario en el perfil (`mcp_adapter_cache` → `mcp-adapter-v1`) para 12 tools críticas (`CRITICAL_CACHEABLE_TOOLS`: perfil, actividades, body battery, HRV, sueño, readiness, carga de Garmin, récords…), con clave tool + argumentos y las 10 últimas respuestas por tool.
  - Con el modo activo, esas tools se responden primero desde la caché, salvo `get_activities_by_date`, que siempre consulta Garmin y solo usa la caché si la llamada falla.
  - Si la llamada al servidor falla por una excepción (transporte, tiempo agotado…) y hay caché, se devuelve la caché. Los errores que devuelve Garmin como respuesta no usan la caché.
  - Cada uso de la caché se anota y la respuesta termina con un bloque "Transparencia de datos" ("Se usó modo de contingencia…", con la tool y el motivo).
  - **La caché no caduca:** con el modo activo, una respuesta puede salir de datos de hace días, aunque lo diga ese bloque. Por eso está desactivado. Antes, `get_training_load_trend` se resolvía contra `load_metrics_daily`; se quitó porque daba el TSS de Kairos con el nombre de la carga de Garmin.

## Seguridad

- Cuentas y acceso a datos: Supabase Auth (email + la misma contraseña que en Garmin) y seguridad por fila (RLS) en las 11 tablas, de modo que cada usuario solo ve y modifica sus filas y la clave `anon` sin sesión no ve nada. Sesión guardada en `~/.kairos/supabase_session.json`. Nada se lee de la base de datos antes de iniciar sesión. Conceptos, políticas, puesta en marcha en dos fases y marcha atrás: [`supabase/README.md`](../supabase/README.md); comprobación: `make check-rls`.
- Contraseña de Garmin: cifrada con Fernet (`ENCRYPTION_KEY` del `.env`); `_sanitize_credentials_for_storage` impide guardarla en claro.
- Solo lectura en Garmin: el MCP propio no expone escrituras y, además, `MCP_READ_ONLY` (activado por defecto) filtra las tools de escritura en `TrainerAgent.initialize()` y las bloquea en el bucle de tool-calls.
- Tokens de Garmin en `~/.garminconnect/kairos/` (fuera del repo).
- El identificador de cuota de Gemini usa SHA-256 de la clave.
- Datos reales del atleta (exports de TP, payloads de Garmin, salidas de la batería) solo en rutas ignoradas por git: `_archivo/`, `fixtures/real_payloads/`, `.tmp/`.
