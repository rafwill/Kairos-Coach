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

    def test_route_key_returns_none_when_disabled(self):
        router = ToolRouter(enabled=False)
        history: list[dict] = []
        profile = {"goals": {"primary": "running"}}

        assert router.route_key("Tengo algun plan?", history, profile) is None
        assert router.route_key("Cuanto TSS llevo esta semana?", history, profile) is None
        assert router.route_key("Como estoy hoy para entrenar?", history, profile) is None
