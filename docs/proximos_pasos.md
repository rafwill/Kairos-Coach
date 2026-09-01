# Próximos pasos — Kairos Coach

Última actualización: 2026-09-01

---

## Estado actual

Validación E2E del punto 55 completada y ampliada:
- Batería base: cerrada.
- Batería complementaria (10 casos nuevos): cerrada.
- Evidencia documentada en `docs/testing-bateria-punto55.md`.

Modelo operativo usado hoy:
- `nvidia/nemotron-3.5-lightning-30b-a3b`

---

## Dónde nos hemos quedado hoy

Quedamos en hardening de fechas/patrones ya aplicado y revalidado en runtime:
- `entre el 25/08 y el 30/08` resuelto como rango literal (sin expansión a semana natural).
- Soporte adicional de parser para:
	- `del X al Y`
	- `del X a Y`
	- `entre el X y el Y`
	- `desde X hasta Y`

Además, quedaron consolidados estos fixes de coherencia:
- Comparativa de `week_tss` histórica contra semana previa real (no contra semana actual).
- `week_activities` muestra `Rango consultado` exacto.
- `última actividad` y `última actividad de trail` resuelven por factual reciente.
- `qué me toca hoy` enrutado a `daily_readiness` determinista.
- Manejo robusto de `403 Forbidden` del proveedor LLM (sin crash).

---

## Próximo arranque (mañana)

### Paso 1 — Empezar por cierre técnico corto
1. Ejecutar smoke de regresión de fechas/intents (tests focales).
2. Commit + push del lote pendiente (código + tests + documentación).

### Paso 2 — Verificación rápida E2E post-commit
1. Lanzar Kairos con `rafwill1@hotmail.com`.
2. Repetir 3 consultas sentinela de fechas:
	 - `que actividades hice entre el 25/08 y el 30/08?`
	 - `cuanto tss hice en la semana del 27/07 al 02/08?`
	 - `como fue mi actividad del 30/08?`

### Paso 3 — Empezar la siguiente prioridad funcional
1. Retomar evaluación/plan del punto 9 del TODO: MCP propio dentro del proyecto basado en Essentials de Garmin MCP.
2. Definir alcance mínimo: catálogo de tools, contrato de entrada/salida y rutas deterministas iniciales.

---

## Nota operativa

Gemini puede fallar por red corporativa (Zscaler). Para validación estable en este entorno, mantener NVIDIA como modelo por defecto durante pruebas E2E.
