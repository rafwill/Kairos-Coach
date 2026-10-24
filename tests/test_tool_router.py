"""
tests/test_tool_router.py
Router determinista: intenciones que se responden sin LLM (docs/Harness.md, G5).
"""

from agent.trainer_agent import ToolRouter


class TestToolRouter:
    def test_route_key_detects_critical_intents_when_enabled(self):
        router = ToolRouter(enabled=True)
        history: list[dict] = []
        profile = {"goals": {"primary": "running"}}

        assert router.route_key("Tengo algun plan?", history, profile) == "plan_status"
        assert router.route_key("Cuanto TSS llevo esta semana?", history, profile) == "week_tss"
        assert router.route_key("Que entrenamiento me propones para mañana?", history, profile) is None
        assert router.route_key("Cual es mi FC umbral?", history, profile) == "hr_threshold"
        assert router.route_key("Cuales son los TSS de esta semana?", history, profile) == "week_tss"
        assert router.route_key("Cual es mi ritmo umbral actual?", history, profile) == "running_threshold"
        assert router.route_key("Que TSS hice ayer?", history, profile) == "mcp_factual"
        assert router.route_key("Como estoy hoy para entrenar?", history, profile) == "daily_readiness"

    def test_exploration_questions_of_24_10_route_where_they_should(self):
        router = ToolRouter(enabled=True)
        profile = {"goals": {"primary": "running"}}
        # Antes: LLM libre sin la decisión del motor (decía "descanso" con 0 señales alteradas).
        assert router.route_key("¿Puedo hacer series hoy?", [], profile) == "daily_readiness"
        # Antes: tabla de un solo día; una comparación de semanas va al LLM (get_hrv_trend).
        assert router.route_key("Compara mi HRV de esta semana con la de la semana pasada", [], profile) is None
        # Antes: 15 llamadas a tools y "reformula tu pregunta".
        assert router.route_key("¿Cómo fue mi semana del 05/10 al 11/10?", [], profile) == "week_tss"
        # Antes: el LLM leía mal la serie y daba un día de otra semana.
        assert router.route_key("¿Qué día de esta semana tuve más carga?", [], profile) == "week_tss"
        # Las de la batería base no cambian.
        assert router.route_key("¿Cuál es mi tendencia de carga de las últimas 4 semanas?", [], profile) == "load_trend"
        assert router.route_key("¿Qué tipo de sesión me recomiendas para esta semana dado mi estado?", [], profile) == "daily_readiness"

    def test_last_named_activity_goes_to_activity_details(self):
        router = ToolRouter(enabled=True)
        # 24/10: el LLM dijo que la última bici era "anterior a agosto" (fue el 12/08).
        assert router.route_key("¿Qué tal fue mi última salida en bici?", [], {}) == "activity_details"
        assert router.route_key("¿Cómo fue mi última carrera?", [], {}) == "activity_details"
        # "rodaje" suelto en una pregunta de estado no es una actividad concreta.
        q = "Con mi estado actual (TSB + ATL + CTL), ¿recomiendas calidad, rodaje suave o descanso hoy? Justifica con datos"
        assert router.route_key(q, [], {}) == "daily_readiness"

    def test_activity_sport_filter_knows_road_biking(self):
        import agent.trainer_agent as ta

        assert ta._activity_matches_sport({"activityType": {"typeKey": "road_biking"}}, "cycling")
        assert ta._activity_matches_sport({"activityType": {"typeKey": "gravel_cycling"}}, "cycling")
        assert not ta._activity_matches_sport({"activityType": {"typeKey": "trail_running"}}, "running")
        assert ta._activity_matches_sport({"activityType": {"typeKey": "trail_running"}}, "trail")

    def test_route_key_returns_none_when_disabled(self):
        router = ToolRouter(enabled=False)
        history: list[dict] = []
        profile = {"goals": {"primary": "running"}}

        assert router.route_key("Tengo algun plan?", history, profile) is None
        assert router.route_key("Cuanto TSS llevo esta semana?", history, profile) is None
        assert router.route_key("Como estoy hoy para entrenar?", history, profile) is None
