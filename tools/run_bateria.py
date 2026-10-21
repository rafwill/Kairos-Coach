"""Lanza la batería de preguntas contra Kairos y guarda la transcripción con el tiempo de cada respuesta.

Uso:
    python tools/run_bateria.py [--preguntas docs/bateria_p55_preguntas_base.txt]
                                [--salida .tmp/bateria/<fecha>] [--modelo nvidia/...]
                                [--username U] [--proveedor 4]

- Usuario: --username o KAIROS_USERNAME del .env (nunca escrito en el código).
- Proveedor: número del menú de arranque de Kairos (4 = NVIDIA). --modelo fija NVIDIA_MODEL solo para esta pasada.
- Envía las preguntas de una en una y mide desde el envío hasta que Kairos vuelve a pedir entrada.
- Genera <salida>.log (crudo), <salida>.md (transcripción limpia) y <salida>.json (tiempos).
  Las salidas contienen datos reales del atleta: .tmp/ está en .gitignore.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = "Tú:"
PER_QUESTION_LIMIT_S = 600

NOISE = (
    re.compile(r"^\s*\[\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\]"),
    re.compile(r"^\s*\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \["),
    re.compile(r"Processing request of type|server\.py:\d+|^\s+(CallToolRequest|ListToolsRequest)\s*$"),
    re.compile(r"login returned|IP rate limited|by Garmin\s*$"),
    re.compile(r"^\s*⏳"),
)


def load_questions(path: Path) -> list[str]:
    """Preguntas = líneas no vacías tras la línea que empieza por 'Pruebas'."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip().lower().startswith("pruebas")), -1)
    return [ln.strip() for ln in lines[start + 1:] if ln.strip()]


def load_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def clean(block: str) -> str:
    out = [ln.rstrip() for ln in block.splitlines() if not any(p.search(ln) for p in NOISE)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out).strip())


def run(questions: list[str], username: str, provider: str, model: str | None) -> tuple[str, float, list[dict]]:
    env = os.environ.copy()
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "COLUMNS": "110", "TERM": "dumb"})
    if model:
        env["NVIDIA_MODEL"] = model
    python = ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    proc = subprocess.Popen([str(python if python.exists() else sys.executable), "-u", "-m", "agent.main"], cwd=str(ROOT),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", env=env, bufsize=1)
    buf: list[str] = []
    lock = threading.Lock()

    def reader() -> None:
        while ch := proc.stdout.read(1):
            with lock:
                buf.append(ch)

    threading.Thread(target=reader, daemon=True).start()

    def prompts_seen() -> int:
        with lock:
            return "".join(buf).count(PROMPT)

    def wait_prompts(n: int, limit: float) -> bool:
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if prompts_seen() >= n or proc.poll() is not None:
                return prompts_seen() >= n
            time.sleep(0.2)
        return False

    t_start = time.monotonic()
    proc.stdin.write(f"{username}\n{provider}\n")
    proc.stdin.flush()
    wait_prompts(1, PER_QUESTION_LIMIT_S)
    startup_s = time.monotonic() - t_start
    timings: list[dict] = []
    for i, q in enumerate(questions, 1):
        t0 = time.monotonic()
        proc.stdin.write(q + "\n")
        proc.stdin.flush()
        ok = wait_prompts(i + 1, PER_QUESTION_LIMIT_S)
        timings.append({"n": i, "q": q, "seconds": round(time.monotonic() - t0, 1), "completed": ok})
        print(f"{i:2d} {timings[-1]['seconds']:6.1f}s {'ok' if ok else 'SIN RESPUESTA'}  {q[:60]}", flush=True)
        if not ok:
            break
    proc.stdin.write("salir\n")
    proc.stdin.flush()
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(0.5)
    return "".join(buf), startup_s, timings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--preguntas", default=str(ROOT / "docs/bateria_p55_preguntas_base.txt"))
    ap.add_argument("--salida", default=str(ROOT / ".tmp/bateria" / datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")))
    ap.add_argument("--modelo", default=None, help="NVIDIA_MODEL para esta pasada (por defecto, el del .env)")
    ap.add_argument("--username", default=None)
    ap.add_argument("--proveedor", default="4", help="opción del menú de proveedor de Kairos (4 = NVIDIA)")
    args = ap.parse_args()

    username = args.username or load_env(ROOT / ".env").get("KAIROS_USERNAME") or os.environ.get("KAIROS_USERNAME")
    if not username:
        ap.error("falta el usuario: usa --username o define KAIROS_USERNAME en .env")
    questions = load_questions(Path(args.preguntas))
    base = Path(args.salida)
    base.parent.mkdir(parents=True, exist_ok=True)

    raw, startup_s, timings = run(questions, username, args.proveedor, args.modelo)
    base.with_suffix(".log").write_text(raw, encoding="utf-8")
    answers = raw.split(PROMPT)[1:]
    label = args.modelo or "modelo del .env"
    md = [f"# Batería Kairos — {label} ({len(questions)} preguntas)\n"]
    for i, q in enumerate(questions):
        ans = clean(answers[i]) if i < len(answers) else "(sin respuesta)"
        secs = timings[i]["seconds"] if i < len(timings) else None
        md.append(f"\n---\n\n### {i + 1}. {q}  ·  {secs} s\n\n{ans}\n")
    base.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    base.with_suffix(".json").write_text(json.dumps({"modelo": label, "arranque_s": round(startup_s, 1), "tiempos": timings},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(t["seconds"] for t in timings)
    print(f"modelo={label} arranque={startup_s:.1f}s preguntas={total:.1f}s respuestas={len(answers)} md={base.with_suffix('.md')}")
    return 0 if all(t["completed"] for t in timings) and len(timings) == len(questions) else 1


if __name__ == "__main__":
    raise SystemExit(main())
