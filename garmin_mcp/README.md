# garmin_mcp — servidor MCP de Garmin Connect

Servidor [MCP](https://modelcontextprotocol.io) que expone **42 tools de solo lectura** sobre los datos de Garmin Connect: actividades, salud diaria, sueño, HRV, rendimiento, zonas de FC y récords. Es el **servidor** de la arquitectura MCP de Kairos: Kairos (`agent/`) es el host y `agent/mcp/` su cliente (ver [`docs/arquitectura.md`](../docs/arquitectura.md)).

Es un paquete independiente: no importa nada de `agent/` (lo comprueba un test), así que cualquier cliente MCP puede usarlo.

## Arranque

```powershell
.venv\Scripts\python.exe -m garmin_mcp          # transporte stdio
tools\garmin-mcp-frozen.cmd                      # lanzador que usa Kairos (tools/garmin-mcp-frozen.sh en Unix)
```

Kairos lo arranca solo como subproceso. Para usarlo desde otro cliente MCP (por ejemplo, Claude Desktop), basta con apuntar a ese comando:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "C:\\ruta\\al\\repo\\.venv\\Scripts\\python.exe",
      "args": ["-m", "garmin_mcp"],
      "cwd": "C:\\ruta\\al\\repo",
      "env": { "GARMIN_EMAIL": "tu_email@ejemplo.com", "GARMIN_PASSWORD": "tu_contraseña" }
    }
  }
}
```

## Configuración

| Variable | Uso |
| --- | --- |
| `GARMIN_EMAIL`, `GARMIN_PASSWORD` | Credenciales de Garmin Connect (obligatorias). Kairos se las pasa al arrancarlo. |
| `GARMINTOKENS` | Opcional: carpeta de tokens. Por defecto `~/.garminconnect/kairos/<hash del email>`. |

- **Login:** reutiliza los tokens guardados y solo entra con contraseña si faltan o Garmin los rechaza (por ejemplo, tras cambiar la contraseña), y entonces los vuelve a guardar. Así se evitan los bloqueos 429 de Garmin por logins repetidos.
- **Solo lectura:** ninguna tool escribe en Garmin. Todas llevan las anotaciones MCP `readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true` y `openWorldHint=true`.
- **Fechas:** formato `AAAA-MM-DD`. Si se omite la fecha, se usa hoy; en los rangos, el fin por defecto es el inicio.
- **Dependencias:** `mcp` (FastMCP), `garminconnect` 0.3.2 y `pydantic`.

## Problemas frecuentes

- **Garmin devuelve 429 o 403.** Bloqueo temporal de Garmin, normalmente por demasiados logins seguidos. Con los tokens guardados ya no debería pasar, porque el servidor solo hace login con contraseña si faltan o no valen. Si aparece: no reintentar en bucle, porque Garmin alarga el bloqueo (de la cuenta o de la IP); esperar al menos 15 minutos antes de volver a arrancar Kairos o lanzar la batería o el healthcheck; y no lanzar trazas largas hasta que un arranque normal funcione.
- **Tokens rechazados tras cambiar la contraseña de Garmin.** No hace falta hacer nada: el servidor borra los tokens guardados y entra con la contraseña.
- **`CERTIFICATE_VERIFY_FAILED` detrás de un proxy corporativo** (por ejemplo, Zscaler). El proxy sustituye el certificado de Garmin por el suyo y Python no lo reconoce, así que fallan todas las tools sin llegar a Garmin. Solución: un fichero con los certificados de Python (`certifi`) más la raíz del proxy, exportada del almacén de Windows, y en el `.env` `REQUESTS_CA_BUNDLE` y `SSL_CERT_FILE` apuntando a él. Kairos pasa su entorno al servidor. Si existe `zscaler-ca.pem` en la raíz del repo, Kairos también se lo pasa al servidor.

## Mantenimiento

| Qué | Comando | Cuándo |
| --- | --- | --- |
| Contrato con `garminconnect` (sin red) | `make test` (`test_every_tool_calls_garminconnect_with_its_real_signature`) | Siempre; en la CI |
| Guía de estilo MCP (apiaddicts) | `make lint-mcp` | Al añadir o cambiar tools |
| Salud de las tools contra Garmin | `make health-mcp` | Al actualizar `garminconnect` o si algo falla |
| Este catálogo | `python tools/gen_mcp_docs.py` | Al añadir o cambiar tools (un test falla si está desfasado) |

## Tools que no son un método de `garminconnect`

La mayoría de las tools llaman a un método de la librería y devuelven su respuesta, a veces compactada. Estas dos consultan directamente un endpoint de Garmin Connect (`connectapi`) y devuelven un formato propio:

- **`get_heart_rate_zones`**: la librería no tiene método para las zonas configuradas. Consulta `/biometric-service/heartRateZones` y devuelve `{"sports": [...]}` con un elemento por deporte configurado (`default`, `running`, `cycling`…): `sport`, `method` (`HR_RESERVE`, `LACTATE_THRESHOLD` o `PERCENT_MAX_HR`), `resting_hr`, `max_hr`, `lactate_threshold_hr` y `zones`, con las cinco zonas como `{zone, min_bpm, max_bpm}`. Garmin solo da el suelo de cada zona; el techo es el suelo de la siguiente menos 1, y el de la Z5, la FC máxima, así que las zonas quedan cerradas y sin huecos. Se omiten los deportes sin los cinco suelos. Kairos la llama al arrancar y guarda el resultado en el perfil (ver [`docs/arquitectura.md`](../docs/arquitectura.md#zonas-de-fc)).
- **`get_vo2max_trend`**: pide el rango en una sola llamada a `maxmet/daily/{inicio}/{fin}` y devuelve una serie plana (`date`, `running`, `cycling`) con los días que tienen estimación.

Al añadir una tool: definirla en `server.py` con `_tool(nombre, título, descripción)` y parámetros descritos con `Annotated[..., Field(description=...)]`, añadirla a una categoría en `tools/gen_mcp_docs.py`, regenerar este catálogo y, si Kairos debe usarla, añadirla en el host al catálogo esencial (`GARMIN_ESSENTIAL_TOOLS` en `agent/mcp/client.py`) y a los contratos de argumentos (`TOOL_CONTRACTS_V1` en `agent/mcp/contracts.py`). Los tests del catálogo fallan si falta una de las dos o si cambia el número de tools (`test_essentials_catalog_counts_and_uniqueness`, `test_contract_registry_covers_all_essential_tools`). Si cambia el lanzador, revisar `tools/garmin-mcp-frozen.cmd` y `.sh`.

<!-- BEGIN: catálogo generado por tools/gen_mcp_docs.py (no editar a mano) -->

## Catálogo de tools (42)

| Categoría | Tools |
|---|---|
| Actividades | [`get_activities`](#get-activities), [`get_activities_by_date`](#get-activities-by-date), [`get_activities_fordate`](#get-activities-fordate), [`get_activity`](#get-activity), [`get_activity_details`](#get-activity-details), [`get_activity_splits`](#get-activity-splits), [`get_activity_hr_in_timezones`](#get-activity-hr-in-timezones), [`get_activity_power_in_timezones`](#get-activity-power-in-timezones), [`get_activity_exercise_sets`](#get-activity-exercise-sets), [`get_training_effect`](#get-training-effect) |
| Salud diaria | [`get_stats`](#get-stats), [`get_heart_rates_summary`](#get-heart-rates-summary), [`get_rhr_day`](#get-rhr-day), [`get_stress_summary`](#get-stress-summary), [`get_all_day_stress`](#get-all-day-stress), [`get_all_day_events`](#get-all-day-events), [`get_body_battery`](#get-body-battery), [`get_respiration_summary`](#get-respiration-summary), [`get_spo2_data`](#get-spo2-data), [`get_daily_steps`](#get-daily-steps), [`get_hydration_data`](#get-hydration-data), [`get_body_composition`](#get-body-composition) |
| Sueño y recuperación | [`get_sleep_data`](#get-sleep-data), [`get_sleep_summary`](#get-sleep-summary), [`get_hrv_data`](#get-hrv-data), [`get_hrv_trend`](#get-hrv-trend), [`get_training_readiness`](#get-training-readiness), [`get_morning_training_readiness`](#get-morning-training-readiness) |
| Rendimiento | [`get_training_status`](#get-training-status), [`get_training_load_trend`](#get-training-load-trend), [`get_vo2max_trend`](#get-vo2max-trend), [`get_endurance_score`](#get-endurance-score), [`get_fitnessage_data`](#get-fitnessage-data), [`get_lactate_threshold`](#get-lactate-threshold), [`get_heart_rate_zones`](#get-heart-rate-zones), [`get_cycling_ftp`](#get-cycling-ftp), [`get_race_predictions`](#get-race-predictions), [`get_personal_record`](#get-personal-record) |
| Resúmenes semanales | [`get_weekly_steps`](#get-weekly-steps), [`get_weekly_intensity_minutes`](#get-weekly-intensity-minutes), [`get_weekly_stress`](#get-weekly-stress) |
| Perfil | [`get_user_profile`](#get-user-profile) |

### Actividades

<a id="get-activities"></a>
#### `get_activities` — Últimas actividades

Lista de actividades más recientes, de la más nueva a la más antigua (resumen de cada una: tipo, fecha, duración, distancia, FC). Para un rango de fechas usa get_activities_by_date.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start` | integer | `0` | Posición desde la que empezar (0 = la más reciente). |
| `limit` | integer | `50` | Número máximo de actividades a devolver. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-activities-by-date"></a>
#### `get_activities_by_date` — Actividades de un rango de fechas

Actividades entre dos fechas, paginadas (count, page, has_more, activities). Es la forma recomendada de localizar actividades por fecha.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start_date` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (incluido). |
| `end_date` | string (opcional) | — | Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio. |
| `startdate` | string (opcional) | — | Alias de start_date (compatibilidad). |
| `enddate` | string (opcional) | — | Alias de end_date (compatibilidad). |
| `page` | integer | `0` | Página a devolver, empezando en 0. |
| `page_size` | integer | `100` | Actividades por página. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-activities-fordate"></a>
#### `get_activities_fordate` — Actividades de un día

Actividades de un día concreto (con date) o de un rango (con startdate/enddate).

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |
| `startdate` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (si no se da date). |
| `enddate` | string (opcional) | — | Fin del rango, AAAA-MM-DD (si no se da date). |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-activity"></a>
#### `get_activity` — Resumen de una actividad

Resumen completo de una actividad: duración, distancia, ritmo, FC, desnivel, potencia, calorías y Training Effect (las métricas vienen en summaryDTO).

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `activity_id` | integer | obligatorio | ID de la actividad en Garmin (activityId), p. ej. de get_activities_by_date. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-activity-details"></a>
#### `get_activity_details` — Detalle segundo a segundo

Series temporales de una actividad (velocidad, FC, altitud, distancia, potencia…) a lo largo de la sesión. Respuesta grande: úsala solo si necesitas el detalle por tramos.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `activity_id` | integer | obligatorio | ID de la actividad en Garmin (activityId), p. ej. de get_activities_by_date. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-activity-splits"></a>
#### `get_activity_splits` — Parciales (laps) de una actividad

Parciales o vueltas de una actividad: distancia, tiempo, ritmo, FC y desnivel de cada lap.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `activity_id` | integer | obligatorio | ID de la actividad en Garmin (activityId), p. ej. de get_activities_by_date. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-activity-hr-in-timezones"></a>
#### `get_activity_hr_in_timezones` — Tiempo por zonas de FC

Tiempo pasado en cada zona de frecuencia cardiaca durante una actividad, con los límites de zona de Garmin.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `activity_id` | integer | obligatorio | ID de la actividad en Garmin (activityId), p. ej. de get_activities_by_date. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-activity-power-in-timezones"></a>
#### `get_activity_power_in_timezones` — Tiempo por zonas de potencia

Tiempo pasado en cada zona de potencia durante una actividad (solo con potenciómetro).

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `activity_id` | integer | obligatorio | ID de la actividad en Garmin (activityId), p. ej. de get_activities_by_date. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-activity-exercise-sets"></a>
#### `get_activity_exercise_sets` — Series de una sesión de fuerza

Ejercicios, series, repeticiones y pesos registrados en una actividad de fuerza.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `activity_id` | integer | obligatorio | ID de la actividad en Garmin (activityId), p. ej. de get_activities_by_date. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-training-effect"></a>
#### `get_training_effect` — Training Effect de una actividad

Training Effect aeróbico y anaeróbico de una actividad (0–5) y su etiqueta (AEROBIC_BASE, TEMPO, VO2MAX…).

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `activity_id` | integer (opcional) | — | ID de la actividad en Garmin (activityId). |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

### Salud diaria

<a id="get-stats"></a>
#### `get_stats` — Resumen diario

Resumen del día: pasos, calorías, FC en reposo, estrés medio, minutos de intensidad y Body Battery.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-heart-rates-summary"></a>
#### `get_heart_rates_summary` — Resumen de FC del día

FC en reposo, mínima, máxima y media del día.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-rhr-day"></a>
#### `get_rhr_day` — FC en reposo

Frecuencia cardiaca en reposo del día.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-stress-summary"></a>
#### `get_stress_summary` — Estrés del día

Estrés del día según Garmin (media, máximo y reparto por niveles).

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-all-day-stress"></a>
#### `get_all_day_stress` — Estrés a lo largo del día

Serie de estrés y Body Battery a lo largo de todo el día.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-all-day-events"></a>
#### `get_all_day_events` — Eventos del día

Eventos registrados durante el día (actividades, siestas, periodos de estrés o de recuperación).

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-body-battery"></a>
#### `get_body_battery` — Body Battery

Body Battery por día: nivel, cargado, drenado y serie a lo largo del día.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start_date` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (incluido). |
| `end_date` | string (opcional) | — | Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio. |
| `date` | string (opcional) | — | Alternativa a start_date/end_date para un solo día (AAAA-MM-DD). |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-respiration-summary"></a>
#### `get_respiration_summary` — Respiración del día

Frecuencia respiratoria del día y de la noche (media, mínima y máxima).

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-spo2-data"></a>
#### `get_spo2_data` — SpO2

Saturación de oxígeno en sangre (SpO2) del día y de la noche.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-daily-steps"></a>
#### `get_daily_steps` — Pasos del día

Pasos del día y objetivo de pasos.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-hydration-data"></a>
#### `get_hydration_data` — Hidratación

Hidratación registrada en el día (consumo y objetivo).

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-body-composition"></a>
#### `get_body_composition` — Composición corporal

Peso y composición corporal (grasa, masa muscular, IMC) registrados en el rango.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start_date` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (incluido). |
| `end_date` | string (opcional) | — | Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio. |
| `date` | string (opcional) | — | Alternativa a start_date/end_date para un solo día (AAAA-MM-DD). |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

### Sueño y recuperación

<a id="get-sleep-data"></a>
#### `get_sleep_data` — Sueño (completo)

Datos completos del sueño de la noche que termina en la fecha: fases, puntuación, respiración y SpO2. Para un resumen compacto usa get_sleep_summary.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-sleep-summary"></a>
#### `get_sleep_summary` — Sueño (resumen)

Resumen del sueño de la noche que termina en la fecha: duración total, puntuación y segundos en sueño profundo, ligero, REM y despierto.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-hrv-data"></a>
#### `get_hrv_data` — HRV de la noche

Variabilidad de la frecuencia cardiaca (HRV) de la noche que termina en la fecha: media nocturna, media semanal, estado (BALANCED, UNBALANCED…) y línea base.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-hrv-trend"></a>
#### `get_hrv_trend` — Tendencia de HRV

HRV nocturna día a día en un rango (máximo 90 días).

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start_date` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (incluido). |
| `end_date` | string (opcional) | — | Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio. |
| `date` | string (opcional) | — | Alternativa a start_date/end_date para un solo día (AAAA-MM-DD). |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-training-readiness"></a>
#### `get_training_readiness` — Training Readiness

Disposición para entrenar de Garmin (0–100) y sus factores (sueño, recuperación, HRV, carga aguda). Indicador de Garmin: complementa, no sustituye, el estado de carga de Kairos.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-morning-training-readiness"></a>
#### `get_morning_training_readiness` — Training Readiness al despertar

Training Readiness calculado al despertar (el valor de la mañana, sin la evolución del día).

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

### Rendimiento

<a id="get-training-status"></a>
#### `get_training_status` — Estado de entrenamiento de Garmin

Estado de entrenamiento según Garmin (productivo, mantenimiento, sobrecarga…), VO2máx y carga aguda de Garmin. Es la valoración de Garmin, no el estado de carga de Kairos.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-training-load-trend"></a>
#### `get_training_load_trend` — Carga Garmin por día (no TSS)

Carga nativa de Garmin por día (suma de activityTrainingLoad de sus actividades). NO es TSS y no sirve para CTL/ATL/TSB: está en otra escala (×0.07 a ×2 frente al TSS). Para el TSS y la serie CTL/ATL/TSB de Kairos usa kairos_load_trends. Si la citas, llámala 'Carga Garmin'.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start_date` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (incluido). |
| `end_date` | string (opcional) | — | Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio. |
| `date` | string (opcional) | — | Alternativa a start_date/end_date para un solo día (AAAA-MM-DD). |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-vo2max-trend"></a>
#### `get_vo2max_trend` — Tendencia de VO2máx

VO2máx estimado por Garmin (carrera y ciclismo) en un rango de fechas (máx. 90 días): una fila por cada día con estimación nueva (fecha, running, cycling). Garmin no lo recalcula a diario.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start_date` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (incluido). |
| `end_date` | string (opcional) | — | Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio. |
| `date` | string (opcional) | — | Alternativa a start_date/end_date para un solo día (AAAA-MM-DD). |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-endurance-score"></a>
#### `get_endurance_score` — Endurance Score

Puntuación de resistencia de Garmin (Endurance Score) por día en el rango, y su clasificación.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start_date` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (incluido). |
| `end_date` | string (opcional) | — | Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-fitnessage-data"></a>
#### `get_fitnessage_data` — Edad física

Edad física (Fitness Age) estimada por Garmin y los factores que la determinan.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `date` | string (opcional) | — | Día en formato AAAA-MM-DD. Por defecto, hoy. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-lactate-threshold"></a>
#### `get_lactate_threshold` — Umbral de lactato de Garmin

Umbral de lactato estimado por Garmin (velocidad y FC). Solo informativo: el ritmo y la FC umbral que usa Kairos para TSS y planes son los del perfil de Kairos; las zonas, las de get_heart_rate_zones.

Sin parámetros.

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-heart-rate-zones"></a>
#### `get_heart_rate_zones` — Zonas de FC de Garmin

Zonas de frecuencia cardiaca configuradas en Garmin Connect (las que usa el reloj), por deporte (default, running, cycling): método, FC de reposo, máxima y umbral, y las cinco zonas en lpm.

Sin parámetros.

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-cycling-ftp"></a>
#### `get_cycling_ftp` — FTP de ciclismo en Garmin

FTP de ciclismo registrado en Garmin (vatios).

Sin parámetros.

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-race-predictions"></a>
#### `get_race_predictions` — Predicciones de carrera

Tiempos previstos por Garmin para 5K, 10K, media maratón y maratón.

Sin parámetros.

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-personal-record"></a>
#### `get_personal_record` — Récords personales

Récords personales del usuario en Garmin (distancias de carrera, ciclismo, natación…), con su fecha.

Sin parámetros.

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

### Resúmenes semanales

<a id="get-weekly-steps"></a>
#### `get_weekly_steps` — Pasos por semana

Pasos acumulados por semana en el rango. Por defecto, las 4 últimas semanas.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start_date` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (incluido). |
| `end_date` | string (opcional) | — | Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-weekly-intensity-minutes"></a>
#### `get_weekly_intensity_minutes` — Minutos de intensidad por semana

Minutos de intensidad moderada y vigorosa por semana en el rango, con el objetivo semanal. Por defecto, las 4 últimas semanas.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start_date` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (incluido). |
| `end_date` | string (opcional) | — | Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<a id="get-weekly-stress"></a>
#### `get_weekly_stress` — Estrés por semana

Estrés medio por semana en el rango. Por defecto, las 4 últimas semanas.

| Parámetro | Tipo | Por defecto | Descripción |
|---|---|---|---|
| `start_date` | string (opcional) | — | Inicio del rango, AAAA-MM-DD (incluido). |
| `end_date` | string (opcional) | — | Fin del rango, AAAA-MM-DD (incluido). Por defecto, igual que el inicio. |

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

### Perfil

<a id="get-user-profile"></a>
#### `get_user_profile` — Perfil del usuario en Garmin

Perfil del usuario en Garmin Connect: sexo, peso, altura, fecha de nacimiento y datos fisiológicos básicos.

Sin parámetros.

*solo lectura, no destructiva, idempotente, consulta un servicio externo (Garmin)*

<!-- END: catálogo generado -->
