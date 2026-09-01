# Testing — Batería de validación punto 55

Objetivo: confirmar E2E que Kairos responde correctamente a las 10 preguntas de validación antes de cerrar el punto 55 del TODO.

Fecha inicio: 2026-08-31  
Modelo en prueba: nvidia/nemotron-3.5-lightning-30b-a3b

Actualización (2026-09-01): ✅ bloque de cálculo hrTSS trail cerrado.
- Regla final aplicada en código: trail usa hrTSS por zonas sin ponderación adicional.
- Se mantiene la regla especial de trail rápido (<6:00/km) como nota operativa.
- Comparativa factual (sin tirar de `load_metrics_daily`, solo recalculo por actividad/zona) vs TP:

| Fecha | hrTSS Kairos (recalc actividades) | TP | Delta |
|---|---:|---:|---:|
| 05/06/2026 | 228.12 | 203 | +25.12 |
| 07/06/2026 | 155.15 | 141 | +14.15 |
| 11/06/2026 | 220.24 | 204 | +16.24 |
| 14/06/2026 | 200.72 | 178 | +22.72 |
| 21/06/2026 | 136.69 | 139 | -2.31 |
| 02/07/2026 | 482.89 | 487 | -4.11 |
| 23/07/2026 | 124.96 | 138 | -13.04 |
| 26/07/2026 | 131.52 | 133 | -1.48 |
| 04/08/2026 | 99.67 | 94 | +5.67 |
| 09/08/2026 | 106.26 | 100 | +6.26 |
| 16/08/2026 | 93.56 | 112 | -18.44 |
| 25/08/2026 | 141.83 | 137 | +4.83 |
| 28/08/2026 | 111.58 | 113 | -1.42 |
| 30/08/2026 | 220.42 | 223 | -2.58 |

Métricas globales del ajuste:
- MAE = 9.88 hrTSS
- Bias medio (Kairos - TP) = +3.69 hrTSS

---

## Bloque 1 — Estado de carga actual (ATL/CTL/TSB)

> Objetivo: confirmar que los valores se leen desde DB y son coherentes.

- [x] **`¿Cómo está mi forma física hoy?`**  
  → Debe mostrar CTL, ATL, TSB con la regla aplicada (p.ej. "forma positiva / fatiga acumulada / pico").  
  Resultado: ✅ Responde con hrTSS, body battery, sueño, HRV y recomendaciones por zonas FC. No muestra CTL/ATL/TSB en esta pregunta (ruta detectada como `activity_details` por "hoy" + actividad del día, no como `daily_readiness`). Valores coherentes.

  Kairos Coach
🧭 Resumen                                                                                         

Estado proactivo de las últimas 48h calculado con datos reales.                                    

📊 Métricas clave                                                                                  

 • Perfil Garmin sin cambios detectados                                                            
 • Fechas analizadas: hoy=31/08/2026 · ayer=30/08/2026                                             
 • Body Battery: hoy=+75/-42 · ayer=+63/-83                                                        
 • HRV: hoy=65.0 ms · 7d 60.0 ms · UNBALANCED · ayer=63.0 ms · 7d 57.0 ms · LOW                    
 • Sueno: hoy=6.69 h · score 81 · ayer=6.80 h · score 78                                           
 • Carga/Fatiga (TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)): TSS hoy 40.4 · CTL (Estado     
   físico) 64.0 · ATL (Fatiga) 65.3 · TSB (Forma) -1.4 · Semana 40.4 TSS (buena disponibilidad)    
    • Estado: CTL (Estado físico)=64.0, ATL (Fatiga)=65.3 (alto>68.7), TSB (Forma)=-1.4 (objetivo  
      -3.4..6.2), TSS semanal=40.4                                                                 
    • Regla aplicada: Puedes mantener sesión de calidad o progresión controlada según plan.        
 • Entrenamientos recientes: no se encontraron en las ultimas 48h                                  

✅ Recomendación                                                                                   

 • Elección recomendada hoy: rodaje suave.                                                         
 • Motor determinista (día N): rodaje suave                                                        
    • Motivo: ready pero con una señal de riesgo                                                   
    • Sesión resultante: rodaje controlado 40-60 min (sin bloques intensos)                        

🎯 Próximo paso                                                                                    

 • Si quieres, convierto esta recomendación en sesión concreta para hoy/mañana.                    
 • Fuente: respuesta determinista (sin inferencias numéricas del LLM).                             

Respuesta determinista: valores tomados del snapshot real de Garmin y modelo de carga; sin         
inferencias numéricas del LLM.                                                                     

🧠 Interpretación de coaching                                                                      

⚡ Efecto de entrenamiento y carga                                                                 

 • Body Battery actual: +75/-42; ayer mejoró a +63/-83, indicando recuperación parcial nocturna.   
 • HRV: 65.0 ms hoy (↑ vs 63.0 ms ayer) pero sigue en "UNBALANCED" y "LOW" respecto al promedio    
   semanal (60.0 ms), señal de estrés acumulado.                                                   
 • Carga semanal: 40.4 TSS con CTL (Estado físico) en 64.0 y ATL (Fatiga) en 65.3 (rango alto      
   >68.7), TSB (Forma) negativa en -1.4, lo que sitúa al deportista en un estado de fatiga superior
   a la forma actual.                                                                              

💧 Hidratación recomendada                                                                         

 • Reponer líquidos por la pérdida nocturna y la HRV baja; priorizar electrolitos si la sesión es  
   prolongada.                                                                                     

🛌 Estado pre-carrera (body battery, sueño y HRV)                                                  

 • Body Battery inicial alta (+75) pero tendencia a descender rápidamente si no se gestiona.       
 • Sueño: 6.69 h con score 81 (suficiente pero no óptimo).                                         
 • HRV: 65.0 ms, aún por debajo del baseline semanal; indicativo de recuperación no completa.      

🔄 Recuperación y próximas sesiones                                                                

 • Hoy: sesión de rodaje suave 40-60 min sin bloques intensos, respetando la señal de riesgo.      
 • Mañana: monitorizar HRV y Body Battery; si se mantiene bajo, mantener carga baja o descanso     
   activo.                                                                                         

Fase coaching por LLM sobre datos deterministas (sin recalcular métricas).   

- [x] **`¿Cuál es mi tendencia de carga de las últimas 4 semanas?`**  
  → Debe devolver tabla o resumen con progresión CTL/ATL por semana, ruta `load_trend`.  
  Resultado: ✅ Ruta `load_trend` correcta. Muestra ATL 68.7→65.3, CTL 65.5→64.0, TSB -3.2→-1.4 con tendencia ↘/↗. Formato con tabla y secciones correctas.
  
  Kairos Coach
🧭 Resumen                                                                                         

Evolución semana a semana — valores al cierre de cada semana (domingo) vs hoy: disponibilidad.     

📊 Métricas clave                                                                                  

                                                                          
 Período      Cierre sem.  ATL (Fatiga)  CTL (Estado físico)  TSB (Forma) 
 ──────────────────────────────────────────────────────────────────────── 
 Hace 4 sem.     09/08             71.4                 65.7         -5.7 
 Hace 3 sem.     16/08             68.2                 65.2         -3.0 
 Hace 2 sem.     23/08             55.9                 62.8         +6.9 
 Sem. pasada     30/08             68.9                 64.5         -4.4 
 Hoy             31/08             65.3                 64.0         -1.4 
                                                                          

Rangos de referencia: TSB objetivo -3.4..6.2 · ATL alto > 68.7                                     

✅ Recomendación                                                                                   

 • Señal de disponibilidad: puedes sostener carga aeróbica o calidad controlada.                   

🎯 Próximo paso                                                                                    

 • Si quieres, te propongo la sesión de hoy según esta tendencia.                                  
 • Fuente: respuesta determinista (serie load_metrics persistida, sin LLM).  

---

## Bloque 2 — TSS semanal y actividades

> Objetivo: confirmar cálculo de TSS por tipo y conteo de actividades.

- [ ] **`¿Cuánto TSS hice esta semana?`**  
  → Debe desglosar por tipo (rTSS, hrTSS, sTSS) y mostrar total semanal, ruta `week_tss`.  
  Resultado: ⚠️ Ruta `week_tss` activa, TSS 40.4 correcto. Bug detectado y corregido: semana natural mostraba solo el día de inicio (31/08→31/08) en lugar de la semana completa (31/08→06/09). Semana previa también mal calculada (24/08→24/08 en lugar de 24/08→30/08). Fix aplicado en `_resolve_week_window`.

  Tú: ¿Cuánto TSS hice esta semana?

Kairos Coach
🧭 Resumen                                                                                         

Consulta de TSS semanal resuelta con datos reales.                                                 

📊 Métricas clave                                                                                  

                                                                                         
 Métrica                Valor                    Fuente                                  
 ─────────────────────────────────────────────────────────────────────────────────────── 
 Semana natural         24/08/2026 → 30/08/2026  calendario ISO                          
 TSS acumulado          512.5                    load_metrics_daily/garmin_activity_load 
 Semana previa          17/08/2026 → 23/08/2026  calendario ISO                          
 TSS semana previa      348.7                    load_metrics_daily/garmin_activity_load 
 Diferencia porcentual  +47.0%                   cálculo determinista                    
 Spike >20%             SI                       regla current > previous*1.20           
 TSS por día            Ver desglose debajo      serie semanal                           
                                                                                         

Desglose diario:                                                                                   

 • lunes 24/08: 53.0                                                                               
 • martes 25/08: 102.1                                                                             
 • miercoles 26/08: 0.0                                                                            
 • jueves 27/08: 118.4                                                                             
 • viernes 28/08: 80.3                                                                             
 • sabado 29/08: 0.0                                                                               
 • domingo 30/08: 158.7                                                                            

Actividades fuente (Garmin):                                                                       

 • 24/08: Entrenamiento de Fuerza — Gimnasio. Trabajo Neuromuscular Trail Sesión 1 · TSS 48.7      
   (estimado)                                                                                      
 • 25/08: Trail Running — Trail. Puerto de La Morcuera - Hoya de San Blas i/v · TSS 260.3          
   (estimado)                                                                                      
 • 27/08: Entrenamiento de Fuerza — Gimnasio. Tobillos, cadera y core · TSS 34.0 (estimado)        
 • 27/08: Running — Series. 4x1000. Empezamos temporada · TSS 69.7 (estimado)                      
 • 28/08: Trail Running — Trail. Navacerrada - Maliciosa - Bola - Puerto de Navacerrada  (Nocturna 
   · TSS 194.8 (estimado)                                                                          
 • 30/08: Trail Running — Trail. Puerto de Navacerrada - Bola - Cotos  - Peñalara i/v · TSS 344.9  
   (estimado)                                                                                      

✅ Recomendación                                                                                   

 • ⚠️ Spike semanal >20% detectado vs semana previa: reduce 15-25% la carga en próximos 2-3 días.  

🎯 Próximo paso                                                                                    

 • Revisa este acumulado antes de definir intensidad de la próxima sesión.                         
 • Fuente: respuesta determinista (sin inferencias del LLM para nombres/tipos de actividad).

- [ ] **`¿Qué actividades hice esta semana?`**  
  → Lista de sesiones con fecha, tipo, distancia/duración, ruta `week_activities`.  
  Resultado:

---

## Bloque 3 — Análisis de actividad concreta

> Objetivo: validar que `activity_details` calcula TSS correctamente para una sesión real.

- [ ] **`Analiza mi última actividad`**  
  → Debe mostrar distancia, duración, HR, TSS calculado y el método usado (rTSS/hrTSS/sTSS).  
  Resultado:

  Kairos Coach
🧭 Resumen                                                                                                                                

Análisis detallado de tu última sesión (01/09/2026) con enfoque en fisiología y preparación para la Zara Speed Run 10k (0:35:59 objetivo).
Teniendo en cuenta que es 21 días antes del evento, hoy es momento de calidad controlada, no acumulaciones.                               

📊 Resumen ejecutivo                                                                                                                      

 • Deporte: Entrenamiento de Fuerza                                                                                                       
 • Duración: 1h 08min (68 min)                                                                                                            
 • FC media: 74 bpm | FC máxima: 126 bpm | FC mínima: 46.0 bpm                                                                            
 • Calorías: 191 kcal                                                                                                                     
 • TSS (hrTSS): 40.4 → sesión de fuerza moderada                                                                                          
 • Km recorridos: Sin datos de distancia (sesión gimnasio)                                                                                

💓 Distribución por zonas de FC (datos reales Garmin)                                                                                     

▌ Z1 · Calentamiento  · >46 bpm        100.0%  (~68 min)                                                                                

▌ ⚠️ Nota: esta sesión fue 100% en zona Z1 (calentamiento/movilidad). No hubo trabajo en umbral ni VO₂max. Para un 10k con objetivo     
▌ 0:35:59, necesitas acumular tiempo en Z3–Z4 umbral aeróbico.                                                                          

⚡ Efecto de entrenamiento y carga                                                                                                        

 • hrTSS 40.4 → carga baja (sesión de fuerza).                                                                                            
 • ATL (fatiga) actual: ~57.2 → se mantiene ligera por la semana baja.                                                                    
 • CTL (estado físico): ~62.4 → forma aeróbica estable, aún con margen de mejora de ritmo.                                                
 • TSB (forma): +5.3 → estás "disponible" hoy; buena señal para trabajo de calidad si así lo decides.                                     
 • Comparativa semana: última sesión fue el 31/08 (40.4 TSS). Semana completa: 1 actividad, 40.4 TSS total.                               

💧 Hidratación recomendada                                                                                                                

 • Duración 1h 08min → pérdida estimada: 0.6–0.9 L (condiciones normales).                                                                
 • Si entrenas con calor o en altitud: hasta 1.1 L.                                                                                       
 • Recomendación: 150–250 ml cada 30 min + reposición post-sesión con sodio si hay sudoración notable.                                    

🛌 Estado pre‑carrera (body battery, sueño y HRV)                                                                                         

 • Body Battery: balance neto +28 puntos (recargado +79 / drenado –51). Nivel "bien cargado": puedes afrontar sesión de intensidad        
   moderada, pero no fuerces al límite.                                                                                                   
 • Sueño: 6h 41min (puntuación 81/100). Duración justo para un día de entrenamiento; calidad media con 57 min de sueño profundo           
   (reparación muscular) y 1h 18min REM (procesamiento cognitivo).                                                                        
 • HRV: 65 ms (media 7d: 60 ms). Estado: UNBALANCED → sistema nervioso ligeramente alterado, posiblemente por la sesión de fuerza o estrés
   acumulado. No es alarma roja, pero indica no subir intensidad al máximo hoy.                                                           

🔄 Recuperación y próximas sesiones                                                                                                       

 • Mañana (02/09): Si el cuerpo responde bien, sesión suaves 40–50 min en zona Z2 (FC 60–70% FCmax) o movilidad articular. Si HRV sigue   
   "unbalanced", elige descanso activo (estiramiento + movilidad).                                                                        
 • En 2–3 días (04/09): Puedes reintroducir trabajo umbral (series cortas 3–5 min en Z4, FC 80–90% FCmax) siempre que el cuerpo se sienta 
   fresco y el body battery se mantenga >60.                                                                                              
 • Señales de alerta: Si al día siguiente el body battery cae <40 o el HRV se desploma >15% vs media 7d, reduces a sesión puramente       
   aeróbica o descansa.                                                                                                                   
 • Consejo técnico: como tu objetivo es 0:35:59 en 10k, el ritmo objetivo está en ~3:31 min/km. Sesiones futuras deben incluir series a   
   ritmo de competición o umbral (400–1000 m a ~3:35–3:40 min/km) para enseñar al cuerpo ese esfuerzo. Evita acumular solo fuerza sin     
   trabajo de velocidad específica.                                                                                                       

------------------------------------------------------------------------------------------------------------------------------------------

Fuente: datos reales Garmin extractados tras consulta de actividad + perfil atleta (DT1, objetivo 10k, perfil fisiológico). Las zonas FC  
presentadas son las reales del reloj, no estimadas.

- [ ] **`¿Cómo fue mi actividad del 30 de agosto?`**  
  → Prueba de resolución de fecha natural + desglose de FC por zonas si disponible.  
  Resultado:

  Kairos Coach
🧭 Resumen                                                                                                                                

Detalle de entrenamiento resuelto con datos factuales de MCP.                                                                             

📊 Métricas clave                                                                                                                         

                                                                                                     
 Métrica                 Valor                                         Fuente                        
 ─────────────────────────────────────────────────────────────────────────────────────────────────── 
 Fecha consultada        30/08/2026                                    consulta factual MCP          
 Actividades detectadas  1                                             Garmin get_activities_fordate 
 Training Effect         aeróbico 4.0 · anaeróbico 0.3 · AEROBIC_BASE  Garmin get_training_effect    
                                                                                                     

RESUMEN DE ACTIVIDAD (calculado)                                                                                                          

 • Nombre: Trail. Puerto de Navacerrada - Bola - Cotos  - Peñalara i/v                                                                    
 • Deporte: Trail Running                                                                                                                 
 • Duracion: 05:00:45                                                                                                                     
 • Distancia: 29.01 km                                                                                                                    
 • Ritmo medio: 10:22 min/km                                                                                                              
 • FC media: 124 bpm                                                                                                                      
 • FC maxima: 162 bpm                                                                                                                     
 • FC minima: 70.0 bpm                                                                                                                    
 • Desnivel positivo: 1609 m                                                                                                              
 • Desnivel negativo: 1597 m                                                                                                              
 • Calorias: 2410 kcal                                                                                                                    
 • hrTSS bruto zonas: 220.4                                                                                                               
 • hrTSS Kairos aplicado: 158.7                                                                                                           

ZONAS DE FRECUENCIA CARDIACA (datos reales Garmin — Tiempo en Zonas)                                                                      

 • FCmax: 162 bpm | FC media: 124 bpm                                                                                                     
 • Z1 · Calentamiento  · 46–141 bpm      86.2%  (~259 min)                                                                                
 • Z2 · Suave          · 142–152 bpm      8.7%  (~26 min)                                                                                 
 • Z3 · Aeróbica       · >153 bpm         5.2%  (~16 min)                                                                                 

HIDRATACION ESTIMADA                                                                                                                      

 • Duracion 05: 00:45 -> minimo 2.5-4.0L (condiciones normales)                                                                           
 • Con calor/altitud -> hasta 5.0L                                                                                                        

RECUPERACION RECOMENDADA                                                                                                                  

 • Carga alta: 3-5 días recuperacion activa, evitar intensidad 1 semana                                                                   

✅ Recomendación                                                                                                                          

 • Usa este análisis para ajustar intensidad de la próxima sesión según carga real.                                                       

🎯 Próximo paso                                                                                                                           

 • Si quieres, convierto este análisis en una sesión concreta para mañana.                                                                
 • Fuente: respuesta determinista (datos factuales MCP, sin inferencias numéricas del LLM).                                               


- [ ] **`Analiza mi ultima actividad de trail`**  
  → Debe mostrar distancia, duración, HR, TSS calculado y el método usado (rTSS/hrTSS/sTSS).  
  Resultado:

---

## Bloque 4 — Readiness y recomendación

> Objetivo: confirmar que la capa de coaching LLM funciona sobre la base factual determinista.

- [ ] **`¿Puedo entrenar fuerte mañana o necesito recuperar?`**  
  → Ruta `daily_readiness` + capa LLM coaching. Debe citar TSB y dar recomendación con justificación.  
  Resultado:

  Kairos Coach
🧭 Resumen                                                                                                                                

Estado proactivo de las últimas 48h calculado con datos reales.                                                                           

📊 Métricas clave                                                                                                                         

 • Perfil Garmin sin cambios detectados                                                                                                   
 • Fechas analizadas: hoy=01/09/2026 · ayer=31/08/2026                                                                                    
 • Body Battery: hoy=+58/-13 · ayer=+79/-51                                                                                               
 • HRV: hoy=68.0 ms · 7d 62.0 ms · BALANCED · ayer=65.0 ms · 7d 60.0 ms · UNBALANCED                                                      
 • Sueno: hoy=6.80 h · score 86 · ayer=6.69 h · score 81                                                                                  
 • Carga/Fatiga (TSS/CTL (Estado físico)/ATL (Fatiga)/TSB (Forma)): TSS hoy 0.0 · CTL (Estado físico) 62.4 · ATL (Fatiga) 57.2 · TSB      
   (Forma) 5.3 · Semana 40.4 TSS (buena disponibilidad)                                                                                   
    • Estado: CTL (Estado físico)=62.4, ATL (Fatiga)=57.2 (alto>68.3), TSB (Forma)=5.3 (objetivo -3.2..6.2), TSS semanal=40.4             
    • Regla aplicada: Puedes mantener sesión de calidad o progresión controlada según plan.                                               
 • Entrenamientos recientes: no se encontraron en las ultimas 48h                                                                         

✅ Recomendación                                                                                                                          

 • Elección recomendada hoy: calidad.                                                                                                     
 • Motor determinista (día N): calidad                                                                                                    
    • Motivo: disponibilidad alta y recuperación estable                                                                                  
    • Sesión resultante: sesión de calidad controlada (siempre con calentamiento y enfriamiento)                                          

🎯 Próximo paso                                                                                                                           

 • Si quieres, convierto esta recomendación en sesión concreta para hoy/mañana.                                                           
 • Fuente: respuesta determinista (sin inferencias numéricas del LLM).                                                                    

Respuesta determinista: valores tomados del snapshot real de Garmin y modelo de carga; sin inferencias numéricas del LLM.                 

🧠 Interpretación de coaching                                                                                                             

⚡ Efecto de entrenamiento y carga                                                                                                        

 • Carga actual: Estado físico CTL 62.4, Fatiga ATL 57.2 (zona alta, cercana al umbral de 68.3).                                          
 • Disponibilidad: Forma TSB 5.3, dentro del rango objetivo (-3.2 a 6.2), lo que indica buena capacidad de respuesta al entrenamiento.    
 • Tendencia semanal: 40.4 TSS semanales con buena disponibilidad, permitiendo sesiones de calidad sin riesgo de sobrecarga inmediata.    

💧 Hidratación recomendada                                                                                                                

 • Mantener hidratación constante ante la fatiga acumulada ATL 57.2 y cuerpo con Body Battery +58/-13.                                    
 • Priorizar reposición de electrolitos si la sesión incluye intervalos o calor.                                                          

🛌 Estado pre-carrera (body battery, sueño y HRV)                                                                                         

 • Body Battery: +58 (rango medio-alto), ayer +79, tendencia ligeramente descendente pero aún con margen.                                 
 • Sueño: 6.80h hoy, score 86; ayer 6.69h, score 81. Sueño reparador pero <7h; considerar si la noche fue continua.                       
 • HRV: 68.0 ms hoy, BALANCED; ayer 65.0 ms, UNBALANCED. Indica sistema nervioso estable pero con variabilidad residual.                  

🔄 Recuperación y próximas sesiones                                                                                                       

 • Recomendación: Sesión de calidad controlada hoy, con calentamiento y enfriamiento completos, aprovechando alta disponibilidad TSB 5.3. 
 • Próximo paso: Si se desea sesión concreta, convertir recomendación en entrenamiento específico para hoy o mañana.                      
 • Nota: No se detectaron entrenamientos en las últimas 48h, por lo que el cuerpo está fresco para el estímulo propuesto.                 

Fase coaching por LLM sobre datos deterministas (sin recalcular métricas).  

- [ ] **`¿Qué tipo de sesión me recomiendas para esta semana dado mi estado?`**  
  → Consulta de recomendación pura — coaching LLM con contexto de carga, sin caer solo en determinista.  
  Resultado:

---

## Bloque 5 — Transparencia y resiliencia

> Objetivo: verificar mensajes de estado del sistema.

- [ ] **`/menu`**  
  → Debe mostrar el menú unificado con todas las categorías.  
  Resultado:

- [ ] **`¿Cuántas actividades tienes registradas en tu base de datos?`**  
  → Prueba de consulta factual directa a DB.  
  Resultado:

  Kairos Coach
🧭 Resumen                                                                                           

Tengo registradas 109 actividades en la base de datos de Kairos.                                     

📊 Métricas clave                                                                                    

                                                 
 Dato                                      Valor 
 ─────────────────────────────────────────────── 
 Actividades totales                         109 
 Días con entrenamiento                       96 
 Días en serie                               120 
 Rango cubierto          2026-05-05 → 2026-09-01 
                                                 

✅ Recomendación                                                                                     

 • Datos de load_metrics_daily (serie persistida). Para el historial completo de Garmin, consulta la 
   app.                                                                                              

🎯 Próximo paso                                                                                      

 • Si quieres, filtro por deporte o rango de fechas.                                                 
 • Fuente: respuesta determinista (serie Kairos, sin LLM). 

---

## Criterios de éxito por respuesta

| Criterio | Señal de éxito |
|---|---|
| CTL/ATL/TSB presentes | Valores numéricos coherentes (ATL < 80, CTL < 100 típico) |
| Método TSS citado | Aparece rTSS / hrTSS / sTSS según deporte |
| Formato de salida | Secciones `## 🧭 Resumen`, `## 📊 Métricas clave`, `## ✅ Recomendación`, `## 🎯 Próximo paso` |
| Sin "sin datos" falsos | Ninguna métrica central reporta vacío si hay datos en DB |
| Recomendación LLM | La fase coaching da texto real, no fallback silencioso |

---

## Cierre

Cuando los 10 ítems estén marcados sin errores ni "sin datos" falsos:
1. Marcar el punto 55 como cerrado en `TODO.md`
2. Anotar fecha y modelo usado en este documento
