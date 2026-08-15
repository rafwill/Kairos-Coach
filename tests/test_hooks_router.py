import pytest

import agent.trainer_agent as ta
from agent.trainer_agent import HookManager, ToolRouter, TrainerAgent


class TestHookManager:
    @pytest.mark.asyncio
    async def test_emit_invokes_sync_and_async_handlers(self):
        manager = HookManager()
        seen = []

        def sync_handler(payload):
            seen.append(("sync", payload.get("value")))

        async def async_handler(payload):
            seen.append(("async", payload.get("value")))

        manager.register("before_message", sync_handler)
        manager.register("before_message", async_handler)

        await manager.before_message({"value": 7})

        assert ("sync", 7) in seen
        assert ("async", 7) in seen

    @pytest.mark.asyncio
    async def test_emit_ignores_handler_failures(self):
        manager = HookManager()
        seen = []

        def broken_handler(_payload):
            raise RuntimeError("boom")

        def ok_handler(payload):
            seen.append(payload.get("ok"))

        manager.register("on_error", broken_handler)
        manager.register("on_error", ok_handler)

        await manager.on_error({"ok": True})

        assert seen == [True]


class TestToolRouter:
    def test_route_key_detects_critical_intents_when_enabled(self):
        router = ToolRouter(enabled=True)
        history: list[dict] = []
        profile = {"goals": {"primary": "running"}}

        assert router.route_key("Tengo algun plan?", history, profile) == "plan_status"
        assert router.route_key("Cuanto TSS llevo esta semana?", history, profile) == "week_tss"
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


class TestChatHookEmission:
    @pytest.mark.asyncio
    async def test_chat_emits_before_and_after_message_hooks_for_deterministic_route(self, monkeypatch):
        # Evita persistencia real durante el test.
        monkeypatch.setattr(ta, "_save_history_entry", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(TrainerAgent, "_build_system_prompt", lambda self: "test prompt")

        agent = TrainerAgent.__new__(TrainerAgent)
        agent.provider = "test"
        agent.system_prompt = "test prompt"
        agent.user_profile = {"training_plan": {"active": True, "status": "active", "title": "Plan Base"}}
        agent.conversation_history = []
        agent.knowledge_chunks = []
        agent.knowledge_sources = []
        agent.hook_manager = HookManager()
        agent.tool_router = ToolRouter(enabled=True)

        events = []

        def on_before(payload):
            events.append(("before", payload.get("user_message")))

        def on_after(payload):
            events.append(("after", payload.get("route")))

        agent.hook_manager.register("before_message", on_before)
        agent.hook_manager.register("after_message", on_after)

        out = await TrainerAgent.chat(agent, "Tengo algun plan?")

        assert "Plan" in out or "plan" in out
        assert ("before", "Tengo algun plan?") in events
        assert ("after", "plan_status") in events
