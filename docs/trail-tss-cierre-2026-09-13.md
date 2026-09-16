# Cierre Trail TSS - 2026-09-13

> Estado: archivado como documento intermedio.
> Referencia consolidada vigente: `docs/tss-cierre-definitivo-2026-09-16.md`.

## Metadatos

- Fecha de cierre: 2026-09-13
- Formula vigente: `TSS_FORMULA_VERSION=24`
- Alcance: actividades trail con TP local desde 2026-07-01

## Estado

- Causa raiz principal cerrada: la sobreestimacion alta venia del calculo de duracion de entrada (`hours`).
- La rama de calculo `non_fast:lthr_details` no se modifico como respuesta principal al desvio grande.

## Estado post-cierre (validacion cruzada abierta)

- Hiking: actividad `24013969366` muestra desviacion alta (`113.0` vs `61.0`, ratio `1.853`) y cambio de etiqueta observado en tabla independiente; pendiente traza determinista rama-por-rama.
- Running: `5.823/+0.941` no tiene artefacto canonico reproducible en repo; el comportamiento actual reproducible es `~78.7` para este caso, introducido en `940f03cc` junto con el pipeline fisico.
- Running: pendiente evaluar si ese resultado es mejora o regresion de precision para patron `fartlek/Z3`, dado que el metodo anterior (`~90.0`) estaba mas cerca de `TP=91.0` en esta actividad concreta.
- Hallazgo adicional (Variante A): esta actividad no activa la regla (`cv_if=0.147`, `transitions_per_h=71.2`, `share_fast=0.056`, todos por debajo de umbral) pese a ser un fartlek con bloque sostenido en Z3; posible hueco de cobertura para patron `tempo sostenido` frente a `repeticiones cortas`.
- Actualizacion post-prueba final junio-septiembre (v25): el frente abierto principal de running ya no son los patrones intervalados, sino `rodaje_continuo` por infraestimacion sistematica (`n=26`, `bias=-6.953118`, `MAE=7.260891`), con contribucion dominante al sesgo global de running.
- Fuerza movilidad/activacion (Tarea 2): IF recalibrado de `0.50` a `0.552292` con muestra junio (5 sesiones). Este encaje en la muestra usada para calibrar es evidencia algebraica in-sample; la validacion empirica out-of-sample de esa subcategoria sigue pendiente por falta de casos con TP (`n=0` en julio-septiembre).
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
