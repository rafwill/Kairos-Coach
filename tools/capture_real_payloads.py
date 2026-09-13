from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from agent.load_metrics import (
    _extract_activity_duration_hours,
    _extract_duration_seconds_from_activity_details_payload,
    _extract_hr_samples_from_activity_details,
    _parse_hr_zones_list,
    _resolve_activity_duration_hours,
)
from agent.mcp_client import call_tool, garmin_mcp_session


def _extract_error(raw) -> str:
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    prefix = "Error executing tool"
    if text.startswith(prefix):
        return text
    return ""


def _safe_json(raw):
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return raw
    return raw


def _to_activity(payload):
    obj = _safe_json(payload)
    if isinstance(obj, list) and obj:
        obj = obj[0]
    if isinstance(obj, dict) and isinstance(obj.get("activity"), dict):
        obj = obj["activity"]
    return obj if isinstance(obj, dict) else {}


def _zones_hours(hr_zones_raw: str | None) -> float:
    if not hr_zones_raw:
        return 0.0
    zones = _parse_hr_zones_list(hr_zones_raw)
    if not zones:
        return 0.0
    total_seconds = 0.0
    for z in zones:
        if not isinstance(z, dict):
            continue
        try:
            total_seconds += max(0.0, float(z.get("secsInZone") or 0.0))
        except (TypeError, ValueError):
            continue
    return total_seconds / 3600.0


def _source_details_root_hours(details_raw: str | None) -> float:
    if not details_raw:
        return 0.0
    seconds = _extract_duration_seconds_from_activity_details_payload(details_raw)
    return (float(seconds) / 3600.0) if seconds else 0.0


def _source_samples_last_hours(details_raw: str | None) -> float:
    if not details_raw:
        return 0.0
    samples = _extract_hr_samples_from_activity_details(details_raw, 0.0)
    if len(samples) < 2:
        return 0.0
    return float(samples[-1][0]) / 3600.0


async def _capture_one(session, activity_id: int, out_dir: Path, expected_hours: float | None):
    raw_activity = await call_tool(session, "get_activity", {"activity_id": activity_id})
    raw_details = await call_tool(session, "get_activity_details", {"activity_id": activity_id})
    raw_zones = await call_tool(session, "get_activity_hr_in_timezones", {"activity_id": activity_id})

    activity_error = _extract_error(raw_activity)
    details_error = _extract_error(raw_details)
    zones_error = _extract_error(raw_zones)
    activity_ok = not bool(activity_error)
    details_ok = not bool(details_error)
    zones_ok = not bool(zones_error)
    capture_status = "ok" if (activity_ok and details_ok and zones_ok) else "error"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    activity_path = out_dir / f"activity_{activity_id}_{ts}.json"
    details_path = out_dir / f"activity_details_{activity_id}_{ts}.json"
    zones_path = out_dir / f"activity_hr_zones_{activity_id}_{ts}.json"

    activity_path.write_text(raw_activity if isinstance(raw_activity, str) else json.dumps(raw_activity, ensure_ascii=False), encoding="utf-8")
    details_path.write_text(raw_details if isinstance(raw_details, str) else json.dumps(raw_details, ensure_ascii=False), encoding="utf-8")
    zones_path.write_text(raw_zones if isinstance(raw_zones, str) else json.dumps(raw_zones, ensure_ascii=False), encoding="utf-8")

    activity = _to_activity(raw_activity)
    details_raw = raw_details if isinstance(raw_details, str) else json.dumps(raw_details, ensure_ascii=False)
    zones_raw = raw_zones if isinstance(raw_zones, str) and raw_zones.strip() and not raw_zones.lower().startswith("error al llamar") else None

    source_summary = _extract_activity_duration_hours(activity)
    source_details_root = _source_details_root_hours(details_raw)
    source_samples_last = _source_samples_last_hours(details_raw)
    source_hr_zones = _zones_hours(zones_raw)
    resolved_hours = _resolve_activity_duration_hours(activity, hr_zones_raw=zones_raw, activity_details_raw=details_raw)

    delta = ""
    if expected_hours is not None:
        delta = round(resolved_hours - expected_hours, 3)

    return {
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "activity_id": activity_id,
        "capture_status": capture_status,
        "activity_ok": activity_ok,
        "details_ok": details_ok,
        "zones_ok": zones_ok,
        "activity_error": activity_error,
        "details_error": details_error,
        "zones_error": zones_error,
        "source_summary": round(source_summary, 3),
        "source_details_root": round(source_details_root, 3),
        "source_samples_last": round(source_samples_last, 3),
        "source_hr_zones": round(source_hr_zones, 3),
        "resolved_hours": round(resolved_hours, 3),
        "expected_hours": "" if expected_hours is None else expected_hours,
        "delta_vs_expected_hours": delta,
        "activity_json": activity_path.name,
        "details_json": details_path.name,
        "zones_json": zones_path.name,
    }


async def main_async(args):
    load_dotenv(".env", override=False)

    out_dir = Path("fixtures/real_payloads")
    out_dir.mkdir(parents=True, exist_ok=True)

    expected_by_id: dict[int, float] = {}
    for item in args.expected:
        if ":" not in item:
            raise ValueError(f"Invalid --expected format: {item}. Use <activity_id>:<hours>")
        aid_raw, hours_raw = item.split(":", 1)
        expected_by_id[int(aid_raw)] = float(hours_raw)

    rows = []
    async with garmin_mcp_session(essential_only=False) as session:
        for aid in args.activity:
            row = await _capture_one(session, aid, out_dir, expected_by_id.get(aid))
            rows.append(row)

    trace_csv = out_dir / f"duration_trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with trace_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"trace_csv={trace_csv}")
    for row in rows:
        print(
            ", ".join(
                [
                    f"activity_id={row['activity_id']}",
                    f"source_summary={row['source_summary']}",
                    f"source_details_root={row['source_details_root']}",
                    f"source_samples_last={row['source_samples_last']}",
                    f"source_hr_zones={row['source_hr_zones']}",
                    f"resolved_hours={row['resolved_hours']}",
                    f"expected_hours={row['expected_hours']}",
                    f"delta_vs_expected_hours={row['delta_vs_expected_hours']}",
                ]
            )
        )


def build_parser():
    parser = argparse.ArgumentParser(
        description="Capture real Garmin payloads to disk and generate a duration trace table for offline replay."
    )
    parser.add_argument(
        "--activity",
        type=int,
        action="append",
        required=True,
        help="Activity ID to capture. Repeat for multiple activities.",
    )
    parser.add_argument(
        "--expected",
        action="append",
        default=[],
        help="Expected duration hours mapping as <activity_id>:<hours> (e.g., 23455001968:10.170).",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
