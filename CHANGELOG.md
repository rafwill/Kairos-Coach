# Changelog

Todos los cambios relevantes de Kairos Coach se registran en este archivo.

## 2026-08-15

### Added
- Ruta determinista para consultas de ritmo umbral actual desde perfil persistido.
- Comando de perfil para FC: `/perfil fc <reposo> <max>`.
- Política de fecha efectiva para parámetros de carga (umbral, FTP, FC).
- Checkpoint incremental de resumen de sesión por día (upsert), guardado tras cada respuesta del coach.

### Changed
- Cálculo de carga incremental: preserva histórico previo al último cambio de parámetros y aplica nuevos valores solo desde la fecha efectiva.
- Refresco de FTP: no actualiza fecha efectiva cuando el valor no cambia.
- Cierre de sesión optimizado: se elimina el resumen final dependiente de LLM en salida y se usa checkpoint ligero local para evitar bloqueos por red/timeouts.

### Tests
- Nuevas pruebas para fecha efectiva de parámetros.
- Nuevas pruebas para comando de FC y política de refresco de FTP.
- Nuevas pruebas para persistencia diaria de resumen y checkpoint local de sesión.
- Suite completa validada en verde.

### Notes
- Commit principal de implementación: 55af659.
- Estado de validación local al cierre: 281 tests passed.
