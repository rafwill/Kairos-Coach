# Kairos en la web

Kairos online para un grupo pequeño de atletas (como mucho 5), consultable desde el móvil o el navegador. Plan acordado el 06/11/2026; se construye por fases en ramas cortas (`web/fase-N-…`) que se integran en `main`. La CLI sigue funcionando: la web y la CLI comparten `agent/` y `garmin_mcp/`.

## Decisiones

| Tema | Decisión | Por qué |
|---|---|---|
| Usuarios | Grupo reducido, como mucho 5, solo por invitación | Lo pidió el usuario (06/11) |
| Arquitectura | Monolito modular en contenedores, no microservicios | Un proyecto de una persona; la única separación real ya existe (host y servidor MCP) |
| Servidor | Máquina virtual Oracle Cloud Always Free (ARM A1) con Docker Compose | Gratis y con control total (decisión del usuario): disco persistente para los tokens de Garmin y procesos que no se duermen. Plan B: Hetzner (~4 €/mes) con el mismo Compose |
| API | FastAPI, que reutiliza `agent/` tal cual | Python asíncrono como Kairos; chat en streaming (SSE) |
| Garmin | El servidor MCP de hoy, un subproceso por atleta activo (stdio) | Ya funciona así; los tokens van por atleta (`~/.garminconnect/kairos/<hash>`) |
| Datos y cuentas | Supabase de hoy: Auth solo por invitación, Postgres con RLS | Ya está online y con RLS por usuario |
| Frontal | React + Vite + TypeScript + Tailwind + shadcn/ui, PWA instalable; en Cloudflare Pages | Buena UX sin diseñar desde cero; se instala en el móvil; gratis |
| Interfaz | Menú con pantallas y formularios en lugar de comandos | Lo pidió el usuario (06/11) |
| Primera pantalla | **Hoy** | Prioridad del usuario (06/11) |
| LLM | Cada atleta con su propia clave (Gemini gratis: su cupo es suyo) | Escala sin coste para el administrador |
| Tareas nocturnas | Actúan como el atleta con su refresh token, nunca con `service_role` | El RLS sigue funcionando también sin el atleta delante |

**Pendiente de confirmar por el usuario**: las claves de LLM, la contraseña de Garmin (solo para el primer login) y el refresh token de cada atleta se guardan **cifrados en Supabase Vault**. Cambia la regla actual («la clave solo en `.env`», CLAUDE.md), que pensaba en un solo atleta en su ordenador.

## Arquitectura

```
 Móvil / navegador (PWA)          React + Vite + TS + Tailwind + shadcn/ui   ← Cloudflare Pages
        │ HTTPS · JWT de Supabase
        ▼
 Máquina virtual Oracle (ARM A1, Ubuntu), Docker Compose:
   ├─ caddy       HTTPS automático (o Cloudflare Tunnel, sin puertos abiertos)
   ├─ api         FastAPI + agent/: una sesión de TrainerAgent por atleta, con caducidad
   │               └─ python -m garmin_mcp por atleta activo (stdio)
   └─ scheduler   por atleta, al despertar: sincronizar Garmin, carga y el «Hoy» listo
        │
        ▼
 Supabase: Auth (invitación) · Postgres con RLS · Vault (secretos cifrados)
```

### Pantallas

| Menú | Contenido | Viene de |
|---|---|---|
| **Hoy** (inicio) | Recuperación (HRV, Body Battery al despertar, sueño), forma (CTL, ATL, TSB y zona), sesión de hoy, decisión del motor, «Tu entrenador», tiempo, bici opcional | Arranque (`StartupMixin`) |
| **Plan** | Calendario por semanas y detalle de cada sesión; asistente «Crear plan»; eventos; replanificar y ajustar | `/plan …` |
| **Chat** | Conversación con respuesta en streaming | `TrainerAgent.chat` |
| **Carga** | CTL, ATL, TSB y semanas | Rutas de carga |
| **Perfil** | Umbrales, zonas, lugar, objetivo | `/perfil …` |
| **Ajustes** | Conectar Garmin, proveedor de IA y clave | `/proveedor`, login |

Las preguntas de `/plan crear` y `/proveedor` pasan a un **formulario declarativo** en `agent/` (campos, opciones, valores por defecto, validación): la CLI lo pregunta en la terminal y la web lo pinta. Así no se duplica la lógica.

## Fases

| Fase | Qué | Resultado |
|---|---|---|
| **0** | Prueba de concepto: Garmin desde la máquina virtual de Oracle | Sabemos si el diseño vale (ver abajo) |
| **1** | El «Hoy» de principio a fin: `today_view()` en el núcleo (datos JSON; el markdown de la CLI sale de ahí), `GET /api/today` con JWT, login y pantalla Hoy (PWA), despliegue | Tu «Hoy» en el móvil |
| **2** | Scheduler (el «Hoy» preparado al despertar), conectar Garmin desde la web, secretos en Vault | Abrir la app es instantáneo |
| **3** | Plan: calendario, eventos, asistente «Crear plan»; crear y replanificar como trabajos en segundo plano con progreso | Planes desde el móvil |
| **4** | Chat en streaming, Carga, Perfil, Ajustes; invitación a los demás atletas | Grupo en marcha |

## Fase 0: prueba de concepto de Garmin en la nube

**Riesgo que mide**: `garminconnect` usa la API no oficial con usuario y contraseña. Desde casa ya vimos 429 por logins repetidos; desde la IP de un centro de datos, Garmin puede bloquear o pedir MFA.

**Cómo**: `tools/poc_garmin_cloud.py` hace cada 3 h (cron) las 7 llamadas del arranque de Kairos con el servidor MCP de verdad y anota cada pasada (estado de cada llamada, tiempos, si los tokens se reutilizaron o hubo un login nuevo). `--informe` da el veredicto.

**Criterio fijado antes de ver resultados**: **VALE** si en al menos 72 h y 20 pasadas, el 95 % o más no tiene ningún fallo (ni 429, ni autenticación, ni error de la tool) y, después del primer login, no vuelve a hacer falta la contraseña (los tokens se reutilizan; que se renueven solos es normal). Si no, **NO VALE**.

**Plan B si no vale**: Garmin se consulta desde casa. Un agente pequeño en el ordenador del atleta (el `garmin_mcp` de hoy) sincroniza sus datos con Supabase, y la web solo lee de Supabase. La API oficial de Garmin, aplazada en `docs/proximos_pasos.md`, sería la otra salida.

### Paso a paso en Oracle

1. **Cuenta**: en cloud.oracle.com, «Sign up» con el nivel Always Free. Pide tarjeta, pero no cobra. **Región de origen: Madrid** (`eu-madrid-1`); no se puede cambiar después, y Fráncfort suele quedarse sin máquinas ARM libres.
2. **Máquina virtual**: Compute → Instances → Create instance.
   - Imagen: Canonical Ubuntu 24.04 (aarch64).
   - Shape: Ampere `VM.Standard.A1.Flex`, 2 OCPU y 12 GB (cabe en el gratuito y deja margen).
   - Red: la VCN que propone, con IP pública.
   - SSH: sube tu clave pública (`~/.ssh/id_ed25519.pub`; si no tienes, `ssh-keygen -t ed25519`).
   - Si dice «Out of capacity», reintenta más tarde o en otro dominio de disponibilidad.
3. **Entrar**: `ssh ubuntu@<IP pública>`.
4. **Preparar**:
   ```bash
   sudo timedatectl set-timezone Europe/Madrid
   sudo apt update && sudo apt install -y python3-venv git
   git clone https://github.com/rafwill/Kairos-Coach.git && cd Kairos-Coach
   git switch web/fase-0-poc-garmin
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e . --no-deps
   printf 'GARMIN_EMAIL=...\nGARMIN_PASSWORD=...\n' > .env && chmod 600 .env   # solo Garmin: nada de Supabase ni LLM
   ```
5. **Primer login** (interactivo; si Garmin pide código MFA, lo escribes): `.venv/bin/python tools/poc_garmin_cloud.py --primer-login`
6. **Primera pasada a mano**: `.venv/bin/python tools/poc_garmin_cloud.py` (debe salir `✅✅✅✅✅✅✅`).
7. **Cron cada 3 h** (`crontab -e`):
   ```
   0 */3 * * * cd ~/Kairos-Coach && .venv/bin/python tools/poc_garmin_cloud.py >> .tmp/poc_garmin/cron.log 2>&1
   ```
8. **A los 3 días**: `.venv/bin/python tools/poc_garmin_cloud.py --informe`.

Durante la prueba, en casa Kairos sigue usando los mismos datos de Garmin con sus propios tokens; son dos sesiones del mismo usuario desde dos sitios, como el móvil y el reloj.
