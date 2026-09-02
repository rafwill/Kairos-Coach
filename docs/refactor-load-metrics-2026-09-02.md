# Refactorizacion Load Metrics (2026-09-02)

Objetivo: desacoplar la logica de calculo de TSS, CTL, ATL y TSB de la capa conversacional para reutilizarla en futuros proyectos sin cambiar comportamiento de Kairos.

## Paso 1. Extraccion del modulo reusable

Archivo creado: `agent/load_metrics.py`

Contenido extraido:
- Estimadores de TSS por modalidad (`_estimate_session_tss` y helpers internos).
- Parseo y normalizacion de zonas FC para hrTSS.
- Resolucion de parametros fisiologicos (umbral running, FC reposo/max).
- Modelo de carga/fatiga EWMA (`_compute_load_fatigue_metrics`).
- Señales semanales (`_compute_weekly_spike_signal`).
- Configuracion por deporte (`_resolve_sport_model_cfg`).
- Version de formula compartida (`TSS_FORMULA_VERSION`).

Criterio: mantener firmas y reglas para evitar cambios funcionales.

## Paso 2. Integracion sin ruptura en TrainerAgent

Archivo modificado: `agent/trainer_agent.py`

Cambios:
- Nuevo import: `from agent import load_metrics as _load_metrics`.
- `_TSS_FORMULA_VERSION` pasa a leer de `TSS_FORMULA_VERSION` del modulo extraido.
- Delegacion (wrappers) en funciones clave para preservar API historica usada por app y tests:
  - `_resolve_hr_profile_values`
  - `_resolve_running_threshold_pace_sec_per_km`
  - `_estimate_session_tss`
  - `_infer_tss_source_tag`
  - `_resolve_sport_model_cfg`
  - `_compute_weekly_spike_signal`
  - `_compute_load_fatigue_metrics`

Decisión de compatibilidad:
- No se rompieron imports existentes de `tests/test_trainer_agent.py`.
- No se alteraron rutas de respuesta ni formato de salida en esta fase.

## Paso 3. Regresion detectada y corregida

Problema encontrado durante E2E:
- Consulta: `¿Cuál es mi tendencia de carga de las últimas 4 semanas?`
- Comportamiento incorrecto observado: entraba por ruta `week_tss` en vez de `load_trend`.

Causa raiz:
- `_is_week_tss_followup_intent` era demasiado permisivo cuando habia contexto previo de TSS semanal.

Correccion aplicada:
- En `agent/trainer_agent.py`, `_is_week_tss_followup_intent` ahora corta si `_is_load_trend_intent(user_message)` es verdadero.

Test nuevo de proteccion:
- `test_is_week_tss_followup_intent_does_not_hijack_load_trend_query` en `tests/test_trainer_agent.py`.

## Paso 4. Verificaciones ejecutadas

Unit/regresion focal:
- `pytest -q tests/test_trainer_agent.py -k "compute_load_fatigue_metrics or estimate_session_tss or infer_tss_source_tag or resolve_sport_model_cfg or weekly_spike"`
- Resultado: OK.

Suite completa de TrainerAgent:
- `pytest -q tests/test_trainer_agent.py`
- Resultado: 325 passed.

Suite completa del repositorio:
- `pytest -q`
- Resultado final: 401 passed.

E2E runtime real con `rafwill1@hotmail.com` (NVIDIA NIM):
- `¿Cuánto TSS hice esta semana?` OK.
- `¿Qué actividades hice esta semana?` OK.
- `¿Puedo entrenar fuerte mañana o necesito recuperar?` OK.
- `¿Cuáles son mis récords personales running?` OK.
- `¿Cuál es mi tendencia de carga de las últimas 4 semanas?` OK (tras fix de routing).

## Paso 5. Estado y siguiente fase

Estado actual:
- Fase 1 completada y estable: modulo reusable extraido + compatibilidad mantenida + regresion controlada.

Siguiente fase sugerida:
- Reducir duplicacion residual en `agent/trainer_agent.py` eliminando implementaciones internas ya delegadas.
- Publicar API explicita del modulo (funciones publicas sin prefijo `_`) y documentar contrato de entrada/salida para reutilizacion externa.
