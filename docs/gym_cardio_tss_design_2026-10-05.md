# Diseno Gym Cardio TSS (eliptica + remo)

Fecha: 2026-10-05
Rama: feature/gym-cardio-design
Estado: diseno aprobado para implementacion por fases

## 1) Objetivo

Definir un bloque Gym Cardio para actividades indoor de cardio sin pendiente real de terreno,
centrado inicialmente en:

- elliptical
- rowing

El objetivo no es copiar exactamente TP, sino mantener consistencia fisiologica interna,
trazabilidad de metodo y robustez ante payloads incompletos.

## 2) Principios de diseno

1. Ruta dedicada por modalidad (evitar rama generica opaca).
2. Anclaje principal por HR relativa (LTHR + HR reposo), coherente con fuerza y con hidratacion automatica ya existente.
3. Componente mecanico opcional cuando la senal exista y sea fiable.
4. Guardarrailes explicitos (clamps y source tags) para observabilidad y debugging.
5. Validacion por monotonia y coherencia interna; TP como referencia secundaria.

## 3) Modelo propuesto (fase 1)

### 3.1 Entrada minima

- duration_h
- avg_hr_bpm
- hr_rest_bpm
- hr_threshold_bpm
- sport_type

### 3.2 Intensidad relativa

z = (avg_hr_bpm - hr_rest_bpm) / max(1.0, (hr_threshold_bpm - hr_rest_bpm))

clamp:

- z in [0.0, 1.15]

### 3.3 IF Gym Cardio

if = intercept + slope * z

clamp:

- if in [0.45, 0.90]

Coeficientes iniciales (subject to calibration):

- elliptical: intercept=0.50, slope=0.30
- rowing: intercept=0.52, slope=0.34

### 3.4 TSS

tss = duration_h * (if ** 2) * 100

Guardarrail soft (solo alerta/diagnostico en fase 1, no hard cap):

- tss_h esperado entre 20 y 95 para sesiones tipicas de gym cardio.

## 4) Modelo propuesto (fase 2: hibrido)

Cuando haya senal mecanica util, mezclar:

- tss_h = w_hr * tss_h_hr + w_mech * tss_h_mech

Pesos iniciales:

- w_hr = 0.75
- w_mech = 0.25

Notas por modalidad:

- rowing: priorizar pace/500m o potencia si el payload lo trae.
- elliptical: usar cadencia/resistencia/stride solo si la senal es estable.
- si no hay senal mecanica valida: w_mech=0 y se marca fallback.

## 5) Routing y source_tag

Agregar routing dedicado en estimate_session_tss:

- elliptical -> _estimate_gym_cardio_tss(..., modality="elliptical")
- rowing -> _estimate_gym_cardio_tss(..., modality="rowing")

source_tag propuestos:

- gym_cardio:lthr
- gym_cardio:lthr_plus_mech
- gym_cardio:fallback_hr

## 6) Fallbacks

1. Si faltan hr_rest/lthr en entrada:
- usar hidratacion automatica ya integrada en trainer_agent.

2. Si avg_hr no util o no disponible:
- fallback conservador por modalidad basado en duracion + IF nominal.
- marcar explicitamente source_tag=fallback.

3. Si payload mecanico existe pero es inconsistente:
- ignorar componente mecanico, mantener HR-only y registrar warning.

## 7) Criterio de validacion

1. Monotonia interna
- A igualdad de modalidad, si sube duracion y sube z, no debe bajar TSS salvo ruido marginal.

2. Coherencia local
- Sesiones cercanas (duracion e intensidad similares) no deben divergir por factores extremos sin causa visible.

3. Distribucion por modalidad
- Revisar percentiles p5/p50/p95 de tss_h por modality + source_tag.

4. TP como referencia secundaria
- Medir delta y ratio contra rTSS/hrTSS, pero no optimizar para forzar igualdad.

## 8) Instrumentacion minima

Campos recomendados en salida/artefactos de auditoria:

- modality
- method=GymCardio
- source_tag
- z_raw, z_clamped
- if_raw, if_clamped
- tss_h
- uses_mech_component (bool)
- mech_signal_type (pace500/power/cadence/resistance/none)

## 9) Plan de implementacion

Fase 1 (PR 1)

- routing dedicado eliptica/remo
- modelo HR-only con clamps
- source tags
- tests unitarios de monotonia y clamps

Fase 2 (PR 2)

- componente mecanico opcional
- auditoria extendida por source_tag
- recalibracion de coeficientes con muestra real

## 10) Definicion de cierre

- No hay regressions en test suite relevante.
- Source tags aparecen correctamente por modalidad.
- Monotonia interna validada en muestra de sesiones gym cardio.
- Documentacion de cierre actualizada con decision final por modalidad.
