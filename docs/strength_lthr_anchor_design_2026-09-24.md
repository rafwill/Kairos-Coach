# Variante fuerza anclada a LTHR (diseño offline)

Fecha: 2026-09-24
Estado: diseño propuesto (sin activar en runtime)

## Contexto

Se confirma que:
- `averageHR` en fuerza existe en payload real (`summaryDTO.averageHR`).
- `LTHR` está disponible y estable (`userData.lactateThresholdHeartRate=169`).
- `hr_max` no aparece en perfil y el máximo observado reciente puede infraestimar techo fisiológico.

Objetivo: evitar dependencia fuerte de `hr_max` en fuerza mientras se mantiene un modelo fisiológico simple y auditable.

## Fórmula propuesta (LTHR-anchored)

Variables:
- `hr_avg`: FC media de sesión.
- `hr_rest`: FC reposo (preferir media `get_rhr_day` 14d; fallback 50).
- `lthr`: FC umbral de lactato (perfil; fallback vacío => no aplica modelo).

Paso 1. Intensidad relativa a umbral:

`z = (hr_avg - hr_rest) / max(1, lthr - hr_rest)`

Paso 2. Clamp conservador para fuerza:

`z_c = clamp(z, 0.00, 1.15)`

Paso 3. Mapeo lineal a IF de fuerza:

`if_strength = clamp(0.46 + 0.28 * z_c, 0.45, 0.80)`

Paso 4. Carga:

`tss = hours * (if_strength^2) * 100`

## Por qué este mapeo

- Elimina `hr_max` del denominador (principal fuente de incertidumbre actual).
- Evita el “aplastamiento” artificial por suelo de `hrr=0.20` del modelo HR-reserve actual.
- Mantiene un IF mínimo operativo (`0.45`) para no caer a cero por ruido de FC en fuerza.
- Pendiente moderada para no inflar sesiones de baja FC media.

## Fallbacks explícitos

1. Si falta `hr_avg`: conservar comportamiento actual (no estimar o usar ruta existente definida para fuerza).
2. Si falta `lthr`: usar modelo HR-reserve actual (como compatibilidad temporal), registrando `source_model`.
3. Si `lthr <= hr_rest + 5`: invalidar ruta LTHR y usar fallback.

## Comparación planificada (cuando MCP esté estable)

Sobre las 18 sesiones TP de fuerza:

1. Modelo A (actual): HR-reserve (`hr_rest/hr_max`).
2. Modelo B (propuesto): LTHR-anchored.

Métricas:
- MAE
- Bias
- Ratio medio `calc/TP`
- % sesiones fuera de banda ratio `[0.90, 1.10]`

Criterio de preferencia:
- Elegir modelo con menor MAE y |bias|, con prioridad a menor sesgo sistemático.

## Nota operativa

Este documento define sólo la variante y su protocolo de evaluación.
No implica activación en runtime hasta tener corrida limpia sin 403/429 y evidencia comparativa cerrada.
