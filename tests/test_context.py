"""
tests/test_context.py
Prompts y contexto del LLM: ficheros de prompts, reglas, perfil, diabetes, base de conocimiento y memoria de sesiones (agent/prompts.py, context.py, knowledge.py, startup.py).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


from agent.trainer_agent import (
    TrainerAgent,
)
from tests.agent_patch import patch_everywhere
from agent.prompts import PROMPTS_DIR, _PROMPT_MARKER, _load_coaching_rules, _load_system_prompt, _render_prompt
from agent.knowledge import _build_athlete_knowledge_context, _load_athlete_knowledge_chunks, _resolve_kb_paths, _retrieve_athlete_knowledge
import agent.data_access as _data_access


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



class TestPromptFiles:
    """Los textos para el LLM viven en prompts/ (23/10); el código solo los compone."""

    def _prompt_names(self):
        pass

        return sorted(
            p.relative_to(PROMPTS_DIR).with_suffix("").as_posix()
            for p in PROMPTS_DIR.rglob("*.md")
            if p.name not in {"system_prompt.md", "coaching_rules.md"}
        )

    def test_every_prompt_renders_with_all_markers_resolved(self):
        pass

        names = self._prompt_names()
        assert len(names) >= 19
        for name in names:
            keys = set(_PROMPT_MARKER.findall((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")))
            out = _render_prompt(name, **{k: f"<{k}>" for k in keys})
            assert "{{" not in out and "}}" not in out, name
            assert out == out.strip("\n"), name

    def test_render_rejects_missing_or_extra_values(self):
        pass

        with pytest.raises(ValueError, match="marcadores"):
            _render_prompt("coaching/user", pregunta="x", ruta="y", horizonte="z")
        with pytest.raises(ValueError, match="marcadores"):
            _render_prompt("coaching/brief", sobra="x")

    def test_every_prompt_file_is_used_by_the_code(self):
        from pathlib import Path

        import agent.trainer_agent as ta

        # Todo agent/: al dividir trainer_agent.py (28/10) los prompts se usan desde varios módulos.
        src = "\n".join(p.read_text(encoding="utf-8") for p in Path(ta.__file__).parent.rglob("*.py"))
        unused = [n for n in self._prompt_names() if f'"{n}"' not in src]
        assert unused == [], f"Prompts sin usar: {unused}"


class TestCoachingRulesSingleSource:
    def test_system_prompt_embeds_coaching_rules_once(self):
        pass

        prompt = _load_system_prompt()
        rules = _load_coaching_rules()
        assert "{{REGLAS_DE_COACHING}}" not in prompt
        assert prompt.count(rules) == 1
        # Reglas de entrenador que viven solo en coaching_rules.md
        for heading in ("## Diabetes", "## Lesiones, sobrecargas y molestias a lo largo de la temporada",
                        "## Intensidades", "## Estado de hoy", "# Límites éticos"):
            assert heading in rules
        # El protocolo de diabetes va con el perfil, solo a quien lo necesita (26/10).
        assert "120–180 mg/dL" not in rules and "hipoglucemia" not in rules.lower()


class TestDiabetesProtocolInContext:
    """Atleta sano, con otra patología, con DT1 o con DT2 (26/10): protocolo según patologías y medicación."""

    def _block(self, health):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.user_profile = {"personal": {"name": "Ana"}, "health": health}
        return agent._profile_context_block()

    def test_healthy_athlete_with_an_injury_is_still_healthy(self):
        block = self._block({"pathologies": [], "injuries": ["periostitis"], "notes": ""})
        assert "- Patologías: ninguna declarada (atleta sano)" in block and "- Lesiones: periostitis" in block
        assert "Protocolo de diabetes" not in block and "gluc" not in block.lower() and "- Diabetes:" not in block

    def test_other_pathology_is_left_to_the_llm(self):
        block = self._block({"pathologies": ["asma"], "injuries": []})
        assert "- Patologías: asma" in block
        assert "Protocolo de diabetes" not in block and "atleta sano" not in block

    def test_diabetes_and_injury_together(self):
        block = self._block({"pathologies": ["DT1"], "diabetes_medication": ["bomba_insulina"], "injuries": ["periostitis"]})
        assert "- Patologías: DT1" in block and "- Lesiones: periostitis" in block
        assert "Protocolo de diabetes con riesgo de hipoglucemia" in block

    def test_old_profile_with_diabetes_in_injuries_is_not_healthy(self):
        block = self._block({"injuries": ["DT1"]})
        assert "atleta sano" not in block and "Protocolo de diabetes con riesgo de hipoglucemia" in block

    def test_dt1_gets_hypoglycemia_protocol_with_its_medication(self):
        block = self._block({"injuries": ["DT1"], "diabetes_medication": ["insulina_basal", "insulina_rapida"]})
        assert "- Diabetes: Diabetes tipo 1 · Insulina lenta (basal), Insulina rápida (bolos)" in block
        assert "Protocolo de diabetes con riesgo de hipoglucemia" in block and "120–180 mg/dL" in block

    def test_dt2_with_metformin_gets_its_own_protocol(self):
        block = self._block({"injuries": ["diabetes tipo 2"], "diabetes_medication": ["metformina"]})
        assert "- Diabetes: Diabetes tipo 2 · Metformina" in block
        assert "Protocolo de diabetes con dieta o fármacos" in block and "120–180 mg/dL" not in block
        # Ni siquiera nombra la hipoglucemia: el LLM la repetía para tranquilizar (prueba en vivo del 26/10).
        assert "hipoglu" not in block.lower()

    def test_dt2_with_insulin_gets_hypoglycemia_protocol(self):
        block = self._block({"injuries": ["DT2"], "diabetes_medication": ["metformina", "insulina_basal"]})
        assert "Protocolo de diabetes con riesgo de hipoglucemia" in block


class TestSystemPromptDateFormatRules:
    def test_full_prompt_requires_global_spanish_date_format(self):
        prompt = _load_system_prompt()
        assert "Formato de fecha obligatorio (España)" in prompt
        assert "Regla global de fechas (OBLIGATORIA)" in prompt
        assert "Regla de rangos explícitos (OBLIGATORIA)" in prompt
        assert "DD/MM/AAAA" in prompt
        assert "YYYY-MM-DD" in prompt


class TestSystemPromptPlanStatusRules:
    def test_full_prompt_includes_plan_status_intent_variants(self):
        prompt = _load_system_prompt()
        assert "Consulta de estado del plan (OBLIGATORIO)" in prompt
        assert "que plan llevo esta semana?" in prompt
        assert "sigo con el plan?" in prompt
        assert "goals" in prompt and "training_plan" in prompt


class TestSystemPromptPlanManagementRules:
    def test_full_prompt_includes_functional_plan_management(self):
        prompt = _load_system_prompt()
        assert "Generacion y manejo funcional de planes (OBLIGATORIO)" in prompt
        assert "/plan crear" in prompt
        assert "/plan listar" in prompt
        assert "/plan ver <plan_id>" in prompt
        assert "/plan activar <plan_id>" in prompt
        assert "No afirmes que un plan quedó guardado/activado" in prompt


class TestPreviousSessionContext:
    """De la sesión anterior solo llegan al LLM las preguntas, nunca las respuestas (cifras viejas)."""

    def _agent(self, history):
        agent = TrainerAgent.__new__(TrainerAgent)
        agent.mcp_session = MagicMock()
        agent.conversation_history = []
        agent.knowledge_chunks = []
        agent._build_system_prompt = lambda: "SYSTEM"
        return agent, {"history": history}

    def test_restores_only_user_questions_and_no_old_answers(self, monkeypatch):
        agent, ctx = self._agent([
            {"role": "user", "content": "¿Cómo ha evolucionado mi HRV?"},
            {"role": "assistant", "content": "No hay datos de HRV (0 días con datos)."},
        ])
        patch_everywhere(monkeypatch, "_load_session_context", lambda: ctx)
        agent._restore_previous_session()
        assert agent.previous_session_questions == ("¿Cómo ha evolucionado mi HRV?",)
        assert agent.conversation_history == []

        messages = agent._build_messages("¿Y hoy?")
        text = "\n".join(m["content"] for m in messages)
        assert "¿Cómo ha evolucionado mi HRV?" in text
        assert "0 días con datos" not in text
        assert [m["role"] for m in messages] == ["system", "system", "user"]

    def test_no_note_without_previous_session(self):
        agent, _ = self._agent([])
        assert [m["role"] for m in agent._build_messages("hola")] == ["system", "user"]

    def test_battery_neither_reads_nor_writes_the_athlete_memory(self, monkeypatch):
        # 26/10: las preguntas de la batería llenaban la memoria del atleta y el LLM las recibía como suyas.
        import agent.trainer_agent as ta
        monkeypatch.setenv("KAIROS_SESSION_MEMORY", "false")
        load, save, persist = MagicMock(), MagicMock(), MagicMock()
        patch_everywhere(monkeypatch, "_load_session_context", load)
        monkeypatch.setattr(ta._storage, "save_history_entry", save)
        monkeypatch.setattr(ta._storage, "persist_session_summary_daily", persist)
        agent, _ = self._agent([])
        agent.conversation_history = [{"role": "user", "content": "¿Dime los TSS de mis 20 últimas actividades?"}]

        agent._restore_previous_session()
        _data_access._save_history_entry("user", "¿Dime los TSS?")
        agent.save_session_summary_checkpoint()

        assert agent.previous_session_questions == ()
        load.assert_not_called()
        save.assert_not_called()
        persist.assert_not_called()


class TestAthleteKnowledgeRag:
    def test_resolve_kb_paths_uses_defaults_when_env_empty(self, tmp_path: Path):
        paths = _resolve_kb_paths("", project_root=tmp_path)
        assert len(paths) >= 3
        assert str(paths[0]).startswith(str(tmp_path))

    def test_load_knowledge_chunks_from_txt_and_json(self, tmp_path: Path):
        txt_file = tmp_path / "athlete_notes.txt"
        txt_file.write_text("Objetivo: bajar de 10h en la PDA.\nFuerza de sóleo 2 veces/semana.", encoding="utf-8")

        json_file = tmp_path / "athlete_profile.json"
        json_file.write_text(
            json.dumps({"nutrition": {"during_long_run": "60-90g CH/h"}, "injuries": ["soleo"]}, ensure_ascii=False),
            encoding="utf-8",
        )

        env_paths = f"{txt_file},{json_file}"
        chunks, sources = _load_athlete_knowledge_chunks(env_paths, project_root=tmp_path, chunk_size=120)

        assert chunks, "Debe generar chunks desde archivos válidos"
        assert "athlete_notes.txt" in sources
        assert "athlete_profile.json" in sources
        joined = "\n".join(c["text"] for c in chunks)
        assert "bajar de 10h" in joined
        assert "during_long_run" in joined

    def test_retrieve_returns_most_relevant_chunks(self):
        chunks = [
            {"source": "a.txt", "text": "Trabajo de umbral y VO2max para 10K."},
            {"source": "b.txt", "text": "Diabetes tipo 1: controlar glucemia antes de tiradas largas."},
            {"source": "c.txt", "text": "Series en cuesta y técnica de bajada con bastones."},
        ]
        out = _retrieve_athlete_knowledge("Tengo diabetes y haré tirada larga", chunks, top_k=2)
        assert out
        assert out[0]["source"] == "b.txt"

    def test_build_knowledge_context_includes_header_and_source(self):
        chunks = [{"source": "kb.md", "text": "Objetivo principal: PDA sub10h."}]
        ctx = _build_athlete_knowledge_context("objetivo PDA", chunks)
        assert "Base de Conocimiento del atleta" in ctx
        assert "Fuente: kb.md" in ctx
        assert "PDA sub10h" in ctx


class TestSessionSummaryCheckpoint:
    def test_generate_session_summary_checkpoint_uses_recent_turns(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.conversation_history = [
            {"role": "user", "content": "Cuánto TSS llevo esta semana?"},
            {"role": "assistant", "content": "TSS acumulado: 260.4"},
            {"role": "user", "content": "Cómo está mi HRV hoy?"},
            {"role": "assistant", "content": "HRV 73 ms, balanced."},
        ]

        summary = TrainerAgent.generate_session_summary_checkpoint(agent)
        assert "Temas recientes:" in summary
        assert "Cómo está mi HRV hoy?" in summary
        # Las respuestas no se guardan: sus cifras caducan.
        assert "Última respuesta:" not in summary
        assert "260.4" not in summary
        assert "73 ms" not in summary

    def test_summary_checkpoint_empty_without_user_questions(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.conversation_history = [{"role": "assistant", "content": "HRV 73 ms, balanced."}]

        assert TrainerAgent.generate_session_summary_checkpoint(agent) == ""

    def test_save_session_summary_checkpoint_calls_daily_upsert(self):
        from agent.trainer_agent import (
            TrainerAgent,
        )

        agent = object.__new__(TrainerAgent)
        agent.conversation_history = [
            {"role": "user", "content": "Mi FTP?"},
            {"role": "assistant", "content": "Tu FTP es 205W."},
        ]

        with patch("agent.trainer_agent._storage.persist_session_summary_daily") as upsert_mock:
            TrainerAgent.save_session_summary_checkpoint(agent)

        upsert_mock.assert_called_once()
