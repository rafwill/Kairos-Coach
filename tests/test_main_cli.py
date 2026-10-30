"""
tests/test_main_cli.py
Comandos de la CLI de Kairos (agent/main.py): despacho, perfil, planes, carga, cambio de modelo, arranque
y el bucle de conversación de main(). Sin red ni Supabase: todo lo externo va con dobles.
"""

import asyncio
import contextlib
import logging
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from agent import main as m
from tests.agent_patch import patch_all


@pytest.fixture
def out(monkeypatch):
    """Consola que graba lo que imprime Kairos; devuelve una función que da el texto."""
    rec = Console(record=True, width=200, color_system=None)
    monkeypatch.setattr(m, "console", rec)
    return lambda: rec.export_text(clear=False)


class _Agent:
    def __init__(self):
        self.user_profile = {}
        self.total_prompt_tokens = 10
        self.total_completion_tokens = 5
        self.set_provider = MagicMock()

    def today_ia_tokens(self):
        return 100


# ─── Despacho de comandos ────────────────────────────────────────────────────

class TestHandleCliCommand:
    @pytest.mark.parametrize("entry, target", [
        ("/perfil", "_show_profile"), ("/profile", "_show_profile"), ("/menu", "_show_menu"),
        ("/ayuda", "_show_help"), ("/help", "_show_help"), ("/?", "_show_help"),
        ("/plan", "_show_plan_help"), ("/plan listar", "_show_training_plans"),
    ])
    def test_simple_commands_call_their_screen(self, entry, target):
        with patch.object(m, target) as screen:
            assert m._handle_cli_command(entry, _Agent()) is True
        screen.assert_called_once()

    def test_plan_create_goes_to_the_async_main_loop(self):
        # 28/10: /plan crear usa el motor de planes (asíncrono); lo atiende el bucle principal de main().
        assert m._handle_cli_command("/plan crear", _Agent()) is False

    def test_plan_view_and_activate_pass_the_id(self):
        agent = _Agent()
        with patch.object(m, "_show_training_plan") as view, patch.object(m, "_activate_training_plan_cli") as act:
            m._handle_cli_command("/plan ver abc-123", agent)
            m._handle_cli_command("/plan activar abc-123", agent)
        view.assert_called_once_with("abc-123")
        act.assert_called_once_with("abc-123", agent)

    def test_carga_passes_the_mode(self):
        agent = _Agent()
        with patch.object(m, "_show_load_trend_cli") as load:
            m._handle_cli_command("/carga meses", agent)
        load.assert_called_once_with(agent, "/carga meses")

    @pytest.mark.parametrize("entry, asks", [
        ("/perfil editar objetivo", ["_ask_goals"]), ("/perfil editar salud", ["_ask_health"]),
        ("/perfil editar", ["_ask_goals", "_ask_health"]),
    ])
    def test_profile_edits_save_and_reload(self, entry, asks, out):
        agent = _Agent()
        with patch.object(m, "_load_user_profile", return_value={"goals": {}}), \
             patch.object(m, "_save_user_profile") as save, \
             patch.object(m, "_ask_goals") as goals, patch.object(m, "_ask_health") as health:
            assert m._handle_cli_command(entry, agent) is True
        called = [name for name, mock in (("_ask_goals", goals), ("_ask_health", health)) if mock.called]
        assert called == asks
        save.assert_called_once()
        assert agent.user_profile == {"goals": {}}
        assert "✓" in out()

    def test_threshold_pace_updates_profile(self, out):
        profile = {}
        with patch.object(m, "_load_user_profile", return_value=profile), patch.object(m, "_save_user_profile") as save:
            m._handle_cli_command("/perfil umbral 4:00", _Agent())
        save.assert_called_once()
        assert profile["performance"]["running_threshold_pace"] == "4:00"
        assert "Umbral running actualizado a 4:00 min/km" in out()

    def test_hr_threshold_updates_both_references_not_the_pace(self, out):
        profile = {}
        with patch.object(m, "_load_user_profile", return_value=profile), patch.object(m, "_save_user_profile") as save:
            m._handle_cli_command("/perfil umbral fc 169", _Agent())
            m._handle_cli_command("/perfil umbral fc", _Agent())
            m._handle_cli_command("/perfil umbral fc alto", _Agent())
        save.assert_called_once()
        assert profile["performance"]["hr_threshold_tp_history"][-1]["bpm"] == 169
        assert profile["performance"]["hr_threshold_bpm"] == 169
        assert "running_threshold_pace" not in profile["performance"]
        assert "169 lpm" in out() and "Uso:" in out() and "✗" in out()

    def test_threshold_pace_usage_and_invalid_value_do_not_save(self, out):
        with patch.object(m, "_load_user_profile", return_value={}), patch.object(m, "_save_user_profile") as save:
            m._handle_cli_command("/perfil umbral", _Agent())
            m._handle_cli_command("/perfil umbral rápido", _Agent())
        save.assert_not_called()
        assert "Uso:" in out() and "✗" in out()

    def test_hr_values(self, out):
        profile = {}
        with patch.object(m, "_load_user_profile", return_value=profile), patch.object(m, "_save_user_profile") as save:
            m._handle_cli_command("/perfil fc 48", _Agent())
            m._handle_cli_command("/perfil fc 110 186", _Agent())
            assert save.call_count == 0
            m._handle_cli_command("/perfil fc 48 186", _Agent())
        save.assert_called_once()
        assert "Uso:" in out() and "Perfil de FC actualizado" in out()

    def test_a_question_is_not_a_command(self):
        assert m._handle_cli_command("¿Cómo estoy hoy?", _Agent()) is False
        assert m._handle_cli_command("", _Agent()) is False


# ─── /proveedor ──────────────────────────────────────────────────────────────

NVIDIA = {"provider": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1", "model": "nvidia/nemotron-x"}
GROQ = {"provider": "groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"}


class TestSwitchProvider:
    def test_other_provider_always_asks_its_key_and_saves(self, monkeypatch, out):
        # Una sola clave en el .env: la del proveedor anterior no vale para el nuevo.
        monkeypatch.setenv("PROVIDER_API_KEY", "nvapi-real")
        agent = _Agent()
        with patch.object(m, "_ask_ia_provider", return_value=GROQ), patch.object(m, "save_ia_provider") as save, \
             patch.object(m, "_ask_api_key", new=AsyncMock()) as ask_key:
            assert asyncio.run(m._switch_provider(agent, NVIDIA)) == GROQ
        assert ask_key.await_args.args[0] == GROQ
        save.assert_called_once_with("groq", GROQ["base_url"], GROQ["model"], None)
        agent.set_provider.assert_called_once_with(GROQ)
        assert "Proveedor de IA guardado" in out()

    def test_same_provider_new_model_keeps_the_key_unless_asked(self, monkeypatch, out):
        monkeypatch.setenv("PROVIDER_API_KEY", "nvapi-real")
        new = dict(NVIDIA, model="otro-modelo")
        with patch.object(m, "_ask_ia_provider", return_value=new), patch.object(m, "save_ia_provider"), \
             patch.object(m.Confirm, "ask", return_value=False), \
             patch.object(m, "_ask_api_key", new=AsyncMock()) as ask_key:
            assert asyncio.run(m._switch_provider(_Agent(), NVIDIA))["model"] == "otro-modelo"
        ask_key.assert_not_awaited()


# ─── Perfil y configuración inicial ─────────────────────────────────────────

class TestProfileScreens:
    def test_empty_profile(self, out):
        with patch.object(m, "_load_user_profile", return_value={}):
            m._show_profile()
        assert "Perfil vacío" in out()

    def test_full_profile_uses_dd_mm_yyyy(self, out):
        profile = {"personal": {"name": "Rafa", "age": 45}, "goals": {"primary": "trail running",
                   "target_race_date": "2027-07-02"}, "health": {"injuries": ["DT1"], "notes": "insulina"},
                   "performance": {"running_threshold_pace": "4:00"}}
        with patch.object(m, "_load_user_profile", return_value=profile):
            m._show_profile()
        text = out()
        assert "Rafa" in text and "02/07/2027" in text and "4:00 min/km" in text and "DT1" in text and "insulina" in text
        assert "Diabetes tipo 1 · medicación sin indicar" in text

    def test_ask_goals_retries_invalid_values(self, out):
        future = (date.today() + timedelta(days=60)).isoformat()
        answers = ["Rafa", "trail running", "200", "8", "Ultra", "2020-01-01", future, "9:3", "9:30:00"]
        profile = {}
        with patch.object(m.Prompt, "ask", side_effect=answers):
            m._ask_goals(profile)
        assert profile["personal"]["name"] == "Rafa"
        assert profile["goals"] == {"primary": "trail running", "weekly_training_hours": 8.0, "target_race": "Ultra",
                                    "target_race_date": future, "target_time": "9:30:00"}
        assert out().count("✗") == 3

    def test_ask_goals_allows_skipping(self):
        profile = {}
        with patch.object(m.Prompt, "ask", side_effect=["", "running", "", "", "", ""]):
            m._ask_goals(profile)
        assert profile["goals"] == {"primary": "running"} and "name" not in profile["personal"]

    def test_ask_health_healthy_athlete_with_an_injury(self):
        # Formulario: patologías, lesiones, notas. Sin patologías no hay más preguntas (aunque haya lesión).
        profile = {"health": {"pathologies": ["DT1"], "diabetes_medication": ["insulina_basal"]}}
        with patch.object(m.Prompt, "ask", side_effect=["", "periostitis", ""]) as ask:
            m._ask_health(profile)
        assert profile["health"] == {"pathologies": [], "injuries": ["periostitis"], "notes": ""}
        assert ask.call_count == 3

    def test_ask_health_other_pathology_gets_no_more_questions(self):
        profile = {}
        with patch.object(m.Prompt, "ask", side_effect=["asma ,", "tendinitis", " sin notas "]):
            m._ask_health(profile)
        assert profile["health"] == {"pathologies": ["asma"], "injuries": ["tendinitis"], "notes": "sin notas"}

    def test_ask_health_diabetes_asks_medication_right_after_pathologies(self, out):
        profile = {}
        answers = ["Diabetes tipo II", "4, 7, 4", "Ozempic", "periostitis", ""]
        with patch.object(m.Prompt, "ask", side_effect=answers):
            m._ask_health(profile)
        assert profile["health"] == {"pathologies": ["Diabetes tipo II"], "diabetes_medication": ["metformina", "isglt2", "Ozempic"],
                                     "injuries": ["periostitis"], "notes": ""}
        assert "Has indicado Diabetes tipo 2" in out()

    def test_ask_health_diabetes_only_in_notes_asks_medication_at_the_end(self):
        profile = {}
        with patch.object(m.Prompt, "ask", side_effect=["", "", "DT1 desde 2010", "1", ""]):
            m._ask_health(profile)
        assert profile["health"]["diabetes_medication"] == ["insulina_basal"]

    def test_medication_retries_invalid_numbers_and_allows_none(self, out):
        h = {"injuries": ["DT2"]}
        with patch.object(m.Prompt, "ask", side_effect=["4, x", "12", "", ""]):
            m._ask_diabetes_medication(h)
        assert h["diabetes_medication"] == [] and out().count("✗") == 2

    def test_ensure_health_splits_old_profile_and_asks_medication_once(self, out):
        profile = {"health": {"injuries": ["DT 1", "periostitis"]}}
        with patch.object(m, "_save_user_profile") as save, patch.object(m.Prompt, "ask", side_effect=["1,2", ""]):
            m._ensure_health_profile(profile)
            m._ensure_health_profile(profile)
        h = profile["health"]
        assert h["pathologies"] == ["DT 1"] and h["injuries"] == ["periostitis"]
        assert h["diabetes_medication"] == ["insulina_basal", "insulina_rapida"]
        assert save.call_count == 2  # separación y medicación, una vez cada una
        assert "Diabetes tipo 1 · Insulina lenta (basal), Insulina rápida (bolos)" in out()

    def test_ensure_health_never_asks_without_diabetes(self):
        for health in ({}, {"injuries": ["periostitis"]}, {"pathologies": ["asma"], "injuries": []}):
            profile = {"health": dict(health)}
            with patch.object(m, "_save_user_profile"), patch.object(m.Prompt, "ask") as ask:
                m._ensure_health_profile(profile)
            ask.assert_not_called()
            assert "pathologies" in profile["health"]
        assert profile["health"]["pathologies"] == ["asma"]

    def test_first_time_setup_marks_profile(self, out):
        profile = {"personal": {"name": "Rafa"}}
        with patch.object(m, "_load_user_profile", return_value=profile), patch.object(m, "_save_user_profile") as save, \
             patch.object(m, "_ask_goals"), patch.object(m, "_ask_health"):
            m._run_first_time_setup()
        assert profile["setup_complete"] is True
        save.assert_called_once_with(profile)
        assert "Todo listo" in out()

    def test_initial_knowledge_includes_profile(self):
        kb = m._build_initial_athlete_knowledge({"personal": {"name": "Rafa"}, "goals": {"target_race_date": "2027-07-02"},
                                                 "health": {"injuries": ["DT1"]}})
        assert "- Nombre: Rafa" in kb and "- Fecha: 02/07/2027" in kb and "DT1" in kb


# ─── Planes ──────────────────────────────────────────────────────────────────

class TestPlanScreens:
    def test_list_error_empty_and_rows(self, out):
        with patch.object(m, "list_training_plans", side_effect=RuntimeError("sin red")):
            m._show_training_plans()
        with patch.object(m, "list_training_plans", return_value=[]):
            m._show_training_plans()
        plans = [{"id": "p1", "title": "Hacia la ultra", "status": "active", "duration_weeks": 8, "objective": "Ultra"},
                 {"id": "p2", "title": "Base", "status": "inactive", "duration_weeks": 4}]
        with patch.object(m, "list_training_plans", return_value=plans):
            m._show_training_plans()
        text = out()
        assert "Error listando planes" in text and "No hay planes" in text
        assert "Hacia la ultra" in text and "8 sem" in text and "✓" in text

    def test_view_plan_paths(self, out):
        m._show_training_plan("")
        with patch.object(m, "get_training_plan", side_effect=OSError("x")):
            m._show_training_plan("p1")
        with patch.object(m, "get_training_plan", return_value=None):
            m._show_training_plan("p1")
        text = out()
        assert "Uso: /plan ver" in text and "Error cargando plan" in text and "No encontré el plan 'p1'" in text

    def test_view_plan_with_and_without_sessions(self, out):
        plan = {"id": "p1", "title": "Hacia la ultra", "status": "active", "duration_weeks": 8}
        sessions = [{"week_index": 1, "day_index": 2, "session_type": "rodaje", "duration_min": 50, "hr_zone": 2}]
        with patch.object(m, "get_training_plan", return_value=plan), \
             patch.object(m, "list_training_plan_sessions", return_value=sessions), \
             patch.object(m, "_load_user_profile", return_value={}), \
             patch.object(m, "_session_intensity_label", return_value="Z2 Aeróbica (135–145 lpm)"):
            m._show_training_plan("p1")
        with patch.object(m, "get_training_plan", return_value=plan), \
             patch.object(m, "list_training_plan_sessions", side_effect=RuntimeError("x")):
            m._show_training_plan("p1")
        text = out()
        assert "Semana 1 · Día 2 · Rodaje · 50 min · Z2 Aeróbica (135–145 lpm)" in text
        assert "Sin sesiones registradas" in text

    def test_activate_plan(self, out):
        agent = _Agent()
        m._activate_training_plan_cli("", agent)
        with patch.object(m, "activate_training_plan", side_effect=KeyError("x")):
            m._activate_training_plan_cli("p1", agent)
        with patch.object(m, "activate_training_plan", return_value=None):
            m._activate_training_plan_cli("p1", agent)
        profile = {}
        with patch.object(m, "activate_training_plan", return_value={"id": "p1", "title": "Base"}), \
             patch.object(m, "_load_user_profile", return_value=profile), patch.object(m, "_save_user_profile") as save:
            m._activate_training_plan_cli("p1", agent)
        save.assert_called_once()
        assert profile["training_plan"] == {"id": "p1", "title": "Base", "status": "active", "active": True, "source": "db"}
        text = out()
        assert "Uso: /plan activar" in text and "Error activando plan" in text and "No se pudo activar" in text
        assert "Plan activado: Base" in text

    def test_create_plan_asks_like_a_coach_saves_preferences_and_shows_the_plan(self, out):
        # 28/10: antes guardaba solo el formulario (sin sesiones). 29/10: pregunta días, límites, dobles, distribución
        # de la intensidad y progresión, y lo guarda en el perfil para el LLM y las reglas del plan.
        agent = _Agent()
        agent.user_profile = {"goals": {"target_race": "Santa Pola"}}
        agent._route_planning = AsyncMock(return_value="## 🧭 Resumen\nPlan activo: Hacia Santa Pola\n- Semana 1: ~380 min")
        answers = iter(["todos", "V", "L-V 75, S 180", "2", "1", "no puedo los martes"])
        with patch.object(m, "_load_user_profile", return_value=agent.user_profile),              patch.object(m.Prompt, "ask", side_effect=lambda *a, **k: next(answers)),              patch.object(m.Confirm, "ask", return_value=True), patch.object(m, "_save_user_profile") as save:
            asyncio.run(m._create_training_plan_cli(agent))
        goals = agent.user_profile["goals"]
        assert goals["availability"]["training_days"] == [1, 2, 3, 4, 6, 7] and goals["availability"]["rest_days"] == [5]
        assert goals["availability"]["max_minutes_per_day"] == {"1": 75, "2": 75, "3": 75, "4": 75, "5": 75, "6": 180}
        assert goals["plan_preferences"] == {"doubles": True, "intensity": "piramidal", "progression": "3+1"}
        save.assert_called_once()
        message = agent._route_planning.await_args.args[0]
        assert agent._route_planning.await_args.kwargs == {"force_new": True}
        assert "plan nuevo" in message.lower() and "no puedo los martes" in message
        text = out()
        assert "Santa Pola" in text and "Semana 1" in text

    def test_create_plan_fallback_reply_is_shown(self, out):
        agent = _Agent()
        agent.user_profile = {}
        agent._route_planning = AsyncMock(return_value=("Plan genérico de respaldo", "planning_fallback"))
        answers = iter(["todos", "", "", "1", "1", ""])
        with patch.object(m, "_load_user_profile", return_value={}),              patch.object(m.Prompt, "ask", side_effect=lambda *a, **k: next(answers)),              patch.object(m.Confirm, "ask", return_value=False), patch.object(m, "_save_user_profile"):
            asyncio.run(m._create_training_plan_cli(agent))
        assert agent._route_planning.await_args.args[0] == "Crea un plan nuevo"
        assert agent.user_profile["goals"]["plan_preferences"]["doubles"] is False
        assert "Plan genérico de respaldo" in out()

    def test_plan_refs_by_number_short_id_or_full_id(self):
        plans = [{"id": "3fad2d42826a4862bca23173e7ed85a0"}, {"id": "3fb01c9e00aa4f6d8f1e2b3c4d5e6f70"},
                 {"id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"}]
        with patch.object(m, "list_training_plans", return_value=plans):
            assert m._resolve_plan_id("1") == plans[0]["id"]
            assert m._resolve_plan_id("3fad2d42") == plans[0]["id"]
            assert m._resolve_plan_id("A1B2") == plans[2]["id"]
            assert m._resolve_plan_id("3f") is None  # dos planes empiezan así
            assert m._resolve_plan_id("9") == "9"  # ni número válido ni prefijo: se prueba tal cual
        with patch.object(m, "list_training_plans", side_effect=RuntimeError("sin red")):
            assert m._resolve_plan_id("3fad2d42") == "3fad2d42"

    def test_list_shows_number_and_short_id(self, out):
        plans = [{"id": "3fad2d42826a4862bca23173e7ed85a0", "title": "Santa Pola", "status": "active", "duration_weeks": 11}]
        with patch.object(m, "list_training_plans", return_value=plans):
            m._show_training_plans()
        text = out()
        assert "3fad2d42" in text and "3fad2d42826a" not in text and "/plan ver <nº>" in text


# ─── /carga ──────────────────────────────────────────────────────────────────

class TestLoadTrend:
    def test_without_series(self, out):
        with patch_all("_load_metrics_series", return_value=[]):
            m._show_load_trend_cli(_Agent())
        assert "Aún no hay datos de carga" in out()

    @pytest.mark.parametrize("cmd", ["/carga", "/carga meses"])
    def test_renders_real_table(self, cmd, out):
        start = date.today() - timedelta(days=100)
        series = [{"date": (start + timedelta(days=i)).isoformat(), "tss": 50.0, "atl": 40.0 + i % 7,
                   "ctl": 45.0, "tsb": 2.0} for i in range(101)]
        agent = _Agent()
        # La serie sale de load_metrics_daily (27/10: antes de una copia en el perfil).
        with patch_all("_load_metrics_series", return_value=series):
            m._show_load_trend_cli(agent, cmd)
        text = out()
        assert "CTL" in text and "ATL" in text


# ─── Arranque: entorno, proveedor, Supabase, Garmin, logging ────────────────

class TestStartupChecks:
    def test_check_garmin_env(self, monkeypatch, out):
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)
        monkeypatch.setenv("GARMIN_PASSWORD", "x")
        with pytest.raises(SystemExit):
            m._check_garmin_env()
        assert "GARMIN_EMAIL" in out()

    @pytest.mark.parametrize("status, exits", [
        ({"configured": False, "connected": False, "error": None}, True),
        ({"configured": True, "connected": False, "error": "timeout"}, True),
        ({"configured": True, "connected": True, "error": None}, False),
    ])
    def test_supabase_check(self, status, exits, out):
        with patch.object(m, "check_supabase_connection", return_value=status):
            if exits:
                with pytest.raises(SystemExit):
                    m._check_and_migrate_supabase()
            else:
                m._check_and_migrate_supabase()
                assert "Supabase conectado" in out()

    def test_garmin_password_change(self, monkeypatch, out):
        monkeypatch.setenv("GARMIN_PASSWORD", "old")
        results = [{"ok": False, "error": "Supabase rechazó el cambio"}, {"ok": True}]
        with patch.object(m.Prompt, "ask", side_effect=["123", "nueva-1", "nueva-2"]), \
             patch.object(m, "update_login_password", side_effect=results):
            assert m._handle_garmin_password_change() == "nueva-2"
        import os
        assert os.environ["GARMIN_PASSWORD"] == "nueva-2"
        assert "Mínimo 6 caracteres" in out() and "Supabase rechazó el cambio" in out()

    def test_garmin_password_change_gives_up_after_three(self, out):
        with patch.object(m.Prompt, "ask", return_value="abc"):
            assert m._handle_garmin_password_change() is None

    def test_logging_goes_to_file_only_by_default(self, monkeypatch, tmp_path):
        log_file = tmp_path / "kairos.log"
        monkeypatch.setenv("KAIROS_LOG_FILE", str(log_file))
        monkeypatch.delenv("KAIROS_LOG_STDOUT", raising=False)
        monkeypatch.setenv("KAIROS_DEBUG_CONSOLE", "yes")
        root = logging.getLogger()
        saved = root.handlers[:], root.level
        try:
            logger, debug = m._configure_logging()
            logger.info("hola")
            kinds = {type(h).__name__ for h in root.handlers}
        finally:
            for h in root.handlers:
                h.close()
            root.handlers[:], _ = saved
            root.setLevel(saved[1])
        assert debug is True
        assert kinds == {"RotatingFileHandler"}
        assert "hola" in log_file.read_text(encoding="utf-8")

    def test_env_bool(self, monkeypatch):
        monkeypatch.setenv("X_FLAG", "on")
        assert m._env_bool("X_FLAG", False) is True
        monkeypatch.setenv("X_FLAG", "")
        assert m._env_bool("X_FLAG", True) is True
        monkeypatch.setenv("X_FLAG", "no")
        assert m._env_bool("X_FLAG", True) is False


# ─── main(): arranque y bucle de conversación ───────────────────────────────

class _MainAgent(_Agent):
    instances: list = []

    def __init__(self, mcp_session=None, provider=None):
        super().__init__()
        self.provider = provider
        self.tools_schema = [{}] * 42
        self.knowledge_chunks, self.knowledge_sources = [], []
        self.conversation_history = []
        self._last_load_compute_meta = {"mode": "incremental_refresh", "reason": "actividad nueva"}
        self.chat = AsyncMock(side_effect=self._chat)
        self.save_session_summary_checkpoint = MagicMock()
        self.initialize = AsyncMock()
        self.compute_and_persist_load_metrics = AsyncMock()
        self.build_startup_status_markdown = AsyncMock(return_value="## Estado\n- Listo")
        self.build_onboarding_mcp_enrichment = AsyncMock(return_value={})
        _MainAgent.instances.append(self)

    async def _chat(self, text):
        self.conversation_history.append(text)
        if "rompe" in text:
            raise RuntimeError("fallo del LLM")
        if "raro" in text:
            raise AttributeError("'NoneType' object has no attribute 'get'")
        return "Respuesta de Kairos"


@contextlib.asynccontextmanager
async def _fake_session():
    yield object()


def _run_main(inputs, *, auth_raw='{"displayName": "x"}', is_new=False, kb="kb", skip_status=False, monkeypatch=None):
    _MainAgent.instances.clear()
    profile = {"load_metrics": {"formula_version": 1}}
    load_view = {"latest": {"ctl": 60.4, "atl": 42.1, "tsb": 18.3}, "series": [{}] * 120}
    if skip_status:
        monkeypatch.setenv("KAIROS_SKIP_STARTUP_STATUS", "1")
    else:
        monkeypatch.delenv("KAIROS_SKIP_STARTUP_STATUS", raising=False)
    patches = [
        patch.object(m, "_configure_logging", return_value=(logging.getLogger("test"), False)),
        patch.object(m, "_load_metrics_view", return_value=load_view),
        patch.object(m, "_check_and_migrate_supabase"),
        patch.object(m, "_authenticate_or_register_user", return_value=("r@example.com", {}, is_new, "pw")),
        patch.object(m, "_ensure_garmin_credentials"),
        patch.object(m, "_check_garmin_env"),
        patch.object(m, "_ensure_ia_provider", AsyncMock(return_value=NVIDIA)),
        patch.object(m, "_detect_zscaler", return_value=False),
        patch.object(m, "garmin_mcp_session", _fake_session),
        patch.object(m, "TrainerAgent", _MainAgent),
        patch("agent.mcp.client.call_tool", AsyncMock(return_value=auth_raw)),
        patch.object(m, "_sync_from_garmin", AsyncMock(return_value=[])),
        patch.object(m, "_load_user_profile", return_value=profile),
        patch.object(m, "_ensure_running_threshold_pace", side_effect=lambda p: p),
        patch.object(m, "_is_first_time", return_value=False),
        patch.object(m, "_run_first_time_setup"),
        patch.object(m, "load_athlete_knowledge", return_value=kb),
        patch.object(m, "save_athlete_knowledge"),
        patch.object(m, "get_active_user", return_value={"username": "r@example.com", "user_id": "u1"}),
        patch.object(m, "_handle_garmin_password_change", return_value=None),
        patch.object(m.Prompt, "ask", side_effect=inputs),
        # El contador de "Consultando Garmin…" espera 1 s por vuelta: en los tests, 0 s.
        patch.object(m.asyncio, "sleep", lambda _s, _real=asyncio.sleep: _real(0)),
        patch.object(m, "_ensure_health_profile", side_effect=lambda p: p),
    ]
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        asyncio.run(m.main())
    return mocks


class TestMainLoop:
    def test_session_answers_runs_commands_and_saves_on_exit(self, monkeypatch, out):
        with patch.object(m, "_show_menu") as menu:
            _run_main(["/menu", "", "¿Cómo estoy hoy?", "rompe esto", "salir"], monkeypatch=monkeypatch)
        agent = _MainAgent.instances[0]
        menu.assert_called_once()
        assert [c.args[0] for c in agent.chat.call_args_list] == ["¿Cómo estoy hoy?", "rompe esto"]
        assert agent.save_session_summary_checkpoint.call_count == 2  # tras la respuesta y al salir
        text = out()
        assert "42 herramientas disponibles" in text and "CTL (Estado físico)=60.4" in text
        assert "Respuesta de Kairos" in text and "Error en el Agente" in text and "fallo del LLM" in text
        assert "refrescó días recientes" in text and "Sesión finalizada" in text

    def test_any_error_in_a_question_keeps_the_session(self, monkeypatch, out):
        # 28/10: antes solo se capturaban RuntimeError, OSError…; un AttributeError, un McpError o un 503 de
        # openai cerraban Kairos. Ahora se avisa y se sigue preguntando.
        _run_main(["fallo raro", "¿Cómo estoy hoy?", "salir"], monkeypatch=monkeypatch, skip_status=True)
        agent = _MainAgent.instances[0]
        assert [c.args[0] for c in agent.chat.call_args_list] == ["fallo raro", "¿Cómo estoy hoy?"]
        text = out()
        assert "Error en el Agente" in text and "no attribute 'get'" in text
        assert "Respuesta de Kairos" in text and "Sesión finalizada" in text

    def test_modelo_switches_provider(self, monkeypatch, out):
        with patch.object(m, "_switch_provider", AsyncMock(return_value=GROQ)) as switch:
            _run_main(["/proveedor", "salir"], monkeypatch=monkeypatch, skip_status=True)
        switch.assert_awaited_once()
        assert "Resumen inicial omitido" in out()

    def test_expired_key_is_asked_and_the_question_repeated(self, monkeypatch, out):
        # 26/10: el proveedor rechaza la clave → se pide la nueva, se guarda y se repite la misma pregunta.
        async def _chat_expired_once(agent, text):
            agent.conversation_history += [text, "error"]
            if not agent.calls:
                agent.llm_auth_failed = True
            agent.calls.append(text)
            return "Respuesta de Kairos"

        with patch.object(m, "_ask_api_key", new=AsyncMock()) as ask_key, \
             patch.object(_MainAgent, "_chat", _chat_expired_once):
            _MainAgent.calls = []
            _run_main(["¿Cómo estoy?", "salir"], monkeypatch=monkeypatch, skip_status=True)
        agent = _MainAgent.instances[0]
        assert agent.calls == ["¿Cómo estoy?", "¿Cómo estoy?"]
        assert ask_key.await_args.args[0] == NVIDIA and "rechazado tu clave" in ask_key.await_args.args[1]
        agent.set_provider.assert_called_once_with(NVIDIA)
        assert agent.conversation_history == ["¿Cómo estoy?", "error"]  # el turno fallido se quita
        assert out().count("Respuesta de Kairos") == 1

    def test_startup_reads_the_garmin_profile_once_and_overlaps_status_with_load(self, monkeypatch, out):
        # 8c (28/10): el perfil de Garmin sirve para comprobar el acceso y para los datos personales, y el
        # resumen inicial recibe el cálculo de la carga para esperarlo solo al leer la serie.
        mocks = _run_main(["salir"], monkeypatch=monkeypatch)
        call_tool, sync = mocks[10], mocks[11]
        assert [c.args[1] for c in call_tool.await_args_list] == ["get_user_profile"]
        assert sync.await_args.kwargs == {"profile_raw": '{"displayName": "x"}'}
        agent = _MainAgent.instances[0]
        assert agent.build_startup_status_markdown.await_args.kwargs["load_ready"] is not None
        assert "Listo" in out()

    def test_plan_create_is_awaited_by_the_main_loop(self, monkeypatch, out):
        with patch.object(m, "_create_training_plan_cli", new=AsyncMock()) as create:
            _run_main(["/plan crear", "salir"], monkeypatch=monkeypatch, skip_status=True)
        create.assert_awaited_once()
        assert _MainAgent.instances[0].chat.call_count == 0

    def test_ctrl_c_ends_the_session(self, monkeypatch, out):
        _run_main(KeyboardInterrupt(), monkeypatch=monkeypatch)
        assert "Sesión finalizada" in out()

    def test_garmin_auth_failure_without_new_password_exits(self, monkeypatch, out):
        _run_main(["salir"], auth_raw="Error 401 Unauthorized", monkeypatch=monkeypatch)
        assert "No se pudo actualizar la contraseña" in out()
        _MainAgent.instances[0].compute_and_persist_load_metrics.assert_not_called()

    def test_new_user_seeds_knowledge_and_full_recalc(self, monkeypatch, out):
        mocks = _run_main(["salir"], is_new=True, kb="", monkeypatch=monkeypatch)
        agent = _MainAgent.instances[0]
        agent.compute_and_persist_load_metrics.assert_awaited_once_with(force_full_recalc=True)
        assert agent.knowledge_sources == ["db:athlete_knowledge"]
        first_setup = mocks[14]
        first_setup.assert_called_once()
        assert "Base de conocimiento inicial enriquecida" in out()

    def test_existing_user_without_knowledge_creates_it(self, monkeypatch, out):
        _run_main(["salir"], kb="", monkeypatch=monkeypatch)
        assert "Base de conocimiento creada" in out()
        assert "Detecté cambios en el modelo de carga" in out()  # formula_version 1 ≠ la vigente
