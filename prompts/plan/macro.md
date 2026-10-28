Datos del atleta:

{{datos}}

Diseña la estructura del plan: una entrada por semana, de la 1 a la {{semanas}}, con su fase, si es de descarga, los minutos totales de la semana (todas las sesiones, fuerza incluida), los minutos de la tirada larga y el enfoque de la semana (las sesiones clave en pocas palabras).

Formato (JSON):
{"title": "Plan hacia …", "rationale": "por qué esta estructura, en 2-4 frases", "weeks": [
  {"week": 1, "phase": "base", "deload": false, "minutes": 330, "long_min": 90, "focus": "cuestas cortas y fartlek; tirada aeróbica" },
  {"week": 2, "phase": "base", "deload": false, "minutes": 355, "long_min": 95, "focus": "…" }
] }

- "phase": base, build, specific, taper o race ("race" solo la semana de la carrera, la {{semana_carrera}}).
- La semana 1 no sube más de un 10 % sobre el volumen sostenido; cada semana de carga, como mucho un 10 % sobre la anterior de carga; la descarga baja al menos un 15 %; el afinamiento y la semana de la carrera, al menos un 15 % por debajo de la semana más alta.
- La tirada larga no pasa del 40 % de la semana ni sube más de 15 min de una semana de carga a la siguiente.
- Los minutos de cada semana tienen que caber en los días de entrenamiento: {{dias_entreno}} días por semana (el resto, descanso obligatorio), con rodajes de 90 min como mucho.
