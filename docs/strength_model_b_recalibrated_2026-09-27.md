# Strength B Recalibration (Least Squares)

Date: 2026-09-27
Status: in-sample calibration only, pending out-of-sample confirmation.

## Inputs
- sessions_n: 18
- hr_rest_used: 40.714286
- lthr_bpm: 169.000

## Fitted Coefficients
- intercept: 0.523104204
- slope: 0.145349624

## Clamp Check
- if_raw_min: 0.542689175
- if_raw_max: 0.569881533
- if_eff_min: 0.542689175
- if_eff_max: 0.569881533
- clamp_hits_low(<0.45): 0
- clamp_hits_high(>0.80): 0

## Interpretation Guardrails
- WARNING: calibration was fit on a narrow z_c range only; outside that range the model extrapolates without validation.
- z_c_support_range: [0.134744, 0.321826] (span=0.187082 over theoretical [0, 1.15]).
- corr(duration_h, tp_local): 0.993083 (very high values indicate TP is largely duration-driven in this sample).
- corr(z_c, tp_local): 0.585128
- corr(duration_h, z_c): 0.499238
- The in-sample MAE should not be interpreted as general precision across medium/high-intensity strength sessions.

## Metrics (same 18 sessions)
| Variant | n | MAE | Bias | Ratio mean |
|---|---:|---:|---:|---:|
| B current | 18 | 3.613082 | -3.613082 | 0.893243 |
| B recalibrated (LS) | 18 | 0.413595 | 0.028637 | 1.000152 |

## Segment Diagnostics (same 18 sessions)
Interpret with caution: each segment also lives in a narrow sub-range of z_c.
| Segment | n | z_c min | z_c max | MAE B current | MAE B recal | Bias B current | Bias B recal | Ratio B current | Ratio B recal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| movilidad_activacion | 6 | 0.1347 | 0.2984 | 3.580759 | 0.361757 | -3.580759 | -0.122891 | 0.866015 | 0.995337 |
| neuromuscular | 8 | 0.2049 | 0.3218 | 3.705539 | 0.423942 | -3.705539 | 0.153942 | 0.913641 | 1.003169 |
| other_strength | 4 | 0.2127 | 0.2595 | 3.476650 | 0.470658 | -3.476650 | 0.005317 | 0.893288 | 1.001342 |

## Recalibrated Per-session
| Date | Activity ID | Segment | TP | z_c | IF_target | IF_recal_raw | IF_recal_eff | TSS_B_current | TSS_B_recal | ratio_B_current | ratio_B_recal |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-09 | 24301501111 | movilidad_activacion | 25.000 | 0.2984 | 0.5719 | 0.5665 | 0.5665 | 22.583 | 24.528 | 0.903 | 0.981 |
| 2026-09-08 | 24279679308 | neuromuscular | 43.000 | 0.2049 | 0.5524 | 0.5529 | 0.5529 | 37.743 | 43.082 | 0.878 | 1.002 |
| 2026-09-02 | 24213935868 | other_strength | 34.000 | 0.2361 | 0.5617 | 0.5574 | 0.5574 | 29.843 | 33.486 | 0.878 | 0.985 |
| 2026-08-31 | 24185208392 | neuromuscular | 36.000 | 0.2595 | 0.5619 | 0.5608 | 0.5608 | 32.369 | 35.867 | 0.899 | 0.996 |
| 2026-08-27 | 24138532236 | other_strength | 33.000 | 0.2127 | 0.5521 | 0.5540 | 0.5540 | 29.240 | 33.232 | 0.886 | 1.007 |
| 2026-08-24 | 24100578449 | neuromuscular | 47.000 | 0.2751 | 0.5602 | 0.5631 | 0.5631 | 43.213 | 47.490 | 0.919 | 1.010 |
| 2026-08-19 | 24032702641 | movilidad_activacion | 31.000 | 0.1347 | 0.5471 | 0.5427 | 0.5427 | 25.673 | 30.504 | 0.828 | 0.984 |
| 2026-08-14 | 23978414652 | other_strength | 28.000 | 0.2439 | 0.5515 | 0.5586 | 0.5586 | 25.703 | 28.720 | 0.918 | 1.026 |
| 2026-06-29 | 23421099617 | movilidad_activacion | 18.000 | 0.1815 | 0.5455 | 0.5495 | 0.5495 | 15.795 | 18.267 | 0.877 | 1.015 |
| 2026-06-26 | 23386161527 | movilidad_activacion | 22.000 | 0.1815 | 0.5557 | 0.5495 | 0.5495 | 18.602 | 21.514 | 0.846 | 0.978 |
| 2026-06-22 | 23335868602 | other_strength | 34.000 | 0.2595 | 0.5643 | 0.5608 | 0.5608 | 30.308 | 33.583 | 0.891 | 0.988 |
| 2026-06-19 | 23307819037 | movilidad_activacion | 32.000 | 0.1815 | 0.5465 | 0.5495 | 0.5495 | 27.971 | 32.349 | 0.874 | 1.011 |
| 2026-06-17 | 23278321499 | neuromuscular | 46.000 | 0.3140 | 0.5691 | 0.5687 | 0.5687 | 42.663 | 45.949 | 0.927 | 0.999 |
| 2026-06-12 | 23223630528 | movilidad_activacion | 31.000 | 0.1815 | 0.5486 | 0.5495 | 0.5495 | 26.891 | 31.100 | 0.867 | 1.003 |
| 2026-06-10 | 23195949762 | neuromuscular | 44.000 | 0.3218 | 0.5741 | 0.5699 | 0.5699 | 40.419 | 43.359 | 0.919 | 0.985 |
| 2026-06-08 | 23178623089 | neuromuscular | 48.000 | 0.2751 | 0.5569 | 0.5631 | 0.5631 | 44.654 | 49.073 | 0.930 | 1.022 |
| 2026-06-03 | 23112485560 | neuromuscular | 41.000 | 0.2829 | 0.5660 | 0.5642 | 0.5642 | 37.227 | 40.745 | 0.908 | 0.994 |
| 2026-06-01 | 23093957193 | neuromuscular | 41.000 | 0.2829 | 0.5597 | 0.5642 | 0.5642 | 38.069 | 41.666 | 0.929 | 1.016 |
