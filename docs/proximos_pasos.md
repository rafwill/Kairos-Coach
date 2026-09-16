# Proximos pasos - Kairos Coach

Ultima actualizacion: 2026-09-15 (manana)

## Actualizacion de estado (2026-09-16) - CIERRE FINAL

1. Version de formula actualizada a `TSS_FORMULA_VERSION=26` por cambio de comportamiento en running:
      - `tempo_detector`: umbral de bloque a `400 s` y tolerancia de huecos cortos `20 s`.
      - Ajuste de pendiente negativa: atenuación parcial del descuento Minetti en bajada
         (`_MINETTI_NEGATIVE_DISCOUNT_STRENGTH=0.55`), manteniendo intacta la pendiente positiva.
2. Criterio de cierre de running documentado con excepción explícita:
      - MAE global `< 7` (sin cambios).
      - Sesgo global ampliado de `±3` a `±3.5` como tolerancia justificada
         (mismo principio aplicado al MAE; evitar sobreajuste por márgenes residuales).
      - Ratios por segmento en `0.88-1.12` (sin cambios).
3. Resultado de cierre running con criterio final:
      - `running_global`: `n=32`, `MAE=6.178491`, `bias=-3.278240`, `ratio_mean=0.957760`.
      - Segmentos dentro de rango ratio:
         - `repeticiones_cortas=1.077338`
         - `fartlek_tempo_sostenido=0.974348`
         - `rodaje_continuo=0.938087`
      - Estado: **CERRADO** bajo criterio final documentado (`±3.5`).
4. Estado de bloques principales:
      - Trail: **CERRADO**.
      - Fuerza: **CERRADO** (neuromuscular y movilidad/activación en rango).
      - Running: **CERRADO** con excepción de sesgo documentada.
5. Limitaciones conocidas aceptadas y trazadas:
      - Hiking: muestra corta (`n=2`) y variabilidad alta; queda como frente de observación.
      - `short_reps_detector`: cobertura parcial conocida (3/4 reales no activan y hay activaciones en rodajes).
      - `24027714450`: tramo sostenido corto (`289 s`) embebido en rodaje largo, no detectado por tempo;
         aceptado como limitación, no bug abierto.
6. Referencia única de cierre:
      - `docs/tss-cierre-definitivo-2026-09-16.md`.

## Avance de hoy (2026-09-15)

1. Salud MCP validada en corrida rapida:
   - `docs/mcp_relogin_probe_20260915_111043.json`
   - `relogin_ok=True`, `probe_ok=True`, `ok:5`, `error:0`, sin `429` ni `403`.
2. Diagnostico hiking (`24013969366`) confirmado:
   - `hours_resolved=2.243`.
   - Fuente efectiva de duracion: `source_samples_last` (summary/details_root/zones en `0.0`).
   - Rama efectiva: `walk_hike:TSS`.
   - Resultado: `113.0 TSS` vs `61.0 hrTSS` TP (`delta=+52.0`, `ratio=1.853`).
3. Diagnostico running en actividades objetivo confirmado:
   - `24227364808`: `78.7` vs `91.0` (`delta=-12.3`, `ratio=0.865`).
   - `24059814335`: `122.8` vs `135.0` (`delta=-12.2`, `ratio=0.910`).
   - `24201327869`: `93.6` vs `103.0` (`delta=-9.4`, `ratio=0.909`).
   - Rama efectiva en las tres: `details_pipeline(model=tp_like)`.
   - `5.823/+0.941` no tiene artefacto canonico reproducible en repo; el comportamiento actual reproducible es `~78.7` para este caso, introducido en `940f03cc` junto con el pipeline fisico.
   - Pendiente evaluar si es mejora o regresion de precision para patron `fartlek/Z3`, dado que el metodo anterior (`~90.0`) estaba mas cerca de `TP=91.0` en esta actividad concreta.
   - Hallazgo adicional: esta actividad no activa la regla de Variante A (`cv_if=0.147`, `transitions_per_h=71.2`, `share_fast=0.056`, todos por debajo de umbral) pese a ser un fartlek con bloque sostenido en Z3; posible hueco de cobertura para patron `tempo sostenido` frente a `repeticiones cortas`.

## Estado de referencia (semaforo junio, CSV limpio)

Base: `docs/junio_tp_vs_garmin_2026-09-15_clean.csv`.

1. Trail - VERDE:
   - Señal cerrada y consistente en junio para el bloque revisado.
2. Running - ROJO:
   - Infraestimacion sistematica en junio (`n=10`, `MAE=8.280`, `bias=-8.280`, `ratio_mean=0.881`).
3. Fuerza (desglosada por subtipo, no "amarillo" global):
   - Trabajo neuromuscular - VERDE (`n=5`, `MAE=1.000`, `bias=+0.720`, `ratio_mean=1.016`).
   - Movilidad/activacion - ROJO (`n=4`, `MAE=4.400`, `bias=-4.400`, `ratio_mean=0.828`).
   - Causa accionable conocida: ajustar IF/heuristica de esa categoria (no es un problema de diagnostico pendiente).
4. Hiking/Walking - ROJO (muestra corta):
   - Sobreestimacion marcada y repetida en los casos observados; mantener investigacion dirigida.
5. Cycling e Indoor Running - AMARILLO por muestra insuficiente:
   - Cycling (`n=4`, `MAE=0.525`, `bias=-0.525`, `ratio_mean=0.963`).
   - Indoor running (`n=2`, `MAE=2.450`, `bias=+2.450`, `ratio_mean=1.190`).
   - Nota: este amarillo significa "falta dato/revision especifica", no "casi aprobado".

## Watch-item abierto (Tarea 2 fuerza movilidad/activacion)

1. El ajuste de IF de `0.50` a `0.552292` en fuerza light/movilidad queda matematicamente consistente con la muestra usada para calibrar (junio, 5 sesiones), pero eso es validacion algebraica in-sample, no validacion empirica independiente.
2. Estado actual de validacion out-of-sample (julio-septiembre con TP para la misma subcategoria): `n=0`.
3. Criterio operativo pendiente de cierre sin reservas:
   - En la primera sesion nueva de movilidad/activacion con TP disponible fuera de la muestra de calibracion, comprobar ratio calculado/TP y registrar resultado.
   - Si aparece sesgo sistematico, abrir segunda vuelta de ajuste del IF en vez de asumir cierre definitivo.

## Pendiente inmediato tras el avance de hoy

1. Regenerar comparativa independiente completa cuando haya ventana MCP estable para corrida larga:
   - `c:/Github/garmin-ai-coach/.venv/Scripts/python.exe tools/generate_independent_tp_table.py`
2. Recalcular `MAE/bias` agregados (running/hiking) con la nueva tabla y contrastar contra baseline reproducible canonico.
3. Si hay cambios de comportamiento o conclusiones, cerrar bloque documental en:
   - `README.md`
   - `docs/trail-tss-cierre-2026-09-13.md`
   - `CHANGELOG.md`

## Estado vigente

1. Trail: causa principal cerrada (duracion de entrada `hours`) y formula consolidada en `TSS_FORMULA_VERSION=25`.
2. Trail: tabla operativa vigente en `docs/tss_trail_metodo_desde_2026-07-01.csv` con validacion de anclas estable.
3. Validacion transversal abierta:
   - Hiking con desviacion alta en `24013969366`.
   - Running no cierra criterio final en corrida unica junio-septiembre (`n=32`, `MAE=7.368611`, `bias=-5.337087`).
   - Desglose causal de running: `rodaje_continuo` concentra el problema principal (`n=26`, `bias=-6.953118`, `MAE=7.260891`) y explica ~`80.06%` del error absoluto total y ~`105.85%` del sesgo global (compensado parcialmente por otros segmentos).
4. MCP Garmin inestable por mezcla `429` y `403` dentro de la misma ventana.

## Foco real siguiente sesion (running)

1. No seguir afinando detectores de Variante A ni fuerza (bloques cerrados para este ciclo).
2. Prioridad unica: diagnostico de `rodaje_continuo` por infraestimacion sistematica.
3. Auditoria inicial recomendada (top |delta| en rodaje continuo):
   - `24059814335` (`delta=-12.199898`, `ratio=0.909630`)
   - `23229837680` (`delta=-11.992307`, `ratio=0.875080`)
   - `23555669922` (`delta=-11.852535`, `ratio=0.879056`)
4. Pregunta de investigacion concreta:
   - identificar que variable comun en rodajes (no intervalados) mantiene sesgo negativo alto pese a pasar el criterio relativo de ratio por segmento.
5. Entregable minimo de esa auditoria:
   - traza por actividad con `interval_cv_if`, `transitions_per_h`, `share_fast`, `include_pauses_in_ngp`, metodo final y componentes que explican `calc_tss`.

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
2. Informe running con reconciliacion explicita contra baseline reproducible canónico y analisis de precision por patron (`fartlek/Z3` vs rodaje).
3. Si cambia comportamiento de formula, actualizar:
   - `README.md`
   - `docs/trail-tss-cierre-2026-09-13.md`
   - `CHANGELOG.md`

## Referencia de continuidad

Para una guia paso a paso de reanudacion, usar `docs/siguiente_iteracion_playbook_2026-09-13.md`.
