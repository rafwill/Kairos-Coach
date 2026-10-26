"""
tests/test_sensors.py
Sensores computacionales sobre el texto del LLM (agent/sensors.py, docs/Harness.md §5).
"""

from datetime import date

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

    def test_progressive_mark_after_the_range_passes(self):
        # Batería del 26/10, pregunta 9: la marca va dentro del paréntesis, detrás del rango.
        text = "| Mié 28 | Calidad | Principal: 3×2000 m Z3–Z4 (146–168 lpm, progresivo de Z3 a Z4) / RPE 6–7 |"
        assert sensors.find_hr_ranges_outside_zones(text, ZONES) == []

    def test_progressive_mark_in_a_later_sentence_does_not_count(self):
        text = "- Series Z4–Z5 (160–175 lpm). Enfriamiento progresivo de Z2 a Z1."
        assert _ids(sensors.find_hr_ranges_outside_zones(text, ZONES)) == ["S10"]
        text = "- Series Z4–Z5 (160–175 lpm); vuelta progresiva de Z4 a Z5."
        assert _ids(sensors.find_hr_ranges_outside_zones(text, ZONES)) == ["S10"]

    def test_progressive_mark_after_with_wrong_zones_fails(self):
        text = "- Bloque (146–168 lpm, progresivo de Z2 a Z3)."
        assert _ids(sensors.find_hr_ranges_outside_zones(text, ZONES)) == ["S10"]

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

    def test_measured_ranges_and_sport_headings(self):
        # Exploración del 24/10: un rango descriptivo y una tabla bajo el encabezado "Cycling".
        assert sensors.find_hr_ranges_outside_zones("- Tu FC se estabilizó en Z4–Z5 (159–178 bpm).", ZONES, ZONES_BIKE) == []
        table = "Cycling (LTHR 153 bpm)\n\n| Zona | Rango |\n| 1 | 105–124 |\n| 5 | 159–179 |"
        assert sensors.find_hr_ranges_outside_zones(table, ZONES, ZONES_BIKE) == []
        # Una viñeta de bici no convierte en bici la siguiente.
        bullets = "- Bici suave Z1 (FC ciclista 105–124 lpm).\n- Calent. progresivo de Z1 a Z2, 115→145 lpm."
        assert sensors.find_hr_ranges_outside_zones(bullets, ZONES, ZONES_BIKE) == []

    def test_zones_table_row_keeps_its_sport(self):
        # Exploración del 24/10: tabla de zonas con la fila de ciclismo; cada celda perdía el deporte.
        row = "| Cycling | 153 lpm | 105–124 | 125–135 | 136–145 | 146–158 | 159–179 |"
        assert sensors.find_hr_ranges_outside_zones(row, ZONES, ZONES_BIKE) == []
        assert sensors.find_hr_ranges_outside_zones("| Running | 169 lpm | 115–134 | 135–145 |", ZONES, ZONES_BIKE) == []

    def test_glucose_and_tss_ranges_are_ignored(self):
        text = ("- Glucemia objetivo 120–180 mg/dL.\n- TSS 150–200 en la semana.\n"
                "- Durante: sesión < 60 min en Z1–Z2 → agua 150–250 ml/30 min.")
        assert sensors.find_hr_ranges_outside_zones(text, ZONES) == []


class TestOtherSensors:
    def test_goal_pace_near_threshold_is_not_the_threshold(self):
        # Exploración del 24/10: el 3:55 era el ritmo meta de la media, no el umbral.
        assert sensors.find_wrong_threshold_pace("mejorar la economía y el umbral (ritmo meta ≈ 3:55 min/km)", 240) == []
        assert sensors.find_wrong_threshold_pace("necesitas bajar el umbral al menos a 3:55/km", 240) == []

    def test_threshold_pace(self):
        assert sensors.find_wrong_threshold_pace("Bloques a ritmo umbral 4:00/km.", 240) == []
        out = sensors.find_wrong_threshold_pace("Tempo cerca del ritmo umbral 4:12/km.", 240)
        assert _ids(out) == ["S11"]
        assert "4:00" in out[0].hint

    def test_zone_names_are_not_the_threshold_pace(self):
        # 26/10: "Supraumbral" contiene "umbral" y la cifra de la zona se leía como ritmo umbral.
        assert sensors.find_wrong_threshold_pace("Z5 Supraumbral (≥ 169 lpm) o ritmo 3:30–3:40 min/km", 240) == []
        assert sensors.find_wrong_threshold_pace("Z4 Subumbral: 4:05/km", 240) == []
        assert _ids(sensors.find_wrong_threshold_pace("Umbral: 3:30/km", 240)) == ["S11"]

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
        out = sensors.add_glucose_note("- Calentamiento 10 min.\n- Parte principal: rodaje suave 40 min en Z2.", True)
        assert out.endswith(sensors.GLUCOSE_NOTE)
        assert sensors.add_glucose_note("Te propongo 40 min en Z2 mañana.", True).endswith(sensors.GLUCOSE_NOTE)

    def test_no_note_on_activity_lists_or_zone_tables(self):
        # Exploración del 24/10: la nota salía en "¿cuántos km he corrido?" y en la tabla de zonas.
        listing = "- 22/10 – 11,72 km (series)\n- 21/10 – 14,46 km (rodaje)"
        assert sensors.add_glucose_note(listing, True) == listing
        zones = "- Rodajes suaves / aeróbicos: Z1–Z2 (techo 145 lpm)."
        assert sensors.add_glucose_note(zones, True) == zones

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


class TestHrvDeviationS25:
    def test_real_case_of_24_10_is_caught(self):
        line = "- HRV: anoche 58 ms (media 7 d 59 ms, estado UNBALANCED) · noche anterior 63 ms → > 10 % por debajo de su media"
        found = sensors.find_wrong_hrv_deviation(line)
        assert [f.sensor for f in found] == ["S25"]
        assert "-1.7 %" in found[0].hint

    def test_table_row_from_the_tool_loop_is_caught(self):
        assert sensors.find_wrong_hrv_deviation("| HRV noche | 58 ms | Media 7d: 59 ms | 🔴 UNBALANCED (>10 % por debajo) |")

    def test_true_claims_and_alert_thresholds_pass(self):
        ok = [
            "HRV anoche 52 ms (media 7 d 59 ms): un 12 % por debajo de tu media.",
            "HRV 52 ms frente a una media de 59 ms: > 10 % por debajo.",
            # El orden no siempre es medida → media: 60 frente a 67 es −10,4 %.
            "La HRV está > 10 % por debajo de su media habitual (60 ms vs. ~67 ms de referencia)",
            # Umbrales de alerta: una sola cifra o línea condicional.
            "- HRV > 10 % por debajo de tu media semanal (≈ < 53 ms) dos mañanas seguidas.",
            "- Si la HRV de mañana (58 ms hoy, media 59 ms) baja > 10 % por debajo, descansa.",
            "HRV (22/10) 56,5 ms (base ~58 ms) -2,6 % → no supera el -10 % de umbral",
        ]
        for line in ok:
            assert sensors.find_wrong_hrv_deviation(line) == [], line

    def test_wrong_direction_is_caught(self):
        assert sensors.find_wrong_hrv_deviation("HRV 70 ms (media 59 ms): un 12 % por debajo de lo normal")


class TestTssLabelS26:
    REF = "- martes 20/10: 58.1 (rTSS 58.1 · hrTSS 0.0 · TSS 0.0)\n- viernes 23/10: Fuerza · hrTSS 36.6"

    def test_running_tss_called_hrtss_is_caught(self):
        found = sensors.find_swapped_tss_label("El rodaje del martes sumó 58.1 hrTSS.", self.REF)
        assert [f.sensor for f in found] == ["S26"] and "rTSS" in found[0].hint

    def test_same_label_plain_tss_or_unlabelled_reference_pass(self):
        assert sensors.find_swapped_tss_label("Rodaje: rTSS 58.1; fuerza: 36.6 hrTSS.", self.REF) == []
        assert sensors.find_swapped_tss_label("El martes hiciste 58.1 TSS.", self.REF) == []
        assert sensors.find_swapped_tss_label("Trail: 120.4 hrTSS.", "TSS 120.4 (actividad)") == []
        assert sensors.find_swapped_tss_label("58.1 hrTSS", "") == []

    def test_live_retry_includes_both(self):
        text = "HRV anoche 58 ms (media 59 ms) → > 10 % por debajo. Rodaje: 58.1 hrTSS."
        found = sensors.findings_to_retry(text, [], None, None, reference=self.REF)
        assert {f.sensor for f in found} == {"S25", "S26"}


class TestRelativeDayS27:
    SAT = date(2026, 10, 24)  # sábado: mañana es domingo 25/10, pasado mañana lunes 26/10

    def test_real_cases_of_24_10_are_caught(self):
        found = sensors.find_wrong_relative_day("Mañana 26/10 (lunes) → SESIÓN SUAVE", self.SAT)
        assert [f.sensor for f in found] == ["S27"] and "domingo 25/10" in found[0].hint
        assert sensors.find_wrong_relative_day("✅ Recomendación para mañana lunes 26/10", self.SAT)
        assert sensors.find_wrong_relative_day("1 Mañana lunes: mide HRV y glucemia al despertar.", self.SAT)

    def test_right_days_pass(self):
        ok = [
            "Pasado mañana (lunes 26/10): rodaje suave.",
            "Mañana domingo 25/10 descanso; hoy sábado 24/10 fuerza.",
            "Ayer viernes 23/10 hiciste 11.7 km. Anteayer 22/10, series.",
            "Hoy 24/10/2026: estado fresco. hoy=24/10/2026",
            # Solo la palabra, o "mañana" como parte del día: no se juzga.
            "Mañana, si la HRV baja, descansa.",
            "Entrena por la mañana del 26/10 o cada mañana 10'.",
            "Esta mañana 24/10 la HRV fue 58 ms.",
        ]
        for text in ok:
            assert sensors.find_wrong_relative_day(text, self.SAT) == [], text

    def test_without_date_nothing_is_judged(self):
        assert sensors.find_wrong_relative_day("Mañana lunes 26/10", None) == []

    def test_pain_score_is_not_a_date(self):
        # 26/10 en vivo: "te duele desde ayer (4/10)" daba S27 con la fecha 04/10.
        for text in ("El sóleo te duele desde ayer (4/10) al bajar escaleras.",
                     "Molestia de hoy: 3/10.", "RPE de hoy 7/10."):
            assert sensors.find_wrong_relative_day(text, self.SAT) == [], text
        # Sin contexto de dolor o escala, sigue siendo una fecha.
        assert sensors.find_wrong_relative_day("Ayer (4/10) hiciste series.", self.SAT)

    def test_live_retry_includes_it(self):
        found = sensors.findings_to_retry("Mañana lunes 26/10: rodaje.", [], None, None, today=self.SAT)
        assert [f.sensor for f in found] == ["S27"]


class TestWeekdayDateS28:
    MON = date(2026, 10, 26)  # lunes

    def test_real_cases_are_caught(self):
        # 26/10: semanas desplazadas un día en una respuesta libre y "domingo 2/11" (es lunes).
        text = "• Esta semana (lunes 20/10 – domingo 26/10): 151 795 pasos."
        found = sensors.find_wrong_weekday_date(text, self.MON)
        assert [f.sensor for f in found] == ["S28", "S28"]
        assert "martes" in found[0].message and "lunes" in found[1].message
        assert sensors.find_wrong_weekday_date("¿Programo la tirada larga el domingo 2/11?", self.MON)
        assert sensors.find_wrong_weekday_date("lo que hiciste el domingo (20/10)", self.MON)
        assert sensors.find_wrong_weekday_date("Rodaje el 20/10 (lunes).", self.MON)

    def test_right_days_and_non_dates_pass(self):
        ok = [
            "Semana del lunes 19/10 al domingo 25/10; hoy lunes, 26/10/2026.",
            "El 25/10 (domingo) hiciste 2 h en Z2.",
            "Domingo día 1/11: tirada larga.",
            "El lunes te duele 4/10.",          # puntuación, no fecha
            "Martes: series. 20/10 fue el test.",  # el día no va pegado a la fecha
        ]
        for text in ok:
            assert sensors.find_wrong_weekday_date(text, self.MON) == [], text
        assert sensors.find_wrong_weekday_date("domingo 20/10", None) == []

    def test_live_retry_includes_it(self):
        found = sensors.findings_to_retry("Tirada larga el domingo 2/11.", [], None, None, today=self.MON)
        assert [f.sensor for f in found] == ["S28"]
