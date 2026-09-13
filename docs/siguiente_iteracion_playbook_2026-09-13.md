# Playbook Siguiente Iteracion (2026-09-13)

## Objetivo

Reanudar trabajo con riesgo minimo y cerrar pendientes de validacion abiertos:

1. Regresion hiking (actividad `24013969366`).
2. Reconciliacion del sesgo running en tabla independiente.
3. Evitar sesgos de diagnostico provocados por bloqueo MCP (`429`/`403`).

## Precondiciones

1. No lanzar llamadas MCP durante `15-30` minutos antes del primer intento.
2. Tener `.env` correcto (`GARMIN_EMAIL`, `GARMIN_PASSWORD`).
3. Ejecutar desde la raiz del repo.

## Paso 1 - Salud MCP

Comando:

```powershell
c:/Github/garmin-ai-coach/.venv/Scripts/python.exe tools/mcp_relogin_probe.py
```

Interpretacion minima:

1. Si hay `429` inmediato, detener y ampliar espera.
2. Si aparece secuencia `429 -> 403`, tratar como escalada anti-abuso y no continuar con trazas largas.
3. Solo continuar cuando el probe salga limpio.

## Paso 2 - Diagnostico hiking (prioridad alta)

Actividad objetivo: `24013969366`.

Validar en una misma corrida:

1. Tipo resuelto para routing.
2. Fuente de duracion efectiva (`activity`, `details_root`, `samples_last`, `zones`).
3. Rama de calculo que determina etiqueta final (`TSS` o `hrTSS`).
4. Delta frente a TP (`61.0`) y causa tecnica confirmada.

## Paso 3 - Diagnostico running (3 actividades)

Actividades objetivo:

1. `24227364808`
2. `24059814335`
3. `24201327869`

Validar por actividad:

1. `duration_hours_resolved` real.
2. Rama efectiva (pipeline con `activity_details` vs fallback).
3. Diferencia contra corte previo oficial (`MAE=5.823`, `bias=+0.941`).

## Paso 4 - Regeneracion de comparativa independiente

Comandos:

```powershell
c:/Github/garmin-ai-coach/.venv/Scripts/python.exe tools/generate_independent_tp_table.py
```

Y, si se requiere vista legible, actualizar Markdown asociado.

## Paso 5 - Cierre documental

Actualizar de forma consistente:

1. `README.md`
2. `docs/proximos_pasos.md`
3. `docs/trail-tss-cierre-2026-09-13.md`
4. `CHANGELOG.md`

## Criterio de cierre de iteracion

1. Hiking con causa tecnica confirmada (no solo patron observado).
2. Running reconciliado de forma explicita con evidencia reproducible.
3. Probe MCP estable en al menos una corrida limpia previa a trazas largas.