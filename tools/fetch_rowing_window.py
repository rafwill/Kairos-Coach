from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.mcp_client import call_tool, garmin_mcp_session


ROWING_KEYWORDS = (
    "rowing",
    "indoor_row",
    "indoor rowing",
    "rower",
    "erg",
    "ergometer",
    "remo",
)


def _safe_json_loads(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_activities_payload(raw: str) -> list[dict]:
    payload = _safe_json_loads(raw)
    rows = _as_activity_list(payload)
    if rows:
        return rows

    # Garmin MCP sometimes returns concatenated JSON objects instead of a JSON list.
    # Reuse production parser to support both formats.
    try:
        from agent.trainer_agent import _parse_activities_response  # lazy import

        parsed, _has_more, _next_start = _parse_activities_response(raw)
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)]
    except Exception:
        return []
    return []


def _as_activity_list(payload) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("activities", "activityList", "results", "list", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _get_activity_id(activity: dict) -> int | None:
    for key in ("activityId", "activity_id", "id"):
        value = activity.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except Exception:
            continue
    return None


def _get_activity_type_text(activity: dict) -> str:
    value = activity.get("type") or activity.get("activityType") or activity.get("typeKey")
    if isinstance(value, dict):
        value = value.get("typeKey") or value.get("key") or value.get("name")
    return str(value or "").strip().lower()


def _get_name_text(activity: dict) -> str:
    return str(activity.get("activityName") or activity.get("name") or "").strip().lower()


def _get_date_text(activity: dict) -> str:
    raw = (
        activity.get("startDateLocal")
        or activity.get("startTimeLocal")
        or activity.get("start_time_local")
        or activity.get("start_time")
        or ""
    )
    return str(raw)[:10]


def _is_rowing_activity(activity: dict) -> bool:
    text = f"{_get_activity_type_text(activity)} {_get_name_text(activity)}"
    return any(keyword in text for keyword in ROWING_KEYWORDS)


def _date_range(start_date: str, end_date: str) -> list[str]:
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    days = []
    cur = start_dt
    while cur <= end_dt:
        days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


async def _fetch_activities_for_window(start_date: str, end_date: str) -> list[dict]:
    rows_by_id: dict[int, dict] = {}
    async with garmin_mcp_session(essential_only=False) as session:
        # Primary call by date window
        raw = await call_tool(session, "get_activities_by_date", {"startDate": start_date, "endDate": end_date})
        activities = _parse_activities_payload(raw)

        # Fallback day-by-day when the range endpoint is sparse/non-standard
        if not activities:
            for day in _date_range(start_date, end_date):
                raw_day = await call_tool(session, "get_activities_fordate", {"date": day})
                day_activities = _parse_activities_payload(raw_day)
                for act in day_activities:
                    aid = _get_activity_id(act)
                    if aid is not None and aid not in rows_by_id:
                        rows_by_id[aid] = act
            activities = list(rows_by_id.values())

        # Final fallback: paginate full history and filter by date window.
        # Useful when date endpoints return empty despite valid historical records.
        if not activities:
            page_start = 0
            page_size = 100
            max_pages = 80
            for _ in range(max_pages):
                raw_page = await call_tool(
                    session,
                    "get_activities",
                    {"start": str(page_start), "limit": str(page_size)},
                )
                page_activities = _parse_activities_payload(raw_page)
                if not page_activities:
                    break

                oldest_date_in_page = "9999-12-31"
                for act in page_activities:
                    act_date = _get_date_text(act)
                    if act_date and act_date < oldest_date_in_page:
                        oldest_date_in_page = act_date
                    if start_date <= act_date <= end_date:
                        aid = _get_activity_id(act)
                        if aid is not None and aid not in rows_by_id:
                            rows_by_id[aid] = act

                if oldest_date_in_page and oldest_date_in_page < start_date and rows_by_id:
                    break
                if len(page_activities) < page_size:
                    break
                page_start += len(page_activities)

            activities = list(rows_by_id.values())

        for act in activities:
            aid = _get_activity_id(act)
            if aid is not None and aid not in rows_by_id:
                rows_by_id[aid] = act

        # Expand rowing activities with detail endpoint for richer comparison data
        rowing_ids = [aid for aid, act in rows_by_id.items() if _is_rowing_activity(act)]
        details: dict[int, dict] = {}
        for aid in rowing_ids:
            raw_detail = await call_tool(session, "get_activity", {"activityId": aid})
            payload_detail = _safe_json_loads(raw_detail)
            if isinstance(payload_detail, dict):
                details[aid] = payload_detail

    merged: list[dict] = []
    for aid, act in rows_by_id.items():
        row = {
            "activity_id": aid,
            "date": _get_date_text(act),
            "start_local": str(act.get("startDateLocal") or act.get("startTimeLocal") or act.get("start_time") or ""),
            "name": act.get("activityName") or act.get("name") or "",
            "type": _get_activity_type_text(act),
            "duration_s": act.get("duration") or act.get("durationInSeconds") or "",
            "distance_m": act.get("distance") or act.get("distanceInMeters") or "",
            "avg_hr": act.get("averageHR") or act.get("averageHr") or "",
            "max_hr": act.get("maxHR") or act.get("maxHr") or "",
            "is_rowing": _is_rowing_activity(act),
        }
        detail = details.get(aid)
        if detail:
            row["detail_available"] = True
            row["detail_avg_hr"] = detail.get("averageHR") or detail.get("averageHr") or ""
            row["detail_max_hr"] = detail.get("maxHR") or detail.get("maxHr") or ""
            row["detail_duration_s"] = detail.get("duration") or detail.get("durationInSeconds") or ""
            row["detail_distance_m"] = detail.get("distance") or detail.get("distanceInMeters") or ""
        else:
            row["detail_available"] = False
            row["detail_avg_hr"] = ""
            row["detail_max_hr"] = ""
            row["detail_duration_s"] = ""
            row["detail_distance_m"] = ""
        merged.append(row)

    merged.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("start_local") or ""), int(r.get("activity_id") or 0)))
    return merged


def _write_outputs(rows: list[dict], start_date: str, end_date: str) -> tuple[Path, Path]:
    out_dir = ROOT / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"rowing_window_{start_date}_to_{end_date}".replace("-", "")
    csv_path = out_dir / f"{stem}.csv"
    json_path = out_dir / f"{stem}.json"

    fields = [
        "activity_id",
        "date",
        "start_local",
        "name",
        "type",
        "duration_s",
        "distance_m",
        "avg_hr",
        "max_hr",
        "is_rowing",
        "detail_available",
        "detail_avg_hr",
        "detail_max_hr",
        "detail_duration_s",
        "detail_distance_m",
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=True, indent=2)

    return csv_path, json_path


async def _amain(start_date: str, end_date: str) -> int:
    load_dotenv(ROOT / ".env", override=False)
    rows = await _fetch_activities_for_window(start_date, end_date)
    csv_path, json_path = _write_outputs(rows, start_date, end_date)

    total = len(rows)
    rowing = sum(1 for r in rows if r.get("is_rowing"))
    print(f"rows_total={total}")
    print(f"rows_rowing={rowing}")
    print(f"csv={csv_path.as_posix()}")
    print(f"json={json_path.as_posix()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Garmin activities in a date window and persist local rowing-focused extracts.")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.start_date):
        raise SystemExit("invalid --start-date format")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.end_date):
        raise SystemExit("invalid --end-date format")

    return asyncio.run(_amain(args.start_date, args.end_date))


if __name__ == "__main__":
    raise SystemExit(main())
