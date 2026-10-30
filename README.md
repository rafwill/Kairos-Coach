# 🏃‍♂️ Kairos Coach

Entrenador deportivo personal con IA que funciona en la terminal sobre tus datos reales de Garmin Connect. Calcula la carga de cada sesión como TrainingPeaks (TSS, Estado físico CTL, Fatiga ATL y Forma TSB), recuerda tu perfil y tus conversaciones y genera planes de entrenamiento versionados a partir de tu objetivo y de lo que realmente has entrenado.

**Los números los calcula Python; el LLM interpreta y entrena.** El LLM decide como entrenador (el plan y sus cambios, la lectura de los datos) y Python calcula y comprueba (TSS, carga, ritmos, reglas del plan). Las preguntas de datos (TSS de una semana, umbrales, plan activo, récords) se responden por rutas deterministas sin LLM. Las de coaching ("¿qué me toca hoy?", "¿puedo entrenar fuerte mañana?") muestran primero los datos y después la interpretación del LLM, limitada a esos datos.

## Qué hace

- **Briefing al arrancar:** Body Battery, HRV, sueño, entrenamientos de las últimas 48 h, estado de carga por zonas de Forma (Joe Friel, relativas a tu CTL) y plan del día.
- **Carga como TrainingPeaks:** un TSS por actividad con el método de su deporte, guardado una vez; PMC 42/7. Validado contra el export de TP del atleta (tabla abajo).
- **Planes de entrenamiento:** los construye y revisa el LLM del atleta como su entrenador (por bloques con descarga, cada día con su fecha y sus tramos); Kairos le da los datos (objetivo, zonas de ritmo y FC, carga real, disponibilidad, lo hecho los últimos días), calcula ritmos, km y TSS planificado y comprueba las reglas (nunca más de un 10 % por semana sobre el volumen real, descargas, afinamiento, disponibilidad) antes de guardar cada versión. Si el modelo no consigue un plan válido, no se guarda ninguno: Kairos dice qué no cumplía y propone repetir o probar un modelo más capaz.
- **Coaching con contexto:** perfil, salud (protocolo de diabetes con o sin insulina según la medicación; lesiones aparte), objetivo, umbrales con zonas en lpm y plan activo.
- **Multiusuario:** cada usuario con su perfil, historial, planes y base de conocimiento en Supabase.
- **Tu propio proveedor de IA:** cualquiera con API compatible con OpenAI (NVIDIA, OpenAI, Gemini, Anthropic, Groq, Mistral, OpenRouter, DeepSeek u otro). Se indica la primera vez y se cambia con `/proveedor`. Admite dos modelos: uno para crear planes y escribir el arranque y otro, más ligero, para el chat, que releva al primero si se queda sin cupo o saturado (con Gemini gratuito: 3.6 Flash, 20 peticiones al día, y 3.5 Flash Lite, 500).

## Estado de la carga (2026-10-21)

| Pieza | Cómo se calcula | Frente a TP |
| --- | --- | --- |
| CTL / ATL / TSB | 42 / 7 días en todos los deportes; TSB = CTL de ayer − ATL de ayer | diferencia media CTL 2.1 · ATL 2.7 · TSB 3.0 |
| Running | rTSS: ritmo normalizado con pendiente (Minetti) frente al ritmo umbral vigente en la fecha | MAPE 6.3 % (n=97) |
| Trail | hrTSS como TP (TSS/h por tramo de FC sobre el umbral de TP); rTSS si va por debajo de 6:00/km | MAPE 1.6 % |
| Bici con potencia | potencia normalizada (NP) y FTP | MAPE 1.6 % |
| Bici sin potencia | hrTSS como TP | — |
| Caminar / senderismo | modelo metabólico (ACSM + Minetti) con tope en el hrTSS de TP | no cuadra por diseño |
| Fuerza / elíptica / remo | hrTSS anclado al umbral de FC | ≈ 1.0 – 1.1 |

La carga nativa de Garmin nunca se usa como TSS: solo se muestra como "Carga Garmin". Detalle del modelo y anclajes en [`CLAUDE.md`](CLAUDE.md); pendientes en [`docs/proximos_pasos.md`](docs/proximos_pasos.md).

## Instalación

Requisitos: Python 3.12+ (lo exige `garminconnect`; la CI prueba 3.12 y 3.14), cuenta de Garmin Connect, un proyecto de Supabase y al menos una API key de LLM.

```powershell
git clone https://github.com/rafwill/Kairos-Coach.git
cd Kairos-Coach
powershell -ExecutionPolicy Bypass -File .\setup.ps1   # Windows
./setup.sh                                              # Unix/macOS
```

El setup crea `.venv`, instala dependencias, instala Kairos en modo editable (`agent` y `garmin_mcp` importables desde cualquier carpeta), copia `.env.example` a `.env` y genera `ENCRYPTION_KEY`. Después:

1. Completa `.env`: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`. La clave del proveedor de IA no hace falta escribirla: Kairos la pide la primera vez y la guarda en `.env`.
2. En Supabase, **Authentication → Sign In / Providers → Email**: proveedor de email activado y **"Confirm email" activado** (hace seguro el vínculo de cada cuenta con sus datos).
3. En el SQL Editor de Supabase (New query → pegar → Run), en este orden:
   1. [`supabase/schema.sql`](supabase/schema.sql): tablas, índices y funciones. En un proyecto ya existente añade solo lo que falte sin tocar los datos.
   2. [`supabase/rls.sql`](supabase/rls.sql): activa la seguridad por fila y las políticas, para que cada usuario vea solo sus filas. **Sin este paso las tablas quedan abiertas** a cualquiera con la clave `anon`.
4. Comprueba la seguridad: `make check-rls` (en Windows sin `make`: `.venv\Scripts\python.exe tools\check_rls.py`). Sin sesión, las 11 tablas deben salir `cerrada`.
5. Primer acceso: arranca Kairos (ver Uso), escribe tu email y la contraseña de Garmin Connect, acepta crear la cuenta, abre el enlace del correo de confirmación y vuelve a entrar. Después, `check_rls` con tu sesión debe ver tus datos.

Qué protege cada política y cómo se migra un proyecto antiguo con usuarios sin cuenta de Auth: [`supabase/README.md`](supabase/README.md).

## Uso

```powershell
.venv\Scripts\python.exe -m agent.main     # Windows
.venv/bin/python -m agent.main             # Unix/macOS
```

Kairos pide el usuario, la primera vez pregunta su proveedor de IA y su clave, y arranca su servidor MCP propio (`garmin_mcp/`, solo lectura) como subproceso. Los tokens de Garmin se reutilizan entre arranques en `~/.garminconnect/kairos/`.

| Comando | Qué hace |
| --- | --- |
| `/ayuda` | Ejemplos de preguntas, comandos e indicadores |
| `/perfil` · `/perfil editar [objetivo\|salud]` | Ver o editar el perfil |
| `/perfil umbral <mm:ss>` | Nuevo ritmo umbral de running (rige desde hoy) |
| `/perfil umbral fc <lpm>` | Nueva FC umbral tras un test, la misma que en TrainingPeaks (rige desde hoy) |
| `/perfil fc <reposo> <max>` | FC de reposo y máxima |
| `/perfil lugar <ciudad>` | Dónde entrenas: lo usa el plan y el tiempo de hoy del arranque (Open-Meteo recibe solo las coordenadas) |
| `/plan listar` · `/plan ver <nº o id>` · `/plan activar <nº o id>` · `/plan crear` | Planes de entrenamiento: `/plan crear` pregunta como un entrenador (días, tiempo máximo por día, dobles, distribución de la intensidad, progresión de la carga, otros deportes como bici o remo, dónde entrenas) y tu LLM construye el plan (semanas y sesiones con fecha, tramos, ritmos o zonas, km y TSS) a partir del objetivo del perfil y Kairos lo comprueba; en el chat, "ajusta el plan: …" cambia los próximos días y "replanifica el plan" lo rehace hasta la carrera (al arrancar, Kairos avisa si el plan ya no encaja con lo que estás haciendo); el nº y el id corto salen en `/plan listar` |
| `/carga` · `/carga meses` | Carga semanal (8 semanas) o mensual (3 meses) |
| `/proveedor` | Cambiar de proveedor de IA, modelos (planes y arranque; chat) o clave |
| `salir` | Salir de Kairos (la sesión queda guardada para la próxima vez) |

Ejemplos: *"¿Cómo está mi forma física hoy?"*, *"¿Cuánto TSS hice la semana pasada?"*, *"Analiza mi última actividad de trail"*, *"¿Qué sesión me recomiendas para esta semana?"*, *"Créame un plan para mi media maratón"*.

Con `make` disponible: `make setup` y `make serve`. Los comandos de pruebas y diagnóstico están en [Tests y diagnóstico](#tests-y-diagnóstico).

## Estructura

```
agent/
  main.py                CLI: login, proveedor de IA, comandos y chat
  trainer_agent.py       TrainerAgent: compone los mixins de abajo; chat() y la tabla de rutas
  router.py              enrutado sin LLM: detectores de intención y ToolRouter
  routes/                respuestas de las rutas deterministas por familia (carga, periodo, sueño, récords,
                         perfil, datos del día, conteo) y tabla ROUTE_HANDLERS (handlers.py)
  tool_loop.py           bucle con tools (LLM + Garmin), G15, rescates y sensores de su respuesta
  prefetch.py            precarga de una actividad (y su análisis) antes del bucle
  llm_calls.py           proveedor de IA (reintento ante 429/503, aviso de cupo diario), capa de coaching y sensores
  context.py             prompt de sistema y mensajes del LLM
  startup.py             arranque, foto de 48 h y resumen inicial (startup_status.py)
  load_compute.py        TSS de las actividades nuevas y serie de carga en Supabase
  daily_engine.py        motor diario: plan de hoy, ajuste por HRV/sueño/body battery, zonas
  plan_llm.py            el LLM construye (estructura + semanas en paralelo) y revisa el plan, con reintentos
  plan_builder.py        datos del atleta para el plan, tramos → ritmos, km y TSS, reglas de estructura y semana
  plan_drift.py          ¿sigue valiendo el plan? Señales de los últimos 7 días para proponer replanificar
  plan_prefs.py          lo que el atleta dice para siempre («los martes no puedo», «prefiero…») para sus planes
  weather.py             el tiempo de hoy donde entrena el atleta (Open-Meteo, sin clave)
  plans.py               validación y estado del plan; motor determinista para planes antiguos
  run_plan.py            plan de carrera en asfalto por bloques: ritmos de Friel, fases, descargas, tramos y rTSS
  activity_analysis.py   localizar una actividad y su análisis precalculado
  hr_profile.py · wellness.py · garmin_data.py · tool_results.py · kairos_tools.py · dates.py ·
  data_access.py · knowledge.py · prompts.py · llm.py · coaching.py · replies.py
                         zonas y umbrales; sueño/HRV/body battery; lectura de Garmin; compactación de tools;
                         tools kairos_*; fechas; Supabase del chat; base de conocimiento; prompts; cliente LLM;
                         horizonte y calendario; avisos de Garmin
  load_metrics.py        TSS por deporte y PMC (CTL/ATL/TSB)
  running_tss.py         pipeline físico de running (NGP con pendiente, rTSS)
  storage.py             persistencia multiusuario en Supabase
  sensors.py             sensores sobre el texto del LLM (zonas, ritmo umbral, fechas…), compartidos con el juez
  diabetes.py            qué protocolo de diabetes recibe el LLM (con o sin insulina), según el perfil
  ia_provider.py         proveedores de IA conocidos (URL, modelo y modelo del chat propuestos) y variable de la clave
  tool_groups.py         metatooling: qué grupos de tools recibe el LLM según la pregunta
  mcp/
    client.py            cliente MCP: arranca el servidor, sesión stdio y llamadas a tools
    contracts.py         contratos de argumentos, normalización y caché de las tools
garmin_mcp/              servidor MCP de Garmin Connect (44 tools, solo lectura), independiente de agent/
  README.md              arranque, configuración y catálogo de las 44 tools
  server.py              definición de las tools; arranque: python -m garmin_mcp
prompts/                 todo el texto que recibe el LLM: system prompt, reglas de entrenador y
                         plantillas por familia (system/, coaching/, rescue/, activity_analysis/, diabetes/)
supabase/                schema.sql (esquema completo e idempotente), rls.sql (seguridad por fila) y README.md
tests/                   suite de pytest (sin red: Garmin, Supabase y LLM mockeados)
tools/
  rebuild_tss_baseline.py   corrección del histórico de TSS (prueba; --apply escribe)
  run_bateria.py            batería de preguntas contra Kairos con tiempos por respuesta
  juez_bateria.py           juez determinista de la batería (rúbrica fija frente a Supabase)
  lint_mcp.py, mcp_lint/    guía de estilo MCP de apiaddicts sobre las tools del servidor (Spectral)
  healthcheck_mcp.py        healthcheck en vivo: llama a todas las tools contra Garmin y dice cuáles fallan
  gen_mcp_docs.py           regenera el catálogo de tools de garmin_mcp/README.md
  check_rls.py              comprueba que la seguridad por fila de Supabase cierra cada tabla
docs/                    arquitectura, harness, bitácora y preguntas de la batería
```

Con la terminología MCP: Kairos (`agent/`) es el **host**, `agent/mcp/` su **cliente** y `garmin_mcp/` el **servidor**; el servidor no depende del host y Kairos lo usa solo por el protocolo MCP. Detalle en [`docs/arquitectura.md`](docs/arquitectura.md).

Rutas locales que nunca se suben (están en `.gitignore`): `.env`, `_archivo/` (evidencias de calibración, exports de TP y herramientas de diagnóstico), `fixtures/real_payloads/` (datos reales de Garmin), `.tmp/` (salidas de la batería) y `memory/`.

## Tests y diagnóstico

| Qué | Con `make` | Sin `make` (Windows) | Cuándo |
| --- | --- | --- | --- |
| Tests (sin red) | `make test` | `.venv\Scripts\python.exe -m pytest` | Siempre; la CI los pasa en cada push |
| Lint de código | `make lint` | `.venv\Scripts\python.exe -m ruff check agent garmin_mcp tests tools` | Lo pasan el pre-commit y la CI (F y E9; `make lint-informe` para el resto) |
| Pre-commit (ruff y tests) | `make hooks` | `git config core.hooksPath .githooks` | Una vez por clon (los setup lo activan) |
| Guía de estilo del MCP | `make lint-mcp` | `.venv\Scripts\python.exe tools\lint_mcp.py` | Al añadir o cambiar tools del MCP |
| Catálogo de tools del MCP | `make docs-mcp` | `.venv\Scripts\python.exe tools\gen_mcp_docs.py` | Al añadir o cambiar tools (un test falla si está desfasado) |
| Salud de las tools del MCP | `make health-mcp` | `.venv\Scripts\python.exe tools\healthcheck_mcp.py` | Al actualizar `garminconnect`, antes de una batería o si algo falla |
| Batería de preguntas | — | `.venv\Scripts\python.exe tools\run_bateria.py --salida .tmp\bateria\hoy` | Tras cambios en rutas, prompts o modelo |
| Juez de la batería | — | `.venv\Scripts\python.exe tools\juez_bateria.py .tmp\bateria\hoy.md` | Justo después de la batería |

- **Tests:** no necesitan red. Los pasan el pre-commit y la CI.
- **`lint_mcp`:** pide `tools/list` al servidor y lo pasa por la guía de estilo MCP de apiaddicts (Spectral; necesita Node 18+). Configuración en `tools/mcp_lint/.spectral.yaml`.
- **`healthcheck_mcp`:** llama a las 44 tools contra Garmin (solo lectura) y marca cada una como OK, VACÍA (aviso), ERROR o ENTORNO (429 o autenticación). Sale con 1 si alguna tool falla. Informe en `.tmp/healthcheck/`.
- **Batería y juez:** 25 preguntas de `docs/bateria_p55_preguntas_base.txt` contra datos reales (necesita `KAIROS_USERNAME` en `.env`; no lee ni escribe la memoria del atleta); el juez comprueba cada respuesta contra Supabase con una rúbrica fija (`docs/Harness.md`) y sale con 1 si alguna suspende.
- Las salidas con datos reales van a `.tmp/`, que está en `.gitignore`.

## Documentación

| Documento | Contenido |
| --- | --- |
| [`CLAUDE.md`](CLAUDE.md) | Estado vigente del cálculo de carga, reglas de método, datos y limitaciones aceptadas |
| [`docs/arquitectura.md`](docs/arquitectura.md) | Capas, tablas de Supabase, flujos de arranque, cálculo y chat, MCP y seguridad |
| [`docs/Harness.md`](docs/Harness.md) | Guías y sensores que rodean al LLM (qué existe, dónde vive y su estado) y rúbrica de la batería |
| [`docs/proximos_pasos.md`](docs/proximos_pasos.md) | Pendientes y descartados |
| [`garmin_mcp/README.md`](garmin_mcp/README.md) | Servidor MCP: arranque, configuración, problemas frecuentes (429/403, certificados) y catálogo de las 44 tools (generado) |
| [`CHANGELOG.md`](CHANGELOG.md) | Cambios (los pendientes, en `docs/proximos_pasos.md`) |

## Privacidad

Cada usuario entra con su cuenta de Supabase Auth y la base de datos solo le deja ver sus filas (seguridad por fila, [`supabase/README.md`](supabase/README.md)). La contraseña la guarda Supabase Auth como hash y la de Garmin va cifrada con Fernet (`ENCRYPTION_KEY`). Los tokens de Garmin viven fuera del repo y los datos reales del atleta solo en rutas ignoradas por git.
