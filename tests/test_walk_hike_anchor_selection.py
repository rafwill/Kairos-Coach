from __future__ import annotations

import agent.load_metrics as lm


def test_walk_hike_anchor_prefers_long_candidate_over_short_higher_norm(monkeypatch):
    activities = [
        {
            "type": "hiking",
            "id": 1,
            "_activity_details_raw": "short",
        },
        {
            "type": "hiking",
            "id": 2,
            "_activity_details_raw": "long",
        },
    ]

    def _fake_obs(details_raw: str):
        if details_raw == "short":
            # Higher normalized speed but only 40 min moving.
            return {"moving_seconds": 40 * 60.0, "normalized_equiv_speed_m_min": 130.0}
        if details_raw == "long":
            # Lower normalized speed but 100 min moving.
            return {"moving_seconds": 100 * 60.0, "normalized_equiv_speed_m_min": 110.0}
        return None

    monkeypatch.setattr(lm, "_compute_walk_hike_metabolic_observables", _fake_obs)

    threshold = lm._derive_walk_hike_threshold_speed_m_min_from_activities(activities)

    assert threshold is not None
    assert abs(threshold - 110.0) < 1e-9


def test_walk_hike_anchor_fallback_logs_when_no_long_candidate(monkeypatch, caplog):
    activities = [
        {
            "type": "hiking",
            "id": 99,
            "_activity_details_raw": "short-only",
        }
    ]

    def _fake_obs(details_raw: str):
        if details_raw == "short-only":
            return {"moving_seconds": 45 * 60.0, "normalized_equiv_speed_m_min": 123.0}
        return None

    monkeypatch.setattr(lm, "_compute_walk_hike_metabolic_observables", _fake_obs)

    with caplog.at_level("WARNING"):
        threshold = lm._derive_walk_hike_threshold_speed_m_min_from_activities(activities)

    assert threshold is not None
    assert abs(threshold - 123.0) < 1e-9
    assert any("walk_hike_anchor: selected fallback candidate below minimum moving time" in rec.message for rec in caplog.records)
