from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from agent.mcp_client import call_tool, garmin_mcp_session


def _safe_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        txt = value.strip()
        if txt.startswith("{") or txt.startswith("["):
            try:
                return json.loads(txt)
            except Exception:
                return value
    return value


def _looks_like_error(raw: Any) -> bool:
    if not isinstance(raw, str):
        return False
    txt = raw.strip().lower()
    return txt.startswith("error") or "error executing tool" in txt


def _extract_first_float(node: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(node, dict):
        for key in keys:
            raw = node.get(key)
            if raw is None:
                continue
            try:
                return float(raw)
            except Exception:
                continue
        for val in node.values():
            out = _extract_first_float(val, keys)
            if out is not None:
                return out
    elif isinstance(node, list):
        for item in node:
            out = _extract_first_float(item, keys)
            if out is not None:
                return out
    return None


def _extract_activities_list(payload: Any) -> list[dict[str, Any]]:
    data = _safe_json(payload)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("activities", "activityList", "items", "data", "result"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _extract_activity_max_hr(activity: dict[str, Any]) -> float | None:
    summary = activity.get("summaryDTO") if isinstance(activity.get("summaryDTO"), dict) else {}
    for key in ("maxHR", "maxHr", "max_hr_bpm", "maxHeartRate"):
        raw = activity.get(key)
        if raw is None:
            raw = summary.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except Exception:
            continue
        if val > 0:
            return val
    return None


def _scan_profile_hr_max_keys(payload: Any) -> list[str]:
    found: list[str] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                key_l = str(k).lower()
                next_path = f"{path}.{k}"
                if ("max" in key_l and "heart" in key_l) or "hrmaximum" in key_l or key_l in {"maxhr", "maxheartrate"}:
                    found.append(next_path)
                _walk(v, next_path)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(payload, "$")
    return sorted(set(found))


async def main() -> None:
    load_dotenv(".env", override=False)

    out_json = Path("docs") / "hr_auto_sources_probe.json"
    out_md = Path("docs") / "hr_auto_sources_probe.md"

    today = date.today()
    window_days = 14
    start = today - timedelta(days=window_days - 1)

    rhr_rows: list[dict[str, Any]] = []
    activity_max_hr_values: list[float] = []
    activity_count = 0
    profile_hr_max_like_keys: list[str] = []
    activity_count_all_history = 0
    activity_max_hr_values_all_history: list[float] = []
    activities_paged_count = 0
    activities_paged_max_hr_values: list[float] = []

    async with garmin_mcp_session(essential_only=False) as session:
        for i in range(window_days):
            d = start + timedelta(days=i)
            raw = await call_tool(session, "get_rhr_day", {"date": d.isoformat()})
            if _looks_like_error(raw):
                rhr_rows.append({"date": d.isoformat(), "rhr": None, "source": "error", "raw": str(raw)})
                continue

            payload = _safe_json(raw)
            rhr = _extract_first_float(
                payload,
                (
                    "restingHeartRate",
                    "resting_heart_rate",
                    "restingHeartRateValue",
                    "resting_hr",
                    "rhr",
                    "value",
                ),
            )
            rhr_rows.append({"date": d.isoformat(), "rhr": rhr, "source": "get_rhr_day"})

        raw_profile = await call_tool(session, "get_user_profile", {})
        if _looks_like_error(raw_profile):
            raise RuntimeError(str(raw_profile).strip())
        profile_payload = _safe_json(raw_profile)
        profile_hr_max_like_keys = _scan_profile_hr_max_keys(profile_payload)

        raw_acts = await call_tool(
            session,
            "get_activities_by_date",
            {
                "start_date": (today - timedelta(days=365)).isoformat(),
                "end_date": today.isoformat(),
            },
        )
        if _looks_like_error(raw_acts):
            raise RuntimeError(str(raw_acts).strip())

        activities = _extract_activities_list(raw_acts)
        activity_count = len(activities)

        for act in activities:
            max_hr = _extract_activity_max_hr(act)
            if max_hr is not None:
                activity_max_hr_values.append(max_hr)

        raw_acts_all = await call_tool(
            session,
            "get_activities_by_date",
            {
                "start_date": "2000-01-01",
                "end_date": today.isoformat(),
            },
        )
        if _looks_like_error(raw_acts_all):
            raise RuntimeError(str(raw_acts_all).strip())

        activities_all = _extract_activities_list(raw_acts_all)
        activity_count_all_history = len(activities_all)
        for act in activities_all:
            max_hr = _extract_activity_max_hr(act)
            if max_hr is not None:
                activity_max_hr_values_all_history.append(max_hr)

        # Extra check: paginate get_activities to access deeper history if available.
        start_idx = 0
        page_limit = 100
        max_pages = 30
        seen_ids: set[int] = set()
        for _ in range(max_pages):
            raw_page = await call_tool(
                session,
                "get_activities",
                {"start": start_idx, "limit": page_limit},
            )
            if _looks_like_error(raw_page):
                break
            page = _extract_activities_list(raw_page)
            if not page:
                break

            new_rows = 0
            for act in page:
                act_id = act.get("activityId") or act.get("activity_id")
                try:
                    act_id_int = int(act_id)
                except Exception:
                    act_id_int = None
                if act_id_int is not None and act_id_int in seen_ids:
                    continue
                if act_id_int is not None:
                    seen_ids.add(act_id_int)
                new_rows += 1
                max_hr = _extract_activity_max_hr(act)
                if max_hr is not None:
                    activities_paged_max_hr_values.append(max_hr)

            activities_paged_count += new_rows
            if new_rows == 0 or len(page) < page_limit:
                break
            start_idx += page_limit

    rhr_values = [float(x["rhr"]) for x in rhr_rows if x.get("rhr") is not None]
    rhr_avg = (sum(rhr_values) / len(rhr_values)) if rhr_values else None
    hr_max_empirical = max(activity_max_hr_values) if activity_max_hr_values else None
    hr_max_empirical_all_history = max(activity_max_hr_values_all_history) if activity_max_hr_values_all_history else None
    hr_max_empirical_paged = max(activities_paged_max_hr_values) if activities_paged_max_hr_values else None

    payload_out = {
        "today": today.isoformat(),
        "rhr_window_days": window_days,
        "rhr_values_count": len(rhr_values),
        "rhr_avg": rhr_avg,
        "rhr_rows": rhr_rows,
        "activities_last_365_count": activity_count,
        "activities_with_max_hr_count": len(activity_max_hr_values),
        "hr_max_empirical": hr_max_empirical,
        "activities_all_history_count": activity_count_all_history,
        "activities_all_history_with_max_hr_count": len(activity_max_hr_values_all_history),
        "hr_max_empirical_all_history": hr_max_empirical_all_history,
        "activities_paged_count": activities_paged_count,
        "activities_paged_with_max_hr_count": len(activities_paged_max_hr_values),
        "hr_max_empirical_paged": hr_max_empirical_paged,
        "profile_hr_max_like_keys": profile_hr_max_like_keys,
    }
    out_json.write_text(json.dumps(payload_out, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Probe fuentes automáticas FC",
        "",
        f"- fecha: {today.isoformat()}",
        f"- rhr_rows con valor: {len(rhr_values)}/{window_days}",
        f"- rhr_avg_14d: {round(rhr_avg, 2) if rhr_avg is not None else 'n/d'}",
        f"- actividades 365d: {activity_count}",
        f"- actividades con maxHR: {len(activity_max_hr_values)}",
        f"- hr_max_empirical_365d: {round(hr_max_empirical, 2) if hr_max_empirical is not None else 'n/d'}",
        f"- actividades histórico completo: {activity_count_all_history}",
        f"- actividades histórico completo con maxHR: {len(activity_max_hr_values_all_history)}",
        f"- hr_max_empirical_historico_completo: {round(hr_max_empirical_all_history, 2) if hr_max_empirical_all_history is not None else 'n/d'}",
        f"- actividades paginadas (get_activities): {activities_paged_count}",
        f"- actividades paginadas con maxHR: {len(activities_paged_max_hr_values)}",
        f"- hr_max_empirical_paginado: {round(hr_max_empirical_paged, 2) if hr_max_empirical_paged is not None else 'n/d'}",
        f"- claves perfil tipo maxHR detectadas: {len(profile_hr_max_like_keys)}",
        "",
        f"- json: {out_json.as_posix()}",
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"output_json={out_json.as_posix()}")
    print(f"output_md={out_md.as_posix()}")
    print(f"rhr_values_count={len(rhr_values)}")
    print(f"activity_count={activity_count}")
    print(f"activity_max_hr_count={len(activity_max_hr_values)}")
    print(f"activity_count_all_history={activity_count_all_history}")
    print(f"activity_max_hr_count_all_history={len(activity_max_hr_values_all_history)}")
    print(f"activities_paged_count={activities_paged_count}")
    print(f"activities_paged_max_hr_count={len(activities_paged_max_hr_values)}")


if __name__ == "__main__":
    asyncio.run(main())
