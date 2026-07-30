from tools.fit_tss_probe import _interval_likelihood


def test_interval_likelihood_high_when_repeated_work_rest_pattern():
    details = {
        "transitions": 21,
        "cv_if": 0.24,
        "work_blocks": 6,
        "rest_blocks": 5,
        "if_mean": 0.82,
    }
    likelihood, _ = _interval_likelihood(details)
    assert likelihood == "high"


def test_interval_likelihood_medium_for_mixed_session():
    details = {
        "transitions": 12,
        "cv_if": 0.12,
        "work_blocks": 2,
        "rest_blocks": 2,
        "if_mean": 0.79,
    }
    likelihood, _ = _interval_likelihood(details)
    assert likelihood == "medium"


def test_interval_likelihood_low_for_steady_run():
    details = {
        "transitions": 9,
        "cv_if": 0.06,
        "work_blocks": 1,
        "rest_blocks": 1,
        "if_mean": 0.78,
    }
    likelihood, _ = _interval_likelihood(details)
    assert likelihood == "low"
