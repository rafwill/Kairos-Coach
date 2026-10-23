"""
tests/test_sensors.py
Sensores computacionales sobre el texto del LLM (agent/sensors.py, docs/Harness.md §5).
"""

from agent import sensors

ZONES = [("Z1 Recuperación", 115, 134), ("Z2 Aeróbica", 135, 145), ("Z3 Tempo", 146, 157),
         ("Z4 Subumbral", 158, 168), ("Z5 Supraumbral", 169, 179)]
ZONES_BIKE = [("Z1 Recuperación", 105, 124), ("Z2 Aeróbica", 125, 135), ("Z3 Tempo", 136, 145),
              ("Z4 Subumbral", 146, 158), ("Z5 Supraumbral", 159, 179)]


def _ids(findings):
    return [f.sensor for f in findings]


class TestHrRanges:
    def test_range_inside_one_zone_passes(self):
        assert sensors.find_hr_ranges_outside_zones("- Rodaje Z2 (135–145 lpm).", ZONES) == []

    def test_continuous_range_across_zones_fails_with_hint(self):
        out = sensors.find_hr_ranges_outside_zones("- Rodaje Z2 alto / Z3 bajo (140–152 lpm).", ZONES)
        assert _ids(out) == ["S10"]
        assert "140–152" in out[0].message
        assert "Z2 135–145" in out[0].hint

    def test_easy_run_in_z1_z2_passes(self):
        assert sensors.find_hr_ranges_outside_zones("- Rodaje suave Z1–Z2 (115–145 lpm).", ZONES) == []

    def test_progressive_segment_passes(self):
        text = "- Calentamiento progresivo de Z1 a Z2, 115→145 lpm."
        assert sensors.find_hr_ranges_outside_zones(text, ZONES) == []

    def test_high_zones_span_fails(self):
        assert _ids(sensors.find_hr_ranges_outside_zones("- Series Z4–Z5 (160–175 lpm).", ZONES)) == ["S10"]

    def test_cycling_line_uses_cycling_zones(self):
        text = "- Bici suave Z1 (FC ciclista 105–124 lpm)."
        assert sensors.find_hr_ranges_outside_zones(text, ZONES, ZONES_BIKE) == []
        assert _ids(sensors.find_hr_ranges_outside_zones(text, ZONES)) == ["S10"]

    def test_sport_is_decided_per_clause_not_per_table_row(self):
        # Batería del 23/10: una fila con el rodaje y una alternativa en bici; el rodaje llevaba zonas de bici.
        row = ("| Parte principal | Rodaje 35–45 min en Z1–Z2 (105–135 lpm). Si notas tirantez, "
               "cambia a 30 min bici en Z1–Z2 (FC ciclismo 105–135 lpm). |")
        out = sensors.find_hr_ranges_outside_zones(row, ZONES, ZONES_BIKE)
        assert len(out) == 1 and "105–135" in out[0].message
        ok = ("| Parte principal | Rodaje 35–45 min en Z1–Z2 (115–145 lpm). Si notas tirantez, "
              "30 min bici en Z1–Z2 (105–135 lpm). |")
        assert sensors.find_hr_ranges_outside_zones(ok, ZONES, ZONES_BIKE) == []

    def test_glucose_and_tss_ranges_are_ignored(self):
        text = ("- Glucemia objetivo 120–180 mg/dL.\n- TSS 150–200 en la semana.\n"
                "- Durante: sesión < 60 min en Z1–Z2 → agua 150–250 ml/30 min.")
        assert sensors.find_hr_ranges_outside_zones(text, ZONES) == []


class TestOtherSensors:
    def test_threshold_pace(self):
        assert sensors.find_wrong_threshold_pace("Bloques a ritmo umbral 4:00/km.", 240) == []
        out = sensors.find_wrong_threshold_pace("Tempo cerca del ritmo umbral 4:12/km.", 240)
        assert _ids(out) == ["S11"]
        assert "4:00" in out[0].hint

    def test_ctl_with_atl_max(self):
        assert _ids(sensors.find_ctl_with_atl_max("Tu CTL 50 (máximo habitual 70).")) == ["S12"]
        assert sensors.find_ctl_with_atl_max("Tu CTL 50; ATL 60 (máximo habitual 70).") == []

    def test_iso_dates(self):
        assert _ids(sensors.find_iso_dates("El 2026-10-23 corriste.")) == ["G3"]
        assert sensors.find_iso_dates("El 23/10/2026 corriste.") == []

    def test_empty_data_marker_but_honest_absence_passes(self):
        assert _ids(sensors.find_empty_data_lines("- Body Battery: datos disponibles")) == ["G4"]
        assert sensors.find_empty_data_lines("- HRV: Sin datos disponibles para esta fecha.") == []
        assert sensors.find_empty_data_lines("- Sueño: no hay datos disponibles hoy") == []
        assert sensors.find_empty_data_lines("- Body Battery: 54 (datos disponibles)") == []

    def test_invented_user_turn(self):
        assert _ids(sensors.find_invented_user_turn("Respuesta.\nUsuario: vale")) == ["G5"]
        coaching = "¿Quieres que te lo prepare?elijo entrenar mañana"
        assert _ids(sensors.find_invented_user_turn("Respuesta.", coaching)) == ["G5"]
        assert sensors.find_invented_user_turn("Respuesta.", "¿Quieres que te lo prepare?") == []

    def test_kairos_notice_is_not_judged(self):
        text = ("- Rodaje Z2 (135–145 lpm).\n\n> ⚠️ Comprobación de Kairos: revisa esto, no cuadra con tus datos:\n"
                "> - rango 150–250 lpm no cabe en ninguna zona")
        assert sensors.check_coaching_text(text, ZONES, ZONES_BIKE, 240) == []

    def test_check_coaching_text_combines_s10_to_s12(self):
        text = "Tu CTL 50 (máximo habitual 70).\n- Series Z4–Z5 (160–175 lpm).\n- Ritmo umbral 4:12/km."
        assert _ids(sensors.check_coaching_text(text, ZONES, ZONES_BIKE, 240)) == ["S12", "S10", "S11"]


class TestLoadFiguresS14:
    REF = "Estado físico (CTL) 60.4 · Fatiga (ATL) 38.9 · Forma (TSB) +21.7"

    def test_invented_figures_fail(self):
        out = sensors.find_unsupported_load_figures("Tu CTL es 55 y tu TSB -12.", self.REF)
        assert [f.message for f in out] == ["LLM: CTL 55 no aparece en los datos", "LLM: TSB -12 no aparece en los datos"]

    def test_rounded_and_labelled_figures_pass(self):
        text = "CTL 60 · ATL 39 · TSB +22 (+36 % del CTL). CTL (Estado físico) 60.4"
        assert sensors.find_unsupported_load_figures(text, self.REF) == []

    def test_conditional_threshold_is_not_a_citation(self):
        # Batería del 23/10: "si ... ATL supera 55 → dos días suaves" es un umbral, no una cifra del atleta.
        text = "- Spike semanal: si TSB baja de +10 o ATL supera 55 → dos días suaves."
        assert sensors.find_unsupported_load_figures(text, self.REF) == []

    def test_percentages_do_not_cut_numbers(self):
        # "+46 %" no se lee como "+4" (falso positivo de la batería del 23/10).
        assert sensors.find_unsupported_load_figures("Forma del CTL). El spike de TSS (+46 % vs)", self.REF) == []


class TestDecisionS15:
    def test_high_intensity_against_soft_decision_fails(self):
        out = sensors.find_intensity_against_decision("- Principal: 4x4 min en Z4 (158–168 lpm).", "easy", ZONES)
        assert [f.sensor for f in out] == ["S15"]
        assert "sesión suave" in out[0].hint

    def test_maintain_allows_quality(self):
        assert sensors.find_intensity_against_decision("- Principal: 4x4 min en Z4 (158–168 lpm).", "maintain", ZONES) == []

    def test_conditionals_hydration_and_cadence_are_ignored(self):
        text = ("- Si la FC pasa de Z4, para.\n- Hidratación: 400–600 ml agua.\n"
                "- Cadencia ~175–180 pasos.\n- Rodaje Z2 (135–145 lpm).")
        assert sensors.find_intensity_against_decision(text, "rest", ZONES) == []


class TestGlucoseNoteS16:
    def test_note_added_when_session_without_glucose(self):
        out = sensors.add_glucose_note("- Rodaje suave 40 min en Z2.", True)
        assert out.endswith(sensors.GLUCOSE_NOTE)

    def test_no_note_if_glucose_mentioned_or_no_diabetes_or_no_session(self):
        assert sensors.add_glucose_note("- Rodaje 40 min; mide la glucemia antes.", True) == "- Rodaje 40 min; mide la glucemia antes."
        assert sensors.add_glucose_note("- Rodaje suave 40 min.", False) == "- Rodaje suave 40 min."
        assert sensors.add_glucose_note("- Tu HRV está estable.", True) == "- Tu HRV está estable."

    def test_insulin_diabetes_markers(self):
        assert sensors.has_insulin_diabetes(["DT1 desde 2010"])
        assert sensors.has_insulin_diabetes(["", "diabetes tipo 1 con bomba de insulina"])
        assert not sensors.has_insulin_diabetes(["tendinitis aquílea"])


def test_sensors_module_does_not_import_the_agent():
    # agent/sensors.py lo usan el agente y las herramientas: no puede depender de trainer_agent.
    import ast
    from pathlib import Path

    tree = ast.parse(Path(sensors.__file__).read_text(encoding="utf-8"))
    imported = {
        (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    }
    assert not any(m.startswith("agent") for m in imported), imported
