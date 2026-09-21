from __future__ import annotations

import pytest
from garminconnect.exceptions import GarminConnectAuthenticationError
from garminconnect.exceptions import GarminConnectConnectionError
from garminconnect.exceptions import GarminConnectTooManyRequestsError

from agent import kairos_mcp_server as server


class _FakeGarmin:
    def get_activities_by_date(self, start: str, end: str):
        return [
            {"startTimeLocal": f"{start}T06:00:00", "trainingLoad": 10.5},
            {"startTimeLocal": f"{start}T18:00:00", "activityTrainingLoad": 5.0},
            {"startTimeLocal": f"{end}T07:00:00", "tss": 20.0},
        ]

    def get_sleep_data(self, day: str):
        return {
            "dailySleepDTO": {
                "sleepTimeSeconds": 25200,
                "sleepScore": 82,
                "deepSleepSeconds": 3600,
                "lightSleepSeconds": 14400,
                "remSleepSeconds": 7200,
                "awakeSleepSeconds": 1000,
            }
        }

    def get_activity(self, activity_id: int):
        return {
            "activityId": activity_id,
            "aerobicTrainingEffect": 3.2,
            "anaerobicTrainingEffect": 1.1,
            "trainingEffectLabel": "IMPROVING",
        }

    def get_personal_record(self):
        return [{"record_type": "Fastest 5K", "value": "17:48"}]


@pytest.fixture(autouse=True)
def _reset_client_cache_state():
    prev_client = server._client
    prev_identity = server._client_identity
    server._client = None
    server._client_identity = None
    try:
        yield
    finally:
        server._client = prev_client
        server._client_identity = prev_identity


class _ClientWithPersonalRecord:
    def __init__(self, response: object):
        self.response = response

    def get_personal_record(self):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response



def test_training_load_trend_aggregates_per_day(monkeypatch):
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server._training_load_trend({"start_date": "2026-09-01", "end_date": "2026-09-02"})

    assert out["start_date"] == "2026-09-01"
    assert out["end_date"] == "2026-09-02"
    assert out["days_with_data"] == 2
    assert out["trend"][0]["trainingLoad"] == 15.5
    assert out["trend"][1]["trainingLoad"] == 20.0


def test_training_effect_reads_activity_fields(monkeypatch):
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server._training_effect({"activity_id": 12345})

    assert out["activity_id"] == 12345
    assert out["aerobicTrainingEffect"] == 3.2
    assert out["anaerobicTrainingEffect"] == 1.1


def test_training_effect_requires_activity_id(monkeypatch):
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server._training_effect({})

    assert "activity_id requerido" in out["message"]


def test_sleep_summary_extracts_main_fields(monkeypatch):
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server.get_sleep_summary("2026-09-02")

    assert out["date"] == "2026-09-02"
    assert out["sleepTimeSeconds"] == 25200
    assert out["sleepScore"] == 82


def test_dispatch_passthrough_personal_record(monkeypatch):
    monkeypatch.setattr(server, "_garmin_client", lambda: _FakeGarmin())

    out = server._dispatch("get_personal_record", {})

    assert isinstance(out, list)
    assert out[0]["record_type"] == "Fastest 5K"


def test_retry_once_on_authentication_error_then_success(monkeypatch):
    first = _ClientWithPersonalRecord(GarminConnectAuthenticationError("expired session"))
    second = _ClientWithPersonalRecord([{"ok": True}])
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return first if calls["count"] == 1 else second

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    out = server._dispatch("get_personal_record", {})

    assert out == [{"ok": True}]
    assert calls["count"] == 2


def test_retry_once_on_connection_error_401_then_success(monkeypatch):
    first = _ClientWithPersonalRecord(GarminConnectConnectionError("API Error 401: Unauthorized"))
    second = _ClientWithPersonalRecord([{"ok": "recovered"}])
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return first if calls["count"] == 1 else second

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    out = server._dispatch("get_personal_record", {})

    assert out == [{"ok": "recovered"}]
    assert calls["count"] == 2


def test_retry_once_on_connection_error_403_then_success(monkeypatch):
    first = _ClientWithPersonalRecord(GarminConnectConnectionError("API Error 403: Forbidden"))
    second = _ClientWithPersonalRecord([{"ok": "recovered-403"}])
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return first if calls["count"] == 1 else second

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    out = server._dispatch("get_personal_record", {})

    assert out == [{"ok": "recovered-403"}]
    assert calls["count"] == 2


def test_connection_error_500_does_not_retry_or_invalidate_cache(monkeypatch):
    cached_client = _ClientWithPersonalRecord(GarminConnectConnectionError("API Error 500: Server exploded"))
    server._client = cached_client
    server._client_identity = ("user@example.com", "secret")
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return cached_client

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    with pytest.raises(GarminConnectConnectionError, match="API Error 500"):
        server._dispatch("get_personal_record", {})

    assert calls["count"] == 1
    assert server._client is cached_client
    assert server._client_identity == ("user@example.com", "secret")


def test_connection_error_429_does_not_retry_or_invalidate_cache(monkeypatch):
    cached_client = _ClientWithPersonalRecord(GarminConnectConnectionError("API Error 429: Rate limit"))
    server._client = cached_client
    server._client_identity = ("user@example.com", "secret")
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return cached_client

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    with pytest.raises(GarminConnectConnectionError, match="API Error 429"):
        server._dispatch("get_personal_record", {})

    assert calls["count"] == 1
    assert server._client is cached_client
    assert server._client_identity == ("user@example.com", "secret")


def test_too_many_requests_direct_does_not_retry_or_invalidate_cache(monkeypatch):
    cached_client = _ClientWithPersonalRecord(GarminConnectTooManyRequestsError("429 direct"))
    server._client = cached_client
    server._client_identity = ("user@example.com", "secret")
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return cached_client

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    with pytest.raises(GarminConnectTooManyRequestsError, match="429 direct"):
        server._dispatch("get_personal_record", {})

    assert calls["count"] == 1
    assert server._client is cached_client
    assert server._client_identity == ("user@example.com", "secret")


def test_successful_passthrough_keeps_previous_behavior(monkeypatch):
    cached_client = _ClientWithPersonalRecord([{"record_type": "Fastest 10K", "value": "37:10"}])
    server._client = cached_client
    server._client_identity = ("user@example.com", "secret")
    calls = {"count": 0}

    def fake_factory():
        calls["count"] += 1
        return cached_client

    monkeypatch.setattr(server, "_garmin_client", fake_factory)

    out = server._dispatch("get_personal_record", {})

    assert out == [{"record_type": "Fastest 10K", "value": "37:10"}]
    assert calls["count"] == 1
    assert server._client is cached_client
    assert server._client_identity == ("user@example.com", "secret")
