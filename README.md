# 🏃‍♂️ Kairos Coach

Entrenador deportivo personal con IA que funciona en la terminal sobre tus datos reales de Garmin Connect. Calcula la carga de cada sesión como TrainingPeaks (TSS, Estado físico CTL, Fatiga ATL y Forma TSB), recuerda tu perfil y tus conversaciones y genera planes de entrenamiento versionados a partir de tu objetivo y de lo que realmente has entrenado.

**Los números los calcula Python; el LLM solo interpreta.** Las preguntas de datos (TSS de una semana, umbrales, plan activo, récords) se responden por rutas deterministas sin LLM. Las de coaching ("¿qué me toca hoy?", "¿puedo entrenar fuerte mañana?") muestran primero los datos y después la interpretación del LLM, limitada a esos datos.

## Qué hace

- **Briefing al arrancar:** Body Battery, HRV, sueño, entrenamientos de las últimas 48 h, estado de carga por zonas de Forma (Joe Friel, relativas a tu CTL) y plan del día.
- **Carga como TrainingPeaks:** un TSS por actividad con el método de su deporte, guardado una vez; PMC 42/7. Validado contra el export de TP del atleta (tabla abajo).
- **Planes de entrenamiento:** motor determinista multisemana (base → build → peak → taper) que parte del volumen real de las últimas 4 semanas, nunca sube más de un 10 % por semana y guarda cada versión.
- **Coaching con contexto:** perfil, condiciones de salud (protocolo de diabetes tipo 1 con insulina), objetivo, umbrales con zonas en lpm y plan activo.
- **Multiusuario:** cada usuario con su perfil, historial, planes y base de conocimiento en Supabase.
- **Cinco proveedores de LLM:** NVIDIA NIM, Gemini, Groq, Mistral y Cerebras; se cambia en caliente con `/modelo`.

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

La carga nativa de Garmin nunca se usa como TSS: solo se muestra como "Carga Garmin". Detalle del modelo, anclajes y pendientes en [`CLAUDE.md`](CLAUDE.md).

## Instalación

Requisitos: Python 3.11+ (recomendado 3.12), cuenta de Garmin Connect, un proyecto de Supabase y al menos una API key de LLM.

```powershell
git clone https://github.com/rafwill/Kairos-Coach.git
cd Kairos-Coach
powershell -ExecutionPolicy Bypass -File .\setup.ps1   # Windows
./setup.sh                                              # Unix/macOS
```

El setup crea `.venv`, instala dependencias, instala Kairos en modo editable (`agent` y `garmin_mcp` importables desde cualquier carpeta), copia `.env.example` a `.env` y genera `ENCRYPTION_KEY`. Después:

1. Completa `.env`: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` y al menos una API key de LLM (`NVIDIA_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY` o `CEREBRAS_API_KEY`). Cada proveedor admite su `*_MODEL`; ver `.env.example`.
2. Ejecuta [`supabase/schema.sql`](supabase/schema.sql) en el SQL Editor de Supabase (New query → pegar → Run). Crea todas las tablas en un proyecto nuevo y, en uno ya existente, añade solo lo que falte sin tocar los datos.
3. Seguridad: cuentas de Supabase Auth y políticas por usuario (`supabase/rls.sql`). Qué son, cómo activarlas y cómo comprobarlas (`make check-rls`): [`supabase/README.md`](supabase/README.md).

## Uso

```powershell
.venv\Scripts\python.exe -m agent.main     # Windows
.venv/bin/python -m agent.main             # Unix/macOS
```

Kairos pide el usuario, muestra los proveedores con clave configurada y arranca su servidor MCP propio (`garmin_mcp/`, solo lectura) como subproceso. Los tokens de Garmin se reutilizan entre arranques en `~/.garminconnect/kairos/`.

| Comando | Qué hace |
| --- | --- |
| `/ayuda` | Ejemplos de preguntas, comandos e indicadores |
| `/perfil` · `/perfil editar [objetivo\|salud]` | Ver o editar el perfil |
| `/perfil umbral <mm:ss>` | Nuevo ritmo umbral de running (rige desde hoy) |
| `/perfil fc <reposo> <max>` | FC de reposo y máxima |
| `/plan listar` · `/plan ver <id>` · `/plan activar <id>` · `/plan crear` | Planes de entrenamiento |
| `/carga` · `/carga meses` | Carga semanal (8 semanas) o mensual (3 meses) |
| `/modelo` | Tokens usados y cambio de proveedor/modelo |
| `salir` | Cerrar la sesión |

Ejemplos: *"¿Cómo está mi forma física hoy?"*, *"¿Cuánto TSS hice la semana pasada?"*, *"Analiza mi última actividad de trail"*, *"¿Qué sesión me recomiendas para esta semana?"*, *"Créame un plan para mi media maratón"*.

Con `make` disponible: `make setup` y `make serve`. Los comandos de pruebas y diagnóstico están en [Tests y diagnóstico](#tests-y-diagnóstico).

## Estructura

```
agent/
  main.py                CLI: login, proveedor LLM, comandos y chat
  trainer_agent.py       agente: rutas deterministas, coaching LLM, planes, carga
  load_metrics.py        TSS por deporte y PMC (CTL/ATL/TSB)
  running_tss.py         pipeline físico de running (NGP con pendiente, rTSS)
  storage.py             persistencia multiusuario en Supabase
  sensors.py             sensores sobre el texto del LLM (zonas, ritmo umbral, fechas…), compartidos con el juez
  mcp/
    client.py            cliente MCP: arranca el servidor, sesión stdio y llamadas a tools
    contracts.py         contratos de argumentos, normalización y caché de las tools
garmin_mcp/              servidor MCP de Garmin Connect (42 tools, solo lectura), independiente de agent/
  README.md              arranque, configuración y catálogo de las 42 tools
  server.py              definición de las tools; arranque: python -m garmin_mcp
prompts/                 todo el texto que recibe el LLM: system prompt, reglas de entrenador y
                         plantillas por familia (system/, coaching/, rescue/, activity_analysis/)
supabase/                schema.sql (esquema completo e idempotente)
tests/                   suite de pytest (sin red: Garmin, Supabase y LLM mockeados)
tools/
  rebuild_tss_baseline.py   corrección del histórico de TSS (prueba; --apply escribe)
  run_bateria.py            batería de preguntas contra Kairos con tiempos por respuesta
  juez_bateria.py           juez determinista de la batería (rúbrica fija frente a Supabase)
  lint_mcp.py, mcp_lint/    guía de estilo MCP de apiaddicts sobre las tools del servidor (Spectral)
  healthcheck_mcp.py        healthcheck en vivo: llama a todas las tools contra Garmin y dice cuáles fallan
  gen_mcp_docs.py           regenera el catálogo de tools de garmin_mcp/README.md
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

- **Tests:** 623 passed (23/10/2026). No necesitan red. Los pasan el pre-commit y la CI.
- **`lint_mcp`:** pide `tools/list` al servidor y lo pasa por la guía de estilo MCP de apiaddicts (Spectral; necesita Node 18+). Configuración en `tools/mcp_lint/.spectral.yaml`.
- **`healthcheck_mcp`:** llama a las 42 tools contra Garmin (solo lectura) y marca cada una como OK, VACÍA (aviso), ERROR o ENTORNO (429 o autenticación). Sale con 1 si alguna tool falla. Informe en `.tmp/healthcheck/`.
- **Batería y juez:** 25 preguntas de `docs/bateria_p55_preguntas_base.txt` contra datos reales (necesita `KAIROS_USERNAME` en `.env`); el juez comprueba cada respuesta contra Supabase con una rúbrica fija (`docs/Harness.md`) y sale con 1 si alguna suspende.
- Las salidas con datos reales van a `.tmp/`, que está en `.gitignore`.

## Documentación

| Documento | Contenido |
| --- | --- |
| [`CLAUDE.md`](CLAUDE.md) | Estado vigente del cálculo de carga, reglas de método, datos y pendientes (hoja de ruta) |
| [`docs/arquitectura.md`](docs/arquitectura.md) | Capas, tablas de Supabase, flujos de arranque, cálculo y chat, MCP y seguridad |
| [`docs/Harness.md`](docs/Harness.md) | Guías y sensores que rodean al LLM (qué existe, dónde vive y su estado) y rúbrica de la batería |
| [`docs/proximos_pasos.md`](docs/proximos_pasos.md) | Bitácora de investigación |
| [`garmin_mcp/README.md`](garmin_mcp/README.md) | Servidor MCP: arranque, configuración, problemas frecuentes (429/403, certificados) y catálogo de las 42 tools (generado) |
| [`CHANGELOG.md`](CHANGELOG.md) | Cambios (los pendientes, en `CLAUDE.md`) |

## Privacidad

Cada usuario entra con su cuenta de Supabase Auth y la base de datos solo le deja ver sus filas (seguridad por fila, [`supabase/README.md`](supabase/README.md)). La contraseña la guarda Supabase Auth como hash y la de Garmin va cifrada con Fernet (`ENCRYPTION_KEY`). Los tokens de Garmin viven fuera del repo y los datos reales del atleta solo en rutas ignoradas por git.
