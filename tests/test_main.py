"""
tests/test_main.py
Suite de tests unitarios para las funciones puras de main.py.

Cubre:
  - _validate_date
  - _validate_time
  - _validate_hours
  - _is_first_time
"""

import asyncio
import os
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from agent.main import (
    _ask_api_key,
    _ask_ia_provider,
    _build_enriched_athlete_knowledge,
    _ensure_ia_provider,
    _ensure_running_threshold_pace,
    _ensure_garmin_credentials,
    _format_coach_markdown,
    _is_first_time,
    _parse_running_threshold_pace_to_sec_per_km,
    _parse_plan_command,
    _set_hr_profile_values,
    _set_running_threshold_pace,
    _sync_from_garmin,
    _validate_date,
    _validate_hours,
    _validate_time,
)


# ─── _validate_date ───────────────────────────────────────────────────────────

class TestValidateDate:
    def test_valid_future_date(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        ok, err = _validate_date(future)
        assert ok
        assert err == ""

    def test_wrong_format_slash(self):
        ok, err = _validate_date("17/06/2026")
        assert not ok
        assert "YYYY-MM-DD" in err

    def test_wrong_format_no_separator(self):
        ok, err = _validate_date("20260617")
        assert not ok

    def test_past_date_rejected(self):
        ok, err = _validate_date("2020-01-01")
        assert not ok
        assert "futura" in err

    def test_today_rejected(self):
        ok, err = _validate_date(date.today().isoformat())
        assert not ok
        assert "futura" in err

    def test_invalid_month_13(self):
        ok, err = _validate_date("2027-13-01")
        assert not ok
        assert "inválida" in err

    def test_invalid_day_32(self):
        ok, err = _validate_date("2027-01-32")
        assert not ok
        assert "inválida" in err

    def test_far_future_valid(self):
        ok, err = _validate_date("2099-12-31")
        assert ok

    def test_empty_string_rejected(self):
        ok, err = _validate_date("")
        assert not ok


# ─── _validate_time ───────────────────────────────────────────────────────────

class TestValidateTime:
    def test_valid_h_mm_ss(self):
        ok, err = _validate_time("9:30:00")
        assert ok
        assert err == ""

    def test_valid_hh_mm_ss(self):
        ok, err = _validate_time("13:45:30")
        assert ok
        assert err == ""

    def test_invalid_format_letters(self):
        ok, err = _validate_time("1h30m")
        assert not ok

    def test_invalid_format_no_seconds(self):
        ok, err = _validate_time("01:30")
        assert not ok

    def test_minutes_over_59(self):
        ok, err = _validate_time("1:60:00")
        assert not ok
        assert "Minutos" in err

    def test_seconds_over_59(self):
        ok, err = _validate_time("1:00:60")
        assert not ok
        assert "Minutos" in err  # mensaje incluye "Minutos y segundos"

    def test_boundary_minutes_59_valid(self):
        ok, _ = _validate_time("1:59:59")
        assert ok

    def test_three_digit_hours_valid(self):
        ok, err = _validate_time("99:59:59")
        assert ok

    def test_hours_over_99_rejected(self):
        ok, err = _validate_time("100:00:00")
        assert not ok
        assert "Horas" in err

    def test_zero_time_valid(self):
        ok, _ = _validate_time("0:00:00")
        assert ok


# ─── _validate_hours ─────────────────────────────────────────────────────────

class TestValidateHours:
    def test_valid_integer(self):
        ok, err = _validate_hours("10")
        assert ok
        assert err == ""

    def test_valid_decimal_dot(self):
        ok, err = _validate_hours("10.5")
        assert ok

    def test_valid_decimal_comma(self):
        # La función acepta coma como separador decimal
        ok, err = _validate_hours("10,5")
        assert ok

    def test_too_low(self):
        ok, err = _validate_hours("0.1")
        assert not ok
        assert "mínimo" in err

    def test_too_high(self):
        ok, err = _validate_hours("41")
        assert not ok
        assert "máximo" in err

    def test_not_a_number(self):
        ok, err = _validate_hours("mucho")
        assert not ok
        assert "número" in err

    def test_boundary_min_valid(self):
        ok, _ = _validate_hours("0.5")
        assert ok

    def test_boundary_max_valid(self):
        ok, _ = _validate_hours("40")
        assert ok

    def test_negative_rejected(self):
        ok, err = _validate_hours("-5")
        assert not ok
        assert "mínimo" in err

    def test_empty_string_rejected(self):
        ok, err = _validate_hours("")
        assert not ok
        assert "número" in err


class TestParseRunningThresholdPace:
    def test_accepts_mm_ss(self):
        assert _parse_running_threshold_pace_to_sec_per_km("4:15") == 255.0

    def test_accepts_dot_format(self):
        assert _parse_running_threshold_pace_to_sec_per_km("4.15") == 255.0

    def test_accepts_comma_format(self):
        assert _parse_running_threshold_pace_to_sec_per_km("4,15") == 255.0

    def test_rejects_invalid_seconds(self):
        assert _parse_running_threshold_pace_to_sec_per_km("4:75") is None

    def test_rejects_invalid_text(self):
        assert _parse_running_threshold_pace_to_sec_per_km("abc") is None


class TestEnsureRunningThresholdPace:
    def test_skips_prompt_if_existing(self):
        profile = {"performance": {"running_threshold_pace_sec_per_km": 250.0}}
        with patch("agent.main.Prompt.ask") as ask_mock, patch("agent.main._save_user_profile") as save_mock:
            out = _ensure_running_threshold_pace(profile)
        assert out["performance"]["running_threshold_pace_sec_per_km"] == 250.0
        ask_mock.assert_not_called()
        save_mock.assert_not_called()

    def test_prompts_and_persists_when_missing(self):
        profile = {}
        with patch("agent.main.Prompt.ask", return_value="4:15") as ask_mock, patch("agent.main._save_user_profile") as save_mock:
            out = _ensure_running_threshold_pace(profile)
        assert out["performance"]["running_threshold_pace_sec_per_km"] == 255.0
        assert out["performance"]["running_threshold_pace"] == "4:15"
        ask_mock.assert_called_once()
        save_mock.assert_called_once()


class TestSetRunningThresholdPace:
    def test_sets_fields_when_valid(self):
        profile = {}
        ok, msg = _set_running_threshold_pace(profile, "4:10")
        assert ok is True
        assert msg == "4:10"
        perf = profile.get("performance") or {}
        assert perf.get("running_threshold_pace_sec_per_km") == 250.0
        assert perf.get("running_threshold_pace") == "4:10"
        assert perf.get("running_threshold_pace_date")

    def test_appends_dated_entry_to_history_without_touching_past(self):
        from datetime import date as _date

        today_iso = _date.today().isoformat()
        profile = {
            "performance": {
                "running_threshold_pace_history": [
                    {"date": "2026-01-30", "sec_per_km": 230},
                    {"date": "2026-05-09", "sec_per_km": 252},
                    {"date": today_iso, "sec_per_km": 260},
                ]
            }
        }
        ok, _ = _set_running_threshold_pace(profile, "4:05")
        assert ok is True
        assert profile["performance"]["running_threshold_pace_history"] == [
            {"date": "2026-01-30", "sec_per_km": 230},
            {"date": "2026-05-09", "sec_per_km": 252},
            {"date": today_iso, "sec_per_km": 245.0},
        ]

    def test_returns_error_when_invalid(self):
        profile = {}
        ok, msg = _set_running_threshold_pace(profile, "foo")
        assert ok is False
        assert "Formato no válido" in msg


class TestSetHrProfileValues:
    def test_sets_hr_profile_and_marks_update_date(self):
        profile = {}
        ok, msg = _set_hr_profile_values(profile, "48", "186")
        assert ok
        assert "FC reposo=48 bpm" in msg
        perf = profile.get("performance") or {}
        assert perf.get("hr_rest_bpm") == 48
        assert perf.get("hr_max_bpm") == 186
        assert perf.get("hr_profile_date")
        assert perf.get("performance_params_updated_at")

    def test_rejects_invalid_hr_ranges(self):
        profile = {}
        ok, msg = _set_hr_profile_values(profile, "110", "186")
        assert not ok
        assert "reposo fuera de rango" in msg.lower()


class _FakeAgentSync:
    def __init__(self, data):
        self._data = data

    async def fetch_garmin_personal_data(self):
        return self._data


class TestSyncFromGarmin:
    def test_no_changes_does_not_persist_profile(self):
        profile = {"personal": {"name": "Rafa", "age": 35}}
        saved = {"called": False}

        def _save(_profile):
            saved["called"] = True

        with patch("agent.main._load_user_profile", return_value=profile), patch(
            "agent.main._save_user_profile", side_effect=_save
        ):
            out = asyncio.run(_sync_from_garmin(_FakeAgentSync({"name": "Rafa", "age": 35})))

        assert out == []
        assert saved["called"] is False

    def test_save_failure_does_not_crash_startup(self):
        profile = {"personal": {"name": "Old"}}

        with patch("agent.main._load_user_profile", return_value=profile), patch(
            "agent.main._save_user_profile", side_effect=RuntimeError("statement timeout")
        ):
            out = asyncio.run(_sync_from_garmin(_FakeAgentSync({"name": "New"})))

        assert out == []

# ─── _is_first_time ──────────────────────────────────────────────────────────

class TestIsFirstTime:
    """_is_first_time() depende solo de setup_complete en el perfil."""

    def test_no_profile_returns_true(self):
        with patch("agent.main._load_user_profile", return_value={}):
            assert _is_first_time() is True

    def test_setup_complete_false_returns_true(self):
        with patch("agent.main._load_user_profile", return_value={"setup_complete": False}):
            assert _is_first_time() is True

    def test_setup_complete_true_returns_false(self):
        with patch("agent.main._load_user_profile", return_value={"setup_complete": True}):
            assert _is_first_time() is False

    def test_ignores_legacy_garmin_uid_field(self):
        with patch("agent.main._load_user_profile", return_value={
            "setup_complete": True,
            "garmin_user_id": "legacy-value",
        }):
            assert _is_first_time() is False


# ─── _format_coach_markdown ────────────────────────────────────────────────

class TestFormatCoachMarkdown:
    def test_keeps_existing_markdown(self):
        md = "## Resumen\n\n| Métrica | Valor |\n|---|---|\n| HRV | 56 |"
        out = _format_coach_markdown(md)
        assert out == md

    def test_wraps_single_plain_line(self):
        out = _format_coach_markdown("Hoy estás recuperado y listo para entrenar.")
        assert out.startswith("## 🧭 Resumen del Coach")
        assert "Hoy estás recuperado" in out

    def test_converts_plain_multiline_to_bullets(self):
        out = _format_coach_markdown("Readiness alta\nBody Battery 82\nSueño sólido")
        assert out.startswith("## 🧭 Resumen del Coach")
        assert "- Readiness alta" in out
        assert "- Body Battery 82" in out
        assert "- Sueño sólido" in out


class TestParsePlanCommand:
    def test_default_help(self):
        action, arg = _parse_plan_command("/plan")
        assert action == "help"
        assert arg is None

    def test_parse_list(self):
        action, arg = _parse_plan_command("/plan listar")
        assert action == "list"
        assert arg is None

    def test_parse_view_with_id(self):
        action, arg = _parse_plan_command("/plan ver plan-123")
        assert action == "view"
        assert arg == "plan-123"

    def test_parse_activate_with_id(self):
        action, arg = _parse_plan_command("/plan activar abc")
        assert action == "activate"
        assert arg == "abc"

    def test_parse_create(self):
        action, arg = _parse_plan_command("/plan crear")
        assert action == "create"
        assert arg is None


# ─── _build_enriched_athlete_knowledge ─────────────────────────────────────

class TestBuildEnrichedAthleteKnowledge:
    def test_includes_profile_and_mcp_sections(self):
        profile = {
            "personal": {"name": "Rafa", "height_cm": 178, "weight_kg": 70.2},
            "goals": {"primary": "trail running", "target_race": "PDA", "target_time": "09:59:00"},
            "health": {"injuries": ["DT1"], "notes": "control glucemia"},
        }
        enrichment = {
            "personal": {"age": 39, "gender": "hombre", "weight_kg": 70.1},
            "startup_48h": {
                "body_battery": {"summary": "hoy=ok · ayer=ok"},
                "hrv": {"summary": "hoy=ok · ayer=no"},
                "sleep": {"summary": "hoy=ok · ayer=ok"},
                "trainings": [{"date": "2026-07-06", "name": "Rodaje suave"}],
            },
        }

        out = _build_enriched_athlete_knowledge(profile, enrichment)

        assert "Base de Conocimiento del Atleta" in out
        assert "Enriquecimiento MCP (arranque)" in out
        assert "Estado de las ultimas 48h" in out
        assert "Rodaje suave" in out
        assert "```json" in out


# ─── _ensure_garmin_credentials ────────────────────────────────────────────

# ─── _ensure_garmin_credentials ────────────────────────────────────────────

class TestEnsureGarminCredentials:
    def test_uses_app_password_directly_sets_env(self):
        """Con email ya guardado, app_password se pone en GARMIN_PASSWORD sin prompt."""
        creds = {"garmin_email": "runner@example.com"}
        captured_env = {}
        with patch("agent.main.update_user_credentials"), \
             patch("agent.main.encrypt_password", return_value="ENC"), \
             patch("agent.main.Prompt.ask") as prompt_mock, \
             patch.dict("os.environ", {}, clear=True):
            _ensure_garmin_credentials(creds, app_password="app-secret")
            # Capturar dentro del contexto donde los env vars están activos
            import os as _os
            captured_env["pw"] = _os.environ.get("GARMIN_PASSWORD")
            captured_env["email"] = _os.environ.get("GARMIN_EMAIL")

        assert captured_env["pw"] == "app-secret"
        assert captured_env["email"] == "runner@example.com"
        assert prompt_mock.call_count == 0

    def test_prompts_email_when_missing(self):
        """Sin email guardado, pide email por prompt y actualiza credenciales."""
        creds = {}
        with patch("agent.main.update_user_credentials") as upd_mock, \
             patch("agent.main.encrypt_password", return_value="ENC"), \
             patch("agent.main.Prompt.ask", return_value="nuevo@example.com") as prompt_mock, \
             patch.dict("os.environ", {}, clear=True):
            out = _ensure_garmin_credentials(creds, app_password="secret")

        assert out["garmin_email"] == "nuevo@example.com"
        prompt_mock.assert_called_once()
        upd_mock.assert_called_once()


# ─── Proveedor de IA del atleta (ia_provider) y clave en .env (26/10) ────────

NVIDIA = {"provider": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1", "model": "nvidia/nemotron-x"}


def _enter_takes_default(answers):
    """Prompt.ask de mentira que, como el real, devuelve el valor por defecto con Enter ("")."""
    it = iter(answers)
    return lambda *a, **k: next(it) or k.get("default", "")


class TestAskIaProvider:
    def test_known_provider_fills_url_and_proposes_model(self):
        with patch("agent.main.Prompt.ask", side_effect=_enter_takes_default(["NVIDIA NIM", ""])) as ask, \
             patch("agent.main.console.print"):
            config = _ask_ia_provider()
        assert config["provider"] == "nvidia" and config["base_url"] == "https://integrate.api.nvidia.com/v1"
        assert config["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
        assert ask.call_count == 2  # proveedor y modelo; la URL no se pregunta

    def test_unknown_provider_asks_url_and_model(self):
        answers = ["Mi Proveedor", "api.ejemplo.com", "https://api.ejemplo.com/v1", "", "modelo-x"]
        with patch("agent.main.Prompt.ask", side_effect=answers), patch("agent.main.console.print") as printed:
            config = _ask_ia_provider()
        assert config == {"provider": "Mi Proveedor", "base_url": "https://api.ejemplo.com/v1", "model": "modelo-x"}
        assert any("http://" in str(c.args[0]) for c in printed.call_args_list)  # URL sin esquema rechazada

    def test_change_keeps_the_current_model_as_default(self):
        with patch("agent.main.Prompt.ask", side_effect=_enter_takes_default(["nvidia", ""])) as ask, \
             patch("agent.main.console.print"):
            config = _ask_ia_provider(NVIDIA)
        assert config["model"] == "nvidia/nemotron-x"
        assert ask.call_args_list[1].kwargs["default"] == "nvidia/nemotron-x"


class TestEnsureIaProvider:
    def _run(self, monkeypatch, stored, *, key=None, asked=None):
        monkeypatch.delenv("PROVIDER_API_KEY", raising=False)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        if key:
            monkeypatch.setenv("PROVIDER_API_KEY", key)
        with patch("agent.main._detect_zscaler", return_value=False), \
             patch("agent.main.get_ia_provider", return_value=stored), \
             patch("agent.main.save_ia_provider") as save, \
             patch("agent.main._ask_ia_provider", return_value=asked or NVIDIA) as ask_provider, \
             patch("agent.main._ask_api_key", new=AsyncMock()) as ask_key, \
             patch("agent.main.console.print"):
            config = asyncio.run(_ensure_ia_provider())
        return config, ask_provider, ask_key, save

    def test_first_time_asks_provider_and_key_and_saves(self, monkeypatch):
        config, ask_provider, ask_key, save = self._run(monkeypatch, None)
        assert config == NVIDIA
        ask_provider.assert_called_once()
        assert ask_key.await_args.args[0] == NVIDIA
        save.assert_called_once_with("nvidia", NVIDIA["base_url"], NVIDIA["model"])

    def test_next_times_ask_nothing(self, monkeypatch):
        config, ask_provider, ask_key, save = self._run(monkeypatch, dict(NVIDIA), key="nvapi-real")
        assert config["provider"] == "nvidia"
        ask_provider.assert_not_called()
        ask_key.assert_not_awaited()
        save.assert_not_called()

    def test_other_computer_without_key_asks_only_the_key(self, monkeypatch):
        config, ask_provider, ask_key, save = self._run(monkeypatch, dict(NVIDIA))
        ask_provider.assert_not_called()
        assert ask_key.await_count == 1
        save.assert_not_called()

    def test_example_value_is_not_a_key(self, monkeypatch):
        _, _, ask_key, _ = self._run(monkeypatch, dict(NVIDIA), key="nvapi-...")
        assert ask_key.await_count == 1


class TestApiKey:
    def test_rejected_key_is_asked_again_and_valid_one_saved(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OTRA=1\nPROVIDER_API_KEY=vieja\n", encoding="utf-8")
        monkeypatch.setattr("agent.main.find_dotenv", lambda usecwd=True: str(env_file))
        probes = iter([("auth", "401 Unauthorized"), ("ok", "")])
        with patch("agent.main.probe_llm_key", new=AsyncMock(side_effect=lambda c: next(probes))), \
             patch("agent.main.Prompt.ask", side_effect=["mala", "buena"]), \
             patch("agent.main.console.print"):
            asyncio.run(_ask_api_key(NVIDIA, "Caducada"))
        assert env_file.read_text(encoding="utf-8").splitlines() == [
            "OTRA=1", "# Proveedor de IA de PROVIDER_API_KEY: NVIDIA NIM (nvidia/nemotron-x)", "PROVIDER_API_KEY=buena"]
        assert os.environ["PROVIDER_API_KEY"] == "buena"
        monkeypatch.delenv("PROVIDER_API_KEY")

    def test_any_provider_uses_provider_api_key_with_a_comment(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        monkeypatch.setattr("agent.main.find_dotenv", lambda usecwd=True: str(env_file))
        config = {"provider": "Mi Proveedor", "base_url": "https://api.ejemplo.com/v1", "model": "m"}
        with patch("agent.main.probe_llm_key", new=AsyncMock(return_value=("error", "proxy"))), \
             patch("agent.main.Prompt.ask", return_value="k"), \
             patch("agent.main.console.print"):
            asyncio.run(_ask_api_key(config, "Primera vez"))
        text = env_file.read_text(encoding="utf-8")
        assert "# Proveedor de IA de PROVIDER_API_KEY: Mi Proveedor (m)\nPROVIDER_API_KEY=k" in text
        monkeypatch.delenv("PROVIDER_API_KEY")

    def test_legacy_key_is_moved_once_without_asking(self, monkeypatch, tmp_path):
        from agent.main import _migrate_legacy_key

        env_file = tmp_path / ".env"
        env_file.write_text("OTRA=1\nNVIDIA_API_KEY=nvapi-real\n", encoding="utf-8")
        monkeypatch.setattr("agent.main.find_dotenv", lambda usecwd=True: str(env_file))
        monkeypatch.delenv("PROVIDER_API_KEY", raising=False)
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-real")
        with patch("agent.main.console.print"):
            _migrate_legacy_key(NVIDIA)
            _migrate_legacy_key(NVIDIA)
        lines = env_file.read_text(encoding="utf-8").splitlines()
        assert lines == ["OTRA=1", "# Proveedor de IA de PROVIDER_API_KEY: NVIDIA NIM (nvidia/nemotron-x)",
                         "PROVIDER_API_KEY=nvapi-real"]
        assert os.environ["PROVIDER_API_KEY"] == "nvapi-real" and "NVIDIA_API_KEY" not in os.environ
        monkeypatch.delenv("PROVIDER_API_KEY")


# ─── Acceso con Supabase Auth (supabase/README.md) ─────────────────────────

class TestAuthenticateWithSupabaseAuth:
    def test_saved_session_logs_in_without_password(self):
        from agent import main as m

        row = {"id": "u1", "credentials": {"garmin_password_encrypted": "enc"}}
        with patch.object(m, "restore_session", return_value=row), \
             patch.object(m, "decrypt_password", return_value="garmin-pw"), \
             patch.object(m, "sign_in") as sign_in, \
             patch.object(m.Prompt, "ask", side_effect=["runner@example.com"]) as ask, \
             patch.object(m.console, "print"):
            user, creds, is_new, password = m._authenticate_or_register_user()

        assert (user, is_new, password) == ("runner@example.com", False, "garmin-pw")
        assert ask.call_count == 1
        sign_in.assert_not_called()

    def test_first_access_creates_account_and_asks_to_confirm_email(self):
        from agent import main as m

        linked = {"id": "legacy", "credentials": {"garmin_email": "runner@example.com"}}
        sign_in_results = [
            {"ok": False, "reason": "invalid", "error": "Email o contraseña incorrectos"},
            {"ok": True, "app_user": linked, "error": None},
        ]
        with patch.object(m, "restore_session", return_value=None), \
             patch.object(m, "sign_in", side_effect=sign_in_results), \
             patch.object(m, "sign_up", return_value={"ok": True, "needs_confirmation": True}) as sign_up, \
             patch.object(m, "encrypt_password", return_value="enc"), \
             patch.object(m, "update_user_credentials") as update_creds, \
             patch.object(m.Confirm, "ask", return_value=True), \
             patch.object(m.Prompt, "ask", side_effect=[
                 "runner@example.com", "pw123456", "pw123456",   # primer intento: crear cuenta
                 "runner@example.com", "pw123456",               # tras confirmar el email
             ]), \
             patch.object(m.console, "print"):
            user, creds, is_new, password = m._authenticate_or_register_user()

        sign_up.assert_called_once_with("runner@example.com", "pw123456")
        assert (user, is_new, password) == ("runner@example.com", False, "pw123456")
        assert update_creds.call_args.args[0]["garmin_password_encrypted"] == "enc"

    def test_new_account_without_data_creates_kairos_user(self):
        from agent import main as m

        with patch.object(m, "restore_session", return_value=None), \
             patch.object(m, "sign_in", return_value={"ok": True, "app_user": None, "error": None}), \
             patch.object(m, "encrypt_password", return_value="enc"), \
             patch.object(m, "create_app_user") as create, \
             patch.object(m.Prompt, "ask", side_effect=["new@example.com", "pw123456", "new@example.com"]), \
             patch.object(m.console, "print"):
            user, creds, is_new, password = m._authenticate_or_register_user()

        assert is_new is True and user == "new@example.com"
        create.assert_called_once()
        assert create.call_args.kwargs["credentials"] == {"garmin_email": "new@example.com", "garmin_password_encrypted": "enc"}
