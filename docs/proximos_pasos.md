# Proximos pasos - Kairos Coach

Ultima actualizacion: 2026-09-13 (noche)

## Estado vigente

1. Trail: causa principal cerrada (duracion de entrada `hours`) y formula consolidada en `TSS_FORMULA_VERSION=24`.
2. Trail: tabla operativa vigente en `docs/tss_trail_metodo_desde_2026-07-01.csv` con validacion de anclas estable.
3. Validacion transversal abierta:
   - Hiking con desviacion alta en `24013969366`.
   - Running con sesgo negativo agregado en tabla independiente (`n=21`, `MAE=7.11`, `bias=-4.33`).
4. MCP Garmin inestable por mezcla `429` y `403` dentro de la misma ventana.

## Objetivo de la siguiente iteracion

Cerrar la validacion pendiente sin contaminar resultados por inestabilidad MCP.

Orden obligatorio de trabajo:

1. Confirmar salud MCP con re-login aislado y sonda lenta.
2. Diagnosticar hiking (actividad `24013969366`) rama-por-rama.
3. Diagnosticar running en `24227364808`, `24059814335`, `24201327869`.
4. Solo despues recalcular tabla independiente y actualizar conclusiones.

## Arranque rapido (checklist)

1. Esperar ventana de enfriamiento real (`15-30 min` sin llamadas MCP).
2. Ejecutar sonda segura:
   - `c:/Github/garmin-ai-coach/.venv/Scripts/python.exe tools/mcp_relogin_probe.py`
3. Revisar salida JSON en `docs/mcp_relogin_probe_YYYYMMDD_HHMMSS.json`.
4. Si hay `429` inmediato (edad de sesion baja), tratar como bloqueo de cuenta/IP y no continuar.
5. Si el probe sale limpio, ejecutar trazas diagnosticas (hiking primero).

## Criterios de decision para el probe

1. `relogin_ok=true` y llamadas probe sin error: se puede continuar a trazas funcionales.
2. Primer `429` en pocos segundos tras abrir sesion: bloqueo de tasa a nivel cuenta/IP probable.
3. Secuencia `429` seguida de `403` en la misma corrida: posible escalada anti-abuso; detener intentos y ampliar espera.

## Artefactos oficiales actuales

1. Trail operativo:
   - `tools/regenerate_trail_tss_table.py`
   - `docs/tss_trail_metodo_desde_2026-07-01.csv`
2. Comparativa independiente general:
   - `tools/generate_independent_tp_table.py`
   - `docs/tss_independiente_desde_2026-07-01_hasta_2026-09-13.csv`
   - `docs/tss_independiente_desde_2026-07-01_hasta_2026-09-13.md`
3. Diagnostico MCP:
   - `tools/mcp_relogin_probe.py`
   - `docs/mcp_relogin_probe_20260913_214913.json`

## Entregables de cierre de la siguiente iteracion

1. Informe hiking con rama efectiva, fuente de duracion y justificacion del metodo final.
2. Informe running con reconciliacion explicita contra el corte `MAE=5.823 / bias=+0.941`.
3. Si cambia comportamiento de formula, actualizar:
   - `README.md`
   - `docs/trail-tss-cierre-2026-09-13.md`
   - `CHANGELOG.md`

## Referencia de continuidad

Para una guia paso a paso de reanudacion, usar `docs/siguiente_iteracion_playbook_2026-09-13.md`.
