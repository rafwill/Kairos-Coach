# 🏃‍♂️ Kairos Coach

**Kairos Coach** es un entrenador deportivo personal con IA que corre en tu terminal. No es un chatbot genérico: tiene acceso en tiempo real a todos tus datos de Garmin Connect, te conoce por tu perfil, recuerda lo que habéis hablado en sesiones anteriores, **calcula métricas de rendimiento en Python antes de pasárselas al LLM**, y guarda tu plan de entrenamiento en base de datos con versionado completo.

La conversación con el coach tiene contexto real porque los números son reales. Antes de responder cualquier pregunta sobre estado, rendimiento o recomendaciones, el sistema consulta tus datos de Garmin. Nunca inventa, nunca generaliza.

---

## 🎯 Qué hace concretamente

#### 1. Habla contigo como un entrenador real, no como un chatbot genérico
Antes de responder cualquier cosa, el agente consulta tus datos reales de Garmin: cómo dormiste anoche, tu HRV de hoy, tu body battery, el estrés acumulado. Solo entonces da una recomendación. No inventa datos ni generaliza. Si Garmin no tiene datos para esa fecha, lo dice.

#### 2. Pre-computa en Python — el LLM solo interpreta
Cuando analizas una actividad, el sistema calcula en Python antes de llamar al LLM:
- Duraciones de segundos a HH:MM:SS
- Ritmo en min/km desde distancia y duración
- Distribución de tiempo en zonas de FC (Z1–Z5) usando **datos reales de Garmin Connect** (`get_activity_hr_in_timezones`), con cascada de 3 estrategias de fetch. Cada zona muestra nombre en español, rango de FC en bpm y porcentaje sobre la duración total de la actividad (no sobre la suma de tiempos por zona)
- Hidratación recomendada según duración y tipo de actividad
- Carga de entrenamiento (TSS) y efecto aeróbico/anaeróbico
- Body battery del día (extracción compacta antes del truncado para evitar pérdida de datos)
- Sueño de la noche previa (fecha exacta con fallback automático, fases y puntuación)
- HRV nocturno como indicador del estado del sistema nervioso autónomo

El LLM recibe un bloque `=== RESUMEN DE ACTIVIDAD ===` ya calculado y se dedica exclusivamente a interpretar y hacer coaching. Nunca hace aritmética.

#### 3. Modelo de carga/fatiga propio (tipo TrainingPeaks)
En cada arranque de sesión, el sistema calcula automáticamente:
- **TSS** (Training Stress Score): carga por sesión
- **ATL** (Fatiga, τ 7–8 días según deporte): cuánto has acumulado a corto plazo
- **CTL** (Estado físico, τ 42–45 días): tu nivel de forma construido en semanas
- **TSB** (Forma = CTL − ATL): disponibilidad real para entrenar hoy

Los tau y percentiles se ajustan automáticamente al deporte principal (running, trail, ciclismo, triatlón). Los rangos son **individualizados** a tus propios datos históricos, no umbrales genéricos. La serie completa (hasta 120 días) se persiste en Supabase. Con las herramientas `kairos_load_trends` y `kairos_correlate`, el agente puede calcular correlaciones estadísticas y tendencias directamente sobre esa serie.

#### 4. Planes de entrenamiento versionados y persistidos
Puedes pedirle que te cree un plan de entrenamiento y lo guarda en Supabase (`training_plan`, `training_plan_session`, `training_plan_version`). Cada edición genera un snapshot de versión. El plan incluye sesiones estructuradas con calentamiento, parte principal en RPE, enfriamiento, hidratación y notas específicas. Si eres corredor de trail, las sesiones se adaptan con contenido específico de montaña. El coach sabe en todo momento si tienes plan activo o no, sin depender del LLM para esa decisión.

Desde la versión actual, la generación del plan se hace con motor determinista multi-semana:
- Periodización por fases `base -> build -> peak -> taper`.
- Progresión de carga semanal con descarga/taper explícitos.
- Variación determinista de sesiones de calidad (evita plantillas planas repetidas).
- Soporte multideporte por preferencia del atleta (running/trail/ciclismo/fuerza).
- Validación estricta antes de guardar (estructura semanal, separación de calidad, variedad y coherencia de carga).
- Planificación por restricciones del atleta (general, no acoplada a una patología concreta): días entrenables/no entrenables, límites de minutos por día, mínimo de descanso semanal y tope de sesiones de calidad.
- El ajuste diario del plan también respeta esas restricciones: si hoy es día no entrenable o supera cap diario, el motor propone descanso o reducción automática.

#### 5. Estado proactivo al arrancar (sin que preguntes nada)
Cada vez que inicias el agente, recibe automáticamente:
- Body battery de hoy y ayer
- HRV de hoy y ayer
- Calidad del sueño de anoche
- Resumen de carga/fatiga (TSS · CTL (Estado físico) · ATL (Fatiga) · TSB (Forma) · semana + regla aplicada)
- Entrenamientos de las últimas 48h
- Si tienes plan activo: propuesta de adaptar la sesión de hoy
- Señal preventiva de **spike semanal >20%** (semana actual vs. semana previa), incluso si el TSB aún no cruzó umbral

Esto funciona como el briefing que te daría un entrenador de élite antes de que hagas la primera pregunta.

#### 10. Harness explícito en runtime (hooks + router)
El runtime del agente ya expone capa de harness explícita en `TrainerAgent.chat()`:
- `HookManager` con eventos `before_message`, `after_message`, `before_tool_call`, `after_tool_call`, `on_error`
- `ToolRouter` determinista opcional para intenciones críticas (`plan_status`, `week_tss`, `week_activities`, `hr_threshold`, `running_threshold`, `activity_details`, `mcp_factual`, `daily_readiness`, `planning`, `personal_records`, `config_options`)
- Flag de control: `KAIROS_DETERMINISTIC_ROUTER=true|false`

Nota de diseño actual:
- Las consultas de propuesta de entrenamiento para mañana pasan por LLM (no ruta determinista).
- Las consultas factuales de detalle por día (por ejemplo, "entrenamiento de ayer" o "entrenamiento del martes") se resuelven por ruta determinista MCP-first para evitar latencia innecesaria del LLM.
- Si el LLM responde de forma genérica pidiendo más información, Kairos lanza un segundo intento con contexto proactivo (48h) para devolver una propuesta útil y concreta.
- En consultas mixtas (dato + recomendación), Kairos responde en dos fases: bloque factual determinista primero y coaching por LLM después. Si el LLM falla o agota timeout, la salida lo indica explícitamente y no inventa recomendación determinista.

La documentación de harness se mantiene en `docs/Harness.md`.

#### 6. Memoria entre sesiones
Durante la sesión, el agente guarda checkpoints ligeros del resumen (upsert por día) sin depender de red. Conserva hasta 10 resúmenes persistidos y, al arrancar la siguiente sesión, inyecta los 3 más recientes como contexto. El coach recuerda que hace tres días hablasteis de la fascitis plantar, o que lleváis dos semanas trabajando el umbral.

#### 7. Sistema multiusuario con autenticación
Varias personas pueden usar la misma instalación. Cada usuario tiene perfil, historial, plan y base de conocimiento separados. La contraseña se almacena cifrada con Fernet AES-128 + HMAC-SHA256. El auto-login evita escribir la contraseña en cada sesión.

#### 8. 6 proveedores de LLM seleccionables en caliente
Gemini, Groq, Mistral, Cerebras, NVIDIA NIM y GitHub Models (VPN). La detección de red es automática. Se puede cambiar de modelo durante la sesión con `/modelo` sin perder el hilo de la conversación.

#### 9. Protocolo médico DT1
Si el perfil incluye Diabetes Tipo 1, el agente aplica protocolos de seguridad glucémica en cada recomendación: diferencia entre ejercicio aeróbico (baja glucemia) e intenso (puede subirla), vigila HRV y body battery como posibles indicadores de hipoglucemia nocturna, y nunca invade competencias médicas.

---

## 📐 ¿Cómo consigue Kairos hacer esto?

La respuesta es una arquitectura en tres capas donde **los datos siempre van por delante del LLM**:

```
┌─────────────────────────────────────────────────────────────────┐
│  CLI (main.py)                                                  │
│  Terminal interactivo · Rich UI · Multiusuario · Login          │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│  TrainerAgent (trainer_agent.py)                                │
│  Agente LLM · 6 proveedores · Tool calling · Pre-cómputo        │
│  Rutas deterministas · Memoria · RAG · TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)          │
│  Tools internas (kairos_*) · suite de tests                     │
└──────────┬────────────────────────────────┬─────────────────────┘
           │                                │
┌──────────▼──────────┐         ┌──────────▼──────────────────────┐
│  Garmin MCP         │         │  Supabase (storage.py)          │
│  hasta 126 tools    │         │  7 tablas · Multiusuario        │
│  datos en tiempo    │         │  Perfil · Plan · Sesiones       │
│  real vía stdio     │         │  TSS series · Knowledge         │
└─────────────────────┘         └─────────────────────────────────┘
```

**Capa de datos (sistema):** conecta con Garmin Connect para obtener señales base (actividades, FC, sueño, HRV, body battery, etc.) y, sobre esos datos, pre-procesa y calcula en Python las métricas derivadas antes de entregárselas al LLM (ritmo en min/km, zonas FC reales con nombre y rango en bpm, hidratación estimada, TSS por sesión y serie CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)).

**Capa de coaching (LLM):** recibe datos ya calculados y aporta interpretación, contextualización con el perfil del atleta y recomendaciones accionables. Nunca hace aritmética.

## 🗄️ Qué guarda Kairos en la BBDD (Supabase)

Kairos no guarda solo chat: persiste estado operativo completo por usuario para que el coaching sea continuo y trazable.

- **`app_user`**
  - Identidad del usuario de aplicación (`id`, `username`)
  - `password_hash` (PBKDF2)
  - `credentials` auxiliares (incluye password Garmin cifrada con Fernet cuando aplica)

- **`user_profile`**
  - Perfil completo del atleta en JSON (`data`)
  - Incluye objetivos (`goals`), plan activo en espejo (`training_plan`), rendimiento (`performance`) y bloque de carga/fatiga (`load_metrics`)
  - También se persisten trazas recientes de inferencia de running (`running_session_inference`) para auditoría/calibración

- **`session_context`**
  - Historial reciente de conversación (`history`, limitado)
  - Resúmenes de sesión (`session_summaries`)

- **`athlete_knowledge`**
  - Base de conocimiento textual del atleta (`content`) para contexto RAG

- **`gemini_usage`**
  - Consumo diario de tokens por usuario/clave (`tokens`)
  - Estado de cuota agotada (`quota_exhausted`)

- **`training_plan`**
  - Cabecera del plan (título, objetivo, dificultad, duración, estado, fuente)
  - `plan_data` en JSON para estructura extendida

- **`training_plan_session`**
  - Sesiones del plan por semana/día
  - Tipo de sesión, duración, intensidad, ejercicios, notas y `structured_workout` (JSON estructurado)

- **`training_plan_version`**
  - Snapshot versionado de cada cambio del plan
  - `version_number` + `change_reason` para trazabilidad

- **`load_metrics_daily`**
  - Serie diaria de carga/fatiga por usuario: `TSS`, `ATL`, `CTL`, `TSB`, `activities_count`
  - Base para análisis histórico y cálculo incremental

**Importante:** hoy Kairos no persiste una tabla propia con todas las actividades Garmin crudas. La persistencia principal está centrada en perfil, contexto, planes y métricas derivadas.

---

## ✨ Características clave

* **🧠 Seis proveedores de IA:**
  | # | Opción | Modelo | Límite gratuito | Requiere |
  |---|--------|--------|-----------------|----------|
  | 1 | **Google Gemini** | `gemini-2.0-flash` | ~1M tokens/día | API key gratuita |
  | 2 | **Mistral** | `mistral-small-latest` | ~1B tokens/mes | API key gratuita |
  | 3 | **Groq** | `llama-3.3-70b-versatile` | 100k tokens/día | API key gratuita |
  | 4 | **Cerebras** | `llama-3.3-70b` | generoso | API key gratuita |
  | 5 | **NVIDIA NIM** | `meta/llama-3.1-70b-instruct` | generoso | API key gratuita |
  | 6 | **GitHub Models** | `gpt-4o-mini` | — | GitHub token + VPN |

  La red se **detecta automáticamente** y te despliega un **menú interactivo** para que selecciones el modelo que quieras usar (también dentro de VPN corporativa con Zscaler, incluyendo GitHub Models si está configurado). Además te permite **cambiar de modelo en caliente** en cualquier momento del chat con el comando `/modelo`.

* **🪵 Logging y compatibilidad Windows:**
  - El agente escribe logs en `agent.log` con timestamps y nivel de severidad.
  - Verbosidad y destino configurables por entorno: `KAIROS_LOG_LEVEL`, `KAIROS_LOG_FILE`, `KAIROS_LOG_STDOUT`.
  - Trazas debug en consola interactiva controladas por `KAIROS_DEBUG_CONSOLE` (por defecto: `false`).
  - En Windows, la salida de consola se fuerza a UTF-8 para evitar errores de Unicode.

* **⌚ Herramientas de Garmin Connect:**
  - Actividades, zonas FC, splits, progreso, récords personales
  - Salud diaria: frecuencia cardíaca, body battery, estrés, pasos, respiración, SPO2
  - Métricas avanzadas: HRV, VO2Max, predicciones de carrera, umbral de lactato, puntuación de resistencia, edad de fitness
  - Sueño, composición corporal, hidratación, perfil de usuario y objetivos

* **🔒 Política MCP solo consulta (modo coach):**
  - Por defecto, el agente opera con `MCP_READ_ONLY=true`.
  - Las tools MCP de escritura se filtran y bloquean en runtime (`create_`, `update_`, `delete_`, `schedule_`, `upload_`, `add_`, `set_`).
  - El MCP aporta datos; la planificación y recomendaciones las realiza el coach (LLM).
  - El prompting incluye checklist MCP mínimo por intención (estado diario, ajuste de sesión, planificación/ajuste de plan, dolor/sobrecarga y máximos/mínimos) para reducir respuestas genéricas.
  - Solo para mantenimiento/admin se puede desactivar con `MCP_READ_ONLY=false`.

* **👤 Perfil de usuario sincronizado:**
  - Al arrancar, sincroniza automáticamente género, peso, altura y edad desde Garmin Connect.
  - Detecta y reporta cambios de perfil Garmin al inicio de sesión (si los hay).
  - Setup guiado la primera vez: deporte principal, horas/semana, próximo evento, tiempo objetivo y condiciones de salud.
  - Todos los campos del perfil se inyectan en el system prompt para que el agente te conozca desde el primer mensaje.
  - El perfil se mantiene por usuario de aplicación (multiusuario) y no se reinicia automáticamente por cambio de cuenta Garmin.
  - El perfil diferencia entre:
    - `goals`: objetivo deportivo (carrera, fecha, tiempo, horas/semana).
    - `training_plan`: plan activo para el día a día (separado del objetivo).
  - Comandos de parámetros de carga:
    - `/perfil umbral <mm:ss>` para actualizar ritmo umbral de running.
    - `/perfil fc <reposo> <max>` para actualizar FC de reposo y FC máxima (ej: `/perfil fc 48 186`).

* **📚 Base de conocimiento del atleta (RAG ligero):**
  - Puedes añadir notas personales del atleta en ficheros `.md`, `.txt` o `.json`.
  - En onboarding de usuario nuevo, se genera y persiste una base inicial enriquecida con perfil + datos MCP de arranque.
  - En cada consulta, el agente recupera los fragmentos más relevantes y los combina con el perfil Garmin y los datos en tiempo real de herramientas.
  - Si no defines rutas, intenta cargar automáticamente:
    - `memory/athlete_knowledge.md`
    - `memory/athlete_knowledge.txt`
    - `memory/athlete_knowledge.json`
  - Memoria de sesión optimizada para cierre rápido:
    - Tras cada respuesta del coach, se guarda un checkpoint ligero del resumen de sesión (upsert por día en BBDD).
    - Al salir, no se bloquea el cierre con un resumen final dependiente del LLM.

* **� Cuantificación de carga y fatiga (TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)):**
  - Al arrancar la sesión, el sistema calcula automáticamente el modelo de carga inspirado en TrainingPeaks:
    - **TSS** (Training Stress Score): carga por sesión y acumulada diaria.
    - **ATL** (fatiga aguda, ventana 7 días por defecto): cuánto estás acumulando a corto plazo.
    - **CTL** (fitness crónico, ventana 42 días por defecto): tu nivel de forma construido en semanas/meses.
    - **TSB** (forma = CTL − ATL): disponibilidad real para entrenar hoy.
  - En usuario nuevo, antes del primer briefing se fuerza un backfill completo desde Garmin para poblar la serie histórica en DB (hasta 120 días) y evitar arrancar con métricas vacías.
  - Los **tau** (constantes de tiempo) y **percentiles** se ajustan automáticamente al deporte principal del perfil:
    | Deporte | ATL tau | CTL tau | Percentiles TSB/ATL |
    |---------|--------:|--------:|---------------------|
    | Running | 7 días | 42 días | estándar |
    | Trail running | 8 días | 42 días | más amplios (sesiones largas) |
    | Ciclismo | 7 días | 45 días | estándar |
    | Triatlón | 7 días | 45 días | más amplios |
  - Los parámetros se pueden **sobreescribir manualmente** en `profile.load_metrics.model`.
  - Genera **rangos individualizados** por atleta usando percentiles de sus propios datos históricos (no umbrales genéricos).
  - **Reglas de actuación automáticas** visibles en el estado proactivo:
    - 🟠 Fatiga alta (TSB por debajo del rango individual) → reduce intensidad/volumen.
    - 🟢 Buena disponibilidad (TSB en rango) → permite calidad o progresión controlada.
    - 🔴 Sobrecarga sostenida → activa descarga y recomendaciones preventivas de lesión.
    - ⚠️ Spike semanal >20% vs semana previa → advertencia activa y reducción temporal (15-25%) de carga aunque TSB no haya cruzado umbral.
  - La serie temporal completa (hasta 120 días) se persiste en el perfil del atleta en Supabase para análisis de tendencias.
  - El bloque de carga/fatiga se incluye en el estado proactivo de arranque con resumen operativo (TSS·CTL (Estado físico)·ATL (Fatiga)·TSB (Forma)·semana) y la regla aplicada.
  - Política de inmutabilidad histórica por parámetros:
    - Si cambias parámetros de cálculo (umbral, FTP o FC), Kairos no recalcula días anteriores a la fecha de cambio.
    - Desde la fecha efectiva del cambio en adelante, los nuevos cálculos usan los nuevos parámetros.
    - Si el FTP se refresca y el valor no cambia, no se actualiza la fecha efectiva para evitar recálculos innecesarios.

* **�🚦 Estado proactivo al iniciar (48h):**
  - Tras seleccionar modelo y conectar herramientas, muestra un briefing automático de últimas 48h.
  - Incluye estado de Body Battery, HRV, sueño y entrenamientos recientes.
  - Muestra fechas analizadas en formato `DD/MM/AAAA`.
  - Si detecta cambio de fórmula de carga (`formula_version`), informa al usuario de recálculo completo y posible latencia mayor al arranque.
  - Tras calcular carga, informa si el cálculo fue incremental o recálculo completo.
  - Recomendación inicial condicional:
    - Sin `training_plan` activo: `No tienes plan asignado. ¿Qué quieres hacer hoy?`
    - Con `training_plan` activo: propone adaptar la sesión de hoy al plan.
  - Sirve como punto de partida antes de la primera pregunta del chat.

* **⚡ Cierre de sesión rápido:**
  - El cierre ya no depende de una llamada final al proveedor LLM para resumir la sesión.
  - Si el proveedor externo está lento o devuelve timeouts, la salida no queda retenida por ese paso.
  - La memoria del día ya va quedando persistida durante la conversación mediante checkpoints incrementales.

* **🧭 Estado de plan coherente (sin alucinaciones):**
  - Preguntas tipo "¿tengo plan?", "¿cuál es ese plan?" o "¿sigo con el plan?" se responden por ruta determinista.
  - La respuesta se basa en `training_plan` real en base de datos (no en inferencias del LLM).
  - `goals` se muestra como objetivo guardado, pero no se interpreta como plan activo.

* **📅 Consultas semanales de carga (deterministas):**
  - Preguntas factuales como "Dime los TSS de esta semana y en qué actividades los he hecho" se resuelven por ruta determinista.
  - La semana se calcula como semana natural (lunes → domingo), con corte en hoy si es semana actual.
  - También soporta semanas históricas explícitas (`semana del 10 de agosto de 2026`).
  - Soporta fechas cortas de entrada (`dd/mm/yy`, por ejemplo `17/08/26`).
  - El detalle de actividades (tipo/nombre) se toma de Garmin como fuente real y se devuelve sin inferencias del LLM.

* **🧩 Formato de salida unificado (prompt + rutas deterministas):**
  - Kairos usa una plantilla única de 4 secciones en respuestas de coaching y factuales:
    - `## 🧭 Resumen`
    - `## 📊 Métricas clave`
    - `## ✅ Recomendación`
    - `## 🎯 Próximo paso`
  - Si una sección no aplica, se mantiene con `No aplica en esta consulta.`
  - Las métricas numéricas se muestran en tabla Markdown (`Métrica | Valor | Fuente`) para consistencia en terminal, Telegram y email.

* **🗂️ Planes de entrenamiento versionados (DB-first):**
  - Los planes se guardan en tablas dedicadas de Supabase (`training_plan`, `training_plan_session`, `training_plan_version`).
  - Cada edición del plan genera una nueva versión (snapshot) para trazabilidad.
  - Generación/ajuste funcional de planes por ruta determinista en runtime (sin depender del LLM para persistir/activar).
  - Motor de periodización por fases (`base/build/peak/taper`) con progresión y taper explícitos.
  - Distribución del microciclo por restricciones reales del perfil (`availability`): días disponibles, días bloqueados y límites de minutos por día.
  - Reglas generales de salud por impacto funcional (`none/low/moderate/high`) para modular volumen e intensidad sin hardcode por enfermedad.
  - Variación de sesiones de calidad y mezcla multideporte según contexto del atleta.
  - Validación previa de coherencia (duración, sesiones, estructura semanal, carga, disponibilidad y rangos de día) antes de guardar.
  - Cada sesión persiste también `structured_workout` (contrato `kairos-workout-v1`) con fallback legacy si la columna aún no existe.
  - Resumen de cambios entre versiones (duración, dificultad, sesiones y volumen semanal) visible en la respuesta del coach.
  - Existe una única fuente de verdad de plan activo por usuario (máximo uno activo a la vez).
  - Compatibilidad backward: el perfil mantiene `training_plan` como espejo temporal para rutas legacy.

* **🥇 Récords personales de running (mejorado):**
  - Consulta directa de PRs desde Garmin con `get_personal_record`.
  - Respuesta en tabla con distancia/record y marca desde la primera interacción.
  - Categorías traducidas al español para facilitar lectura.
  - Follow-up contextual soportado (ej: "en qué distancias son esas marcas") sin perder acceso a datos.
  - Filtrado priorizando registros de running para evitar mezclar ciclismo/natación en esa consulta.

* **🚴 Récords por deporte (running/ciclismo):**
  - Si el usuario pregunta por ciclismo, solo se muestran marcas de ciclismo.
  - Si pregunta por running, solo se muestran marcas de running.
  - Nunca se mezclan disciplinas en la misma respuesta salvo petición explícita.

* **✅ Validación de inputs:**
  - `target_race_date`: formato `YYYY-MM-DD` + debe ser fecha futura.
  - `target_time`: formato `H:MM:SS` / `HH:MM:SS` con rangos de minutos/segundos.
  - `weekly_training_hours`: número entre 0.5 y 40, acepta coma o punto decimal.
  - Bucle de reintento con mensaje de error en color hasta que el valor sea válido.

* **🔐 Auto-login con contraseña cifrada:**
  - Al arrancar, solo se pide el nombre de usuario. Si ya existe, accede **automáticamente** sin volver a pedir contraseña.
  - La contraseña se almacena cifrada (Fernet AES-128 + HMAC-SHA256) en Supabase — nunca en texto claro.
  - Si la contraseña de Garmin Connect cambia, el sistema lo detecta y ofrece un flujo de actualización sin perder la sesión.
  - La política de seguridad es: contraseña de la app = contraseña de Garmin Connect (una sola contraseña para todo).

* **📊 Análisis profundo de actividades por fecha:**
  - Pregunta directamente: *"Analiza mi competición del 2 de julio"* y el agente localiza la actividad automáticamente.
  - Pre-fetch enriquecido: antes de llamar al LLM, el sistema carga actividad + zonas FC reales (cascada `get_activity_hr_in_timezones`) + body battery (extracción compacta) + sueño (fecha exacta con fallback) + HRV (compact handler con mapeo correcto de campos `lastNightAvg`/`weeklyAvg`/`status`) + carga de entrenamiento.
  - Todos los cálculos se realizan **en Python**: zonas FC Z1–Z5 con nombre en español, rango de FC en bpm y % sobre la duración total; ritmo en min/km; hidratación estimada; efecto de entrenamiento; sueño con fases; HRV. El LLM solo interpreta.
  - El análisis post-actividad genera **6 secciones**:
    1. **Resumen ejecutivo** — tipo de deporte como primer dato, luego duración, distancia, ritmo, FC, desnivel, calorías, TSS
    2. **Distribución por zonas de FC** — nombre español · rango bpm · % (sin barras gráficas)
    3. **Efecto de entrenamiento y carga** — Training Effect, carga de sesión, minutos de alta/moderada intensidad
    4. **Hidratación recomendada** — estimación basada en duración y temperatura
    5. **Estado pre-carrera** — body battery (valor numérico + interpretación), sueño con fases (valor + interpretación), HRV nocturno (valor + interpretación del SNA)
    6. **Bloque final contextual por recencia**:
       - Actividad reciente: **Recuperación y próximas sesiones** (horizonte corto, con recomendaciones operativas).
       - Actividad histórica: **Aprendizajes para futuras sesiones similares** (sin pautas de "mañana" o "en 2-3 días").

* **💾 Memoria persistente entre sesiones:**
  - Durante la conversación, el agente guarda checkpoints incrementales del resumen sin llamadas al LLM.
  - Al salir, persiste un checkpoint ligero final (sin bloquear el cierre por timeouts de proveedor).
  - Conserva hasta 10 resúmenes persistidos e inyecta los últimos 3 como contexto al arrancar la siguiente sesión — el agente recuerda lo que habéis hablado.
  - Todo el estado de usuario (perfil, historial, base de conocimiento y cuota de Gemini) se guarda en Supabase por usuario.
  - Si el agente crea una planificación base por fallback, persiste un `training_plan` activo mínimo para distinguirlo del objetivo (`goals`).

* **👥 Modo multiusuario (nuevo):**
  - Inicio con `login` o alta de `usuario nuevo` desde terminal.
  - Cada usuario tiene su propio perfil, objetivos, contexto, base de conocimiento y claves en BBDD.
  - En usuarios nuevos, el onboarding conecta con Garmin, sincroniza biometría y crea base de conocimiento inicial.

* **🔧 Sin dependencias de Node.js:**
  - El servidor MCP es 100% Python, lanzado localmente como `agent.kairos_mcp_server`.

* **📊 Motor de análisis histórico (herramientas internas `kairos_*`):**
  - Tres herramientas Python puras que el LLM puede invocar directamente, sin llamadas extra al MCP:
    - `kairos_load_trends`: devuelve la serie temporal de TSS, ATL, CTL o TSB con granularidad diaria y semanal. Cubre preguntas como *"¿cómo ha evolucionado mi forma en los últimos 2 meses?"*
    - `kairos_correlate`: calcula la correlación de Pearson entre dos métricas de carga/fatiga (N, r, intensidad e interpretación). Cubre preguntas como *"¿correlaciona mi TSS semanal con mi TSB?"*
    - `kairos_weekly_sport_breakdown`: agrega actividades Garmin por deporte en N semanas — sesiones, horas y km por disciplina. Para multideportistas y triatletas.
  - Fuente: `load_metrics.series` persistido en Supabase (ya calculado al arrancar) + Garmin MCP live para actividades.

* **🧠 Inteligencia de prompting mejorada:**
  El system prompt incorpora reglas de análisis aprendidas del estado del arte:
  - **Relaciones > valores aislados**: nunca reporta un valor puntual sin cruzarlo con otra métrica relacionada (HRV + sueño + body battery como composite de recuperación).
  - **Tendencia + valor puntual**: para HRV, body battery, sueño, FC en reposo y VO₂máx, siempre muestra valor de hoy + media 7d + dirección. Ejemplo: *"HRV hoy: 42ms (media 7d: 48ms → tendencia descendente)"*
  - **Detección de anomalías biométricas**: detecta y reporta flags independientes de la carga (FC reposo elevada sin carga, sueño malo ≥2 noches, HRV ≥15% bajo media 7d, body battery crónicamente <30).
  - **Transparencia de datos**: si el dato es N=1 o ruidoso, lo indica explícitamente antes de interpretar.
  - **Protocolo plan activo ↔ datos del día**: antes del entreno, cruza readiness/TSB con la sesión planificada (✅/🔴/🟠/🟡); después, compara ejecutado vs planificado con análisis de desviación.
  - **Historial profundo para planes**: al generar un plan, analiza las últimas 8–12 semanas de actividades reales para calibrar el nivel de partida real del atleta.
  - **Race Readiness**: si hay carrera objetivo, monitoriza progresión del largo, desnivel semanal y volumen vs. demanda de la carrera.
  - **Revisión post-sesión**: cuando el usuario comparte una actividad sin pedir análisis profundo, el coach da una nota estructurada rápida (qué fue bien / qué desvió / un ajuste).

* **✅ CI/CD con GitHub Actions:**
  - `.github/workflows/tests.yml` ejecuta la suite completa de pytest en cada push y pull request.
  - Sin credenciales reales: los tests mockean toda la capa de Supabase y Garmin.

---

## 🛠️ Requisitos previos

| Requisito | Versión mínima | Notas |
|-----------|---------------|-------|
| **Git** | cualquiera | Para clonar el repositorio |
| **Python** | 3.10+ | Recomendado 3.12 o 3.13 |
| **Cuenta Garmin Connect** | — | Para consultar datos reales |
| **Supabase** | — | Obligatorio en arquitectura DB-first |
| **Una API key LLM** | — | Gemini, Groq, Mistral, Cerebras, NVIDIA o GitHub Models |
| **uv / uvx** | opcional | No requerido para runtime MCP propio |

### Instalar uv (recomendado)
```powershell
pip install uv
```

---

## 🚀 Instalación y Configuración (desde cero)

### Camino recomendado para principiantes (Windows)

1. Clona el repositorio:
```powershell
git clone https://github.com/rafwill/Kairos-Coach.git
cd Kairos-Coach
```

2. Ejecuta setup automático:
```powershell
./setup.ps1
```

Si PowerShell bloquea scripts por políticas de ejecución, usa:
```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

3. Abre `.env` y completa:
- `GARMIN_EMAIL`
- `GARMIN_PASSWORD`
- una API key (por ejemplo `GEMINI_API_KEY`)
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`

4. Ejecuta esquema de Supabase:
- abre [`supabase/schema.sql`](supabase/schema.sql)
- pégalo en SQL Editor de Supabase y pulsa Run

5. (Opcional) pre-autentica Garmin con un primer arranque de Kairos y guarda sesión.

6. Arranca Kairos:
```powershell
.venv\Scripts\python.exe -m agent.main
```

### Camino recomendado para Unix/macOS

```bash
git clone https://github.com/rafwill/Kairos-Coach.git
cd Kairos-Coach
chmod +x setup.sh
./setup.sh
```

Después completa `.env`, configura Supabase con `supabase/schema.sql`, y arranca:
```bash
.venv/bin/python -m agent.main
```

### ¿Qué hace el setup automatizado?

- Crea `.venv`
- Instala `requirements.txt` y `requirements-dev.txt`
- Genera `.env` desde `.env.example` si no existe
- Genera `ENCRYPTION_KEY` automáticamente si falta en `.env`

Modo rápido opcional (sin dependencias dev):
- Windows: `./setup.ps1 -SkipDev`
- Unix/macOS: `SKIP_DEV=1 ./setup.sh`

### Uso de Makefile (opcional)

Si tienes `make` instalado:

```bash
make setup      # Unix/macOS
make setup-win  # Windows (PowerShell)
make serve      # arranca Kairos
make test       # pytest -q
make lint       # ruff check agent tests tools
```

En Windows, si no tienes `make`, usa directamente `setup.ps1` y `.venv\Scripts\python.exe`.

### Windows: error "make no se reconoce"

Si en PowerShell aparece este error:

```powershell
make : El término 'make' no se reconoce como nombre de un cmdlet...
```

No es un problema de Kairos: significa que `make` no está instalado en tu Windows.

Tienes dos opciones:

1. Sin instalar `make` (recomendado para empezar rápido):
```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
.\.venv\Scripts\python.exe -m agent.main
```

2. Instalar `make` con Chocolatey (si lo tienes disponible):
```powershell
choco install make -y
make --version
make login
```

### Problemas frecuentes (Windows)

1. Error: `running scripts is disabled on this system`

Solución (solo para ejecutar setup una vez):
```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

2. Error: `python is not recognized`

Solución:
- Instala Python 3.10+ desde https://www.python.org/downloads/
- Marca la opción **Add Python to PATH** durante la instalación
- Cierra y abre una nueva terminal
- Verifica con:
```powershell
python --version
```

3. Error: problemas de autenticación Garmin en primer arranque

Solución:
- Verifica `GARMIN_EMAIL` y `GARMIN_PASSWORD` en `.env`.
- Reintenta arranque con `.venv\Scripts\python.exe -m agent.main`.

### Configuración de `.env` (detalle)

Si prefieres hacerlo manual:

Windows:
```powershell
Copy-Item .env.example .env
```

Unix/macOS:
```bash
cp .env.example .env
```

Campos mínimos obligatorios en `.env`:
- `GARMIN_EMAIL` y `GARMIN_PASSWORD`
- una API key LLM (`GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `CEREBRAS_API_KEY`, `NVIDIA_API_KEY` o `GITHUB_TOKEN`)
- `SUPABASE_URL` y `SUPABASE_ANON_KEY`
- `ENCRYPTION_KEY` (si no la generó setup)

Generación manual de clave:
```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Base de conocimiento del atleta (opcional)

Puedes añadir rutas en `.env`:

```dotenv
ATHLETE_KB_PATHS=memory/athlete_knowledge.md,memory/pda_strategy.json
```

Si no defines `ATHLETE_KB_PATHS`, Kairos intenta cargar automáticamente los ficheros por defecto en `memory/`.

### Supabase (obligatorio)

El modo actual del agente es DB-first multiusuario: sin Supabase no arranca.

1. Crea proyecto en [supabase.com](https://supabase.com)
2. Ejecuta [`supabase/schema.sql`](supabase/schema.sql)
3. Copia URL y anon key a `.env`

---

## 🏃‍♂️ Uso

Windows:
```powershell
.venv\Scripts\python.exe -m agent.main
```

Unix/macOS:
```bash
.venv/bin/python -m agent.main
```

El agente iniciará el MCP propio local de Kairos (`agent.kairos_mcp_server`) y después aparecerá el menú de proveedores:

Selección de backend MCP (transición a MCP propio):

- `MCP_BACKEND=frozen` (default): usa el launcher local `tools/garmin-mcp-frozen.*`.
- `KAIROS_MCP_FROZEN_COMMAND`: permite indicar un binario frozen específico.

Runbook operativo de backend MCP propio: `docs/mcp-frozen-runbook.md`.

```
  1 · GitHub Models (gpt-4o-mini)           — dentro de VPN
  2 · Groq         (llama-3.3-70b)          — 100k tokens/día
  3 · Google Gemini (gemini-2.0-flash)      — ~1M tokens/día gratis
  4 · Mistral      (mistral-small)          — gratis · function calling nativo  ← recomendado
  5 · Cerebras     (llama-3.3-70b)          — ultrarrápido · gratis
  6 · NVIDIA NIM   (llama3-70b-instruct)    — gratis · API compatible OpenAI
```

A continuación se selecciona el modo de herramientas y el agente conecta con Garmin Connect.

### Comandos disponibles en el chat

| Comando | Descripción |
|---------|-------------|
| `/ayuda` · `/help` · `/?` | Muestra ejemplos de preguntas, todos los comandos y guía de indicadores |
| `/perfil` | Muestra el perfil actual (datos personales + objetivos + salud) |
| `/modelo` · `/model` | Muestra estadísticas de tokens y te permite **cambiar de modelo de IA en caliente** sin perder la sesión |
| `/perfil editar` | Edita todos los campos del perfil |
| `/perfil editar objetivo` | Edita solo los objetivos de entrenamiento |
| `/perfil editar salud` | Edita solo los datos de salud |
| `/plan listar` | Lista planes de entrenamiento y marca el activo |
| `/plan ver <plan_id>` | Muestra detalle del plan y sus sesiones |
| `/plan activar <plan_id>` | Activa un plan y desactiva el anterior |
| `/plan crear` | Crea y activa un plan base persistido en Supabase |
| `/carga` | Tabla semanal de carga/fatiga (TSS · ATL · CTL · TSB) de las últimas 8 semanas |
| `/carga meses` | Vista mensual de carga/fatiga de los últimos 3 meses |
| `salir` | Guarda el resumen de sesión y cierra el agente |

### Ejemplos de preguntas
- *"¿Cuál ha sido mi mejor ritmo en media maratón y qué necesito para bajar de 1h45?"*
- *"Analízame como deportista usando mis métricas de la última semana"*
- *"¿Cuál es mi VO2Max actual y cómo ha evolucionado?"*
- *"Dame un plan de entrenamiento para la próxima carrera de 10K"*
- *"¿Cómo ha sido mi sueño y HRV esta semana?"*
- *"¿Qué indicadores debo vigilar esta noche como diabético tipo 1 tras el entrenamiento?"*

---

## 🧩 Servidor MCP: `kairos_mcp_server`

Este proyecto usa un MCP propio local implementado en `agent/kairos_mcp_server.py`, que consulta directamente Garmin Connect mediante la librería `garminconnect`.

| Detalle | Valor |
|---------|-------|
| **Implementación** | `agent/kairos_mcp_server.py` |
| **Herramientas** | 40 Garmin Essentials usadas por Kairos |
| **Transporte** | stdio (subproceso local desde `tools/garmin-mcp-frozen.*`) |
| **Autenticación** | credenciales Garmin del `.env` |
| **Dependencia externa** | solo API de Garmin Connect |

### Modo de herramientas

Al iniciar el agente se pregunta qué conjunto de herramientas cargar:

| Modo | Herramientas | Tokens por petición | Uso recomendado |
|------|-------------|---------------------|------------------|
| **Essential Tools** *(default)* | Subset reducido (configurable) | ~3-5k | Uso diario: salud, actividades, entrenamiento |
| **Todas** | 40 (scope Kairos) | ~3-5k | El MCP propio expone el catálogo operativo de Kairos |

Puedes fijar el subconjunto permanentemente añadiendo `GARMIN_ENABLED_TOOLS=tool1,tool2,...` en tu `.env`.

### Compatibilidad MCP (verificado local)

Cambios recientes del servidor MCP de Garmin que ya están contemplados en el código:

- `get_personal_record` es el endpoint vigente para récords personales (el alias plural `get_personal_records` puede no existir según versión).
- `get_body_battery` ahora usa rango de fechas: `start_date` + `end_date`.
- `get_body_composition` ahora usa rango de fechas: `start_date` + `end_date`.

Si actualizas contratos del MCP propio, revisa `agent/mcp_adapter.py` y `tests/test_mcp_client.py` antes de desplegar cambios en prompts o rutas de tools.

---

## 📁 Estructura del Proyecto

```
kairos-coach/
├── .github/
│   └── workflows/
│       └── tests.yml          # CI de pytest en push/pull request.
├── agent/
│   ├── __init__.py
│   ├── main.py            # Punto de entrada: menú de proveedor, herramientas, chat e interfaz de usuario.
│   ├── mcp_client.py      # Cliente MCP asíncrono — lanza el MCP propio local.
│   ├── kairos_mcp_server.py # Servidor MCP propio con herramientas Garmin Essentials.
│   ├── storage.py         # Capa de persistencia multiusuario DB-first (Supabase).
│   └── trainer_agent.py   # Agente: tool-calling, adaptadores LLM, lógica de conversación.
├── docs/
│   ├── Harness.md
│   ├── mcp-tools-completo-vs-essentials.md
│   └── validation-load-fatigue-e2e-2026-08-15.md
├── memory/                # Base de conocimiento local opcional (RAG).
│   └── users/
├── prompts/
│   ├── system_prompt.md            # Prompt principal: personalidad, protocolos y uso de MCP por intención.
│   ├── system_prompt_compact.md    # Versión compacta del prompt para reducir tokens manteniendo reglas críticas.
│   └── mcp_tool_routing_guide.md   # Guía operativa de enrutado de tools MCP por intención.
├── supabase/
│   ├── schema.sql         # DDL para crear las tablas en Supabase (ejecutar en SQL Editor).
│   └── migrations/        # Migraciones incrementales de esquema.
├── tests/
│   ├── __init__.py
│   ├── test_trainer_agent.py  # Tests de funciones puras + mock de Gemini.
│   ├── test_main.py           # Tests de validaciones de input + flujo principal.
│   ├── test_storage.py        # Tests de persistencia DB-first y seguridad de credenciales.
│   ├── test_hooks_router.py   # Tests de HookManager y ToolRouter.
│   └── test_fit_tss_probe.py  # Tests sintéticos de detección de patrones de intervalos.
├── tools/
│   └── fit_tss_probe.py   # Utilidad local de soporte para análisis TSS.
├── tmp/                   # Scripts utilitarios de catálogo/render de tools MCP.
│   ├── build_mcp_catalog.py
│   ├── mcp_tools_dump.py
│   └── render_mcp_catalog.py
├── .env                   # Credenciales locales (no subir a git).
├── .env.example           # Plantilla de configuración con comentarios.
├── agent.log              # Log de ejecución del agente (local, no versionar).
├── requirements.txt       # Dependencias de producción.
├── requirements-dev.txt   # Dependencias de desarrollo: pytest, pytest-asyncio.
├── setup.ps1              # Setup automático para Windows.
├── setup.sh               # Setup automático para Unix/macOS.
├── Makefile               # Atajos de tareas de desarrollo.
├── pytest.ini             # Configuración de pytest.
├── TODO.md                # Roadmap y mejoras futuras planificadas.
└── README.md
```

---

## 📊 Comparativa con otras soluciones

| Capacidad | Kairos Coach | FitMCP / TP-MCP |
|---|:---:|:---:|
| Datos en tiempo real (live MCP) | ✅ | ❌ (sync manual) |
| Modelo TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma) propio | ✅ percentiles individualizados | ✅ nativo TP |
| Sistema multiusuario cloud | ✅ Supabase | ❌ single-user local |
| Protocolo médico DT1 | ✅ | ❌ |
| Especialización trail running | ✅ | ❌ |
| Memoria persistente entre sesiones | ✅ (hasta 10 resúmenes persistidos) | ❌ |
| Pre-cómputo en Python (zonas FC, ritmo, hidratación) | ✅ | ❌ |
| Rutas deterministas para plan y PRs | ✅ | ❌ |
| Escritura en calendario externo (TP, Garmin) | ❌ | ✅ TP |
| Comparación planificado vs ejecutado | ❌ pendiente | ✅ TP |
| Annual Training Plan (ATP) | ❌ pendiente | ✅ TP |
| Power PRs por duración (5s–90min) | ❌ pendiente | ✅ TP |

---

## ⚙️ Arquitectura de flujo interno

Esta sección describe qué ocurre dentro del código en cada operación clave. Útil para entender cómo se generan los outputs y dónde actúa cada capa.

### Arranque de sesión

```
main.py → asyncio.run(run_agent())
  └─ TrainerAgent.initialize()
       └─ list_available_tools(mcp_session)   → filtra tools de escritura si MCP_READ_ONLY=true
  └─ TrainerAgent.compute_and_persist_load_metrics()
    ├─ modo incremental (normal): recalcula desde último cierre en DB
    ├─ modo incremental_refresh: refresca días recientes si entraron actividades nuevas
    └─ modo full_recalc (casos puntuales): usuario nuevo/cambio fórmula/serie inválida
  └─ TrainerAgent.build_startup_status_markdown()
    └─ collect_startup_snapshot_48h() + serie canónica DB
      └─ _build_proactive_status_markdown(snapshot)  → briefing visible al usuario
```

### Cálculo del modelo TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)

> **Estado actual (v14):** La serie se calcula de forma incremental (solo procesa días necesarios desde el último registro en DB). Migración automática de fórmulas legacy (`formula_version`) y refresco de días recientes para capturar actividad nueva no reflejada aún en DB.

### Reset one-shot del precálculo (cuando cambia la fórmula)

Si cambias la lógica de cálculo (por ejemplo `walking/hiking`, fuerza o running) y quieres reconstruir toda la serie desde cero:

1. Borra `load_metrics_daily` del usuario activo.
2. Elimina `load_metrics` dentro de `user_profile.data` del mismo usuario.
3. Arranca Kairos normalmente.

En el siguiente arranque, al no existir serie previa, Kairos recalcula la ventana completa (120 días) y repuebla DB.

Comportamiento normal de arranque:

- Si `formula_version` guardada en perfil es distinta de la versión del código, fuerza recálculo completo automático.
- Si la versión coincide y la serie está al día, reutiliza DB y puede hacer `up_to_date` o `incremental_refresh` según actividad reciente.

```
_compute_load_fatigue_metrics(activities, trend_payload, profile, days_window)
  │
  ├─ 1. Recopilación: _extract_training_load_points(trend_payload) + _estimate_session_tss(act)
  ├─ 2. Config por deporte: _resolve_sport_model_cfg(profile)
  │       └─ lee profile["goals"]["primary"] → _SPORT_MODEL_DEFAULTS[deporte]
  │       └─ aplica overrides de profile["load_metrics"]["model"] si existen
  ├─ 3. Semilla: profile["load_metrics"]["last"] → atl_prev, ctl_prev (continuidad)
  ├─ 4. EWMA día a día:
  │       atl = atl_prev + (tss - atl_prev) / tau_atl
  │       ctl = ctl_prev + (tss - ctl_prev) / tau_ctl
  │       tsb = ctl - atl
  ├─ 5. Percentiles individualizados (últimos 28 días del propio atleta):
  │       tsb_low = p15, tsb_high = p80, atl_high = p85
  ├─ 6. Decisión de status (por prioridad):
  │       abs_overload  → tsb <= tsb_abs_floor (suelo fijo por deporte)
  │       sustained_overload → todos últimos 7 días TSB <= tsb_low
  │       fatigue_high  → tsb < tsb_low OR atl > atl_high
  │       ready         → tsb en rango AND not fatigue_high
  │       neutral       → resto
  └─ 7. Flag warm-up: days_with_load < 21 → aviso de calibración al usuario
```

### Cada mensaje en el chat

```
TrainerAgent.chat(user_message)
  │
  ├─ Ruta 1 — Plan status (determinista, sin LLM)
  │    └─ _is_plan_status_intent(msg) → _build_training_plan_status_markdown(profile)
  │         └─ _get_active_training_plan() → prioriza DB, fallback a profile
  │
  ├─ Ruta 2 — Planificación estructurada (determinista + LLM para texto)
  │    └─ _is_planning_intent(msg) → _generate_structured_plan_payload(profile, msg)
  │         ├─ Calcula duración, dificultad y razón del ajuste
  │         ├─ Genera 7 sesiones base con duraciones proporcionales
  │         └─ Si trail: _apply_trail_overrides() → tipos y notas específicos de trail
  │
  ├─ Ruta 3 — Récords personales (determinista)
  │    └─ _is_personal_records_intent(msg) → call_tool("get_personal_record") → tabla
  │
  ├─ Ruta 4 — Detalle factual de actividad por día (determinista)
  │    └─ `activity_details` / `mcp_factual`
  │         ├─ Resuelve fecha objetivo (hoy/ayer/anteayer/ISO/día de semana)
  │         ├─ Consulta Garmin MCP (actividades + métricas diarias)
  │         └─ Devuelve markdown estructurado sin pasar por LLM
  │
  ├─ Ruta 5 — Análisis profundo por fecha (pre-fetch + LLM)
  │    └─ _extract_iso_date_from_text(msg) → _find_activity_id_by_date()
  │         ├─ Pre-carga: actividad + body battery + sueño + HRV + carga
  │         ├─ Zonas FC reales con cascada get_activity_hr_in_timezones
  │         └─ _build_activity_analysis_block() inyectado al LLM
  │
  └─ Ruta 6 — LLM con tool-calling (resto de intenciones)
       └─ Bucle hasta 15 iteraciones:
            ├─ LLM decide qué tools llamar
            ├─ call_tool() → resultado → _compact_tool_result() → max 3000 chars
            └─ Si tool de escritura y MCP_READ_ONLY → bloqueo inmediato
```

### Suelos absolutos de TSB por deporte

| Deporte | TSB abs. floor | Motivo |
|---------|---------------:|--------|
| Trail running | −35 | Sesiones largas con picos de TSS muy altos |
| Running | −30 | Volumen moderado, recuperación más rápida |
| Ciclismo | −32 | Mayor volumen horario, fatiga muscular menor |
| Triatlón | −35 | Multimodal, acumulación alta entre disciplinas |

Cuando `TSB ≤ floor` el sistema fuerza `status=OVERLOAD` independientemente de los percentiles históricos del atleta, evitando que atletas crónicamente sobrecargados normalicen rangos peligrosos.

### Training Load de Garmin vs. TSS de TrainingPeaks

Kairos **no toma CTL (Estado físico)/ATL (Fatiga)/TSB (Forma) de Garmin**: los calcula localmente en Python.
Para el **TSS por actividad**, Kairos aplica una jerarquía por modalidad (potencia+FTP, zonas FC, ritmo umbral, RPE) y usa `trainingStressScore`/`trainingLoad` nativo de Garmin como fuente/fallback cuando está disponible.
Aquí la diferencia técnica entre referencias:

**Training Load de Garmin** se basa en **EPOC** (Excess Post-exercise Oxygen Consumption):

- Garmin estima dos umbrales por atleta: VT1 (aeróbico ligero→moderado) y VT2 (umbral de lactato), usando VO₂max e historial de FC.
- A cada segundo de actividad le asigna un coste metabólico según la zona (por debajo de VT1, entre VT1-VT2, por encima de VT2).
- Integra ese coste durante toda la sesión y lo normaliza en una escala empírica (~0 a 500).
- Se recalibra automáticamente con cada actividad. No requiere configuración manual.

**TSS de TrainingPeaks** (Coggan 2003) nació para ciclismo con potenciómetro:

$$TSS = \frac{t \times NP \times IF}{FTP \times 3600} \times 100$$

Una sesión en FTP durante exactamente 1 hora = **100 TSS**. Para running sin potenciómetro, TP usa hrTSS basado en la fórmula TRIMP de Banister (FC media vs. LTHR).

**Comparativa:**

| Aspecto | Garmin Training Load | TrainingPeaks TSS |
|---------|----------------------|-------------------|
| Fórmula base | EPOC integrado | Potencia normalizada o TRIMP-HR |
| Calibración | Automática (VO₂max + historial) | Manual (FTP o LTHR del atleta) |
| Exactitud | Alta con FC calibrada | Muy alta con potenciómetro |
| Comparabilidad entre atletas | No (relativa al historial propio) | Sí (100 TSS = 1h en umbral) |
| Deportes | Todos (running, trail, cycling, swimming) | Nació en ciclismo; adaptado a running/triatlón |

**Por qué nuestro modelo es válido:** CTL (Estado físico)/ATL (Fatiga)/TSB (Forma) son modelos relacionales, no absolutos. Lo que importa es que la unidad de carga sea **consistente para el mismo atleta**, no que sea exactamente 100 en umbral. La individualización está en los tau y percentiles propios de cada atleta, no en el valor absoluto de cada sesión.

**Unidad de esfuerzo por tipo de actividad (persistida en el agente):**

| Tipo de actividad | Prioridad 1 | Prioridad 2 | Prioridad 3 |
|-------------------|-------------|-------------|-------------|
| Fuerza / Gimnasio | **hrTSS por zonas FC (si cobertura >=35%)** | **TSS por IF estimado de fuerza** | **TSS por RPE/minuto** |
| Running (no Trail) | **TSS por ritmo umbral (rTSS interno)** | **hrTSS por FC** | - |
| Trail running | **hrTSS por zonas FC (calibrado)**<br/>**Excepción:** si ritmo final `< 6:00/km` usa **hrTSS bruto por zonas** | **hrTSS por FC** | **TSS por ritmo umbral / hrTSS por RPE** |
| Senderismo / Hike / Caminar | **TSS por bandas walking/hiking (suave, vivo, carga/cuestas)** | **Blend con hrTSS por zonas (si cobertura >=35%)** | **TSS por banda sin zonas** |
| Ciclismo (cualquier modalidad) | **TSS por potencia + FTP** | **hrTSS por zonas FC** | **hrTSS por FC** |
| Otras modalidades (natación, remo, etc.) | **hrTSS por zonas FC** | **hrTSS por FC** | **Training Effect / IF por defecto** |

Notas de implementación:
- Para running, el ritmo umbral se obtiene del perfil persistido por usuario (`/perfil umbral <mm:ss>`).
- Para ciclismo, el FTP se obtiene del perfil cacheado o de `get_cycling_ftp`.
- Para fuerza/gimnasio sin señales fiables de FC, se usa IF por tipología de sesión:
  - Movilidad/acondicionamiento ligero: IF ~= 0.50
  - Mantenimiento: IF ~= 0.55
  - Fuerza general/hipertrofia: IF ~= 0.56
  - Neuromuscular: IF ~= 0.57
  - Fuerza máxima/potencia pesada: IF ~= 0.80
- Fallback por RPE/minuto en fuerza cuando hay RPE explícito:
  - RPE 3-4: ~0.5 TSS/min
  - RPE 5-6: ~1.0 TSS/min
  - RPE 7: ~1.2 TSS/min
  - RPE 8: ~1.35 TSS/min
  - RPE 9-10: ~1.5 TSS/min
- Para walking/hiking se aplica una calibración por bandas de carga por hora:
  - Caminata suave / regenerativa: 15-25 TSS/h
  - Caminata ritmo vivo / power walking: 25-40 TSS/h
  - Senderismo con mochila / cuestas largas: 40-60+ TSS/h
- Importante: esta calibración afecta solo a `walking/hiking`.
- En `trail_running` se aplica calibración por defecto (`hrTSS zonas * 0.72`), salvo regla de trail rápido: si el ritmo final/efectivo es `< 6:00/km`, Kairos usa `hrTSS bruto por zonas`.
- En análisis de actividad trail con zonas FC disponibles, Kairos muestra explícitamente ambos valores: `hrTSS bruto zonas` y `hrTSS Kairos aplicado`.
- Si faltan datos clave, el sistema conserva fallbacks defensivos (trainingStressScore/trainingLoad nativo, Training Effect e IF por defecto) para no perder continuidad de la serie CTL (Estado físico)/ATL (Fatiga)/TSB (Forma).

Reglas verificadas por tipología:
- Running asfalto/pista: `rTSS`.
- Trail running: `hrTSS` calibrado, excepto trail rápido (`< 6:00/km`) donde usa `hrTSS` bruto por zonas.
- Walking/hiking: `TSS` por bandas (con blend de zonas cuando existe cobertura suficiente).
- Ciclismo con potencia+FTP: `TSS de potencia`.

Persistencia de umbral de running por usuario (`user_profile.data.performance`):
- `running_threshold_pace_sec_per_km`
- `running_threshold_pace`
- `running_threshold_pace_date`

### Clasificacion de running por tipo de sesion

Para sesiones de running no-trail, Kairos clasifica cada actividad en una de estas categorias:

- `rodaje`
- `fartlek`
- `series`
- `calidad` (fallback cuando la evidencia es ambigua)

La clasificacion usa señales combinadas del payload de actividad:

- Relacion velocidad maxima / velocidad media (`speed_ratio`)
- Numero de vueltas (`lap_count`)
- Minutos vigorosos
- RPE del entrenamiento
- Etiqueta de efecto de entrenamiento (`training_effect_label`)
- Texto de nombre/descripcion/notas (keywords de rodaje, fartlek y series)

Cada clase obtiene un score, y el sistema estima ademas una confianza (`high`, `medium`, `low`).

### Impacto directo en el calculo de TSS running

- `rodaje`: se mantiene el TSS base por ritmo umbral (sin inflado artificial).
- `fartlek`: uplift pequeno y acotado para evitar sobreestimacion sistematica.
- `series`: uplift mayor para conservar sensibilidad en trabajos fraccionados.
- Confianza baja: se reduce automaticamente el uplift para priorizar estabilidad.

Adicionalmente, durante el recálculo de carga se persiste una traza de inferencia (`running_session_inference`) con muestras recientes de `session_kind` y `confidence` por actividad, para auditoria y calibracion futura.

En recálculo completo (`force_full_recalc=True`), Kairos enriquece ciclismo y running no-trail con `get_activity` para incorporar señales de variabilidad (`lap_count`, `avg_speed_mps`, `max_speed_mps`, `workout_rpe`, `training_effect_label`) antes de calcular TSS.

---

## 🧪 Tests

El proyecto incluye una suite activa de tests unitarios (colección en crecimiento continuo) que cubre funciones críticas sin necesidad de conexión a Garmin ni a ningún LLM. Como referencia reciente, `tests/test_trainer_agent.py` valida actualmente 307 tests en verde.

### Instalar dependencias de desarrollo
```powershell
pip install -r requirements-dev.txt
```

### Ejecutar los tests
```powershell
pytest
```

En Windows, si aparece un error de captura de salida (`ValueError: I/O operation on closed file`), usa:

```powershell
pytest -s
```

### Ejecutar con informe de cobertura
```powershell
pytest --cov=agent --cov-report=term-missing
```

Para generar un informe HTML navegable:
```powershell
pytest --cov=agent --cov-report=html
# El informe se genera en htmlcov/index.html
```

### Cobertura

| Módulo | Qué cubre |
|--------|-----------|
| `trainer_agent.py` | `_seconds_to_hhmmss`, `_normalize_date_args`, `_strip_garmin_object`, `_compact_tool_result`, `_compact_personal_records`, `_clean_schema_for_gemini`, `_GeminiCompletions._parse`, resolución de actividad por fecha, zonas FC y análisis profundo, estado proactivo 48h, fallbacks de planificación, modelo de carga/fatiga (TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)), configuración por deporte, tabla de tendencia `/carga`, plan trail específico, cálculo TSS por potencia+FTP / zonas FC / ritmo umbral / HR / RPE, fetch histórico por fechas |
| `main.py` | `_validate_date`, `_validate_time`, `_validate_hours`, `_is_first_time`, KB enriquecida de onboarding, `_ensure_garmin_credentials`, `_build_enriched_athlete_knowledge` |
| `storage.py` | sanitización de credenciales, no-persistencia de passwords Garmin |
| `test_fit_tss_probe.py` | validación sintética del detector de intervalos (alta/media/baja probabilidad) |

---

## 🔒 Privacidad y Seguridad

- **Contraseña cifrada en BD:** La contraseña se almacena con cifrado simétrico Fernet (AES-128-CBC + HMAC-SHA256) en Supabase. La `ENCRYPTION_KEY` en `.env` es la única clave — nunca la subas a Git.
- **Hash unidireccional para verificación:** Además del cifrado, el login verifica contra un hash PBKDF2-SHA256 (120.000 iteraciones) para autenticación segura.
- **Nunca texto claro:** La `_sanitize_credentials_for_storage` garantiza que `garmin_password` y `garmin_password_strategy` nunca lleguen a la columna `credentials` de Supabase.
- **OAuth tokens de Garmin:** Los tokens OAuth se guardan en `~/.garminconnect` (válidos ~6 meses). La contraseña solo circula en memoria durante la sesión.
- **API keys hasheadas:** El identificador local de cuota de Gemini usa SHA-256 — la clave nunca se escribe en texto plano.
- **Pruning inteligente:** Los metadatos innecesarios de la API de Garmin se eliminan antes de enviarlos al LLM, reduciendo tokens y evitando fugas de datos irrelevantes.

---

## 📝 Contribuciones

¡Las contribuciones, issues y sugerencias son bienvenidas! Si encuentras algún cálculo de ritmos incorrecto o quieres añadir nuevas herramientas, abre un *Pull Request* o una incidencia.

¡Buen entrenamiento! 🏁
