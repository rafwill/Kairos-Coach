from __future__ import annotations

import agent.load_metrics as lm


def _veq(speed_m_min: float, grade: float) -> float:
    speed_mps = speed_m_min / 60.0
    vo2, src = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, grade)
    return lm._walk_equivalent_flat_speed_from_vo2_m_min(vo2, src, speed_mps=speed_mps, grade=grade)


def test_walk_hike_model_has_no_blend_transition():
    speed_mps = 80.0 / 60.0
    _, src_low = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, 0.02)
    _, src_high = lm._walk_vo2_from_speed_grade_mlkgmin(speed_mps, 0.04)

    assert src_low == "acsm"
    assert src_high == "minetti"


def test_walk_hike_equivalent_speed_monotonic_uphill():
    # Uphill monotonicity: for fixed speed, equivalent flat speed must not decrease
    # as positive grade increases.
    grades = [idx * 0.005 for idx in range(0, 91)]  # 0.0 .. 0.45
    values = [_veq(80.0, g) for g in grades]

    for i in range(1, len(values)):
        assert values[i] + 1e-9 >= values[i - 1], (
            f"non-monotonic at g={grades[i - 1]:.3f}->{grades[i]:.3f}: "
            f"{values[i - 1]:.3f}->{values[i]:.3f}"
        )
