# Supabase en Kairos: cuentas y seguridad por fila

Kairos guarda en Supabase los datos de cada atleta: perfil, historial de conversación, planes, TSS por actividad, serie de carga y bienestar. Este documento explica cómo se protegen esos datos para que cada usuario vea solo los suyos, y cómo se pone en marcha.

Ficheros de esta carpeta:

| Fichero | Qué es | Cuándo se ejecuta |
| --- | --- | --- |
| `schema.sql` | Tablas, índices, el vínculo con las cuentas y las funciones que usan las políticas | Al crear el proyecto y cada vez que cambie el esquema (es idempotente) |
| `rls.sql` | Activa la seguridad por fila y crea las políticas | Una vez, en la fase 2 (también idempotente) |
| `README.md` | Este documento | — |

Los dos `.sql` se ejecutan en **Supabase Dashboard → SQL Editor → New query → pegar → Run**.

**Estado del proyecto real:** fases 1 y 2 aplicadas el 24/10/2026 y comprobadas con `make check-rls` (0 filas visibles sin sesión en las 11 tablas) y con la batería (25/25). Un segundo usuario de `app_user` sigue sin vincular: verá sus datos cuando entre una vez con su cuenta.

## 1. El problema que resuelve

Kairos habla con Supabase usando dos datos del `.env`: la URL del proyecto y la **clave `anon`**. Esa clave no es secreta: va en el `.env` de cada instalación y cualquiera que la tenga puede hacer consultas.

Hasta octubre de 2026, las tablas no tenían ninguna protección propia y era Kairos quien filtraba por usuario en su código. Con la URL y la clave `anon`, cualquiera podía leer y modificar los datos de **todos** los usuarios, incluida la fila de `app_user` con el hash de la contraseña y la contraseña de Garmin cifrada. `tools/check_rls.py` lo mostraba así el 24/10/2026: 8 de las 11 tablas visibles sin iniciar sesión (2 usuarios, 245 días de carga, 224 actividades…).

## 2. Conceptos

**Supabase Auth.** Es el servicio de cuentas de Supabase. Cada usuario tiene una cuenta con email y contraseña (tabla interna `auth.users`). Al iniciar sesión, Supabase devuelve un **token de sesión** firmado que dice "esta petición es del usuario X". Kairos lo manda en cada consulta.

**Roles.** Supabase trata cada petición según quién la hace:
- `anon`: sin sesión, solo con la clave `anon`.
- `authenticated`: con un token de sesión válido.

**RLS (Row Level Security, seguridad por fila).** Es una función de PostgreSQL: con RLS activado en una tabla, una consulta **solo ve y modifica las filas que permita una política**. Sin ninguna política que lo permita, no se ve nada. El filtro lo aplica la propia base de datos, así que no depende de que el programa que consulta se porte bien.

**Política (policy).** Una regla escrita en SQL para una tabla. Tiene dos partes:
- `using (...)`: qué filas puedes **ver** (y modificar o borrar). Por ejemplo, "las filas cuyo `app_user_id` es el tuyo".
- `with check (...)`: qué filas puedes **escribir**. Impide crear o cambiar una fila a nombre de otro usuario.

`auth.uid()` es una función de Supabase que devuelve el id de la cuenta que hace la petición, o vacío si no hay sesión.

## 3. Cómo lo usa Kairos

```
Kairos ──(email + contraseña)──> Supabase Auth ──> token de sesión
   │                                                   │
   └──(consulta + token)──> PostgreSQL ──> políticas RLS: solo tus filas
```

- **Cuentas.** Cada usuario de Kairos tiene una cuenta de Supabase Auth con **su email y la misma contraseña que usa en Garmin Connect** (así sigue habiendo una sola contraseña). Supabase guarda solo un hash de ella.
- **Vínculo.** La tabla `app_user` gana la columna `auth_user_id`, que apunta a la cuenta de Auth. El resto de tablas siguen colgando de `app_user.id`, así que **no cambia ningún dato**.
- **Funciones** (en `schema.sql`):
  - `kairos_app_user_id()`: devuelve el `app_user.id` de quien tiene la sesión. La usan todas las políticas.
  - `kairos_claim_app_user()`: la llama Kairos al iniciar sesión. Vincula la cuenta de Auth con el `app_user` que ya existía **con el mismo email**, solo si ese email está **confirmado**. Así nadie puede quedarse con los datos de otro registrándose con su email.
- **Políticas** (en `rls.sql`):
  - `app_user`: ver y modificar solo tu fila; crear solo la tuya y con tu propio email como usuario; no se puede borrar.
  - Las 8 tablas con `app_user_id`: todo (ver, crear, modificar, borrar) solo sobre tus filas.
  - `training_plan_session` y `training_plan_version`: solo las de tus planes.
  - Sin sesión (rol `anon`): ninguna política, así que no se ve nada.
- **Sesión guardada.** Kairos guarda la sesión en `~/.kairos/supabase_session.json` (fuera del repo, como los tokens de Garmin en `~/.garminconnect/`) y la renueva sola: antes de cada consulta mira por la hora del reloj si el token caduca en menos de un minuto (desde el 26/10; tras suspender el equipo, el auto-refresco del cliente llegaba tarde y un "JWT expired" cerraba Kairos). Al arrancar no pide contraseña mientras la sesión siga válida. Las herramientas (`juez_bateria.py`, `rebuild_tss_baseline.py`, `check_rls.py`) usan la misma sesión. Se puede cambiar la ruta con `KAIROS_SESSION_FILE`.
- **Nada antes de entrar.** Kairos ya no lee la base de datos antes de iniciar sesión. La contraseña de Garmin sigue guardada cifrada en `app_user.credentials` (con la `ENCRYPTION_KEY` local) y se descifra después de entrar.

## 4. Puesta en marcha

Hay dos fases para no dejar a nadie fuera: primero el código nuevo, que funciona con RLS activado o no; después se activan las políticas.

**Requisito previo (ya comprobado el 24/10/2026):** en **Authentication → Sign In / Providers → Email**, el proveedor de email activado y **"Confirm email" activado**. Es lo que hace seguro el vínculo por email. Kairos lo puede consultar con `GET /auth/v1/settings` (`mailer_autoconfirm` debe ser `false`).

### Fase 1: código y esquema

1. Actualiza Kairos en cada ordenador (`git pull`).
2. Ejecuta **`schema.sql`** en el SQL Editor. Añade `app_user.auth_user_id` y las dos funciones, y deja de desactivar RLS. No cambia permisos ni datos.
3. **Cada usuario**, en cada ordenador, entra una vez en Kairos (`python -m agent.main`):
   1. Escribe su email y su contraseña de siempre. La primera vez aparece "Email o contraseña incorrectos (o la cuenta aún no existe)", porque aún no hay cuenta de Auth.
   2. Responde **sí** a "¿Es tu primer acceso con este email? Crear tu cuenta de Kairos" y repite la contraseña.
   3. Abre el **enlace del correo** que envía Supabase.
   4. Vuelve a entrar en Kairos con el mismo email y contraseña. Al entrar, la cuenta queda vinculada a sus datos de siempre y la sesión se guarda en ese ordenador.

   En los demás ordenadores ya no hace falta crear la cuenta: basta con entrar una vez con email y contraseña.
4. Comprueba el vínculo en el SQL Editor:
   ```sql
   select username, auth_user_id is not null as vinculado from app_user;
   ```
   Todos los usuarios que se vayan a seguir usando deben salir `vinculado = true`.

### Fase 2: activar las políticas

5. Ejecuta **`rls.sql`** en el SQL Editor.
6. Comprueba desde Kairos:
   ```bash
   make check-rls        # o: .venv/Scripts/python.exe tools/check_rls.py
   ```
   - Bloque "Sin sesión": las 11 tablas deben salir `cerrada` (0 filas).
   - Bloque "Con tu sesión": tu fila de `app_user` debe ser exactamente 1 y ver tus datos.
7. Entra en Kairos y haz una pregunta normal (por ejemplo, "¿cuánto TSS hice esta semana?"). Si quieres la prueba completa, lanza la batería (`tools/run_bateria.py` + `tools/juez_bateria.py`).

Un usuario que no se vinculó antes de la fase 2 **no pierde sus datos**: cuando entre por primera vez, `kairos_claim_app_user()` hace el vínculo igual (la función puede leer la tabla aunque RLS esté activo).

## 5. Día a día

| Situación | Qué hacer |
| --- | --- |
| Arrancar Kairos | Escribir el email. Con sesión guardada entra solo |
| Ordenador nuevo | Entrar una vez con email y contraseña. Si su `ENCRYPTION_KEY` es distinta, Kairos pide otra vez la contraseña de Garmin para cifrarla en ese ordenador |
| Usuario nuevo | Entrar con su email, aceptar crear la cuenta, confirmar el correo, volver a entrar. Kairos crea su `app_user` vinculado |
| Cambias la contraseña de Garmin | Kairos lo detecta al conectar con Garmin y te pide la nueva: la cambia también en Supabase Auth y en la copia cifrada |
| Olvidas la contraseña | Cámbiala en Garmin y, en el Dashboard, en **Authentication → Users → … → Send password recovery** o fija la nueva desde ahí; luego entra en Kairos con ella |
| Cerrar la sesión de un ordenador | Borrar `~/.kairos/supabase_session.json` |
| Herramientas (`juez`, `rebuild_tss_baseline`, `check_rls`) | Usan la sesión guardada por Kairos. Si dicen "Sin sesión… entra una vez en Kairos", eso |

## 6. Volver atrás

Si algo falla tras la fase 2 y hace falta volver a como estaba mientras se investiga, en el SQL Editor:

```sql
alter table app_user              disable row level security;
alter table user_profile          disable row level security;
alter table session_context       disable row level security;
alter table athlete_knowledge     disable row level security;
alter table ia_provider           disable row level security;
alter table training_plan         disable row level security;
alter table training_plan_session disable row level security;
alter table training_plan_version disable row level security;
alter table load_metrics_daily    disable row level security;
alter table activity_tss          disable row level security;
alter table wellness_daily        disable row level security;
```

Las políticas se quedan creadas pero sin efecto; para volver a activarlas basta con ejecutar `rls.sql` otra vez. Volver atrás deja los datos expuestos como antes: hazlo solo el tiempo necesario.

## 7. Problemas frecuentes

| Mensaje o síntoma | Causa | Solución |
| --- | --- | --- |
| "Falta ejecutar supabase/schema.sql en Supabase" | La función `kairos_claim_app_user` no existe | Fase 1, paso 2 |
| "Email sin confirmar: abre el enlace del correo de Supabase" | La cuenta existe pero no se ha confirmado | Abrir el enlace; si no llega, mirar spam o reenviarlo en **Authentication → Users** |
| Entras pero Kairos se comporta como un usuario nuevo | El email de la cuenta de Auth no coincide con el `username` de tu `app_user` | Comprobar en **Table Editor → app_user**; el vínculo es por email exacto (sin distinguir mayúsculas) |
| `check_rls` muestra tablas ABIERTAS después de la fase 2 | `rls.sql` no se ejecutó entero, o se ejecutó después un SQL antiguo que desactivaba RLS | Ejecutar `rls.sql` de nuevo. `schema.sql` ya no desactiva RLS |
| Una herramienta dice "Sin sesión de … en este ordenador" | No hay sesión guardada en ese ordenador | Entrar una vez en Kairos |
| Correos de confirmación que no llegan | El envío de correo de Supabase tiene un límite bajo por hora en el plan gratuito | Esperar, o configurar un SMTP propio en **Authentication → Emails** |

## 8. Preguntas

**¿Por qué no basta con que Kairos filtre por usuario?** Porque la clave `anon` es pública: cualquiera que la tenga puede saltarse Kairos y consultar la base de datos directamente. RLS pone el filtro en la base de datos.

**¿Y la clave `service_role`?** Esa sí es secreta y se salta RLS. Kairos no la usa ni debe estar nunca en un `.env` de usuario.

**¿Puedo ver los datos en el Dashboard?** Sí: el Table Editor del Dashboard usa permisos de administrador y no le afecta RLS.

**¿Se puede usar otra contraseña en Kairos distinta de la de Garmin?** Hoy no: se mantiene una sola contraseña, como antes. Separarlas es posible (Kairos guardaría las dos), pero cambia el flujo de entrada y se decidiría aparte.
