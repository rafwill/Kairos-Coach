# Diseño: método metabólico para walking/hiking (sin FC)

Fecha: 2026-09-29
Estado: diseño cerrado (coeficientes confirmados), listo para pasar a implementación

## Por qué un tercer método, no un ajuste del actual

El modelo actual (componente cardiaco anclado a LTHR + desnivel aditivo) sigue midiendo esfuerzo a través de la FC. Caminar horas a ritmo suave apenas eleva la FC por encima de reposo, aunque el cuerpo sí acumula carga real (desplazar el propio peso durante horas, más si hay desnivel). Por eso cualquier modelo basado en FC —el nuestro incluido, y el hrTSS de TP con más razón— tiene un techo: nunca va a diferenciar bien "caminar poco tiempo fuerte" de "caminar mucho tiempo suave", porque la FC no separa bien esos dos casos en caminata.

La alternativa: calcular el **coste metabólico real** a partir de velocidad y pendiente —el mismo principio que ya usamos en running con Minetti—, en vez de a partir del corazón.

## Fórmula base: ecuación ACSM de caminar

Documentada, pública, con coeficientes conocidos (Colegio Americano de Medicina del Deporte, válida aproximadamente entre 50 y 100 m/min de velocidad horizontal, es decir 3-6 km/h, y para pendiente horizontal o positiva):

```
VO2 (ml/kg/min) = 0.1 × v + 1.8 × v × i + 3.5

donde:
v = velocidad horizontal en m/min
i = pendiente como fracción (0.10 = 10%)
3.5 = coste metabólico en reposo (1 MET)
```

Esto da el coste de oxígeno por kg de peso corporal y minuto, en función solo de velocidad y pendiente — nada de FC.

### Cobertura de bajada y pendiente extrema: curva de Minetti para caminar (coeficientes cerrados)

La ecuación ACSM no está validada para pendiente negativa (bajada) ni para pendientes muy fuertes. Para ese rango, Minetti et al. (2002) publicaron una curva específica para caminar —distinta de la de correr que ya usamos en running—, con coeficientes confirmados contra fuente citable (Looney et al., tabla comparativa de ecuaciones de coste de caminar):

```
Cw(i) = 280.5·i⁵ − 58.7·i⁴ − 76.8·i³ + 51.9·i² + 19.6·i + 2.5

donde:
Cw = coste metabólico de caminar, en J·kg⁻¹·m⁻¹
i = pendiente como fracción (0.10 = 10%), igual formato que la Cr(i) de running ya implementada
Válida documentadamente entre -45% y +45% de pendiente.
```

Conversión a VO2 para poder combinarla con la rama ACSM: `VO2(ml/kg/min) ≈ Cw(i) × v(m/min) / 20.9`, usando el equivalente energético estándar de ~20.9 J por ml de O2.

**Precisión conocida de esta curva (documentada, no asunción nuestra):** un estudio de validación cruzada encontró que sobreestima el coste en pendientes moderadas (≤15%) y lo subestima en pendientes muy fuertes (≥22.5%), con buena concordancia alrededor del 19%. Es la mejor referencia pública disponible para este rango, pero no es exacta — se documenta así en el resultado, sin fingir mayor precisión de la que tiene.

**Regla de combinación de las dos curvas:**
- Pendiente entre 0 y el límite superior validado de ACSM (aprox. +25%, a confirmar con la fuente exacta del rango ACSM antes de implementar): usar ACSM.
- Pendiente negativa, o positiva fuera del rango de ACSM: usar Minetti-caminar.
- Se etiqueta en el resultado qué curva se usó en cada tramo, para poder auditar después si hace falta.

## De VO2 a intensidad relativa (IF), sin FC

Igual que en running convertimos velocidad+pendiente en una "velocidad equivalente en llano" (NGP) y la comparamos contra el umbral de ritmo del atleta, aquí:

1. Por muestra, calcula `VO2(v, i)` con la fórmula de arriba.
2. Normaliza a una "velocidad equivalente en llano" invirtiendo la fórmula para pendiente 0: `v_equivalente = (VO2 - 3.5) / 0.1` (aproximación; a validar con más cuidado si el desnivel es fuerte, porque el término cruzado `1.8×v×i` no se invierte de forma tan directa — puede hacer falta resolver numéricamente por tramo en vez de algebraicamente).
3. Integra esa velocidad equivalente con el mismo esquema de suavizado que ya usamos en running (media móvil corta + potencia par para penalizar picos), dando una "velocidad de marcha normalizada".
4. `IF = velocidad_marcha_normalizada / velocidad_umbral_de_marcha`

### La pieza que falta: velocidad umbral de marcha

Necesitamos un equivalente al FTPace de running, pero para caminar — la velocidad (en llano) que el atleta podría sostener a esfuerzo de umbral caminando. No lo tenemos todavía. Dos formas de obtenerlo, sin inventarlo a ojo:

- **Opción A (recomendada):** derivarlo la primera vez a partir de la sesión de caminata/hiking más exigente que tengamos con datos reales (la de más desnivel sostenido a buen ritmo), de forma análoga a como se fija el FTPace de running con un test de 45-60 min.
- **Opción B:** pedirlo como dato de configuración manual, con el mismo riesgo que ya vimos con el RPE de fuerza (0/137 capturas) — no lo elegiría como opción principal.

## Fórmula final de carga

```
TSS_marcha = duración_h × IF² × 100
```

Mismo principio que rTSS y el modelo de fuerza: 1 hora exacta a umbral = 100 puntos. El desnivel ya no se suma aparte — queda embebido en el propio cálculo de VO2, que es donde pertenece fisiológicamente (subir cuesta más por definición del coste metabólico, no por un término añadido a mano).

## Qué se necesita del perfil/datos, y qué ya tenemos

| Dato | ¿Disponible? |
|---|---|
| Velocidad muestra a muestra | Sí — mismo parser que running (`directSpeed`) |
| Pendiente muestra a muestra | Sí — mismo cálculo Minetti/elevación ya implementado |
| Peso corporal | No confirmado — revisar si está en el perfil de Kairos; si no, VO2 en ml/kg/min sigue siendo comparable entre sesiones del mismo atleta sin necesitar el peso absoluto, así que no bloquea el diseño, pero sí haría falta si se quisiera expresar en kcal reales |
| Velocidad umbral de marcha | No existe — hay que fijarla (Opción A) |

## Validación planificada

Sobre las 8 actividades ya identificadas con TP verificado (`23468464527`, `23478220005`, `23829149525`, `24013969366`, `24430006167`, `24484006590`, `24492874850`, `24502427862`):

1. Fijar la velocidad umbral de marcha con la Opción A, usando la sesión de más desnivel/intensidad de ese conjunto (probablemente `23478220005` o `24013969366`, los dos hiking).
2. Calcular `TSS_marcha` para las 8.
3. Comparar contra ambos números de TP (rTSS y hrTSS) sin esperar que ninguno de los dos sea "el correcto" — el objetivo es que el resultado escale de forma coherente con el esfuerzo real de cada sesión (más tiempo + más pendiente + más ritmo → más TSS, de forma proporcional y sin saltos raros entre sesiones parecidas), no que iguale a ningún TP concreto.
4. Repetir el chequeo de estabilidad entre corridas que detectamos hoy (mismo input, mismo resultado, sin depender de qué día se ejecute el cálculo).

## Riesgo a vigilar

Convertir VO2 en velocidad equivalente cuando hay pendiente fuerte (paso 2 de la sección "De VO2 a intensidad relativa") no es una inversión algebraica trivial por el término cruzado `1.8×v×i`. Antes de implementar, conviene resolverlo con cuidado (por ejemplo, trabajando directamente en espacio de VO2/coste en vez de convertir a una "velocidad equivalente", y anclando el umbral también en VO2 en vez de en velocidad) para no introducir un bug sutil de conversión — lo marco explícitamente porque es el punto más delicado de todo el diseño y el que más fácil sería estropear sin darse cuenta.
