"""Servidor MCP de Garmin Connect usado por Kairos (solo lectura, transporte stdio).

Paquete independiente del host: no importa nada de `agent/`. Cualquier cliente MCP puede usarlo
(Kairos, el Inspector MCP, tools/healthcheck_mcp.py). Arranque: `python -m garmin_mcp`.
"""
