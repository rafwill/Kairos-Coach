# Próximos pasos — Kairos Coach

Última revisión: 26/10/2026.

Este fichero tiene dos partes: **lo que queda pendiente** y la **biblioteca de experiencia** (una entrada por cada cambio adoptado, según `docs/Harness.md` §7). Lo que se termina se borra de aquí: el detalle queda en `CHANGELOG.md` y en git. El estado vigente del cálculo de carga y las limitaciones aceptadas están en `CLAUDE.md`.

La bitácora anterior (investigación del TSS frente a TrainingPeaks y sesiones del 16/09 al 25/10/2026) se quitó en esta revisión; se puede consultar en git con `git show 4578226:docs/proximos_pasos.md`.

## Dónde lo dejamos

Se reescribe al final de cada sesión (la anterior se pierde: el detalle está en git y en el CHANGELOG).

**Sesión del 26/10/2026** (ordenador 2). Todo subido a GitHub (código hasta `4f72186` y este commit de documentación), sin cambios a medias. 751 tests.

- Hecho: puesta al día del ordenador 2 (`.venv`, pre-commit, `NVIDIA_MODEL`, Supabase Auth; batería 25/25 con el juez nuevo); S10 con la marca de progresivo detrás del rango; perfil de salud (patologías y lesiones separadas, diabetes con su medicación, protocolo solo si aplica, atleta sano sin preguntas); reglas de lesiones a lo largo de la temporada; S27 sin confundir "4/10" de dolor con una fecha.
- Al empezar en casa: `git pull`, entrar una vez en Kairos (el perfil ya está migrado en Supabase, no preguntará nada) y repasar con S10 nuevo las baterías guardadas allí (punto 1).
- También hecho el 26/10: atleta sano y DT2 comprobados en vivo; modelo elegido por el atleta (perfil) con la clave en `.env` y renovación de la clave caducada; "45 herramientas" explicado (42 de Garmin + 3 de Kairos).
- Al entrar por primera vez tras este cambio, Kairos enseña el menú de modelo una vez (el perfil aún no tiene `llm.provider`); en casa, si el `.env` no tiene la clave elegida, la pide.
- Siguiente paso propuesto: punto 4 (límite de tools y metatooling) o los sensores pendientes del punto 1.

## Pendiente

### A. Puesta en producción

1. **Uso real y revisión de las líneas `[SENSOR]` de `agent.log`** (punto 6 de la lista previa a producción; los puntos 1 a 5 se cerraron entre el 24 y el 25/10). Usar Kairos unos días con preguntas reales y revisar juntos:
   - cuánto salta cada sensor, falsos positivos nuevos y qué guía conviene reforzar (bucle de gobierno, `docs/Harness.md` §7);
   - **posible falso positivo de S10** (24/10, 09:18): marcó como fuera de zona 105–124 y 159–179 lpm, que son justo la Z1 y la Z5 de bici. Parece que tomó una línea de ciclismo como de carrera (detección del deporte en `_range_sport`). Reproducirlo con la respuesta completa antes de tocar el sensor;
   - **S10 con la marca de progresivo detrás del rango** (arreglado el 26/10 en el ordenador 2): repasar en casa las baterías guardadas (las 891 respuestas) con el sensor nuevo y confirmar que solo desaparecen falsos positivos de este tipo;
   - **S10 en el juez con tablas partidas**: el juez lee la transcripción pintada a 110 columnas y una celda larga se parte en varias líneas; batería 20261026_100523, pregunta 9: "progresivo" quedó en la línea anterior a "Z2→Z3 (135→157 lpm)" y el juez dio S10, mientras que en vivo (texto sin partir) no saltó. Decidir si el juez reconstruye las celdas o si se acepta como ruido conocido. No confundir con el acierto de la batería 20261026_095716 (12 km continuos "Z3 Tempo–Z4 Subumbral (146–168 lpm)", error real del LLM que el reintento no corrigió);
   - si **S25** (diferencia de HRV), **S26** (etiqueta rTSS/hrTSS) y **S27** ("mañana" con el día que no toca) saltan con preguntas reales; S27 solo ve el error cuando el LLM escribe el día o la fecha, no un "mañana" a secas;
   - **día de la semana que no cuadra con su fecha** (26/10, prueba de lesiones): el LLM escribió "el domingo (20/10)" y el 20/10/2026 fue lunes. Ningún sensor lo ve: S27 solo comprueba fechas junto a hoy/ayer/mañana. Posible sensor nuevo (cualquier "día de la semana + DD/MM" del texto, con el año resuelto como en S27), con el criterio de siempre: 0 falsos positivos en las baterías guardadas y cada aviso revisado a mano.
2. **Ordenador 2 (ECI)**: hecho el 26/10 (pull, Supabase Auth, `.venv` y pre-commit, `ENCRYPTION_KEY`, batería). Queda comprobar que la memoria entre sesiones funciona allí.

### B. Chat y LLM

4. **Límite de tools y metatooling** (nota del usuario). El servidor expone 42 tools. Estudiar si algún proveedor o modelo limita el número de tools por petición (del orden de 20) y si conviene agruparlas o que el LLM las pida por búsqueda (metatooling).
5. **Cobertura restante de `agent/main.py`** (89 %). Quedan sin tests las ramas de error del inicio de sesión y el ajuste de la consola de Windows. Prioridad baja.

### C. Cálculo de carga (TSS frente a TrainingPeaks)

Antes de tocar cualquier fórmula: hipótesis escrita, criterio de cierre fijado antes de ver resultados y prueba sin escribir (reglas de método de `CLAUDE.md`).

6. **Trail rápido (< 6:00/km).** Validar cuando haya más salidas con rTSS en TP. Hoy n = 1 (16/08: Kairos 128.0 frente a TP 112.4).
7. **Series cortas de running** (6x500, 6x1000, 4x400, 8x500): salen entre un 11 % y un 20 % por encima de TP; 23966766696 (13/08, 8x45" en cuesta) ×1.20. Plantear hipótesis antes de tocar el detector (p. ej. relación FC-velocidad con retraso fisiológico).
8. **Bici sin potencia de más de 2 h.** 07/08: Kairos 65.9 frente a TP 93. La tabla hrTSS de TP no tiene datos de bici por encima del 85 % del umbral.
9. **Revalidación periódica** con un export nuevo de TP: prueba sin escribir, criterio fijado antes, solo las filas esperadas.
10. **Mantenimiento de umbrales.** Si cambia el umbral de FC en TP, añadir ese mismo día la entrada con fecha a `hr_threshold_tp_history` (no hay comando). El ritmo umbral se registra con `/perfil umbral`.

### Descartado

- Quitar a Copilot de los contributors de GitHub: exigiría reescribir el historial; decidido no hacerlo (23/10).
- Capturar el RPE de fuerza después de la sesión: el TSS de fuerza sale de la FC (modelo B) y ya cuadra con TP (≈ 1,00); decidido quitarlo (25/10).

## Biblioteca de experiencia

Formato de `docs/Harness.md` §7: **señal** (qué falló y dónde), **lección** (regla general) y **evidencia** (antes y después). Lo que quedó abierto de cada entrada está en Pendiente.

### 23/10/2026 — El router no decide con texto del LLM

- Señal: batería 20261023_203446, la pregunta 9 fue a la ruta `week_tss` (24/25); en las siete baterías anteriores del día, a la de estado.
- Lección: el router determinista no puede decidir con texto que escribe el LLM. Cualquier regla que mire el historial lee solo la parte determinista de las respuestas.
- Evidencia: el coaching de la pregunta 8 decía "TSS acumulado" y `_is_week_tss_followup_intent` lo tomó como contexto de TSS semanal. Corregido con test; batería posterior en el CHANGELOG.

### 24/10/2026 — Seguridad de Supabase

- Señal: las 11 tablas con RLS desactivado y la clave `anon` (pública) en cada `.env`. `check_rls`: 8 tablas legibles sin sesión, incluida `app_user` con hashes y contraseñas de Garmin cifradas. Kairos leía `app_user` antes de iniciar sesión.
- Lección: el filtro por usuario tiene que estar en la base de datos (RLS), no en el cliente, y ningún fichero de esquema debe desactivar RLS al reejecutarse (`schema.sql` lo hacía en cada ejecución).
- Evidencia: Supabase Auth y `rls.sql` aplicados; `check_rls` da 0 filas sin sesión; escrituras ajenas rechazadas; batería 25/25 antes y después de activar las políticas. Detalle en `supabase/README.md` y CHANGELOG.

### 25/10/2026 — Fiabilidad frente a Garmin

- Señal: exploración del 24/10, "¿cuál fue mi actividad más larga de septiembre?": 5 timeouts de `get_activity` seguidos y 144 s.
- Lección: FastMCP ejecuta las tools síncronas dentro de su bucle de eventos. Una petición lenta a Garmin (hasta 30 s en `garminconnect`) paraba el servidor y encolaba las demás, y Kairos, que corta a los 8 s, seguía mandando llamadas que también caducaban. Las tools van en hilos con un tope (4) para no provocar 429, el login con un cerrojo, y Kairos lanza a la vez las tools de un mismo mensaje del LLM. No se baja el límite de 30 s de `garminconnect` (va fijo en cada petición; habría que parchear la librería).
- Evidencia: 6 tools reales en 3,14 s en serie frente a 1,36 s en paralelo; la misma pregunta, 30 s y 0 timeouts; batería 25/25; ningún 429. Detalle en `garmin_mcp/README.md`, `docs/arquitectura.md`, `docs/Harness.md` (S7) y CHANGELOG.

### 25/10/2026 — Rutas deterministas de periodo y sueño

- Señal: exploración del 24/10. "¿Cuál fue mi actividad más larga de septiembre?" y "¿cuántos km este mes?" las resolvía el LLM sumando series (actividad equivocada, 144 s); "¿cómo dormí anoche?" devolvía la tabla de todo el día.
- Lección: una suma o un máximo sobre una lista no se deja al LLM. Además, `get_activities_by_date` pagina de 100 en 100: la primera versión daba "100 actividades" en el año (285 reales), así que hay que pedir páginas hasta `has_more = false`. Y el sueño de hoy puede no estar sincronizado: se dice y se muestra la última noche con su fecha.
- Evidencia: rutas `period_activities` y `sleep_last_night`; cifras iguales a la suma directa del servidor (151,42 km en octubre, 309,24 km en septiembre, 149 carreras en el año); 2–3 s por mes, 22 s el año entero; batería 25/25.

### 25/10/2026 — Sensores de HRV y de etiqueta del TSS

- Señal: auditoría del 24/10. El LLM decía "> 10 % por debajo de su media" con 58 frente a 59 ms y etiquetaba como hrTSS un TSS de carrera. Ningún sensor lo veía.
- Lección: un % afirmado se comprueba con las cifras de la misma línea, pero el orden de las cifras no es fiable ("60 ms vs. ~67 ms de referencia"), así que el sensor acepta si alguna pareja lo justifica. Los IDs S18–S24 ya eran sensores fuera de línea; los nuevos son S25 y S26.
- Evidencia: sobre 891 respuestas guardadas, S25 da 4 avisos y los 4 son el error real; S26, 0. Batería 25/25. Detalle en CHANGELOG y `docs/Harness.md`.

### 25/10/2026 — Cobertura de la CLI

- Señal: `agent/main.py` al 34 % de cobertura; los comandos que más se usan (`/perfil`, `/plan`, `/carga`, `/modelo`) no tenían tests porque vivían dentro del bucle de `main()`.
- Lección: sacar el despacho a funciones (`_handle_cli_command`, `_switch_provider`) permite probar cada comando sin arrancar Kairos; `main()` se prueba entero con dobles de Supabase, Garmin y el LLM.
- Evidencia: 34 % → 89 % (criterio ≥ 70 %), 55 tests nuevos, comandos en vivo sin cambios.

### 25/10/2026 — Sensor de "mañana" y pasado mañana

- Señal: auditoría del 24/10 (sábado). A "¿qué hago pasado mañana?" el LLM respondía "Mañana 26/10 (lunes)"; ningún sensor lo veía.
- Lección: el error solo es comprobable cuando la palabra relativa va con un día o una fecha; entonces basta con la fecha de hoy. "Mañana" también es la parte del día ("por la mañana", "esta mañana") y se excluye.
- Evidencia: sobre 554 respuestas con fecha, 3 avisos y los 3 son el error real; 0 falsos positivos. Detalle en CHANGELOG y `docs/Harness.md`.

### 25/10/2026 — Sueño: una noche concreta y tendencias

- Señal: "¿cómo dormí el martes?" y "¿cómo he dormido últimamente?" iban al LLM libre, que pide los días a Garmin, suma y compara él (donde salen las cifras mal leídas y los días equivocados).
- Lección: buscar un dato (una noche) es determinista; interpretar una tendencia es del LLM, pero con las medias y diferencias ya calculadas (ruta mixta). Una tabla con dos periodos iguales no es un fallo hasta comprobarlo en la fuente: las dos últimas semanas daban lo mismo de verdad.
- Evidencia: rutas `sleep_night` y `sleep_trend`; cifras contra Garmin directo; una noche en 2–3 s, tendencia en 22 s con la lectura; batería 25/25.

### 26/10/2026 — S10: la marca de progresivo puede ir detrás del rango

- Señal: batería del 26/10 (ordenador 2), pregunta 9, 24/25: "3×2000 m Z3–Z4 (146–168 lpm, progresivo de Z3 a Z4)" marcado como fuera de zona.
- Lección: un sensor sobre texto libre no puede fijar el orden en que el LLM escribe las cosas; la marca y el rango van en la misma frase, pero en cualquier orden. El límite es la frase (o el paréntesis), no la posición.
- Evidencia: la misma batería, 25/25 con el sensor nuevo; tests nuevos para la marca detrás, la marca en otra frase (sigue fallando) y zonas que no cuadran (sigue fallando). Detalle en CHANGELOG.

### 26/10/2026 — Atleta sano, diabético o con otra patología

- Señal: el protocolo de diabetes con insulina iba en todas las llamadas al LLM, `/ayuda` hablaba de DT1 a todos, no había nada para DT2 y "DT 1" estaba guardado como lesión (el plan bajaba la dificultad por "lesión activa").
- Lección: lo que depende del atleta va en el contexto solo si aplica, no como regla condicional que decide el LLM. El riesgo de hipoglucemia lo marca la medicación, no el tipo de diabetes. Una patología y una lesión son cosas distintas: sin patologías el atleta es sano aunque esté lesionado.
- Evidencia: protocolo según patologías y medicación (`agent/diabetes.py`, `prompts/diabetes/`); formulario y migración de perfiles; 750 tests. En vivo (perfil sustituido solo en memoria, 4 preguntas de coaching): sano con periostitis, 0 menciones a la diabetes y la periostitis en todos los planes; DT2 con insulina, protocolo completo; DT2 con metformina, sin nota S16 ni carbohidratos preventivos. Detalle en CHANGELOG.
- Lección añadida: el título de un protocolo también ceba al LLM ("sin riesgo de hipoglucemia" hacía que lo repitiera en 3 de 4 respuestas). Aun quitándolo, el LLM nombra la hipoglucemia por su cuenta para tranquilizar (2 de 4): un prompt no borra lo que el modelo sabe. Aceptado por el usuario (26/10): no son avisos ni limitan el entrenamiento; no se añade sensor.

### 26/10/2026 — El atleta elige su modelo; la clave caducada se vuelve a pedir

- Señal: nota del usuario para productizar Kairos (el menú de modelo salía en cada arranque y solo con claves ya escritas en `.env`). Al revisarlo: un 401 en la capa de coaching cerraba la sesión, y una clave mala de NVIDIA no da 401 sino 403 "Authorization failed".
- Lección: lo que es del atleta (su modelo) va en su perfil; lo que es del ordenador (la clave), en `.env`. Un error de clave se detecta en un solo punto (la llamada al LLM) y se convierte en un tipo que todas las capas ya capturan. El código de error de un proveedor se comprueba en vivo antes de clasificarlo: el supuesto (401) era falso para NVIDIA.
- Evidencia: primera vez con menú y clave, siguientes sin menú, otro ordenador solo con la clave, clave caducada pedida y pregunta repetida (tests); en vivo, clave real "ok" y clave inventada "auth"; batería sin menú con `KAIROS_PROVIDER`. "45 herramientas" = 42 de Garmin + 3 internas de Kairos, ahora desglosado al arrancar.