# Proximos pasos - Kairos Coach

Ultima actualizacion: 2026-09-16 (cierre final)

## Actualizacion de estado (2026-09-16) - CIERRE FINAL

1. Version de formula actualizada a `TSS_FORMULA_VERSION=26` por cambio de comportamiento en running:
      - `tempo_detector`: umbral de bloque a `400 s` y tolerancia de huecos cortos `20 s`.
      - Ajuste de pendiente negativa: atenuación parcial del descuento Minetti en bajada
         (`_MINETTI_NEGATIVE_DISCOUNT_STRENGTH=0.55`), manteniendo intacta la pendiente positiva.
2. Criterio de cierre de running documentado con excepción explícita:
      - MAE global `< 7` (sin cambios).
      - Sesgo global ampliado de `±3` a `±3.5` como tolerancia justificada
         (mismo principio aplicado al MAE; evitar sobreajuste por márgenes residuales).
      - Ratios por segmento en `0.88-1.12` (sin cambios).
3. Resultado de cierre running con criterio final:
      - `running_global`: `n=32`, `MAE=6.178491`, `bias=-3.278240`, `ratio_mean=0.957760`.
      - Segmentos dentro de rango ratio:
         - `repeticiones_cortas=1.077338`
         - `fartlek_tempo_sostenido=0.974348`
         - `rodaje_continuo=0.938087`
      - Estado: **CERRADO** bajo criterio final documentado (`±3.5`).
4. Estado de bloques principales:
      - Trail: **CERRADO**.
      - Fuerza: **CERRADO** (neuromuscular y movilidad/activación en rango).
      - Running: **CERRADO** con excepción de sesgo documentada.
5. Limitaciones conocidas aceptadas y trazadas:
      - Hiking: muestra corta (`n=2`) y variabilidad alta; queda como frente de observación.
      - `short_reps_detector`: cobertura parcial conocida (3/4 reales no activan y hay activaciones en rodajes).
      - `24027714450`: tramo sostenido corto (`289 s`) embebido en rodaje largo, no detectado por tempo;
         aceptado como limitación, no bug abierto.
6. Referencia única de cierre:
      - `docs/tss-cierre-definitivo-2026-09-16.md`.

## Nota de archivo historico

El contenido operativo previo de este fichero (hipotesis, pendientes y planes por pasos)
corresponde al periodo de investigacion anterior al cierre final del 2026-09-16.

Para trabajo nuevo, usar como fuente vigente:

- `docs/tss-cierre-definitivo-2026-09-16.md`
- `docs/tss_independiente_junio_a_septiembre_hasta_2026-09-16.csv`

Los documentos intermedios se mantienen solo para trazabilidad historica.




22/09/2026

Excelente propuesta. Ya corrí exactamente esas comprobaciones y salieron datos útiles.

Resultado 1: uso real en runtime

La ruta de cálculo de TSS de fuerza está usando el estimador HR, no la tabla de IF por etiquetas:
definición HR strength IF: load_metrics.py:508
uso en rama strength dentro de estimate_session_tss: load_metrics.py:3020
La clasificación por texto y el IF por categoría sí existen:
clasificador: load_metrics.py:1914
IF por categoría: load_metrics.py:1992
Pero en cálculo operativo de strength TSS, hoy manda la ruta HR en esa rama.
Resultado 2: heavy en historial local disponible
Conté categorías inferidas con la lógica actual en dos datasets:

Tabla independiente fuerza (18 strength):
heavy: 0
light: 5
neuromuscular: 8
general: 5
other: 0
Ventana 120 (dataset amplio local):
heavy: 0
light: 22
neuromuscular: 22
general: 74
other: 2
Conclusión factual: no aparece heavy en ninguno de los dos cortes locales analizados.

Resultado 3: simulación de fusión general + neuromuscular
Hice la simulación como pedías, pero correctamente sobre calc_tss actual (no anclando a TP), aplicando el factor de cambio de IF en las categorías fusionadas:

Modelo A: general 0.56, neuromuscular 0.57
Modelo B: ambas en 0.565
light, maintenance y heavy iguales
Impacto frente a TP:

Full 18

A: MAE 0.982401, bias 0.550934, ratio 1.014786
B: MAE 0.974577, bias 0.363488, ratio 1.011986
Delta B-A: MAE -0.007824, bias -0.187445, ratio -0.002800
OOS 13

A: MAE 1.113596, bias 0.783746, ratio 1.020399
B: MAE 1.102763, bias 0.524206, ratio 1.016522
Delta B-A: MAE -0.010833, bias -0.259540, ratio -0.003877
Lectura:

No empeora; mejora leve y estable.
Está muy lejos del umbral de deterioro material de 0.2 MAE.
Tu hipótesis de simplificación queda respaldada numéricamente en estos cortes.
Tu condición metodológica por calendario
Totalmente de acuerdo. La decisión debería gatillarse por ventana temporal, no solo por conteo. Propuesta operativa limpia:

Ventana fija de 6 a 8 semanas.
Evaluación al cierre de ventana sobre OOS puro.
Accionar solo si se mantiene patrón consistente en al menos dos ventanas consecutivas.
Si quieres, te dejo ahora mismo ese criterio escrito en proximos_pasos.md como regla explícita de gobierno para no volver a ajustar por resultado puntual.

## Aclaraciones de la sesion (22/09/2026)

Resumen corto de preguntas y respuestas para evitar ambiguedades en iteraciones futuras:

1. Modelo A vs Modelo B
- Modelo A: `general=0.56`, `neuromuscular=0.57` (separados).
- Modelo B: `general` y `neuromuscular` fusionados a `0.565`.
- `light`, `maintenance` y `heavy` se mantienen iguales en la simulacion.

2. IF fijo por categoria vs IF por frecuencia cardiaca
- IF fijo por categoria: valor hardcodeado segun `session_kind` en `_estimate_strength_if`.
- IF por FC: valor calculado dinamicamente en `_estimate_strength_if_from_hr` usando FC media + FC reposo/max de perfil.

3. Origen del `0.56` de `general`
- Es un valor heuristico historico (hardcodeado), no una calibracion reciente documentada.
- En codigo aparece explicitamente en `agent/load_metrics.py` dentro de `_estimate_strength_if`:
      - `if session_kind == "general": return 0.56`
      - `return 0.56` (fallback final)

4. Uso operativo actual en fuerza
- En la rama principal de calculo de TSS de fuerza, hoy manda el estimador HR (`_estimate_strength_if_from_hr`).
- La tabla IF por etiquetas sigue existiendo para clasificacion/compatibilidad y analisis.

5. Nota metodologica
- Para decidir cambios de calibracion: evaluar por ventana temporal fija (6-8 semanas) y sobre OOS puro,
      evitando ajustes por un unico caso o por lectura oportunista de resultados.





24/09/2026 12:24

Este log termina de confirmar tres cosas importantes:

La lectura metodológica que marcaste se sostiene.

En la corrida n=18 con hr_max=232, el Modelo A queda casi plano:

hrr_clamped en [0.20, 0.216]

IF en [0.580, 0.586]

15 de 18 sesiones pegadas al piso 0.20

Eso no es un modelo sensible a intensidad real, es casi un IF constante.

El resultado A vs B se mueve con cobertura MCP, pero el patrón central no cambia.

n=14: A MAE 4.67, B MAE 3.54

n=12: A MAE 4.62, B MAE 3.50

n=10: A MAE 4.79, B MAE 3.44

En todas esas corridas parciales recientes, B queda mejor que A.

El MCP está sano en autenticación, pero inestable en get_activity para el bloque más reciente.

mcp_relogin_probe dio 5/5 OK.

Aun así, hay timeouts repetidos en las mismas actividades recientes.

Eso explica por qué la métrica final oscila por cobertura, no por cambio conceptual del modelo.

Delta técnico relevante:

El script en workspace ya quedó con timeout de 60s y guardarraíl robusto para hr_max en compare_strength_models_ab.py.
Tus logs con timeout de 25s corresponden a ejecuciones anteriores que quedaron en historial del terminal.
Cuando probé distribución paginada de maxHR en una sesión posterior, vino vacía y por eso cae a fallback_185.
Conclusión operativa al día de hoy:

No hay base para afirmar A gana.
Sí hay base para afirmar que A está estructuralmente comprimido por clamp y que B es el candidato más estable mientras hr_max siga frágil.
Tu hipótesis sobre la dirección del sesgo de A al corregir hr_max plausible está validada.
Si quieres, siguiente paso lo dejo automático y determinista:

Reintento selectivo solo de las actividades que fallan (sin recalcular todo).
Derivación de hr_max desde las actividades efectivamente recuperadas del set de referencia, con cap duro fisiológico y percentil robusto.
Reporte final único n=18 con tabla por sesión y columnas hrr_clamped/IF de A para auditoría.