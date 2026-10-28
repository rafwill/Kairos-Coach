"""
tests/test_tool_results.py
Resultados de las tools de Garmin para el LLM: poda, series y récords (agent/tool_results.py).
"""

import json



from agent.tool_results import _compact_personal_records, _compact_tool_result, _flatten_garmin_activity, _strip_garmin_object
from agent.routes.records import _build_personal_records_markdown
from agent.load_metrics import _try_parse_json


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



class TestConcatenatedJsonFromMcp:
    # Formato real del MCP para get_personal_record: objetos JSON seguidos, sin lista.
    RAW = (
        '{\n  "typeId": 3,\n  "activityName": "Carrera 5K",\n  "activityType": "running",\n  "value": 1659.0\n}\n'
        '{\n  "typeId": 5,\n  "activityName": "Media",\n  "activityType": "running",\n  "value": 5483.0\n}'
    )

    def test_try_parse_json_reads_concatenated_objects_as_list(self):
        pass

        data = _try_parse_json(self.RAW)
        assert isinstance(data, list) and [d["typeId"] for d in data] == [3, 5]

    def test_personal_records_route_renders_concatenated_payload(self):
        compact = _compact_tool_result(self.RAW, "get_personal_record")
        out = _build_personal_records_markdown(compact, preferred_sport="running")
        assert "| 5K | 27:39 |" in out
        assert "| Medio Maratón | 01:31:23 |" in out

    def test_flatten_garmin_activity_lifts_summary_dto(self):
        pass

        act = {"activityName": "X", "activityTypeDTO": {"typeKey": "trail_running"},
               "summaryDTO": {"duration": 3600.0, "distance": 10000.0, "averageHR": 140}}
        flat = _flatten_garmin_activity(act)
        assert (flat["duration"], flat["distance"], flat["averageHR"], flat["activityType"]) == (3600.0, 10000.0, 140, "trail_running")


class TestStripGarminObject:
    def test_removes_strip_fields(self):
        obj = {
            "startTimeGMT": "2026-06-17T08:00:00",
            "distance": 10000,
        }
        result = _strip_garmin_object(obj)
        assert "startTimeGMT" not in result
        assert result["distance"] == 10000

    def test_activity_id_NOT_stripped(self):
        """activityId debe llegar al LLM para que pueda llamar get_activity después de get_activities."""
        obj = {"activityId": "abc123", "distance": 5000}
        result = _strip_garmin_object(obj)
        assert "activityId" in result, "activityId no debe estar en _GARMIN_STRIP_FIELDS"

    def test_removes_image_url_keys(self):
        obj = {"profileImageUrl": "http://cdn.example.com/img.png", "steps": 8000}
        result = _strip_garmin_object(obj)
        assert "profileImageUrl" not in result
        assert result["steps"] == 8000

    def test_simplifies_activity_type_dict(self):
        obj = {"activityType": {"typeKey": "running", "sortOrder": 1}}
        result = _strip_garmin_object(obj)
        assert result["activityType"] == "running"

    def test_simplifies_event_type_dict(self):
        obj = {"eventType": {"typeKey": "race", "sortOrder": 2}}
        result = _strip_garmin_object(obj)
        assert result["eventType"] == "race"

    def test_nested_strip(self):
        obj = {"heartRate": {"avg": 155, "userProfileId": 999}}
        result = _strip_garmin_object(obj)
        assert "userProfileId" not in result["heartRate"]
        assert result["heartRate"]["avg"] == 155

    def test_list_truncated_to_4(self):
        lst = [{"v": i} for i in range(10)]
        result = _strip_garmin_object(lst)
        assert len(result) == 4

    def test_scalar_passthrough(self):
        assert _strip_garmin_object(42)      == 42
        assert _strip_garmin_object("hello") == "hello"
        assert _strip_garmin_object(None)    is None

    def test_empty_values_removed(self):
        obj = {"distance": 5000, "nothing": {}, "empty_list": []}
        result = _strip_garmin_object(obj)
        assert "nothing"    not in result
        assert "empty_list" not in result
        assert result["distance"] == 5000


class TestCompactToolResult:
    def test_none_returns_sin_datos(self):
        assert _compact_tool_result(None) == "(sin datos)"

    def test_empty_string_returns_sin_datos(self):
        assert _compact_tool_result("") == "(sin datos)"

    def test_list_truncated_to_8(self):
        raw = json.dumps([{"v": i} for i in range(20)])
        result = _compact_tool_result(raw)
        assert len(json.loads(result)) == 8

    def test_non_json_passthrough(self):
        assert _compact_tool_result("respuesta de texto plano") == "respuesta de texto plano"

    def test_long_string_truncated(self):
        long_str = "x" * 5000
        result = _compact_tool_result(long_str)
        assert result.endswith("...(truncado)")
        assert len(result) <= 3015  # _MAX_TOOL_RESULT_CHARS + sufijo

    def test_long_json_truncated(self):
        # JSON válido pero muy grande
        raw = json.dumps([{"data": "a" * 500} for _ in range(20)])
        result = _compact_tool_result(raw)
        assert result.endswith("...(truncado)")

    def test_personal_records_dispatched(self):
        data = [{"typeId": 3, "value": 1200.0, "activityName": "5K race", "activityType": "running"}]
        result = _compact_tool_result(json.dumps(data), tool_name="get_personal_records")
        assert "20:00" in result  # 1200 s = 20 min
        assert "5K"    in result

    def test_personal_record_singular_dispatched(self):
        data = [{"typeId": 3, "value": 1200.0, "activityName": "5K race", "activityType": "running"}]
        result = _compact_tool_result(json.dumps(data), tool_name="get_personal_record")
        assert "20:00" in result
        assert "5K" in result

    def test_dict_json_stripped(self):
        data = {"activityId": "abc", "startTimeGMT": "2026-01-01T07:00:00", "distance": 10000}
        result = _compact_tool_result(json.dumps(data))
        result_dict = json.loads(result)
        assert "startTimeGMT" not in result_dict
        assert result_dict["distance"] == 10000

    def test_get_activity_adds_normalized_fields(self):
        data = {"activityId": 123, "duration": 36612.18359375, "distance": 54428.41015625}
        result = _compact_tool_result(json.dumps(data), tool_name="get_activity")
        result_dict = json.loads(result)
        assert result_dict["duration_hhmmss"] == "10:10:12"
        assert result_dict["distance_km"] == 54.43

    def test_get_activity_without_distance_does_not_raise_unboundlocal(self):
        data = {
            "activityId": 123,
            "activityType": "running",
            "duration": 3600,
            "avgPower": 250,
            "maxPower": 410,
        }
        result = _compact_tool_result(json.dumps(data), tool_name="get_activity")
        result_dict = json.loads(result)
        assert result_dict["duration_hhmmss"] == "01:00:00"

    def test_get_activity_without_duration_does_not_raise_unboundlocal(self):
        data = {
            "activityId": 456,
            "activityType": "running",
            "distance": 10000,
            "avgHr": 150,
            "maxHr": 178,
        }
        result = _compact_tool_result(json.dumps(data), tool_name="get_activity")
        result_dict = json.loads(result)
        assert result_dict["distance_km"] == 10.0

    def test_get_body_battery_prefers_item_with_level_when_multiple_entries(self):
        data = [
            {"date": "2026-10-06", "charged": 58, "drained": 31},
            {"date": "2026-10-06", "bodyBatteryLevel": 69, "charged": 58, "drained": 31},
        ]
        result = _compact_tool_result(json.dumps(data), tool_name="get_body_battery")
        parsed = json.loads(result)
        assert parsed.get("level") == 69
        assert parsed.get("charged") == 58
        assert parsed.get("drained") == 31

    def test_get_hrv_data_reads_nested_hrv_summary(self):
        # Forma real de Garmin: los valores van en hrvSummary; antes caía a la poda genérica.
        data = {"userProfilePk": 1, "hrvReadings": [{"hrvValue": 70}] * 50,
                "hrvSummary": {"weeklyAvg": 61, "lastNightAvg": 56, "status": "BALANCED"}}
        parsed = json.loads(_compact_tool_result(json.dumps(data), tool_name="get_hrv_data"))
        assert parsed == {"lastNightAvg": 56, "weeklyAvg": 61, "status": "BALANCED"}

    def test_hrv_trend_keeps_every_day_of_the_series(self):
        # La poda genérica dejaba 4 de 15 días y el LLM inventaba el resto.
        rows = [{"date": f"2026-10-{d:02d}", "hrv": None if d == 9 else 60 + d % 5, "weekly_avg": 61,
                 "status": "BALANCED"} for d in range(8, 23)]
        data = {"start_date": "2026-10-08", "end_date": "2026-10-22", "days_with_data": 14, "trend": rows}
        parsed = json.loads(_compact_tool_result(json.dumps(data), tool_name="get_hrv_trend"))
        assert parsed["columns"] == ["date", "hrv", "weekly_avg", "status"]
        assert len(parsed["rows"]) == 15
        assert parsed["rows"][1] == ["2026-10-09", None, 61, "BALANCED"]
        assert parsed["days_with_data"] == 14
        assert "omitted_oldest_days" not in parsed
        # Resumen determinista para que el LLM no cuente ni promedie.
        assert parsed["rows_total"] == 15
        hrv = parsed["summary"]["hrv"]
        values = [r["hrv"] for r in rows if r["hrv"] is not None]
        assert hrv["n"] == 14 and hrv["min"] == min(values) and hrv["max"] == max(values)
        assert hrv["mean"] == round(sum(values) / 14, 1)
        assert hrv["first"] == {"date": "2026-10-08", "value": rows[0]["hrv"]}
        assert hrv["last"] == {"date": "2026-10-22", "value": rows[-1]["hrv"]}
        assert parsed["summary"]["status"] == {"n": 15, "counts": {"BALANCED": 15}}
        assert "date" not in parsed["summary"]

    def test_long_daily_series_drops_oldest_days_and_says_so(self):
        rows = [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "trainingLoad": 123.45} for i in range(200)]
        data = {"start_date": "x", "end_date": "y", "days_with_data": 200, "trend": rows}
        compact = _compact_tool_result(json.dumps(data), tool_name="get_training_load_trend")
        parsed = json.loads(compact)
        assert len(compact) <= 3000
        assert parsed["omitted_oldest_days"] + len(parsed["rows"]) == 200
        assert parsed["rows"][-1] == [rows[-1]["date"], 123.45]


class TestCompactPersonalRecords:
    def test_5k_time_conversion(self):
        data = [{"typeId": 3, "value": 1200.0, "activityName": "5K", "activityType": "running"}]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["tiempo"] == "20:00"
        assert result[0]["tipo"]   == "5K"

    def test_half_marathon_time(self):
        data = [{"typeId": 5, "value": 5400.0, "activityName": "HM", "activityType": "running"}]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["tiempo"] == "01:30:00"

    def test_marathon_time(self):
        # 4 horas = 14400 s
        data = [{"typeId": 6, "value": 14400.0, "activityName": "Marathon", "activityType": "running"}]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["tiempo"] == "04:00:00"

    def test_longest_run_km(self):
        data = [{"typeId": 7, "value": 42195.0, "activityName": "Long Run", "activityType": "running"}]
        result = json.loads(_compact_personal_records(data))
        assert "42.20 km" in result[0]["distancia"]

    def test_swim_short_returns_metres(self):
        # 400 m de natación → metros
        data = [{"typeId": 17, "value": 400.0, "activityName": "Swim", "activityType": "pool_swimming"}]
        result = json.loads(_compact_personal_records(data))
        assert "400 m" in result[0]["distancia"]

    def test_swim_long_returns_km(self):
        data = [{"typeId": 17, "value": 3800.0, "activityName": "Open water", "activityType": "open_water"}]
        result = json.loads(_compact_personal_records(data))
        assert "3.80 km" in result[0]["distancia"]

    def test_unknown_type_id(self):
        data = [{"typeId": 999, "value": 100, "activityName": "X", "activityType": "running"}]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["tipo"] == "typeId=999"

    def test_skips_non_dict_entries(self):
        data = [
            {"typeId": 3, "value": 900.0, "activityName": "5K", "activityType": "running"},
            "bad_entry",
            None,
        ]
        result = json.loads(_compact_personal_records(data))
        assert len(result) == 1

    def test_daily_steps(self):
        data = [{"typeId": 12, "value": 25432, "activityName": "Day", "activityType": "steps"}]
        result = json.loads(_compact_personal_records(data))
        assert "pasos" in result[0]

    def test_supports_snake_case_payload_shape(self):
        data = [
            {
                "record_type": "Fastest 10K",
                "type_id": 4,
                "value": "35:53",
                "raw_value": 2153.6,
            }
        ]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["categoria"] == "10K"
        assert result[0]["valor"] == "35:53"

    def test_translates_unknown_record_type_to_spanish(self):
        data = [
            {
                "record_type": "Longest Ride",
                "value": "199.02 km",
            }
        ]
        result = json.loads(_compact_personal_records(data))
        assert result[0]["categoria"] == "Ciclismo más largo"
