"""
tests/test_diabetes.py
Diabetes del atleta (agent/diabetes.py, 26/10): se detecta en las patologías y el protocolo sale de la medicación.
"""

from agent import diabetes


class TestDetectType:
    def test_cases(self):
        cases = [
            ({"injuries": ["DT1", "tendinitis"]}, "dt1"),
            ({"injuries": ["Diabetes Tipo I"]}, "dt1"),
            ({"notes": "diabetes tipo 1 con bomba de insulina"}, "dt1"),
            ({"injuries": ["DT2"]}, "dt2"),
            ({"injuries": ["diabetes tipo II"]}, "dt2"),
            ({"injuries": ["diabetes 2"]}, "dt2"),
            ({"injuries": ["LADA"]}, "otra"),
            ({"injuries": ["diabetes"]}, "otra"),
            ({"injuries": ["prediabetes"]}, None),
            ({"injuries": ["asma", "tendinitis aquílea"]}, None),
            ({"pathologies": ["asma", "DT2"], "injuries": ["periostitis"]}, "dt2"),
            ({"pathologies": [], "injuries": ["periostitis"]}, None),
            ({}, None),
            (None, None),
        ]
        for health, expected in cases:
            assert diabetes.detect_type(health) == expected, health

    def test_is_diabetes_per_item(self):
        assert [diabetes.is_diabetes(x) for x in ("DT 1", "periostitis", "prediabetes")] == [True, False, False]


class TestProtocol:
    def test_healthy_or_other_pathology_has_none(self):
        assert diabetes.protocol({}) is None
        assert diabetes.protocol({"injuries": ["asma"], "diabetes_medication": ["insulina_basal"]}) is None

    def test_dt1_always_has_hypoglycemia_protocol(self):
        assert diabetes.protocol({"injuries": ["DT1"]}) == "insulina"
        assert diabetes.protocol({"injuries": ["DT1"], "diabetes_medication": []}) == "insulina"

    def test_dt2_by_medication(self):
        cases = [
            (["metformina"], "sin_insulina"),
            (["metformina", "isglt2"], "sin_insulina"),
            ([], "sin_insulina"),
            (["metformina", "insulina_basal"], "insulina"),
            (["sulfonilureas"], "insulina"),
            (["glinidas"], "insulina"),
            (["Insulina NPH"], "insulina"),  # texto libre
        ]
        for meds, expected in cases:
            assert diabetes.protocol({"injuries": ["DT2"], "diabetes_medication": meds}) == expected, meds

    def test_dt2_without_medication_answer_uses_the_text(self):
        assert diabetes.protocol({"injuries": ["DT2"]}) == "sin_insulina"
        assert diabetes.protocol({"injuries": ["DT2"], "notes": "con insulina"}) == "insulina"


def test_label():
    assert diabetes.label({"injuries": ["asma"]}) == ""
    assert diabetes.label({"injuries": ["DT1"]}) == "Diabetes tipo 1 · medicación sin indicar"
    assert diabetes.label({"injuries": ["DT1"], "diabetes_medication": ["insulina_basal", "insulina_rapida"]}) == (
        "Diabetes tipo 1 · Insulina lenta (basal), Insulina rápida (bolos)")
    assert diabetes.label({"injuries": ["DT2"], "diabetes_medication": []}) == "Diabetes tipo 2 · sin medicación (solo dieta)"
    assert diabetes.label({"injuries": ["DT2"], "diabetes_medication": ["Ozempic"]}) == "Diabetes tipo 2 · Ozempic"
