# Validacion E2E real de carga/fatiga (2026-08-15)

## Objetivo
Cerrar la validacion operacional real de TSS, ATL, CTL y TSB con evidencia ejecutable y trazable.

## Entorno
- Repo: Kairos-Coach
- Fecha: 2026-08-15
- Runtime: agente en ejecucion real con usuario activo
- Fuente de evidencia principal: `tmp/tss_load_120d_report.json`, `tmp/tss_load_120d_daily.csv`

## Evidencia de serie real persistida
Resumen extraido de `tmp/tss_load_120d_report.json`:
- Ventana: 2026-04-16 -> 2026-08-14 (120 dias)
- Actividades procesadas: 109
- Total TSS agregado (todos los tipos): 8152.52
- Distribucion por tipo:
  - rTSS: 2854.02 (44 actividades)
  - hrTSS: 2773.33 (22 actividades)
  - sTSS: 732.04 (21 actividades)
  - TSS: 1793.13 (22 actividades)
- Ultima carga diaria registrada (2026-08-14):
  - ATL=55.23
  - CTL=60.49
  - TSB=5.26

## Contraste de muestra actividad vs TSS de referencia
Comando ejecutado:

`c:/Github/Garmin-AI/.venv-1/Scripts/python.exe tools/fit_tss_probe.py tmp/tp-2026-08-13.fit --threshold 245`

Resultado relevante:
- Actividad: running, 11.01 km, timer=3625.289s, avg_hr=130
- TSS por metodo promedio (avg_method): 55.71
- TSS por serie punto a punto (series_method): 57.69
- Delta: +1.98 TSS
- Interpretacion: diferencia baja, consistente con una sesion mixta/intervalica (deteccion: `likelihood=high`).

## Conclusion de validacion
- La serie de carga/fatiga persistida en DB muestra continuidad y coherencia operacional (120 dias, 109 actividades).
- El contraste de muestra con FIT real produce TSS consistente entre dos metodos de estimacion.
- Se considera cerrada la validacion E2E real para el alcance definido en TODO #55.

## Artefactos
- `tmp/tss_load_120d_report.json`
- `tmp/tss_load_120d_daily.csv`
- `tmp/tp-2026-08-13.fit`
- `tools/fit_tss_probe.py`

## Addendum (2026-08-18)
- Se aplicaron mejoras de consistencia en rutas deterministas de consulta factual/semanal y en formato de salida.
- Se activó contrato de plantilla única en prompts (completo y compacto) y se alinearon respuestas deterministas al mismo esquema.
- Estas mejoras no modifican el criterio de validez del modelo TSS/ATL/CTL/TSB documentado arriba; afectan presentación, robustez de consulta y frescura de datos.
- Estado de regresión local posterior a estos cambios: `python -m pytest -q` en verde (`353 passed`).
