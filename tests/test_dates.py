"""
tests/test_dates.py
Fechas escritas por el usuario y formatos de duración (agent/dates.py).
"""

from datetime import date, timedelta



from tests.agent_patch import patch_everywhere
from agent.dates import _extract_iso_date_from_text, _extract_iso_date_range_from_text, _normalize_date_args, _seconds_to_hhmmss


# ─── _seconds_to_hhmmss ───────────────────────────────────────────────────────
from tests.agent_fixtures import _no_baseline_in_db  # noqa: F401 — autouse: sin activity_tss ni semilla reales



class TestSecondsToHhmmss:
    def test_below_one_hour_returns_mmss(self):
        assert _seconds_to_hhmmss(90) == "00:01:30"

    def test_zero_returns_mmss(self):
        assert _seconds_to_hhmmss(0) == "00:00:00"

    def test_sub_minute(self):
        assert _seconds_to_hhmmss(45) == "00:00:45"

    def test_exactly_one_hour(self):
        assert _seconds_to_hhmmss(3600) == "01:00:00"

    def test_above_one_hour(self):
        assert _seconds_to_hhmmss(5400) == "01:30:00"


class TestNormalizeDateArgs:
    def test_hoy(self):
        today = date.today().isoformat()
        assert _normalize_date_args({"date": "hoy"})["date"] == today

    def test_today_english(self):
        today = date.today().isoformat()
        assert _normalize_date_args({"startDate": "today"})["startDate"] == today

    def test_ayer(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert _normalize_date_args({"date": "ayer"})["date"] == yesterday

    def test_yesterday_english(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert _normalize_date_args({"endDate": "yesterday"})["endDate"] == yesterday

    def test_iso_passthrough(self):
        result = _normalize_date_args({"date": "2026-06-01"})
        assert result["date"] == "2026-06-01"

    def test_dd_mm_yyyy_converted_to_iso(self):
        result = _normalize_date_args({"start_date": "27/07/2026", "end_date": "02/08/2026"})
        assert result["start_date"] == "2026-07-27"
        assert result["end_date"] == "2026-08-02"

    def test_dd_mm_without_year_converted_to_iso(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        result = _normalize_date_args({"date": "27/07"})
        assert result["date"] == "2026-07-27"

    def test_non_date_field_never_replaced(self):
        # "activityId" no está en DATE_FIELDS → aunque el valor sea "hoy" no se toca
        result = _normalize_date_args({"activityId": "hoy"})
        assert result["activityId"] == "hoy"

    def test_keyword_case_insensitive(self):
        today = date.today().isoformat()
        result = _normalize_date_args({"date": "HOY"})
        assert result["date"] == today

    def test_empty_dict(self):
        assert _normalize_date_args({}) == {}

    def test_multiple_fields(self):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        result = _normalize_date_args({
            "startDate": "hoy",
            "endDate":   "ayer",
            "activityId": 123,
        })
        assert result["startDate"] == today
        assert result["endDate"]   == yesterday
        assert result["activityId"] == 123


class TestExtractIsoDateFromText:
    def test_extracts_iso(self):
        assert _extract_iso_date_from_text("2026-07-02") == "2026-07-02"

    def test_extracts_spanish_month_with_year(self):
        assert _extract_iso_date_from_text("2 de julio de 2026") == "2026-07-02"

    def test_extracts_dd_mm_yyyy(self):
        assert _extract_iso_date_from_text("02/07/2026") == "2026-07-02"

    def test_extracts_dd_mm_yy_short_year(self):
        assert _extract_iso_date_from_text("17/08/26") == "2026-08-17"

    def test_extracts_dd_mm_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        assert _extract_iso_date_from_text("27/07") == "2026-07-27"


class TestExtractIsoDateRangeFromText:
    def test_extracts_del_al_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        assert _extract_iso_date_range_from_text("semana del 27/07 al 02/08") == ("2026-07-27", "2026-08-02")

    def test_extracts_entre_y_iso(self):
        assert _extract_iso_date_range_from_text("entre 2026-07-27 y 2026-08-02") == ("2026-07-27", "2026-08-02")

    def test_extracts_entre_el_y_el_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        assert _extract_iso_date_range_from_text("entre el 25/08 y el 30/08") == ("2026-08-25", "2026-08-30")

    def test_extracts_desde_hasta_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 9, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        assert _extract_iso_date_range_from_text("desde 25/08 hasta 30/08") == ("2026-08-25", "2026-08-30")

    def test_extracts_cross_year_without_year(self, monkeypatch):
        from datetime import date as _Date

        class _FakeDate(_Date):
            @classmethod
            def today(cls):
                return cls(2026, 12, 1)

        patch_everywhere(monkeypatch, "date", _FakeDate)
        assert _extract_iso_date_range_from_text("del 27/12 al 03/01") == ("2026-12-27", "2027-01-03")
