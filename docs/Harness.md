# Harness Engineering — Kairos Coach

## ¿Qué es Harness Engineering en IA?

En el ecosistema de agentes e IA, **harness engineering** es la disciplina de diseñar y construir toda la infraestructura que envuelve y controla al LLM. No es el modelo: es **todo lo que lo rodea** — políticas de comportamiento, mecanismos de observabilidad, puntos de intervención en el ciclo de vida y validación del sistema.

Se articula en tres componentes principales:

| Componente | Definición | Analogía |
|---|---|---|
| **Guides** | Instrucciones que dirigen al agente — qué hacer, cuándo, en qué orden | Manuales de operación |
| **Sensors** | Mecanismos que observan y miden — estado del atleta y del sistema | Instrumentación de telemetría |
| **Hooks** | Puntos de intervención en el ciclo de vida — antes/después de eventos clave | Callbacks del pipeline |

> La idea central: **el harness es tan crítico como el modelo**. Un LLM sin harness bien diseñado es impredecible; con uno sólido, es confiable y auditable.

---

## Guides

Son las instrucciones que definen el comportamiento del agente.

### Guía de herramientas (dónde vive)
El LLM elige herramientas con tres fuentes que sí recibe: la **descripción de cada tool** del servidor MCP (`garmin_mcp/server.py`; catálogo legible en `garmin_mcp/README.md`), el apartado **"Checklist mínimo por intención"** y **"Uso eficiente de herramientas"** del system prompt, y el bloque de contexto (perfil, carga, umbrales, plan). La antigua `prompts/mcp_tool_routing_guide.md` no se inyectaba al LLM y estaba duplicada; se archivó el 22/10 (sus reglas de ahorro de tokens pasaron al system prompt).

La política de solo lectura no depende de ninguna guía: el servidor no expone escrituras, sus tools llevan `readOnlyHint` y `MCP_READ_ONLY` (activado por defecto) filtra y bloquea cualquier tool de escritura en el host.

### System Prompt
**Archivo:** `prompts/system_prompt.md`

Documento central de comportamiento del agente. Define:
- Rol e identidad del coach.
- Arquitectura de dos capas: capa de datos (Python) vs capa de coaching (LLM).
- Reglas de lo que el agente NUNCA debe hacer (no recalcular datos ya procesados, no presentar datos crudos, no inventar).
- Cuándo consultar herramientas vs cuándo usar datos pre-computados.
- Protocolos especiales: Diabetes Tipo 1, Race Readiness, revisión post-sesión.
- Flags de anomalías biométricas (ver sección Sensors).
- Reglas de prompting: tendencia junto al valor puntual, transparencia de muestra, relaciones > valores aislados.
- Contrato de formato unificado obligatorio (4 secciones): resumen, métricas clave, recomendación y próximo paso.

### System Prompt Compacto
**Archivo:** `prompts/system_prompt_compact.md`

Versión reducida del system prompt para modelos con límite bajo de tokens (GitHub Models en red corporativa con Zscaler). Mantiene las mismas reglas esenciales, incluyendo la plantilla única de salida.

---

## Sensors

Mecanismos que observan y miden el estado — tanto del atleta como del propio sistema.

### Sensores biométricos (sobre el atleta)

#### Snapshot proactivo de 48h
**Función:** `TrainerAgent.collect_startup_snapshot_48h()` / `_build_proactive_status_markdown()`

Al arrancar el sistema, se recopilan automáticamente:
- Body battery (últimas 48h)
- HRV
- Sueño
- Entrenamientos recientes
- Recomendación de plan activo (si existe)

Genera un resumen operativo que el coach presenta al usuario sin que este lo pida.

#### Detección de anomalías biométricas
**Definido en:** `prompts/system_prompt.md`

Flags activos que el coach debe detectar y reportar:
- FC en reposo elevada sin carga previa.
- Sueño malo ≥ 2 noches consecutivas.
- HRV > 15% por debajo de la media de 7 días.
- Body battery < 30 durante ≥ 2 días.

#### Modelo de carga y fatiga TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)
**Función:** `TrainerAgent.compute_and_persist_load_metrics()`; datos en `activity_tss` y `load_metrics_daily` (Supabase)

Sensor continuo de carga y fatiga, calculado como TrainingPeaks:
- Un TSS por actividad, calculado una vez con el método de su deporte y guardado en `activity_tss` con el modelo y los anclajes usados. Nunca se recalcula hacia atrás (línea base desde el 22/02/2026).
- EWMA con las constantes del PMC de TP (CTL 42, ATL 7) para todos los deportes; Forma = CTL de ayer − ATL de ayer.
- Anclajes con fecha en el perfil (umbral de FC de TP, ritmo umbral) aplicados según la fecha de cada actividad.
- Percentiles individualizados por atleta para las alertas.
- Integrado en el snapshot proactivo con regla aplicada de actuación.

#### Validación frente a TrainingPeaks
**Herramientas:** `tools/rebuild_tss_baseline.py`, export de TP (`_archivo/datos_tp/workouts.csv`, local)

Sensor de exactitud del propio cálculo:
- `rebuild_tss_baseline.py` recalcula todo por la ruta de producción con las escrituras interceptadas (prueba) y deja un CSV por actividad; con `--apply` persiste.
- Cada cambio de fórmula se valida contra el export de TP con criterios fijados antes de ver resultados (mediana Kairos/TP, MAPE, sesiones dentro de ±10 %) y comprobando que solo cambian las filas esperadas.
- Estado a 2026-10-21: running MAPE 6.3 %, trail 1.6 %, bici con potencia 1.6 %; serie frente a TP con diferencia media CTL 2.1 · ATL 2.7 · TSB 3.0.

#### Tendencia junto al valor puntual
**Definido en:** `prompts/system_prompt.md`

Regla sensor: para HRV, body battery, sueño, FC en reposo y VO2max, reportar siempre: valor hoy + media 7d + dirección de tendencia. Nunca un valor aislado.

#### Transparencia de datos
**Definido en:** `prompts/system_prompt.md`

Regla: declarar N (tamaño de muestra) y calidad del dato en toda afirmación sobre tendencias.

### Sensores del sistema (sobre el agente)

#### Suite de tests (estado validado en CI y local)
**Directorio:** `tests/`

Verifica que el harness no se rompe (2026-10-22: 570 passed, 6 skipped):
- `test_trainer_agent.py` — agente: rutas deterministas, coaching, planes (`TestStructuredPlanEngine`), pantalla de carga y umbrales por fecha.
- `test_load_metrics.py` — TSS por deporte, PMC (CTL/ATL/TSB), zonas de Forma, duración de actividad y modelo de caminar/senderismo.
- `test_running_tss.py` — pipeline físico de running (NGP, rTSS).
- `test_storage.py` — capa de persistencia.
- `test_main.py` — flujo principal y comandos de perfil.
- `test_hooks_router.py`, `test_garmin_mcp_server.py`, `test_mcp_client.py` — hooks, router y MCP.

Toda la capa de Supabase y Garmin está mockeada — no se realizan conexiones reales en ningún test.

#### Batería y juez determinista
**Herramientas:** `tools/run_bateria.py` y `tools/juez_bateria.py`

`run_bateria.py` lanza las 25 preguntas de `docs/bateria_p55_preguntas_base.txt` contra Kairos real y guarda la transcripción con tiempos (`.tmp/bateria/`). `juez_bateria.py <transcripción>.md` la contrasta con Supabase (`load_metrics_daily`, `activity_tss`, perfil, planes) y, para los récords, con Garmin en solo lectura; reutiliza funciones de producción (`form_zone`, umbral por fecha, `_hr_zone_bounds_bpm`, familia de deporte). Rúbrica fijada antes de ver resultados (22/10/2026):

- Globales (G1–G5): hay respuesta y coaching; Garmin accesible (si no, fallo de entorno); fechas DD/MM/AAAA; nada de "datos disponibles" sin cifras; sin turnos de usuario inventados.
- Por pregunta: CTL/ATL/TSB y zona de Forma = serie del día; TSS semanal, diario y por actividad = `load_metrics_daily` / `activity_tss`; actividades de un rango ⊇ `activity_tss`; análisis de la actividad correcta con su TSS y su Carga Garmin (`native_load`); recuento y desglose por deporte; umbrales y su historial = perfil; plan activo = `training_plan`; récords = Garmin; objetivo = `goals`; últimas 20 por deporte = `activity_tss` (orden, TSS, carga, media y máximo).
- En el texto del LLM: no atribuye a la CTL el máximo habitual de la ATL; cada rango de FC en lpm cabe en una zona (zonas de carrera de Garmin guardadas en el perfil, las del reloj y TP; desde el 22/10, antes % del LTHR), salvo un rango dentro de Z1–Z2 (rodaje suave: las dos zonas están bajo VT1 y dan el mismo estímulo; desde el 23/10, decisión del usuario; que un día de recuperación vaya solo en Z1 lo pide el prompt, el juez no lo comprueba) y salvo un tramo progresivo (desde el 23/10, decisión del usuario): en la misma línea, marcado como calentamiento, vuelta a la calma, enfriamiento o progresivo y con "de Zx a Zy" / "Zx–Zy", con la FC de inicio en la zona más baja nombrada y la de fin en la más alta; un tramo continuo entre dos zonas de Z2/Z3 para arriba ("Z2 alto / Z3 bajo", "Z4–Z5") sigue siendo fallo; un ritmo citado como umbral es el vigente.
- Tolerancias: ±0,1 en TSS/CTL/ATL/TSB, ±1 lpm, ±2 s/km. No juzga la calidad del consejo (revisión humana).

Genera `<transcripción>.juez.md/.json` y sale con 1 si algo suspende. Primera pasada (22/10): detectó que la "Carga Garmin" de las salidas en bici con potencia mostraba el TSS de Garmin.

#### Guía de estilo del MCP
**Herramienta:** `tools/lint_mcp.py` (`make lint-mcp`)

Pasa la guía de estilo MCP de apiaddicts (`mcps-style-guide`, Spectral) sobre el `tools/list` real del servidor: título, descripción y parámetros descritos en cada tool, `inputSchema` de tipo object. Configuración en `tools/mcp_lint/.spectral.yaml` (ruleset anclado a un commit; camelCase y `outputSchema` desactivadas con su motivo). 22/10: 0 hallazgos. Un test sin red (`test_all_tools_follow_mcp_style_guide`) lo mantiene en la CI.

#### Salud de las tools del MCP
**Herramientas:** test de contrato (`test_every_tool_calls_garminconnect_with_its_real_signature`, en CI) y `tools/healthcheck_mcp.py` (`make health-mcp`, bajo demanda)

- El test de contrato, sin red, comprueba que cada tool llama a `garminconnect` con argumentos válidos para la versión instalada (habría detectado las 8 tools rotas del 22/10 y detecta cambios de firma al actualizar la librería).
- El healthcheck en vivo llama a las 42 tools contra Garmin (ayer, últimas dos semanas, actividad más reciente) y clasifica cada una: OK, VACÍA (aviso), ERROR o ENTORNO (429/autenticación: no es fallo de Kairos). Informe en `.tmp/healthcheck/`; sale con 1 si alguna tool falla. Lanzarlo al actualizar `garminconnect`, antes de una batería o si algo parece raro; no en el arranque de Kairos. 22/10: 37 OK, 4 VACÍA (potencia sin potenciómetro, Training Readiness no disponible en el Fénix 6X Pro, sin eventos), 0 ERROR.

#### GitHub Actions CI
**Archivo:** `.github/workflows/tests.yml`

Sensor continuo en cada `push` a `main`/`dev` y en cada `pull_request` a `main`. Ejecuta la suite completa con variables de entorno mínimas de CI.

#### Filtrado de herramientas de escritura (guardrail)
**Función:** `TrainerAgent.initialize()` en `agent/trainer_agent.py`

```python
if self.mcp_read_only:
    tools = [t for t in tools if not _is_write_mcp_tool(t.get("name", ""))]
```

Sensor de seguridad: filtra las tools de escritura MCP antes de que el LLM pueda verlas. Complementado con bloqueo en el loop de tool-calls.

#### Verificación de autenticación Garmin
**En:** `main()`, `agent/main.py`

Llama a `get_user_profile` como test de conectividad antes de continuar. Detecta si la contraseña de Garmin Connect ha cambiado y activa el flujo de recuperación.

#### Detección de cambios de perfil Garmin
**Función:** `_sync_from_garmin()` en `agent/main.py`

En cada arranque, sincroniza datos personales desde Garmin y reporta cambios de perfil (peso, edad, género...) de forma contextual.

---

## Hooks

Puntos de intervención en el ciclo de vida del agente. El proyecto ahora incluye un sistema de hooks **explícito** y mínimo en runtime:

- `HookManager` en `agent/trainer_agent.py`.
- Eventos soportados: `before_message`, `after_message`, `before_tool_call`, `after_tool_call`, `on_error`.
- Integrado en `TrainerAgent.chat()` para cubrir rutas deterministas y flujo LLM+tools.

Además, se añadió un `ToolRouter` determinista opcional para intenciones críticas (activado por defecto con `KAIROS_DETERMINISTIC_ROUTER=true`).

### Pipeline de arranque

```
main()
  ├─ [hook: fail-fast infra]     _check_and_migrate_supabase()
  ├─ [hook: autenticación]       _authenticate_or_register_user()
  ├─ [hook: credenciales]        _ensure_garmin_credentials()
  ├─ [hook: selección provider]  _auto_select_provider()
  │
  └─ async with garmin_mcp_session():
       ├─ [hook: carga de tools]     agent.initialize()
       │    └─ [hook: filtro r/o]    _is_write_mcp_tool() filter
       │
       ├─ [hook: verificación auth]  call_tool("get_user_profile") → detección 401
       ├─ [hook: sincronización]     _sync_from_garmin() → profile_changes
       ├─ [hook: primer uso]         _is_first_time() → _run_first_time_setup()
       ├─ [hook: knowledge base]     build_onboarding_mcp_enrichment() (si nuevo)
       ├─ [hook: carga/fatiga]       agent.compute_and_persist_load_metrics() → activity_tss + load_metrics_daily
       ├─ [hook: snapshot proactivo] _build_proactive_status_markdown()
       └─ [loop de chat]            while True: agent.chat(user_input)
```

### Hook de filtrado de tools (`initialize()`)
Hook de seguridad de arranque: se ejecuta una vez al iniciar y determina qué herramientas MCP son visibles para el LLM durante toda la sesión (modo read-only).

### Hooks de conversación (`TrainerAgent.chat()`)
Hooks explícitos alrededor del ciclo de conversación:

- `before_message`: antes de procesar el mensaje del usuario.
- `after_message`: tras construir la respuesta final (incluye ruta usada).
- `before_tool_call`: antes de cada tool-call (determinista o generado por LLM).
- `after_tool_call`: después de cada tool-call, con estado de éxito/bloqueo.
- `on_error`: cuando ocurre error en etapas críticas (`planning_route`, `llm_completion`, `tool_call`, etc.).

### Router determinista opcional (`ToolRouter`)
Router de intenciones críticas, habilitado por defecto:

- `config_options`
- `plan_status`
- `goal_status`
- `week_tss`
- `week_activities`
- `load_trend`
- `hr_threshold`
- `running_threshold`
- `today_load_status`
- `activity_details`
- `recent_tss_by_sport` (últimos N entrenamientos con su TSS guardado, varios deportes)
- `personal_records`
- `mcp_factual`
- `daily_readiness`
- `planning`

Las rutas que muestran TSS leen `activity_tss`: una actividad sin fila aparece como "pendiente de cálculo" y la carga de Garmin solo como "Carga Garmin (no TSS)".

Se puede desactivar con `KAIROS_DETERMINISTIC_ROUTER=false` para volver al comportamiento basado solo en lógica existente/LLM.

### Hook de CI (GitHub Actions)
Se dispara en `push` y `pull_request`. Actúa como hook de calidad del código fuente.

---

## Gaps identificados

Elementos de harness engineering que actualmente faltan en el proyecto:

| Gap | Descripción | TODO relacionado |
|---|---|---|
| Git hooks locales | No hay `.githooks/` — no hay validaciones pre-commit | #22 |

---

## Estado del harness por capa

```
┌─────────────────────────────────────────────────────┐
│                   GUIDES                            │
│  system_prompt.md           ✓ operativo             │
│  system_prompt_compact.md   ✓ operativo             │
├─────────────────────────────────────────────────────┤
│                   SENSORS                           │
│  snapshot proactivo 48h     ✓ operativo             │
│  anomalías biométricas      ✓ en prompting          │
│  TSS por actividad + PMC    ✓ operativo             │
│  validación frente a TP     ✓ rebuild en prueba     │
│  guardrail read-only        ✓ operativo             │
│  suite de tests             ✓ operativo             │
│  batería + juez             ✓ manual (tools/)       │
│  guía de estilo MCP         ✓ lint + test en CI     │
│  salud de tools MCP         ✓ contrato CI + vivo    │
│  CI GitHub Actions          ✓ operativo             │
│  spike semanal >20%         ✓ operativo             │
│  logging por niveles        ✓ operativo             │
├─────────────────────────────────────────────────────┤
│                   HOOKS                             │
│  pipeline arranque          ✓ operativo             │
│  filtro tools read-only     ✓ en initialize()       │
│  hook pre/post mensaje      ✓ explícito             │
│  hook pre/post tool call    ✓ explícito             │
│  hook error centralizado    ✓ explícito             │
│  router determinista        ✓ opcional (env flag)   │
│  git hooks locales          ✗ no existe             │
└─────────────────────────────────────────────────────┘
```
