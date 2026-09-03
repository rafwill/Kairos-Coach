from __future__ import annotations

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
