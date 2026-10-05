# TSS historico - Gym Cardio (eliptica y remo)

Fecha de generacion: 2026-10-05
Fuente local: docs/tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv
Criterio: tomar todas las actividades historicas disponibles de eliptica/remo y ordenarlas desde la mas antigua.

## Resumen

- Eliptica: 3 actividades encontradas (desde 2026-06-26 hasta 2026-09-09).
- Remo: 0 actividades encontradas en el dataset local consolidado.
- Nota: el filtro MCP en vivo no se pudo usar en esta sesion por error TLS de certificado local.

## Tabla historica (mas antiguas primero)

| fecha | activity_id | modalidad | actividad | tp_local | tp_unit | kairos_tss | metodo | delta_kairos_vs_tp | ratio_kairos_vs_tp | tp_status |
|---|---:|---|---|---:|---|---:|---|---:|---:|---|
| 2026-06-26 | 23385726742 | indoor_running | Eliptica. 20' Z1 | 13.000 | hrTSS | 15.478 | hrTSS | 2.478 | 1.191 | exact_modality |
| 2026-06-29 | 23420585341 | indoor_running | Eliptica. 20' Z1 | 13.000 | hrTSS | 15.439 | hrTSS | 2.439 | 1.188 | exact_modality |
| 2026-09-09 | 24301805124 | treadmill_running | Eliptica. 30' Z1 Transferencia Fuerza | 20.000 | rTSS | 23.223 | hrTSS | 3.223 | 1.161 | exact_modality |

## Observacion operativa

En esta foto historica local no hay sesiones etiquetadas como remo, asi que la siguiente ampliacion natural es reextraer historico completo desde MCP cuando se resuelva el problema de certificado para no limitarse al corte local junio-septiembre.
