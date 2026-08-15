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

### Routing Guide
**Archivo:** `prompts/mcp_tool_routing_guide.md`

Guía determinista de enrutado de intenciones a herramientas MCP. Su función es minimizar la exploración del MCP y el consumo de tokens.

Estructura de uso:
1. Identificar intención principal del usuario.
2. Buscar la sección equivalente en el documento.
3. Llamar primero la tool prioritaria (o secuencia corta recomendada).
4. Escalar a tools secundarias solo si falta contexto.

Cubre intenciones: estado diario/energía, actividad reciente, actividad por fecha, records personales, planificación, etc.

También contiene la **política de seguridad** del harness: el agente opera en modo solo consulta (read-only) para MCP. Las herramientas de escritura están explícitamente excluidas del modo coach.

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

### System Prompt Compacto
**Archivo:** `prompts/system_prompt_compact.md`

Versión reducida del system prompt para modelos con límite bajo de tokens (GitHub Models en red corporativa con Zscaler). Mantiene las mismas reglas esenciales.

---

## Sensors

Mecanismos que observan y miden el estado — tanto del atleta como del propio sistema.

### Sensores biométricos (sobre el atleta)

#### Snapshot proactivo de 48h
**Función:** `TrainerAgent.collect_startup_snapshot_48h()` / `build_startup_status_markdown()`

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
**Función:** series temporales en `load_metrics.series` (Supabase)

Sensor continuo de carga y fatiga con EWMA:
- Tau ajustados por deporte.
- Percentiles individualizados por atleta.
- Persistencia de hasta 120 días.
- Integrado en el snapshot proactivo con regla aplicada de actuación.

#### Tendencia junto al valor puntual
**Definido en:** `prompts/system_prompt.md`

Regla sensor: para HRV, body battery, sueño, FC en reposo y VO2max, reportar siempre: valor hoy + media 7d + dirección de tendencia. Nunca un valor aislado.

#### Transparencia de datos
**Definido en:** `prompts/system_prompt.md`

Regla: declarar N (tamaño de muestra) y calidad del dato en toda afirmación sobre tendencias.

### Sensores del sistema (sobre el agente)

#### Suite de tests (estado validado en CI y local)
**Directorio:** `tests/`

Verifica que el harness no se rompe:
- `test_trainer_agent.py` — funciones puras del agente.
- `test_storage.py` — capa de persistencia.
- `test_main.py` — flujo principal.

Toda la capa de Supabase y Garmin está mockeada — no se realizan conexiones reales en ningún test.

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
       ├─ [hook: snapshot proactivo] build_startup_status_markdown()
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

- `plan_status`
- `week_tss`
- `running_threshold`
- `mcp_factual`
- `daily_readiness`
- `planning`
- `personal_records`

Se puede desactivar con `KAIROS_DETERMINISTIC_ROUTER=false` para volver al comportamiento basado solo en lógica existente/LLM.

### Hook de CI (GitHub Actions)
Se dispara en `push` y `pull_request`. Actúa como hook de calidad del código fuente.

---

## Gaps identificados

Elementos de harness engineering que actualmente faltan en el proyecto:

| Gap | Descripción | TODO relacionado |
|---|---|---|
| Git hooks locales | No hay `.githooks/` — no hay validaciones pre-commit | #22 |
| Logging de producción | Sin niveles configurables, los `print` son ciegos en producción | #4 |
| Sensor de spike semanal >20% | Regla no implementada como sensor activo en runtime | #27 |

---

## Estado del harness por capa

```
┌─────────────────────────────────────────────────────┐
│                   GUIDES                            │
│  mcp_tool_routing_guide.md  ✓ operativo             │
│  system_prompt.md           ✓ operativo             │
│  system_prompt_compact.md   ✓ operativo             │
├─────────────────────────────────────────────────────┤
│                   SENSORS                           │
│  snapshot proactivo 48h     ✓ operativo             │
│  anomalías biométricas      ✓ en prompting          │
│  TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma) EWMA       ✓ operativo             │
│  guardrail read-only        ✓ operativo             │
│  suite de tests             ✓ operativo             │
│  CI GitHub Actions          ✓ operativo             │
│  spike semanal >20%         ✗ pendiente (#27)       │
│  logging por niveles        ✗ pendiente (#4)        │
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
