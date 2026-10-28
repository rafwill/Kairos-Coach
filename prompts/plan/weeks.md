Datos del atleta:

{{datos}}

Estructura del plan (ya aprobada):

{{estructura}}

Detalla los 7 días de las semanas {{semanas}}, y solo esas: las demás las detalla otra llamada. Cada semana tiene que cuadrar con su entrada de la estructura: los días suman sus minutos (±12 %) y la tirada larga dura lo que dice. No sumes tú: pon "min": "auto" en el tramo continuo ("steady") de los rodajes y de la tirada larga, y Kairos les da la duración para que cuadre.

Reglas que Kairos comprueba en cada semana:
{{reglas}}

Formato (JSON):
{"weeks": [ {"week": 1, "days": [
  {"day": 1, "type": "strength", "title": "Fuerza para corredores", "minutes": 40, "rpe": "6-7", "note": "sentadilla búlgara, peso muerto rumano, gemelo, core; 3×8" },
  {"day": 2, "type": "quality", "title": "Fartlek 6×(2'/1')", "rpe": "7", "steps": [
    {"kind": "warmup", "min": 15, "zone": "Z2" },
    {"repeat": 6, "name": "Fartlek", "steps": [ {"kind": "work", "min": 2, "zone": "Z4" }, {"kind": "recovery", "min": 1, "zone": "Z2" } ] },
    {"kind": "cooldown", "min": 10, "zone": "Z1" } ] },
  {"day": 3, "type": "easy", "title": "Rodaje suave", "rpe": "3-4", "steps": [ {"kind": "warmup", "min": 10, "zone": "Z1" }, {"kind": "steady", "min": "auto", "zone": "Z2" }, {"kind": "cooldown", "min": 5, "zone": "Z1" } ] },
  {"day": 4, "type": "quality", "title": "Series 5×1000 m", "rpe": "8", "steps": [ {"kind": "warmup", "min": 20, "zone": "Z2" }, {"repeat": 5, "steps": [ {"kind": "work", "m": 1000, "zone": "Z5b" }, {"kind": "recovery", "min": 2, "zone": "Z1", "note": "trote" } ] }, {"kind": "cooldown", "min": 10, "zone": "Z1" } ] },
  {"day": 5, "type": "rest" },
  {"day": 7, "type": "long", "title": "Tirada larga con 2×15' a RP", "rpe": "5-6", "steps": [ {"kind": "warmup", "min": 10, "zone": "Z1" }, {"kind": "steady", "min": "auto", "zone": "Z2" }, {"repeat": 2, "steps": [ {"kind": "work", "min": 15, "zone": "RP" }, {"kind": "recovery", "min": 5, "zone": "Z2" } ] }, {"kind": "cooldown", "min": 5, "zone": "Z1" } ] }
] } ] }

- "type": rest, easy, recovery, long, quality, strength, race o cross. "rest" es el día sin entrenar (sin tramos); "recovery", un rodaje regenerativo con tramos; "race" solo el día de la carrera; "long", la tirada larga de la semana.
- "min": "auto" va en un solo tramo "steady" de cada rodaje (easy, recovery) y de la tirada larga (long), nunca dentro de las series. Las sesiones de calidad, la fuerza y la carrera llevan sus minutos o distancias exactos.
- Cada sesión que no sea rest lleva "steps" que empiezan con "warmup" y acaban con "cooldown" (fuerza y cross pueden ir solo con "minutes", "rpe" y "note").
- Cada tramo: "kind" (warmup, steady, work, recovery, cooldown), tiempo ("min" o "sec") o distancia ("m" o "km", solo por ritmo) y su objetivo: "zone" (Z1…Z5c, RP o FC1…FC5) o "pace" con un ritmo concreto ("pace": "4:00"). Las series van en un bloque {"repeat": n, "steps": [...]}.
- Día doble (si el atleta dobla): repite el "day" con la segunda sesión, p. ej. {"day": 3, "type": "easy", …} y {"day": 3, "type": "strength", …}; primero la principal.
- "title" dice la sesión como la diría un entrenador (p. ej. "Umbral 3×10'", "Tirada larga con 2×15' a RP"); "note" da la indicación técnica que haga falta (cuesta del 6-8 %, superficie, cadencia, nutrición).
