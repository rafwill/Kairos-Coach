# PROMPT PARA GITHUB COPILOT — Motor de cálculo de Training Stress Score (rTSS / hrTSS) para running

## Contexto

Estoy construyendo un módulo dentro de mi proyecto `garmin-ai-coach` que debe calcular el **Training Stress Score para carrera** replicando, con la mayor fidelidad posible, la metodología que usa TrainingPeaks (rTSS y hrTSS), a partir de datos extraídos de Garmin Connect.

Quiero que implementes un módulo Python autocontenido, testeable y documentado, siguiendo exactamente la especificación de abajo. No inventes fórmulas alternativas ni "mejores" — donde la especificación diga que algo es una aproximación (porque el algoritmo original es propietario), impleméntalo tal cual se indica.

---

## 1. Fundamento teórico

### 1.1 Fórmula general (heredada de Coggan/Allen, TSS de ciclismo)

```
TSS = (t × Intensidad_normalizada × IF) / (Umbral × 3600) × 100
```

Por definición, 1 hora exactamente al umbral del atleta = 100 puntos.

### 1.2 rTSS (basado en ritmo/velocidad)

```
rTSS = (t × NGP × IF) / (FTPace × 3600) × 100
IF   = NGP / FTPace
```

- `t`: duración en segundos
- `NGP` (Normalized Graded Pace): velocidad normalizada y ajustada por pendiente, en m/s
- `FTPace`: Functional Threshold Pace del atleta, en m/s (velocidad sostenible ~1h en llano)

**Cálculo de NGP (dos fases):**

**Fase A — Ajuste por pendiente**, muestra a muestra, usando el modelo de coste energético de carrera de Minetti et al. (2002) como sustituto público del algoritmo propietario de TrainingPeaks:

```
C(i) = 155.4·i⁵ − 30.4·i⁴ − 43.3·i³ + 46.3·i² + 19.5·i + 3.6
```
donde `i` es la pendiente como fracción (0.10 = 10%).

```
factor_ajuste(i) = C(i) / C(0)
velocidad_ajustada = velocidad_instantánea × factor_ajuste(i)
```

Limitar `i` a un rango razonable (ej. [-0.30, 0.30]) para evitar que ruido de sensor dispare el ajuste.

**Fase B — Normalización tipo "4º grado"** (mismo principio que Normalized Power en ciclismo):

1. Media móvil de 30 segundos sobre la velocidad ajustada.
2. Elevar cada valor de esa media móvil a la 4ª potencia.
3. Promediar.
4. Raíz cuarta del resultado.

```
NGP = ( media( media_movil_30s(v_ajustada)^4 ) )^(1/4)
```

### 1.3 hrTSS (basado en frecuencia cardíaca) — vía TRIMP calibrado

TrainingPeaks usa internamente una tabla de multiplicadores por zona de FC no publicada. En su lugar, implementa el **TRIMP exponencial de Banister**, calibrado para que 1h a la frecuencia cardíaca umbral (LTHR) del atleta equivalga a 100 puntos:

```
TRIMP = Σ [ Δt_min × frac_reserva × 0.64 × e^(k × frac_reserva) ]

frac_reserva = (FC_media_intervalo − FC_reposo) / (FC_max − FC_reposo)
k = 1.92 (hombre) / 1.67 (mujer)
```

```
hrTSS = 100 × ( TRIMP_del_entreno / TRIMP_de_1h_continua_a_LTHR )
```

`TRIMP_de_1h_continua_a_LTHR` se calcula una única vez por atleta, aplicando la misma fórmula a 60 minutos continuos con `FC_media = LTHR`.

---

## 2. Origen y forma de los datos (Garmin Connect)

La fuente de datos es la respuesta de `get_activity_details(activity_id)` de la librería `python-garminconnect` (o el MCP de Garmin ya integrado en el proyecto). Estructura esperada:

- `metricDescriptors`: lista de objetos `{key, metricsIndex, ...}` que mapean el nombre de una métrica a su posición dentro de cada fila.
- `activityDetailMetrics`: lista de filas, cada una con un array `metrics[]` indexado según lo anterior.

Claves relevantes a extraer (verificar nombres exactos contra el JSON real la primera vez, porque esta API no es pública/estable): `directTimestamp`, `directSpeed` (m/s), `directHeartRate` (bpm), `directElevation` (m), `sumDistance` (m acumulados).

**Consideraciones de calidad de dato que el código debe manejar:**
- Garmin usa "smart recording": intervalos entre muestras no son constantes → **remuestrear a 1 Hz por interpolación lineal** antes de cualquier ventana móvil.
- `directElevation` viene del altímetro barométrico (más fiable que altitud GPS) pero **debe suavizarse** (media móvil corta, 5–10 s) antes de derivar pendiente, porque el ruido se amplifica al derivar.
- La pendiente debe calcularse como `Δelevación_suavizada / Δdistancia`, con una distancia mínima de referencia para evitar división por valores casi nulos en paradas.
- Detectar y excluir (o marcar) tramos de velocidad ≈ 0 sostenidos (semáforos, cortes) para que no distorsionen la ventana de 30 s del NGP — TrainingPeaks aplica una lógica de pausa automática equivalente.

**Umbrales del atleta (FTPace, LTHR, FC_reposo, FC_max):**
Garmin no expone de forma fiable y consistente un umbral de ritmo de carrera entre todos los dispositivos/cuentas. **No derives estos valores automáticamente de Garmin por defecto**: deben ser parámetros de entrada configurables por atleta (obtenidos idealmente de un test de 45–60 min a máximo esfuerzo sostenido), con posibilidad de usar el dato de Garmin (`get_training_status` / lactate threshold estimado por Firstbeat, si existe) únicamente como sugerencia inicial editable, nunca como fuente de verdad silenciosa.

---

## 3. Qué debe construir Copilot

Un módulo Python (paquete `running_tss/` o similar, a integrar en `garmin-ai-coach`) con:

1. **`garmin_parser.py`**
   - Función para parsear la respuesta cruda de `get_activity_details` y devolver arrays numpy de `tiempo, velocidad, hr, elevacion, distancia`.
   - Manejo defensivo: si falta alguna clave esperada en `metricDescriptors`, lanzar un error explícito indicando qué clave falta (no fallar en silencio).

2. **`resampling.py`**
   - Remuestreo a 1 Hz por interpolación lineal.
   - Suavizado (media móvil configurable) para elevación.
   - Detección de tramos parados/pausados según un umbral de velocidad mínimo y duración mínima, configurables.

3. **`grade_adjustment.py`**
   - Implementación de `coste_minetti(i)` y `velocidad_ajustada(velocidad, pendiente)`.
   - Cálculo de pendiente por muestra con el clipping de seguridad descrito arriba.

4. **`ngp.py`**
   - Media móvil de 30 s (ventana configurable) sobre la velocidad ajustada.
   - Cálculo de NGP (potencia 4 → media → raíz 4).

5. **`rtss.py`**
   - `calcular_rtss(duracion_seg, ngp, ftpace_ms) -> (rtss, IF)`.

6. **`hrtss.py`**
   - `trimp_banister(hr_media, hr_reposo, hr_max, minutos, sexo) -> float`.
   - `calcular_hrtss(hr_serie_1hz, hr_reposo, hr_max, lthr, sexo) -> float`, calibrado contra 1h a LTHR.

7. **`pipeline.py`**
   - Función de orquestación `procesar_actividad(activity_details_json, atleta: dict) -> dict`, donde `atleta` incluye `ftpace_ms, hr_reposo, hr_max, lthr, sexo`.
   - Devuelve un diccionario con `duracion_seg, ngp_ms, IF, rTSS, hrTSS` y, si es útil, las series intermedias para depuración/gráficas.

8. **Tests unitarios (`pytest`)** cubriendo al menos:
   - `coste_minetti(0) == 3.6` y monotonía creciente para pendientes positivas.
   - NGP de una serie constante en llano ≈ la velocidad constante (sin variabilidad, NGP debe converger a la media).
   - rTSS = 100 cuando `NGP == FTPace` y `t == 3600`.
   - hrTSS = 100 cuando `hr_media == lthr` durante 60 minutos continuos.
   - Manejo correcto de una serie con un tramo parado (velocidad 0 sostenida) sin que dispare el NGP hacia abajo de forma artificial.
   - Manejo de datos con muestreo irregular (timestamps no equiespaciados) tras el remuestreo.

9. **Docstrings** en español explicando cada función, con las fórmulas matemáticas en el docstring (no solo en comentarios sueltos).

---

## 4. Restricciones explícitas

- No usar librerías externas de terceros para el cálculo salvo `numpy` (evitar dependencias pesadas tipo `pandas` si no es estrictamente necesario; si Copilot considera que `pandas` simplifica mucho el remuestreo/rolling, puede proponerlo pero debe justificarlo en un comentario).
- No implementar ningún ajuste de pendiente "inventado": debe ser exactamente el modelo de Minetti indicado arriba.
- No asumir un umbral fijo global: `ftpace_ms`, `lthr`, `hr_reposo`, `hr_max` siempre deben venir como parámetros por atleta.
- Dejar explícito en el código y en el README del módulo que el ajuste por pendiente y el hrTSS son **aproximaciones documentadas** al algoritmo propietario de TrainingPeaks (NGP exacto y tabla de multiplicadores de hrTSS no son públicos), no una réplica bit a bit.

---

## 5. Entregable esperado

- Código del módulo completo según la estructura de archivos anterior.
- Un `README.md` corto dentro del módulo resumiendo las fórmulas, qué es aproximado y por qué, y cómo calibrar (comparando contra rTSS reales de TrainingPeaks si se dispone de ellos).
- Suite de tests en verde.
