# Kairos en la web

Kairos online para un grupo pequeño de atletas (como mucho 5), consultable desde el móvil o el navegador. Plan acordado el 06/11/2026; se construye por fases en ramas cortas (`web/fase-N-…`) que se integran en `main`. La CLI sigue funcionando: la web y la CLI comparten `agent/` y `garmin_mcp/`.

## Decisiones

| Tema | Decisión | Por qué |
|---|---|---|
| Usuarios | Grupo reducido, como mucho 5, solo por invitación | Lo pidió el usuario (06/11) |
| Arquitectura | Monolito modular en contenedores, no microservicios | Un proyecto de una persona; la única separación real ya existe (host y servidor MCP) |
| Servidor | Una máquina virtual con Docker Compose (disco persistente para los tokens de Garmin, procesos que no se duermen). Proveedor por decidir tras la fase 0 (ver «Servidor») | Control total (decisión del usuario). Mismo Compose en cualquier proveedor: mudarse es copiar la configuración |
| API | FastAPI, que reutiliza `agent/` tal cual | Python asíncrono como Kairos; chat en streaming (SSE) |
| Garmin | El servidor MCP de hoy, un subproceso por atleta activo (stdio) | Ya funciona así; los tokens van por atleta (`~/.garminconnect/kairos/<hash>`) |
| Datos y cuentas | Supabase de hoy: Auth solo por invitación, Postgres con RLS | Ya está online y con RLS por usuario |
| Frontal | React + Vite + TypeScript + Tailwind + shadcn/ui, PWA instalable; en Cloudflare Pages | Buena UX sin diseñar desde cero; se instala en el móvil; gratis |
| Interfaz | Menú con pantallas y formularios en lugar de comandos | Lo pidió el usuario (06/11) |
| Primera pantalla | **Hoy** | Prioridad del usuario (06/11) |
| LLM | Cada atleta con su propia clave (Gemini gratis: su cupo es suyo) | Escala sin coste para el administrador |
| Tareas nocturnas | Actúan como el atleta con su refresh token, nunca con `service_role` | El RLS sigue funcionando también sin el atleta delante |

**Secretos (decidido y hecho el 06/11, en `main`)**: la clave de LLM y la contraseña de Garmin de cada atleta están **cifradas en Supabase Vault** y solo se leen con su sesión (`kairos_set_secret`/`kairos_get_secret`, `storage.get_secret`/`set_secret`). Vault es obligatorio: sin `.env` de secretos ni Fernet. El refresh token de cada atleta para las tareas nocturnas irá también a Vault (fase 2).

## Servidor

Con un solo atleta (pruebas y desarrollo, fases 0 a 4) Kairos necesita ~600-700 MB de RAM: API 300-400 MB, un servidor de Garmin ~120 MB, tareas, Caddy y Docker 150-200 MB. Con 5 atletas a la vez, ~1,2-1,5 GB. La CPU apenas trabaja (espera a Gemini y a Garmin) y bastan 10-15 GB de disco (los datos están en Supabase).

| Opción | Coste | Notas (06/11) |
|---|---|---|
| Oracle Always Free (región Madrid) | 0 € | **Sin capacidad** con cuenta gratuita: ni ARM A1 ni AMD E2.1.Micro; Madrid tiene un solo availability domain; los reintentos seguidos dan "Too many requests". Con **Pay As You Go** (sigue gratis dentro del Always Free; crear presupuesto con alerta) suele haber ARM |
| GCP `e2-micro` | 0 € (confirmar el coste de la IPv4 externa) | 1 GB, solo regiones de EE. UU.; 300 $ de prueba 90 días; pide tarjeta |
| Azure B1s | ~6,8 $/mes + disco e IP | El usuario la estaba creando (Spain Central, usuario `kairos`) con su cuenta de empresa: solo si es una suscripción de Visual Studio (desarrollo) o con permiso; para producción, cuenta personal |
| Clouding (Barcelona) | Plan 1 (½ vCore, 1 GB): 3 €/mes; plan 2 (1 vCore, 2 GB, 30 GB): 8 €/mes + IVA ≈ 9,70 € | Por horas (la prueba de 3 días con el plan 1, ~0,30 €); se amplía sin reinstalar |
| En casa + Cloudflare Tunnel | El equipo (Raspberry Pi, mini PC) | IP de casa (Garmin funciona); depende de la luz y la conexión. Plan B si la fase 0 no vale |

Para la fase 0 vale cualquiera: lo que mide (Garmin desde una IP de centro de datos) es igual en todas. Para producción con 5 atletas, ≥ 2 GB y cuenta personal.

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
| **0** | Prueba de concepto: Garmin desde una máquina virtual en la nube | Sabemos si el diseño vale (ver abajo) |
| **1** | El «Hoy» de principio a fin: `today_view()` en el núcleo (datos JSON; el markdown de la CLI sale de ahí), `GET /api/today` con JWT, login y pantalla Hoy (PWA), despliegue | Tu «Hoy» en el móvil |
| **2** | Scheduler (el «Hoy» preparado al despertar), conectar Garmin desde la web, secretos en Vault | Abrir la app es instantáneo |
| **3** | Plan: calendario, eventos, asistente «Crear plan»; crear y replanificar como trabajos en segundo plano con progreso | Planes desde el móvil |
| **4** | Chat en streaming, Carga, Perfil, Ajustes; invitación a los demás atletas | Grupo en marcha |

## Fase 0: prueba de concepto de Garmin en la nube

**Riesgo que mide**: `garminconnect` usa la API no oficial con usuario y contraseña. Desde casa ya vimos 429 por logins repetidos; desde la IP de un centro de datos, Garmin puede bloquear o pedir MFA.

**Cómo**: `tools/poc_garmin_cloud.py` hace cada 3 h (cron) las 7 llamadas del arranque de Kairos con el servidor MCP de verdad y anota cada pasada (estado de cada llamada, tiempos, si los tokens se reutilizaron o hubo un login nuevo). `--informe` da el veredicto.

**Criterio fijado antes de ver resultados**: **VALE** si en al menos 72 h y 20 pasadas, el 95 % o más no tiene ningún fallo (ni 429, ni autenticación, ni error de la tool) y, después del primer login, no vuelve a hacer falta la contraseña (los tokens se reutilizan; que se renueven solos es normal). Si no, **NO VALE**.

**Plan B si no vale**: Garmin se consulta desde casa. Un agente pequeño en el ordenador del atleta (el `garmin_mcp` de hoy) sincroniza sus datos con Supabase, y la web solo lee de Supabase. La API oficial de Garmin, aplazada en `docs/proximos_pasos.md`, sería la otra salida.

### Paso a paso

1. **Clave SSH** en el ordenador del usuario (PowerShell): `ssh-keygen -t ed25519`; la pública (`Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub`) se pega al crear la máquina. La del ordenador 1 existe desde el 06/11.
2. **Máquina virtual** (cualquier proveedor de la tabla «Servidor»): Ubuntu 24.04, 1 GB de RAM o más, IP pública, solo el puerto 22 abierto, la clave SSH. En Oracle: VCN creada con el asistente «Create VCN with Internet Connectivity» y subred pública (sin VCN no deja asignar IP pública); no fijar el fault domain. En Azure: disco Standard SSD y «Delete public IP and NIC when VM is deleted».
3. **Entrar**: `ssh <usuario>@<IP pública>` (`ubuntu` en Oracle y GCP; el que se elija en Azure, p. ej. `kairos`).
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
