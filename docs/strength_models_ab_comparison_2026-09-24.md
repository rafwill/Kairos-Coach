# Strength Model A/B Comparison

Generated: 2026-09-27 16:39:04
Sample note: n is small (18 sessions); result is directional, not final calibration.
Coefficients for model B were fixed before this comparison (no post-hoc tuning in this run).

## Inputs
- hr_rest_used: 40.714 (derived_get_rhr_day_14d)
- hr_max_used: n/d (fallback_185)
- lthr_bpm: 169.0

## Aggregate
| Model | n | MAE | Bias | Ratio mean |
|---|---:|---:|---:|---:|
| A (HR reserve) | 18 | 4.426684 | 4.426684 | 1.123277 |
| B (LTHR anchored) | 18 | 3.613082 | -3.613082 | 0.893243 |

## Per-session
| Date | Activity ID | Segment | TP | A | B | dA | dB | ratioA | ratioB | B source |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 2026-09-09 | 24301501111 | movilidad_activacion | 25.000 | 28.082 | 22.583 | 3.082 | -2.417 | 1.123 | 0.903 | lthr_model |
| 2026-09-08 | 24279679308 | neuromuscular | 43.000 | 47.411 | 37.743 | 4.411 | -5.257 | 1.103 | 0.878 | lthr_model |
| 2026-09-02 | 24213935868 | other_strength | 34.000 | 36.771 | 29.843 | 2.771 | -4.157 | 1.081 | 0.878 | lthr_model |
| 2026-08-31 | 24185208392 | neuromuscular | 36.000 | 40.025 | 32.369 | 4.025 | -3.631 | 1.112 | 0.899 | lthr_model |
| 2026-08-27 | 24138532236 | other_strength | 33.000 | 36.422 | 29.240 | 3.422 | -3.760 | 1.104 | 0.886 | lthr_model |
| 2026-08-24 | 24100578449 | neuromuscular | 47.000 | 53.557 | 43.213 | 6.557 | -3.787 | 1.140 | 0.919 | lthr_model |
| 2026-08-19 | 24032702641 | movilidad_activacion | 31.000 | 34.843 | 25.673 | 3.843 | -5.327 | 1.124 | 0.828 | lthr_model |
| 2026-08-14 | 23978414652 | other_strength | 28.000 | 31.707 | 25.703 | 3.707 | -2.297 | 1.132 | 0.918 | lthr_model |
| 2026-06-29 | 23421099617 | movilidad_activacion | 18.000 | 20.352 | 15.795 | 2.352 | -2.205 | 1.131 | 0.877 | lthr_model |
| 2026-06-26 | 23386161527 | movilidad_activacion | 22.000 | 23.970 | 18.602 | 1.970 | -3.398 | 1.090 | 0.846 | lthr_model |
| 2026-06-22 | 23335868602 | other_strength | 34.000 | 37.476 | 30.308 | 3.476 | -3.692 | 1.102 | 0.891 | lthr_model |
| 2026-06-19 | 23307819037 | movilidad_activacion | 32.000 | 36.042 | 27.971 | 4.042 | -4.029 | 1.126 | 0.874 | lthr_model |
| 2026-06-17 | 23278321499 | neuromuscular | 46.000 | 53.173 | 42.663 | 7.173 | -3.337 | 1.156 | 0.927 | lthr_model |
| 2026-06-12 | 23223630528 | movilidad_activacion | 31.000 | 34.650 | 26.891 | 3.650 | -4.109 | 1.118 | 0.867 | lthr_model |
| 2026-06-10 | 23195949762 | neuromuscular | 44.000 | 50.431 | 40.419 | 6.431 | -3.581 | 1.146 | 0.919 | lthr_model |
| 2026-06-08 | 23178623089 | neuromuscular | 48.000 | 55.343 | 44.654 | 7.343 | -3.346 | 1.153 | 0.930 | lthr_model |
| 2026-06-03 | 23112485560 | neuromuscular | 41.000 | 46.191 | 37.227 | 5.191 | -3.773 | 1.127 | 0.908 | lthr_model |
| 2026-06-01 | 23093957193 | neuromuscular | 41.000 | 47.235 | 38.069 | 6.235 | -2.931 | 1.152 | 0.929 | lthr_model |
