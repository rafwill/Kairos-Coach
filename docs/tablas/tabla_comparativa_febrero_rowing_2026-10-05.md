# Tabla comparativa - ventana febrero 2026 (remo/eliptica)

Fuente principal: `docs/rowing_window_20260215_to_20260225.csv`.
Cruce adicional: `docs/strength_calibration_window_120_2026-09-21.csv`.

## Objetivo

Dejar en una sola tabla las actividades de cardio indoor objetivo (remo/eliptica) para:

- 2026-02-16: remo
- 2026-02-24: remo

con columnas listas para completar TP cuando sea necesario.

## Tabla

| fecha | activity_id | modalidad | actividad | dur_h | avg_hr | max_hr | kairos_tss | kairos_method | tp_tss | tp_unit | tp_status | fuente |
|---|---:|---|---|---:|---:|---:|---:|---|---:|---|---|---|
| 2026-02-16 | 21887902894 | indoor_rowing | Remo indoor | 0.417 | 100 | 114 | 17.139 | hrTSS | 17 | hrTSS | user_reported | MCP + usuario |
| 2026-02-24 | 21971848782 | indoor_rowing | Remo. 30' complemento | 0.500 | 101 | 118 | 20.844 | hrTSS | 20 | hrTSS | user_reported | MCP + usuario |

## Notas

- En esta ventana no aparecen actividades de eliptica; para febrero las actividades indoor objetivo detectadas son de remo.
- En la tabla actual quedan completos los `kairos_tss` y TP reportado para las filas de remo.
- El usuario reporta para remo: 17 hrTSS (16/02) y 20 hrTSS (actividad de remo de la segunda fecha reportada).
- Se elimina la fila de running y las filas de gimnasio para mantener foco exclusivo en cardio indoor objetivo.
- Recalculo Kairos de remo (pipeline productivo):
	- `21887902894`: `17.139 hrTSS` (delta vs TP = `+0.139`, ratio = `1.008`).
	- `21971848782`: `20.844 hrTSS` (delta vs TP = `+0.844`, ratio = `1.042`).
