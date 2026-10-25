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

    def get_daily_usage_info(self):
        return {"quota_exhausted": False, "today_usage": 100, "limit": 1000, "remaining": 900}


# ─── Despacho de comandos ────────────────────────────────────────────────────

class TestHandleCliCommand:
    @pytest.mark.parametrize("entry, target", [
        ("/perfil", "_show_profile"), ("/profile", "_show_profile"), ("/menu", "_show_menu"),
        ("/ayuda", "_show_help"), ("/help", "_show_help"), ("/?", "_show_help"),
        ("/plan", "_show_plan_help"), ("/plan listar", "_show_training_plans"),
        ("/plan crear", "_create_training_plan_cli"),
    ])
    def test_simple_commands_call_their_screen(self, entry, target):
        with patch.object(m, target) as screen:
            assert m._handle_cli_command(entry, _Agent()) is True
        screen.assert_called_once()

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


# ─── /modelo ─────────────────────────────────────────────────────────────────

class TestSwitchProvider:
    def test_same_provider_keeps_tokens(self, out):
        agent = _Agent()
        with patch.object(m, "_select_provider_menu", return_value="nvidia"):
            assert m._switch_provider(agent, "nvidia") == "nvidia"
        agent.set_provider.assert_not_called()
        assert agent.total_prompt_tokens == 10
        assert "ya está activo" in out()

    def test_new_provider_resets_tokens(self, out):
        agent = _Agent()
        with patch.object(m, "_select_provider_menu", return_value="groq"), patch.object(m, "_check_env"):
            assert m._switch_provider(agent, "nvidia") == "groq"
        agent.set_provider.assert_called_once_with("groq")
        assert (agent.total_prompt_tokens, agent.total_completion_tokens) == (0, 0)
        assert "Modelo cambiado con éxito" in out()

    def test_exhausted_quota_is_reported(self, out):
        agent = _Agent()
        agent.get_daily_usage_info = lambda: {"quota_exhausted": True, "today_usage": 1000, "limit": 1000, "remaining": 0}
        with patch.object(m, "_select_provider_menu", return_value="gemini"), patch.object(m, "_check_env"):
            m._switch_provider(agent, "nvidia")
        assert "agotada" in out()


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

    def test_ask_health(self):
        profile = {}
        with patch.object(m.Prompt, "ask", side_effect=["DT1, tendinitis ,", " sin notas "]):
            m._ask_health(profile)
        assert profile["health"] == {"injuries": ["DT1", "tendinitis"], "notes": "sin notas"}

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
        assert "Semana 1 · Día 2 · rodaje · 50 min · Z2 Aeróbica (135–145 lpm)" in text
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

    def test_create_plan_retries_weeks_and_mirrors_profile(self, out):
        agent = _Agent()
        profile = {"goals": {"target_race": "Ultra PDA"}}
        answers = ["Plan hacia Ultra PDA", "Ultra PDA", "Plan base", "moderate", "ocho", "8", "rodaje suave"]
        created = {"id": "1234567890ab", "title": "Plan hacia Ultra PDA", "source": "cli_plan_create"}
        with patch.object(m, "_load_user_profile", return_value=profile), patch.object(m, "_save_user_profile"), \
             patch.object(m.Prompt, "ask", side_effect=answers), \
             patch.object(m, "create_training_plan", return_value=created) as create:
            m._create_training_plan_cli(agent)
        payload = create.call_args.args[0]
        assert payload["duration_weeks"] == 8 and payload["plan_data"]["today_focus"] == "rodaje suave"
        assert profile["training_plan"]["id"] == "1234567890ab"
        text = out()
        assert "Introduce un número entero" in text and "Plan creado y activado" in text

    def test_create_plan_error_does_not_touch_profile(self, out):
        profile = {"goals": {}}
        with patch.object(m, "_load_user_profile", return_value=profile), \
             patch.object(m.Prompt, "ask", side_effect=["T", "O", "D", "easy", "4", ""]), \
             patch.object(m, "create_training_plan", side_effect=RuntimeError("rls")):
            m._create_training_plan_cli(_Agent())
        assert "training_plan" not in profile and "Error creando plan" in out()


# ─── /carga ──────────────────────────────────────────────────────────────────

class TestLoadTrend:
    def test_without_series(self, out):
        m._show_load_trend_cli(_Agent())
        assert "Aún no hay datos de carga" in out()

    @pytest.mark.parametrize("cmd", ["/carga", "/carga meses"])
    def test_renders_real_table(self, cmd, out):
        start = date.today() - timedelta(days=100)
        series = [{"date": (start + timedelta(days=i)).isoformat(), "tss": 50.0, "atl": 40.0 + i % 7,
                   "ctl": 45.0, "tsb": 2.0} for i in range(101)]
        agent = _Agent()
        agent.user_profile = {"load_metrics": {"series": series}}
        m._show_load_trend_cli(agent, cmd)
        text = out()
        assert "CTL" in text and "ATL" in text


# ─── Arranque: entorno, proveedor, Supabase, Garmin, logging ────────────────

class TestStartupChecks:
    def test_check_env_exits_without_key(self, monkeypatch, out):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        with pytest.raises(SystemExit):
            m._check_env("nvidia")
        assert "NVIDIA_API_KEY" in out()

    def test_check_env_passes_with_key(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "k")
        m._check_env("nvidia")

    def test_check_garmin_env(self, monkeypatch, out):
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)
        monkeypatch.setenv("GARMIN_PASSWORD", "x")
        with pytest.raises(SystemExit):
            m._check_garmin_env()
        assert "GARMIN_EMAIL" in out()

    def test_provider_menu_without_keys_exits(self, monkeypatch, out):
        for var in ("GEMINI_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "NVIDIA_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(SystemExit):
            m._select_provider_menu()
        assert "No hay ninguna API key" in out()

    def test_provider_menu_lists_only_configured_keys(self, monkeypatch, out):
        for var in ("GEMINI_API_KEY", "MISTRAL_API_KEY", "CEREBRAS_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("GROQ_API_KEY", "gsk_real")
        monkeypatch.setenv("NVIDIA_API_KEY", "nv_real")
        usage = {"gsk_real": 0, "nv_real": 1234}
        with patch("agent.storage.get_gemini_daily_usage", side_effect=lambda k: usage[k]), \
             patch("agent.storage.is_gemini_quota_exhausted", return_value=False), \
             patch.object(m.Prompt, "ask", return_value="2"):
            assert m._select_provider_menu() == "nvidia"
        text = out()
        assert "recomendado" in text and "1,234 tokens consumidos hoy" in text and "Gemini" not in text

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
            assert m._handle_garmin_password_change("runner@example.com") == "nueva-2"
        import os
        assert os.environ["GARMIN_PASSWORD"] == "nueva-2"
        assert "Mínimo 6 caracteres" in out() and "Supabase rechazó el cambio" in out()

    def test_garmin_password_change_gives_up_after_three(self, out):
        with patch.object(m.Prompt, "ask", return_value="abc"):
            assert m._handle_garmin_password_change("runner@example.com") is None

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
        return "Respuesta de Kairos"


@contextlib.asynccontextmanager
async def _fake_session():
    yield object()


def _run_main(inputs, *, auth_raw='{"displayName": "x"}', is_new=False, kb="kb", skip_status=False, monkeypatch=None):
    _MainAgent.instances.clear()
    profile = {"load_metrics": {"last": {"ctl": 60.4, "atl": 42.1, "tsb": 18.3}, "series": [{}] * 120,
                                "formula_version": 1}}
    if skip_status:
        monkeypatch.setenv("KAIROS_SKIP_STARTUP_STATUS", "1")
    else:
        monkeypatch.delenv("KAIROS_SKIP_STARTUP_STATUS", raising=False)
    patches = [
        patch.object(m, "_configure_logging", return_value=(logging.getLogger("test"), False)),
        patch.object(m, "_check_and_migrate_supabase"),
        patch.object(m, "_authenticate_or_register_user", return_value=("r@example.com", {}, is_new, "pw")),
        patch.object(m, "_ensure_garmin_credentials"),
        patch.object(m, "_check_garmin_env"),
        patch.object(m, "_auto_select_provider", return_value="nvidia"),
        patch.object(m, "_check_env"),
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

    def test_modelo_switches_provider(self, monkeypatch, out):
        with patch.object(m, "_switch_provider", return_value="groq") as switch:
            _run_main(["/modelo", "salir"], monkeypatch=monkeypatch, skip_status=True)
        switch.assert_called_once()
        assert "Resumen inicial omitido" in out()

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
