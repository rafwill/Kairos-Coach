from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.mcp_client import call_tool, garmin_mcp_session


@dataclass
class ProbeEvent:
    phase: str
    call_index: int
    tool: str
    args: dict
    ok: bool
    kind: str
    http_code: str
    retry_after_seconds: str
    session_age_seconds: float
    message_excerpt: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _classify_error_text(text: str) -> tuple[str, str, str]:
    raw = str(text or "")
    low = raw.lower()

    match = re.search(r"\bhttp\s*(\d{3})\b", raw, flags=re.IGNORECASE)
    http_code = match.group(1) if match else ""
    if not http_code and "rate limited (429)" in low:
        http_code = "429"

    has_403 = http_code == "403" or "forbidden" in low or " 403" in low
    has_429 = http_code == "429" or "rate limited" in low or "too many requests" in low
    has_auth = any(
        token in low
        for token in (
            "login failed",
            "all login strategies exhausted",
            "unauthorized",
            "forbidden",
            "credentials",
            "token",
            "session",
            "authentication",
        )
    )

    if has_403 and has_429:
        kind = "mixed_auth_and_rate_limit"
    elif has_429:
        kind = "rate_limit"
    elif has_403 or has_auth:
        kind = "auth_or_session"
    else:
        kind = "unknown_error"

    retry_after = ""
    retry_match = re.search(r"retry-?after\s*[:=]?\s*(\d+)", low)
    if retry_match:
        retry_after = retry_match.group(1)

    return kind, http_code, retry_after


def _is_error_text(text: str) -> bool:
    t = str(text or "").strip().lower()
    return t.startswith("error") or "error executing tool" in t


async def _phase_relogin_only() -> tuple[bool, str]:
    """Open and initialize a session with no data calls.

    This phase intentionally avoids Garmin data tools so auth refresh is isolated.
    """
    try:
        async with garmin_mcp_session(essential_only=False) as session:
            _ = await session.list_tools()
        return True, "session_initialized"
    except Exception as ex:  # pragma: no cover - defensive around MCP transport
        return False, str(ex)


async def _phase_probe(
    activity_ids: list[int],
    delay_between_calls_seconds: float,
    stop_on_first_error: bool,
) -> tuple[list[ProbeEvent], dict]:
    events: list[ProbeEvent] = []
    summary = {
        "session_started_at": "",
        "session_ended_at": "",
        "first_403_session_age_seconds": None,
        "first_429_session_age_seconds": None,
        "calls_total": 0,
        "calls_ok": 0,
        "calls_error": 0,
    }

    async with garmin_mcp_session(essential_only=False) as session:
        session_started_wall = _utc_now()
        session_started_mono = time.monotonic()
        summary["session_started_at"] = session_started_wall

        planned_calls: list[tuple[str, dict]] = [("get_user_profile", {})]
        planned_calls.extend(("get_activity", {"activity_id": aid}) for aid in activity_ids)

        for idx, (tool, args) in enumerate(planned_calls, start=1):
            if idx > 1 and delay_between_calls_seconds > 0:
                await asyncio.sleep(delay_between_calls_seconds)

            text = await call_tool(session, tool, args)
            age_sec = round(time.monotonic() - session_started_mono, 3)

            if _is_error_text(text):
                kind, http_code, retry_after = _classify_error_text(text)
                ok = False
                summary["calls_error"] += 1
                if http_code == "403" and summary["first_403_session_age_seconds"] is None:
                    summary["first_403_session_age_seconds"] = age_sec
                if http_code == "429" and summary["first_429_session_age_seconds"] is None:
                    summary["first_429_session_age_seconds"] = age_sec
            else:
                ok = True
                kind, http_code, retry_after = "ok", "", ""
                summary["calls_ok"] += 1

            events.append(
                ProbeEvent(
                    phase="probe",
                    call_index=idx,
                    tool=tool,
                    args=args,
                    ok=ok,
                    kind=kind,
                    http_code=http_code,
                    retry_after_seconds=retry_after,
                    session_age_seconds=age_sec,
                    message_excerpt=str(text or "")[:220],
                )
            )

            # Avoid escalating 429 into 403 by hammering during the same window.
            if stop_on_first_error and not ok:
                break

        summary["session_ended_at"] = _utc_now()
        summary["calls_total"] = len(planned_calls)

    return events, summary


async def _run(args: argparse.Namespace) -> int:
    load_dotenv(".env", override=False)

    started_at = _utc_now()
    relogin_ok, relogin_note = await _phase_relogin_only()

    # Keep relogin and probes separated to avoid false negatives from immediate rate limiting.
    await asyncio.sleep(max(0.0, float(args.cooldown_seconds)))

    probe_events: list[ProbeEvent] = []
    probe_summary: dict = {}
    probe_error = ""

    try:
        probe_events, probe_summary = await _phase_probe(
            activity_ids=args.activity_ids,
            delay_between_calls_seconds=max(0.0, float(args.delay_between_calls_seconds)),
            stop_on_first_error=bool(args.stop_on_first_error),
        )
    except Exception as ex:  # pragma: no cover - defensive around MCP transport
        probe_error = str(ex)

    ended_at = _utc_now()

    payload = {
        "started_at": started_at,
        "ended_at": ended_at,
        "relogin": {
            "ok": bool(relogin_ok),
            "note": relogin_note,
            "cooldown_seconds": float(args.cooldown_seconds),
        },
        "probe": {
            "ok": probe_error == "",
            "error": probe_error,
            "summary": probe_summary,
            "events": [asdict(item) for item in probe_events],
        },
    }

    if probe_summary:
        first_429 = probe_summary.get("first_429_session_age_seconds")
        first_403 = probe_summary.get("first_403_session_age_seconds")
        kinds = [str((e.kind or "")).strip().lower() for e in probe_events]
        saw_429 = any(k == "rate_limit" for k in kinds)
        saw_403 = any(k == "auth_or_session" for k in kinds)
        payload["interpretation"] = {
            "rate_limit_scope_hint": (
                "account_or_ip_likely"
                if isinstance(first_429, (int, float)) and first_429 <= 10.0
                else "unknown"
            ),
            "escalation_429_to_403_possible": bool(saw_429 and saw_403),
            "first_403_session_age_seconds": first_403,
            "first_429_session_age_seconds": first_429,
            "stop_on_first_error": bool(args.stop_on_first_error),
        }

    out_path = ROOT / "docs" / f"mcp_relogin_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    print(f"output={out_path.as_posix()}")
    print(f"relogin_ok={relogin_ok}")
    print(f"probe_ok={probe_error == ''}")
    if probe_summary:
        print(
            "probe_counts="
            f"total:{probe_summary.get('calls_total', 0)} "
            f"ok:{probe_summary.get('calls_ok', 0)} "
            f"error:{probe_summary.get('calls_error', 0)}"
        )
        print(f"first_403_session_age_seconds={probe_summary.get('first_403_session_age_seconds')}")
        print(f"first_429_session_age_seconds={probe_summary.get('first_429_session_age_seconds')}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Hard re-login + probe diagnostic for Garmin MCP. "
            "Phase 1 initializes session only; phase 2 runs isolated probe calls."
        )
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=900.0,
        help="Seconds to wait between re-login phase and probe phase (default: 900s = 15 min).",
    )
    parser.add_argument(
        "--delay-between-calls-seconds",
        type=float,
        default=8.0,
        help="Seconds to wait between probe calls inside the same session (default: 8s).",
    )
    parser.add_argument(
        "--stop-on-first-error",
        action="store_true",
        default=True,
        help="Stop probe immediately after first 403/429-style error (default: enabled).",
    )
    parser.add_argument(
        "--no-stop-on-first-error",
        action="store_false",
        dest="stop_on_first_error",
        help="Continue probing after first error (use with caution; may escalate rate-limit to auth blocks).",
    )
    parser.add_argument(
        "--activity-ids",
        type=int,
        nargs="*",
        default=[24013969366, 24227364808, 24059814335, 24201327869],
        help="Activity IDs to probe after get_user_profile.",
    )

    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())