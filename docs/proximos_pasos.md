# Proximos pasos - Kairos Coach

Ultima actualizacion: 2026-09-04

---

## Recomendacion concreta para hoy (primero de todo)

Objetivo: no romper nada, dejar todo guardado en GitHub y retomar manana exactamente en este punto.

1. Crear rama checkpoint desde el estado actual.
2. Excluir temporales `.tmp_*` para no contaminar commits.
3. Limpiar temporales locales que no formen parte del producto.
4. Ejecutar validacion rapida de regresion antes de commitear.
5. Hacer 2 commits separados:
	- Commit 1: codigo + tests.
	- Commit 2: documentacion + evidencia de bateria.
6. Hacer push de la rama checkpoint a GitHub.
7. Crear tag de recuperacion del punto exacto.
8. Manana, retomar desde esa rama antes de seguir cambiando codigo.

Comandos recomendados:

1. `git checkout -b checkpoint/2026-09-04-estabilizacion`
2. Actualizar `.gitignore` con: `.tmp_*`
3. `git clean -f .tmp_*`
4. `c:/Github/garmin-ai-coach/.venv/Scripts/python.exe -m pytest -q tests/test_trainer_agent.py -k "estimate_session_tss or compute_load_fatigue_metrics or weekly_spike or load_trend or week_tss"`
5. Commit de codigo/tests.
6. Commit de docs/evidencias.
7. `git push -u origin checkpoint/2026-09-04-estabilizacion`
8. `git tag checkpoint-2026-09-04`
9. `git push origin checkpoint-2026-09-04`

Regla de seguridad para hoy:
1. No hacer push directo a `main` hasta revisar en frio manana.

---

## Estado actual

Punto en el que estamos:
1. Estabilizacion de punto 55 completada con revalidaciones E2E reales documentadas.
2. Refactor de calculo en `agent/load_metrics.py` completado (fase 1 y fase 2).
3. Verificacion forense pre/post refactor realizada: no se detectan cambios en formulas base ATL/CTL/TSB/TSS ni en el set de llamadas MCP usadas por Kairos durante la ventana de refactor.
4. Ajustes de coherencia ya aplicados y documentados en bateria: readiness 48h, transparencia de fallback y conciliacion de fuentes de TSS semanal.

Evidencia viva:
1. `docs/testing-bateria-punto55.md`
2. `docs/refactor-load-metrics-2026-09-02.md`

---

## Siguientes pasos (priorizados)

1. Revision de formulas. MUcha nueva variacion. Habria que hacer una tabla comparativa de hrTss, Tss bruto, Tss kairos como ya hicimos anteriormente.
2. Revision de TSS del fartlek del dia 3.
3. Revision de las recomendaciones, creo que no esta llamando al LLM.
4. Revision: Generacion de un flujo completo de prueba... copilot pregunta, kairos responde en Proximos pasos, copilot lo lee y toma la decision de que hacer y asi tiene un dialogo real de comprobacion de la conversacion.
5. Revision del nuevo MCP y eliminar todo lo antiguo.
6. Cuando todo lo anterior funcione, buscar incongruencias y errores en el codigo, asi como eliminar ficheros que ya no sirvan.

---

## Propuesta adicional

Siguiente paso natural:

1. Si quieres, agrego una prueba de equivalencia explicita pre/post (golden test) para bloquear cualquier desviacion futura en ATL/CTL/TSB/TSS con el mismo fixture.

---

## Plan de ejecucion recomendado

Fase A - Control de calculo:
1. Construir tabla comparativa por actividad y por dia: hrTSS, TSS bruto Garmin, TSS Kairos canonico.
2. Auditar de forma especifica el fartlek del 03/09 (payload bruto, estimador aplicado, valor final persistido).
3. Cerrar criterios de prioridad de fuente por ruta (`week_tss`, readiness, `load_trend`) y documentarlos.

Fase B - Control de recomendaciones:
1. Trazar decision de recomendaciones para confirmar en que rutas entra LLM y en cuales no.
2. Verificar logs de tool routing y tiempo de respuesta para detectar bypass accidental del LLM.
3. Definir criterio objetivo de "LLM llamado correctamente" y testearlo.

Fase C - Flujo de prueba conversacional end-to-end:
1. Definir un guion de dialogo real (copilot pregunta -> kairos responde -> copilot decide siguiente pregunta).
2. Ejecutar y registrar en este repositorio una corrida completa reproducible.
3. Comparar decisiones esperadas vs observadas y abrir lista de desvios.

Fase D - Consolidacion MCP:
1. Migrar al nuevo MCP como camino principal.
2. Eliminar adaptadores, rutas y codigo legado que ya no aporten.
3. Correr regresion completa y limpieza final de artefactos/documentos obsoletos.

---

## Nota operativa

Para nuevas revalidaciones largas en Windows, mantener append UTF-8 controlado en evidencias para evitar mojibake.

---

## Conclusiones de cierre (2026-09-04)

Ya estan los 2 commits separados; ahora hago push de la rama checkpoint y creo el tag de recuperacion exacta.

`Executed git push and tagged checkpoint branch`
`Ran git push -u origin checkpoint/2026-09-04-estabiliz...`
`Ran git tag checkpoint-2026-09-04; git push origin che...`

Ejecutado completo con minimo riesgo, y ya esta todo guardado en GitHub.

Resultado:

1. Rama checkpoint creada y activa:
	- `checkpoint/2026-09-04-estabilizacion`
2. Regla anadida para temporales:
	- `.gitignore`
3. Temporales `.tmp_*` limpiados del working tree.
4. Validacion ejecutada:
	- `pytest` focal -> 33 passed, 0 failed.
5. Commit 1 (codigo + tests):
	- `fb7df1b feat: estabilizacion readiness/tss y base nuevo mcp frozen`
6. Commit 2 (docs + evidencias):
	- `68b27d2 docs: revalidacion e2e, plan de continuidad y runbook mcp`
7. Push de rama realizado a origin.
8. Tag de recuperacion creado y subido:
	- `checkpoint-2026-09-04`

Estado final:

1. HEAD en:
	- `68b27d2`
2. Rama remota disponible:
	- `origin/checkpoint/2026-09-04-estabilizacion`
3. Tag remoto disponible:
	- `checkpoint-2026-09-04`
4. Working tree limpio.

Nota:
GitHub respondio que el repo fue movido y recomienda la nueva URL:
`https://github.com/rafwill/Kairos-Coach.git`
El push igualmente se realizo correctamente en el remoto actual.

Para retomar manana:

1. `git fetch --all`
2. `git checkout checkpoint/2026-09-04-estabilizacion`
3. `git pull`

---

## Guia para continuar desde otro ordenador

Que hay ahora mismo:

1. Main sigue existiendo y no es la rama de trabajo de este checkpoint.
2. Tu estado exacto de cierre esta en la rama `checkpoint/2026-09-04-estabilizacion`.
3. El tag `checkpoint-2026-09-04` apunta a ese punto exacto (foto inmutable).

Si quieres continuar exactamente donde lo dejamos (recomendado):

1. En el otro ordenador, trae todo del remoto:
	- `git fetch --all --tags`
2. Cambia a la rama checkpoint:
	- `git checkout -b checkpoint/2026-09-04-estabilizacion origin/checkpoint/2026-09-04-estabilizacion`
	- (si ya existe local: `git checkout checkpoint/2026-09-04-estabilizacion`)
3. Actualiza:
	- `git pull`

Con eso quedas exactamente en el mismo punto de trabajo.

Para volver a tu flujo normal en main:

1. Terminas lo pendiente en la rama checkpoint.
2. Cuando este validado, haces merge a main (PR o merge directo).
3. A partir de ahi, en cualquier ordenador vuelves al flujo normal:
	- `git checkout main`
	- `git pull`

Para que sirve cada cosa:

1. Rama checkpoint: donde sigues desarrollando.
2. Tag `checkpoint-2026-09-04`: marcador de recuperacion exacta, por si necesitas volver a ese snapshot.
3. Main: estable/habitual; no la tocaste durante el cierre de minimo riesgo.

Nota practica:

1. Si en el otro equipo tienes el remoto antiguo y quieres dejarlo limpio con el nombre nuevo:
	- `git remote set-url origin https://github.com/rafwill/Kairos-Coach.git`
