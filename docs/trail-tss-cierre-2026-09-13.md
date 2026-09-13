# Cierre Trail TSS - 2026-09-13

## Metadatos

- Fecha de cierre: 2026-09-13
- Formula vigente: `TSS_FORMULA_VERSION=24`
- Alcance: actividades trail con TP local desde 2026-07-01

## Estado

- Causa raiz principal cerrada: la sobreestimacion alta venia del calculo de duracion de entrada (`hours`).
- La rama de calculo `non_fast:lthr_details` no se modifico como respuesta principal al desvio grande.

## Estado post-cierre (validacion cruzada abierta)

- Hiking: actividad `24013969366` muestra desviacion alta (`113.0` vs `61.0`, ratio `1.853`) y cambio de etiqueta observado en tabla independiente; pendiente traza determinista rama-por-rama.
- Running: en comparativa independiente actual aparece sesgo negativo agregado (`n=21`, `MAE=7.11`, `bias=-4.33`) pendiente de reconciliacion contra el corte previo oficial.
- MCP Garmin: diagnostico de estabilidad bloqueado temporalmente por mezcla de `429` y `403` dentro de la misma ventana de sesion.

## Referencias

- Tabla trail vigente: `docs/tss_trail_metodo_desde_2026-07-01.csv`
- Script de regeneracion: `tools/regenerate_trail_tss_table.py`
- Plan de continuidad: `docs/proximos_pasos.md`
- Tabla independiente multideporte: `docs/tss_independiente_desde_2026-07-01_hasta_2026-09-13.csv`
- Sonda de estabilidad MCP: `tools/mcp_relogin_probe.py`
- Resultado de referencia de la sonda: `docs/mcp_relogin_probe_20260913_214913.json`

## Evidencia clave

1. Validacion de anclas estable en regeneracion automatica:
   - `23455001968` -> ratio `1.000`
   - `24107904670` -> ratio `1.023`
   - `23852863121` -> ratio `1.001`
2. Barrido completo trail (11 actividades con TP):
   - 7 actividades convergen en torno a `1.00-1.02`.
   - 2 actividades quedan con infraestimacion notable (`~0.79`, `~0.76`).
3. Cobertura de muestras HR alta en los dos casos residuales (`~1.000` y `~0.976`), por lo que no se atribuye a falta de señal HR.

## Artefactos oficiales

- Tabla de comparativa trail vigente:
  - `docs/tss_trail_metodo_desde_2026-07-01.csv`
- Script reproducible para regenerar tabla y validar anclas:
  - `tools/regenerate_trail_tss_table.py`

## Columnas canonicas de analisis

- Baseline historico:
  - `tss_prod_corregido`, `ratio_tss_tp`
- Recalculo vigente vs TP:
  - `tss_recomputed_live`, `ratio_recomputed_vs_tp`, `delta_recomputed_vs_tp`
- Diagnostico causal:
  - `duration_hours_resolved`, `hr_coverage_ratio`
  - `grade_source`, `grade_p50`, `grade_steep_up_share`

## Operativa recomendada para siguientes datos

1. Esperar entrada de 2-3 trails cortas nuevas con TP.
2. Ejecutar `tools/regenerate_trail_tss_table.py`.
3. Evaluar evolucion de `ratio_recomputed_vs_tp` en funcion de `duration_hours_resolved`.
4. Mantener separacion explicita entre patron observado y causa confirmada hasta aumentar muestra.
