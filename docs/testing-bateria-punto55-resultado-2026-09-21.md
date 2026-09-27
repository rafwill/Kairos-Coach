# Resultado bateria punto55 - 2026-09-21

## 1) Contexto revisado (`docs/proximos_pasos.md`)

Estado vigente confirmado en el documento:
- Cierre final del bloque TSS/running con `TSS_FORMULA_VERSION=26`.
- Running/Trail/Fuerza marcados como cerrados (running con excepcion de sesgo documentada).
- Documento de referencia vigente: `docs/tss-cierre-definitivo-2026-09-16.md`.

## 2) Ejecucion de bateria (`docs/testing-bateria-punto55.md`)

Se ejecutaron corridas reales contra Kairos con el usuario solicitado:
- Usuario: `rafwill1@hotmail.com`
- Backend: `frozen`
- Fecha: `2026-09-21`

Se hicieron varias corridas por resiliencia (cambios de proveedor y timeout):
- `docs/bateria_p55_2026-09-21_raw.txt` (intento inicial, bloqueo 403 en Gemini)
- `docs/bateria_p55_2026-09-21_raw_nim.txt` (NVIDIA, sin 403, con timeouts largos)
- `docs/bateria_p55_2026-09-21_raw_groq.txt` (Groq, multiples 403)
- `docs/bateria_p55_2026-09-21_raw_nim_t20.txt` (NVIDIA con `KAIROS_LLM_TIMEOUT_SECONDS=20` para forzar avance)

## 3) Resultado consolidado (corrida de referencia)

Corrida de referencia usada para cierre operativo de hoy:
- Archivo: `docs/bateria_p55_2026-09-21_raw_nim_t20.txt`
- Proveedor: NVIDIA NIM (`nemotron-3.5-lightning-30b`)
- Prompting enviado: bateria de 22 preguntas + `salir`

Metricas observadas en traza:
- `Kairos Coach` apariciones: `26`
- `403/Forbidden`: `0`
- Mensajes de timeout LLM: `7`
- `Sesion guardada en memoria`: no observado en esta corrida por corte de ejecucion/timeout en flujo interactivo

Interpretacion:
- La parte determinista/factual (MCP + calculo local) respondio de forma consistente en la bateria.
- La parte de coaching LLM tuvo inestabilidad por latencia (timeouts), sin errores 403 en la corrida NVIDIA de referencia.
- Se obtuvo evidencia suficiente de comportamiento para los bloques factuales clave (carga diaria/semanal, actividades por rango, umbrales, estado de plan).

## 4) Hallazgos funcionales principales

1. Salud de datos y carga:
- Se observa recalculo de serie de carga al inicio y respuestas factuales de TSS/estado diario.

2. Consultas de actividades:
- Respuestas factuales correctas en ventanas por fecha (incluyendo rango 25/08-30/08 con 5 actividades detectadas).

3. Perfil persistido:
- Se respondieron correctamente `ritmo umbral` y `FC umbral` desde perfil.

4. Plan activo:
- Se devolvio consistentemente `No tienes plan asignado ahora mismo` en las consultas repetidas de plan.

5. Riesgo actual de E2E:
- Timeouts del proveedor LLM en prompts de coaching/recomendacion degradan la continuidad de una bateria larga en modo no interactivo.

## 5) Estado final de la bateria (hoy)

Estado: **EJECUTADA CON INCIDENCIAS DE LATENCIA LLM**

Conclusiones:
- Objetivo cumplido parcialmente en capa factual/determinista.
- Persisten incidencias de timeout LLM en una parte de prompts de coaching.
- Evidencia completa guardada en los 4 archivos raw listados.

## 6) Recomendacion para siguiente rerun (opcional)

Para un cierre E2E completamente limpio (22/22 sin timeout visible):
- Repetir en ventana de menor latencia o con proveedor estable en la red actual.
- Mantener `KAIROS_SKIP_STARTUP_STATUS=1`.
- Mantener timeout LLM controlado y hacer rerun interactivo para confirmar `Sesion guardada en memoria` al salir.
