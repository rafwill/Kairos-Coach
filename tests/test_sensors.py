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

    def test_glucose_and_tss_ranges_are_ignored(self):
        text = "- Glucemia objetivo 120–180 mg/dL.\n- TSS 150–200 en la semana."
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

    def test_check_coaching_text_combines_s10_to_s12(self):
        text = "Tu CTL 50 (máximo habitual 70).\n- Series Z4–Z5 (160–175 lpm).\n- Ritmo umbral 4:12/km."
        assert _ids(sensors.check_coaching_text(text, ZONES, ZONES_BIKE, 240)) == ["S12", "S10", "S11"]


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
