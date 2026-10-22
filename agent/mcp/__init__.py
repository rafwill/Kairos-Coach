"""Lado cliente del MCP dentro del host (Kairos).

- `client`: arranca el servidor MCP (`garmin_mcp`) por stdio, abre la sesión y llama a las tools.
- `contracts`: contratos de argumentos por tool, normalización y caché de contingencia.

El servidor vive en el paquete `garmin_mcp/`, independiente de `agent/`.
"""
