# TODO - Kairos Coach Roadmap

## Estado actual (2026-10-21)

- Carga calculada como TrainingPeaks y validada contra el export de TP del atleta (01/02–21/10/2026):
  - PMC CTL 42 / ATL 7, Forma = CTL de ayer − ATL de ayer.
  - Running MAPE 6.3 % · trail 1.6 % · bici con potencia 1.6 % · fuerza ≈ 1.00.
  - Serie frente a TP: diferencia media CTL 2.1 · ATL 2.7 · TSB 3.0.
- Un TSS por actividad en `activity_tss`, línea base desde el 22/02/2026, sin recálculo hacia atrás.
- Tests: 564 passed, 6 skipped.
- Detalle técnico: `CLAUDE.md`. Bitácora: `docs/proximos_pasos.md`.

## Limitaciones conocidas (aceptadas)

1. Caminar/senderismo no cuadra con TP por diseño (modelo intermedio entre rTSS y hrTSS de TP).
2. Maratón de Madrid (26/04): −23 % frente a TP por una anomalía de TP en esa sesión.
3. Short reps detector con cobertura parcial; caso 24027714450 (tramo sostenido corto embebido) sin detectar.
4. Hiking con n bajo; RPE de fuerza sin capturar; elíptica sin FC máxima real.

## Pendiente real para próximas sesiones

### Prioridad alta

1. Trail rápido (< 6:00/km con rTSS): validar cuando haya más salidas (hoy n=1, +14 %).
2. Series cortas de running (+11 % a +20 % frente a TP): hipótesis antes de tocar umbrales (p. ej. relación FC-velocidad con retraso fisiológico).
3. Historiales del perfil al día: si cambia el umbral de FC en TP, añadir la entrada en `hr_threshold_tp_history` (falta un comando como `/perfil umbral`).

### Prioridad media

0. MCP: arreglar las 8 tools que fallan siempre (get_daily_steps, get_training_status, get_vo2max_trend, get_endurance_score, get_fitnessage_data, get_weekly_steps, get_weekly_stress, get_weekly_intensity_minutes) y revisar `get_activities` con el Inspector MCP.
1. Bici sin potencia de más de 2 h: la tabla hrTSS no tiene datos de bici por encima del 85 % del umbral.
2. Revalidación periódica con un export nuevo de TP (prueba sin escribir, criterio fijado antes, solo filas esperadas).
3. Capturar RPE de fuerza post-sesión (persistencia por activity_id).
4. Git hooks locales (pre-commit con tests).

## Referencia de documentos vigentes

- CLAUDE.md
- docs/arquitectura.md
- docs/Harness.md
- docs/proximos_pasos.md
- CHANGELOG.md
- README.md
