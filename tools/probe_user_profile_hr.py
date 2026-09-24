from __future__ import annotations

import asyncio
import json
import sys
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


def _find_key_paths(obj: Any, target_keys: set[str], path: str = "$") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            next_path = f"{path}.{k}"
            if k in target_keys:
                out.append((next_path, v))
            out.extend(_find_key_paths(v, target_keys, next_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            out.extend(_find_key_paths(item, target_keys, f"{path}[{i}]"))
    return out


async def main() -> None:
    load_dotenv(".env", override=False)

    out_json = Path("docs") / "profile_hr_probe_raw.json"
    out_txt = Path("docs") / "profile_hr_probe_summary.txt"

    async with garmin_mcp_session(essential_only=False) as session:
        raw = await call_tool(session, "get_user_profile", {})
        if _looks_like_error(raw):
            raise RuntimeError(str(raw).strip())

    payload = _safe_json(raw)

    with out_json.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    target_keys = {
        "resting_hr",
        "restingHeartRate",
        "resting_heart_rate",
        "rhr",
        "max_hr",
        "maxHeartRate",
        "max_heart_rate",
        "hrRestingValue",
        "hrMaximumValue",
        "userData",
    }
    paths = _find_key_paths(payload, target_keys)

    top_keys: list[str] = []
    if isinstance(payload, dict):
        top_keys = sorted(payload.keys())

    lines = [
        f"raw_type={type(raw).__name__}",
        f"parsed_type={type(payload).__name__}",
        f"top_level_keys_count={len(top_keys)}",
        "top_level_keys=" + ", ".join(top_keys),
        "",
        "matched_paths:",
    ]

    if not paths:
        lines.append("(none)")
    else:
        for p, v in paths:
            lines.append(f"- {p} = {repr(v)}")

    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"output_json={out_json.as_posix()}")
    print(f"output_summary={out_txt.as_posix()}")
    print(f"matched_paths={len(paths)}")


if __name__ == "__main__":
    asyncio.run(main())
