# Próximos pasos - Kairos Coach

Ultima actualizacion: 2026-09-02

---

## Estado actual

Cierre de hoy completado:
- Punto 55 (validacion E2E real): REALIZADO.
- Refactor de modulo de calculo load metrics:
	- Fase 1: REALIZADA.
	- Fase 2 (deduplicacion + API publica): REALIZADA.
- Publicado en remoto:
	- Commit: 3bbbce7
	- Rama: main
	- Push: origin/main

Validaciones ejecutadas en este cierre:
- `pytest -q tests/test_trainer_agent.py` -> 326 passed.
- `pytest -q` -> 401 passed.
- Smoke E2E real con usuario `rafwill1@hotmail.com` y modelo NVIDIA (opcion 4):
	- tendencia de carga 4 semanas -> OK
	- TSS semanal -> OK
	- readiness (entrenar fuerte o recuperar) -> OK
	- records running -> OK

Evidencia actualizada en:
- `docs/testing-bateria-punto55.md`
- `docs/refactor-load-metrics-2026-09-02.md`

---

## Donde nos hemos quedado

El sistema queda estable y publicado tras cerrar la Fase 2 del refactor, con una unica implementacion reusable para calculos de carga/fatiga y sin regresiones detectadas en tests ni en E2E de control.

---

## Que haremos al retomar

### Paso 1 - Reenganche rapido (10-15 min)
1. Ejecutar chequeo rapido:
	 - `git status -sb`
	 - `pytest -q tests/test_trainer_agent.py`
2. Ejecutar smoke E2E minimo (4 preguntas sentinela ya usadas en este cierre) para confirmar continuidad del runtime.

### Paso 2 - Siguiente prioridad del roadmap
1. Empezar el punto 9 del TODO (MCP propio basado en Essentials):
	 - inventario de tools realmente usadas
	 - contratos de entrada/salida minimos
	 - propuesta de adapter estable en Kairos
2. Definir plan de transicion con flag de backend (`frozen|upstream`) para rollback inmediato.

### Paso 3 - Entregable de la proxima sesion
1. Documento de diseno inicial del MCP propio (alcance minimo + fases + riesgos).
2. Lista priorizada de tools Essentials candidatas para implementar primero.

---

## Nota operativa

En red corporativa (Zscaler), mantener NVIDIA como modelo por defecto para pruebas E2E estables.
