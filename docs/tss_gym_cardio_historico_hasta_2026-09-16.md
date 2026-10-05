# TSS historico - Gym Cardio (eliptica y remo)

Fecha de generacion: 2026-10-05
Fuente local: docs/tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv
Criterio: tomar todas las actividades historicas disponibles de eliptica/remo y ordenarlas desde la mas antigua.

## Resumen

- Eliptica: 3 actividades encontradas (desde 2026-06-26 hasta 2026-09-09).
- Remo: 0 actividades verificadas en el dataset local consolidado.
- Remo: 2 actividades verificadas por MCP en febrero (2026-02-16 y 2026-02-24).
- Nota: la verificacion MCP de febrero se obtuvo con extraccion paginada local y filtro por ventana de fechas.

## Tabla historica (mas antiguas primero)

| fecha | activity_id | modalidad | actividad | tp_local | tp_unit | kairos_tss | metodo | delta_kairos_vs_tp | ratio_kairos_vs_tp | tp_status |
|---|---:|---|---|---:|---|---:|---|---:|---:|---|
| 2026-06-26 | 23385726742 | indoor_running | Eliptica. 20' Z1 | 13.000 | hrTSS | 15.478 | hrTSS | 2.478 | 1.191 | exact_modality |
| 2026-06-29 | 23420585341 | indoor_running | Eliptica. 20' Z1 | 13.000 | hrTSS | 15.439 | hrTSS | 2.439 | 1.188 | exact_modality |
| 2026-09-09 | 24301805124 | treadmill_running | Eliptica. 30' Z1 Transferencia Fuerza | 20.000 | rTSS | 23.223 | hrTSS | 3.223 | 1.161 | exact_modality |

## Fechas reportadas por el usuario (cierre MCP)

El usuario informa este reparto de actividades:

- 2026-02-24: gimnasio + remo + rodaje
- 2026-02-16: remo + gimnasio

Trazabilidad cruzada (local + MCP):

| fecha | activity_id | modalidad | actividad | fuente | estado |
|---|---:|---|---|---|---|
| 2026-02-16 | 21887549903 | strength_training | Gimnasio. Cadena Posterior | CSV local + MCP | verificado |
| 2026-02-16 | 21887902894 | indoor_rowing | Remo indoor | MCP | verificado |
| 2026-02-24 | 21971310877 | strength_training | Gimnasio. Cadena posterior | CSV local + MCP | verificado |
| 2026-02-24 | 21971848782 | indoor_rowing | Remo. 30' complemento | MCP | verificado |
| 2026-02-24 | 21975471848 | running | Rodaje. 50' Z2 con Yuma | MCP | verificado |

Validacion de ventana MCP usada para cierre:

- Archivo de trabajo generado: `docs/rowing_window_20260215_to_20260225.csv` (14 actividades en ventana, 2 de remo).

## Observacion operativa

En esta foto historica local no habia sesiones etiquetadas como remo; con la reextraccion MCP de febrero ya quedan identificadas y trazables las sesiones de remo reportadas por el usuario.
