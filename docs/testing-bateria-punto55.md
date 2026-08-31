# Testing — Batería de validación punto 55

Objetivo: confirmar E2E que Kairos responde correctamente a las 10 preguntas de validación antes de cerrar el punto 55 del TODO.

Fecha inicio: 2026-08-31  
Modelo en prueba: nvidia/nemotron-3.5-lightning-30b-a3b

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

- [ ] **`¿Cómo fue mi actividad del [fecha]?`**  
  → Prueba de resolución de fecha natural + desglose de FC por zonas si disponible.  
  Resultado:

- [ ] **`Analiza mi ultima actividad de trail`**  
  → Debe mostrar distancia, duración, HR, TSS calculado y el método usado (rTSS/hrTSS/sTSS).  
  Resultado:

---

## Bloque 4 — Readiness y recomendación

> Objetivo: confirmar que la capa de coaching LLM funciona sobre la base factual determinista.

- [ ] **`¿Puedo entrenar fuerte mañana o necesito recuperar?`**  
  → Ruta `daily_readiness` + capa LLM coaching. Debe citar TSB y dar recomendación con justificación.  
  Resultado:

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
