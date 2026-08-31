# Testing — Batería de validación punto 55

Objetivo: confirmar E2E que Kairos responde correctamente a las 10 preguntas de validación antes de cerrar el punto 55 del TODO.

Fecha inicio: 2026-08-31  
Modelo en prueba: nvidia/nemotron-3.5-lightning-30b-a3b

---

## Bloque 1 — Estado de carga actual (ATL/CTL/TSB)

> Objetivo: confirmar que los valores se leen desde DB y son coherentes.

- [ ] **`¿Cómo está mi forma física hoy?`**  
  → Debe mostrar CTL, ATL, TSB con la regla aplicada (p.ej. "forma positiva / fatiga acumulada / pico").  
  Resultado:

- [ ] **`¿Cuál es mi tendencia de carga de las últimas 4 semanas?`**  
  → Debe devolver tabla o resumen con progresión CTL/ATL por semana, ruta `load_trend`.  
  Resultado:

---

## Bloque 2 — TSS semanal y actividades

> Objetivo: confirmar cálculo de TSS por tipo y conteo de actividades.

- [ ] **`¿Cuánto TSS hice esta semana?`**  
  → Debe desglosar por tipo (rTSS, hrTSS, sTSS) y mostrar total semanal, ruta `week_tss`.  
  Resultado:

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
