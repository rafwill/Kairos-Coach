# Changelog

Todos los cambios relevantes de Kairos Coach se registran en este archivo.

## 2026-08-15

### Added
- Ruta determinista para consultas de ritmo umbral actual desde perfil persistido.
- Comando de perfil para FC: `/perfil fc <reposo> <max>`.
- Política de fecha efectiva para parámetros de carga (umbral, FTP, FC).

### Changed
- Cálculo de carga incremental: preserva histórico previo al último cambio de parámetros y aplica nuevos valores solo desde la fecha efectiva.
- Refresco de FTP: no actualiza fecha efectiva cuando el valor no cambia.

### Tests
- Nuevas pruebas para fecha efectiva de parámetros.
- Nuevas pruebas para comando de FC y política de refresco de FTP.
- Suite completa validada en verde.

### Notes
- Commit principal de implementación: 55af659.
- Estado de validación local al cierre: 277 tests passed.
