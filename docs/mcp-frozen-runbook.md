# Runbook MCP Frozen (Punto 9)

## Objetivo
Operar Kairos con backend MCP propio local (`frozen`) como única ruta de ejecución.

## Estado operativo
- Backend por defecto: `MCP_BACKEND=frozen`.
- Señal en runtime: `KAIROS_MCP_BACKEND_EFFECTIVE`.
- Catálogo Essentials: 45 tools (`42 Garmin + 3 Kairos internas`).
- Contratos versionados: `mcp-adapter-v1`.
- Ruta de invocación por defecto: single-path directo a MCP.
- Fallback de contingencia: desactivado por defecto (activar con `KAIROS_MCP_ENABLE_FALLBACK=true`).

## Comandos de operación

### Arranque normal (frozen)
Windows:
```powershell
$env:MCP_BACKEND='frozen'; .venv\Scripts\python.exe -m agent.main
```

Unix/macOS:
```bash
.venv/bin/python -m agent.main
```

## Verificación rápida (<= 5 min)
1. Confirmar backend efectivo en startup: `MCP backend efectivo: frozen`.
2. Confirmar tools cargadas: `44 herramientas disponibles`.
3. Ejecutar smoke sentinela (4 preguntas):
   - `¿Cuál es mi tendencia de carga de las últimas 4 semanas?`
   - `¿Cuánto TSS hice esta semana?`
   - `¿Puedo entrenar fuerte mañana o necesito recuperar?`
   - `¿Cuáles son mis récords personales running?`

## Estrategia de resiliencia
- Camino principal: llamada directa MCP (sin rutas paralelas ni fallback implícito).
- Modo contingencia (opt-in):
  - Fast-path local en frozen para `get_training_load_trend` contra `load_metrics_daily`.
  - Caché local por usuario para tools críticas (clave: tool + args + versión de contrato).
  - Si falla llamada MCP y existe caché válida, se devuelve caché.

## Validaciones obligatorias antes de release
```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_mcp_client.py tests/test_main.py tests/test_trainer_agent.py
```

## Criterios de salud
- p95 frozen mejora >= 30% frente a baseline histórico previo a MCP propio.
- 0 regresiones en tests y smoke E2E sentinela.
- Drift-check de catálogo Essentials en CI.

## Incidentes 403 y 429 (diagnostico operativo)

Desde el 21/10/2026 el MCP reutiliza los tokens de Garmin en `~/.garminconnect/kairos/<hash del email>` (o `GARMINTOKENS`) y solo hace login real si faltan o no valen. Los 429/403 venían de hacer un login completo en cada arranque.

Si aun así aparecen `429` y `403` mezclados:

1. No reintentar en bucle: Garmin endurece el bloqueo (cuenta/IP) si se insiste.
2. Esperar al menos 15 min antes de volver a arrancar Kairos o lanzar la batería.
3. No lanzar trazas largas de análisis hasta que un arranque normal funcione.

La sonda de re-login que se usó para diagnosticarlo (`mcp_relogin_probe.py`) está archivada en `_archivo/tools/` (local, fuera de git).

## Mantenimiento
- Si cambia el catálogo o contrato de tools, actualizar:
  - `agent/mcp/client.py` (`GARMIN_ESSENTIAL_TOOLS`, `ALL_ESSENTIAL_TOOLS`).
  - `agent/mcp/contracts.py` (`TOOL_CONTRACTS_V1`, normalización y fallback).
  - `garmin_mcp/server.py` (definición de las tools; `make lint-mcp` y `make health-mcp` tras cambiarlas).
  - `tests/test_mcp_client.py` (drift-check y contrato).
- Si cambia política de resiliencia, alinear también:
  - `docs/arquitectura.md` (sección Servidor MCP propio).
  - `KAIROS_MCP_ENABLE_FALLBACK` en `.env.example`/runbooks si aplica.
- Si cambia launcher local, revisar:
  - `tools/garmin-mcp-frozen.cmd`
  - `tools/garmin-mcp-frozen.sh`
