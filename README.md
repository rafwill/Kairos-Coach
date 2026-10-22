# 🏃‍♂️ Kairos Coach

Entrenador deportivo personal con IA que funciona en la terminal sobre tus datos reales de Garmin Connect. Calcula la carga de cada sesión como TrainingPeaks (TSS, Estado físico CTL, Fatiga ATL y Forma TSB), recuerda tu perfil y tus conversaciones y genera planes de entrenamiento versionados a partir de tu objetivo y de lo que realmente has entrenado.

**Los números los calcula Python; el LLM solo interpreta.** Las preguntas de datos (TSS de una semana, umbrales, plan activo, récords) se responden por rutas deterministas sin LLM. Las de coaching ("¿qué me toca hoy?", "¿puedo entrenar fuerte mañana?") muestran primero los datos y después la interpretación del LLM, limitada a esos datos.

## Qué hace

- **Briefing al arrancar:** Body Battery, HRV, sueño, entrenamientos de las últimas 48 h, estado de carga por zonas de Forma (Joe Friel, relativas a tu CTL) y plan del día.
- **Carga como TrainingPeaks:** un TSS por actividad con el método de su deporte, guardado una vez; PMC 42/7. Validado contra el export de TP del atleta (tabla abajo).
- **Planes de entrenamiento:** motor determinista multisemana (base → build → peak → taper) que parte del volumen real de las últimas 4 semanas, nunca sube más de un 10 % por semana y guarda cada versión.
- **Coaching con contexto:** perfil, condiciones de salud (protocolo de diabetes tipo 1 con insulina), objetivo, umbrales con zonas en lpm y plan activo.
- **Multiusuario:** cada usuario con su perfil, historial, planes y base de conocimiento en Supabase.
- **Seis proveedores de LLM:** Gemini, Groq, Mistral, Cerebras, NVIDIA NIM y GitHub Models; se cambia en caliente con `/modelo`.

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

El setup crea `.venv`, instala dependencias, copia `.env.example` a `.env` y genera `ENCRYPTION_KEY`. Después:

1. Completa `.env`: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` y al menos una API key de LLM (`NVIDIA_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `CEREBRAS_API_KEY` o `GITHUB_TOKEN`). Cada proveedor admite su `*_MODEL`; ver `.env.example`.
2. Ejecuta [`supabase/schema.sql`](supabase/schema.sql) en el SQL Editor de Supabase (New query → pegar → Run). Es el único fichero de esquema: crea todas las tablas en un proyecto nuevo y, en uno ya existente, añade solo lo que falte sin tocar los datos.

## Uso

```powershell
.venv\Scripts\python.exe -m agent.main     # Windows
.venv/bin/python -m agent.main             # Unix/macOS
```

Kairos pide el usuario, muestra los proveedores con clave configurada y arranca su servidor MCP propio (`agent/kairos_mcp_server.py`, solo lectura). Los tokens de Garmin se reutilizan entre arranques en `~/.garminconnect/kairos/`.

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

Con `make` disponible: `make setup`, `make serve`, `make test`, `make lint`, `make lint-mcp` (guía de estilo MCP), `make health-mcp` (las 41 tools contra Garmin).

## Estructura

```
agent/
  main.py                CLI: login, proveedor LLM, comandos y chat
  trainer_agent.py       agente: rutas deterministas, coaching LLM, planes, carga
  load_metrics.py        TSS por deporte y PMC (CTL/ATL/TSB)
  running_tss.py         pipeline físico de running (NGP con pendiente, rTSS)
  storage.py             persistencia multiusuario en Supabase
  kairos_mcp_server.py   servidor MCP propio sobre Garmin Connect (solo lectura)
  mcp_client.py, mcp_adapter.py   cliente MCP y contratos de tools
prompts/                 system prompt (completo y compacto) y guía de tools MCP
supabase/                schema.sql (esquema completo e idempotente)
tests/                   suite de pytest (sin red: Garmin, Supabase y LLM mockeados)
tools/
  rebuild_tss_baseline.py   corrección del histórico de TSS (prueba; --apply escribe)
  run_bateria.py            batería de preguntas contra Kairos con tiempos por respuesta
  juez_bateria.py           juez determinista de la batería (rúbrica fija frente a Supabase)
  lint_mcp.py, mcp_lint/    guía de estilo MCP de apiaddicts sobre las tools del servidor (Spectral)
  healthcheck_mcp.py        healthcheck en vivo: llama a todas las tools contra Garmin y dice cuáles fallan
  garmin-mcp-frozen.*       lanzadores del servidor MCP
docs/                    arquitectura, harness, bitácora, runbook del MCP y batería
```

Rutas locales que nunca se suben (están en `.gitignore`): `.env`, `_archivo/` (evidencias de calibración, exports de TP y herramientas de diagnóstico), `fixtures/real_payloads/` (datos reales de Garmin), `.tmp/` (salidas de la batería) y `memory/`.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest           # 570 passed, 6 skipped (22/10/2026)
.venv\Scripts\python.exe tools\run_bateria.py --salida .tmp\bateria\hoy
.venv\Scripts\python.exe tools\juez_bateria.py .tmp\bateria\hoy.md   # comprueba cada respuesta contra Supabase
```

La suite no necesita red. Los 6 tests saltados son la prueba de humo del MCP con Inspector (`RUN_MCP_INSPECTOR_SMOKE=1`). La CI (`.github/workflows/tests.yml`) ejecuta pytest en cada push y pull request. La batería (`docs/bateria_p55_preguntas_base.txt`) se lanza contra datos reales y necesita `KAIROS_USERNAME` en `.env`.

## Documentación

| Documento | Contenido |
| --- | --- |
| [`CLAUDE.md`](CLAUDE.md) | Estado vigente del cálculo de carga, reglas de método, datos y pendientes |
| [`docs/arquitectura.md`](docs/arquitectura.md) | Capas, tablas de Supabase, flujos de arranque, cálculo y chat, MCP y seguridad |
| [`docs/Harness.md`](docs/Harness.md) | Hooks, router determinista y sensores de calidad |
| [`docs/proximos_pasos.md`](docs/proximos_pasos.md) | Bitácora de investigación |
| [`docs/mcp-frozen-runbook.md`](docs/mcp-frozen-runbook.md) | Operación del servidor MCP e incidentes 429/403 |
| [`CHANGELOG.md`](CHANGELOG.md) · [`TODO.md`](TODO.md) | Cambios y hoja de ruta |

## Privacidad

Las contraseñas no se guardan en claro: la de la app con hash PBKDF2-SHA256 y la de Garmin cifrada con Fernet (`ENCRYPTION_KEY`). Los tokens de Garmin viven fuera del repo y los datos reales del atleta solo en rutas ignoradas por git.
