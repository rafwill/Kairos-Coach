# Changelog

Todos los cambios relevantes de Kairos Coach se registran en este archivo.

## 2026-08-15

### Added
- Ruta determinista para consultas de ritmo umbral actual desde perfil persistido.
- Comando de perfil para FC: `/perfil fc <reposo> <max>`.
- Política de fecha efectiva para parámetros de carga (umbral, FTP, FC).
- Checkpoint incremental de resumen de sesión por día (upsert), guardado tras cada respuesta del coach.
- Contrato estructurado de sesión (`structured_workout`) reforzado en prompts (incluye ejemplos válido/inválido).
- Análisis plan-vs-ejecutado por bloques del `structured_workout` con resumen de completados/parciales/omitidos.

### Changed
- Cálculo de carga incremental: preserva histórico previo al último cambio de parámetros y aplica nuevos valores solo desde la fecha efectiva.
- Refresco de FTP: no actualiza fecha efectiva cuando el valor no cambia.
- Cierre de sesión optimizado: se elimina el resumen final dependiente de LLM en salida y se usa checkpoint ligero local para evitar bloqueos por red/timeouts.
- Ajuste diario del plan: ahora muta el JSON estructurado (intensityClass/target/rango, duración y reps) y guarda trazabilidad del ajuste.

### Tests
- Nuevas pruebas para fecha efectiva de parámetros.
- Nuevas pruebas para comando de FC y política de refresco de FTP.
- Nuevas pruebas para persistencia diaria de resumen y checkpoint local de sesión.
- Nuevas pruebas para mutación de `structured_workout` en ajuste diario y feedback por bloques.
- Suite completa validada en verde.

### Notes
- Commits clave del día: 55af659 (base), b9ea941 (cierre rápido de sesión), 367d770 (cierre completo punto 38).
- Estado de validación local al cierre: 287 tests passed.
