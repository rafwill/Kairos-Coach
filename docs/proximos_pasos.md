# Próximos pasos — Kairos Coach

Última actualización: 2026-09-01

---

## Estado actual

Estamos en medio de la **validación E2E del punto 55** usando la batería de 10 preguntas documentada en `docs/testing-bateria-punto55.md`.

Modelo activo: `nvidia/nemotron-3.5-lightning-30b-a3b`

---

## Cambios aplicados en esta sesión (pendientes de commit/confirmar)

### Fixes aplicados hoy — aún sin commitear
| Archivo | Cambio |
|---|---|
| `agent/trainer_agent.py` | Sport-filter lookup para "última actividad de trail/running" |
| `agent/trainer_agent.py` | Ruta determinista "cuántas actividades" desde DB series |
| `agent/trainer_agent.py` | `week_tss` table formato `/carga` (CTL/ATL/TSB/Estado) |
| `agent/trainer_agent.py` | `load_trend` vista semana-a-semana (cierre domingo) |
| `agent/trainer_agent.py` | `daily_readiness` siempre lanza LLM coaching con 4 secciones |
| `agent/trainer_agent.py` | `_is_daily_readiness_intent`: añadidos "mañana", "necesito recuperar/descansar" |
| `agent/trainer_agent.py` | Trazas timing `[LLM]`, `[STARTUP]`, `[SNAPSHOT]` |
| `agent/trainer_agent.py` | Nemotron `enable_thinking=False` |
| `agent/trainer_agent.py` | Timeout LLM: default 300s, cap 300s |
| `agent/main.py` | Contador de segundos durante verificación Garmin al arranque |

### Commits ya pusheados (ver CHANGELOG.md)
- `016f381` — perf/fix: paralelización MCP, essentials 40 tools, Nemotron 3.5
- `f813ae6` — fix/ux: routing daily_readiness, semanas en load_trend, contadores

---

## Batería de validación punto 55 — Estado

| # | Pregunta | Estado |
|---|---|---|
| B1-1 | `¿Cómo está mi forma física hoy?` | ✅ Pasado |
| B1-2 | `¿Cuál es mi tendencia de carga de las últimas 4 semanas?` | ✅ Pasado |
| B2-1 | `¿Cuánto TSS hice esta semana?` | ⚠️ Fix aplicado, pendiente confirmar |
| B2-2 | `¿Qué actividades hice esta semana?` | ⬜ No testado |
| B3-1 | `Analiza mi última actividad` | ⚠️ Fix aplicado, pendiente confirmar |
| B3-2 | `¿Cómo fue mi actividad del [fecha]?` | ⬜ No testado |
| B3-3 | `Analiza mi ultima actividad de trail` | ⚠️ Fix sport-filter aplicado, pendiente confirmar |
| B4-1 | `¿Puedo entrenar fuerte mañana o necesito recuperar?` | ⚠️ Fix routing aplicado, pendiente confirmar |
| B4-2 | `¿Qué tipo de sesión me recomiendas para esta semana?` | ⬜ No testado |
| B5-1 | `/menu` | ⬜ No testado |
| B5-2 | `¿Cuántas actividades tienes registradas en tu base de datos?` | ⚠️ Fix aplicado, pendiente confirmar |

---

## Plan para mañana

### Paso 1 — Commit y push de los cambios pendientes
```powershell
cd C:\Github\garmin-ai-coach
git add -A
git commit -m "fix: sport-filter actividades, /carga format week_tss, load_trend semanal, coaching sections, timing traces"
git push
```

### Paso 2 — Arrancar Kairos y completar los 6 checks pendientes
```powershell
.venv\Scripts\python.exe -m agent.main
```
Completar las preguntas ⚠️ y ⬜ de la tabla anterior y anotar resultados en `docs/testing-bateria-punto55.md`.

### Paso 3 — Cerrar punto 55 si todos pasan
- Marcar el punto 55 como cerrado en `TODO.md`
- Actualizar `CHANGELOG.md` con fecha de cierre

### Paso 4 — Siguiente prioridad: punto 37
**Integración TrainingPeaks MCP** (capa de escritura):
- Añadir `trainingpeaks-mcp` como servidor MCP secundario
- Funciones clave: `tp_create_workout`, `tp_pair_workout`, `tp_get_fitness`, `tp_get_atp`
- Ver TODO.md sección 37 para detalles

---

## Issues conocidos / limitaciones actuales

| Issue | Estado |
|---|---|
| Nemotron tarda 40-120s en responder (modelo lento) | Aceptado para depuración; cambiar a Gemini cuando sea estable |
| "Analiza mi última actividad de running" puede traer trail si el typeKey es ambiguo | Fix de sport-filter aplicado, por confirmar |
| Datos incorrectos en análisis de actividad si el LLM no usa el pre-fetch context | Fix de sport-filter previene el fallback al LLM libre |
