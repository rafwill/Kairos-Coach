"""
agent/knowledge.py
Base de conocimiento del atleta: ficheros y texto guardado, troceado y recuperación de los trozos que tocan a la pregunta.
"""

import json
import re
from pathlib import Path
from typing import Any


_KB_CHUNK_SIZE_CHARS = 900


_KB_MAX_CHUNKS = 4


_KB_MAX_CHARS_PER_FILE = 50_000


_KB_DEFAULT_FILES = (
    "memory/athlete_knowledge.md",
    "memory/athlete_knowledge.txt",
    "memory/athlete_knowledge.json",
)


def _resolve_kb_paths(env_value: str | None, project_root: Path | None = None) -> list[Path]:
    """Resuelve los archivos de base de conocimiento del atleta a rutas absolutas.

    Si ATHLETE_KB_PATHS no está definido, usa una lista de rutas por defecto
    dentro del proyecto.
    """
    root = project_root or (Path(__file__).parent.parent)
    raw_paths = [p.strip() for p in (env_value or "").split(",") if p.strip()]
    if not raw_paths:
        raw_paths = list(_KB_DEFAULT_FILES)

    resolved: list[Path] = []
    seen: set[str] = set()
    for raw in raw_paths:
        p = Path(raw)
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(p)
    return resolved


def _json_to_kb_text(data: Any, prefix: str = "") -> str:
    """Aplana JSON a texto legible para recuperación semántica ligera."""
    lines: list[str] = []

    if isinstance(data, dict):
        for k, v in data.items():
            next_prefix = f"{prefix}.{k}" if prefix else str(k)
            lines.append(_json_to_kb_text(v, next_prefix))
        return "\n".join(line for line in lines if line)

    if isinstance(data, list):
        for idx, item in enumerate(data):
            next_prefix = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            lines.append(_json_to_kb_text(item, next_prefix))
        return "\n".join(line for line in lines if line)

    value = "" if data is None else str(data).strip()
    if not value:
        return ""
    return f"{prefix}: {value}" if prefix else value


def _load_athlete_knowledge_chunks(
    env_value: str | None = None,
    project_root: Path | None = None,
    chunk_size: int = _KB_CHUNK_SIZE_CHARS,
) -> tuple[list[dict[str, str]], list[str]]:
    """Carga archivos de conocimiento del atleta y devuelve chunks + fuentes.

    Formatos soportados: .md, .txt, .json.
    """
    chunks: list[dict[str, str]] = []
    sources: list[str] = []
    for path in _resolve_kb_paths(env_value, project_root):
        if not path.exists() or not path.is_file():
            continue

        suffix = path.suffix.lower()
        if suffix not in {".md", ".txt", ".json"}:
            continue

        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not raw.strip():
            continue

        text = raw
        if suffix == ".json":
            try:
                parsed = json.loads(raw)
                text = _json_to_kb_text(parsed)
            except (TypeError, json.JSONDecodeError):
                text = raw

        text = text.strip()[:_KB_MAX_CHARS_PER_FILE]
        if not text:
            continue

        # Preferimos cortar por párrafos para que los fragmentos sean más útiles.
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not paragraphs:
            paragraphs = [text]

        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= chunk_size:
                current = candidate
                continue

            if current:
                chunks.append({"source": path.name, "text": current})
                current = ""

            # Si un párrafo es demasiado largo, lo partimos por ventanas fijas.
            start = 0
            while start < len(paragraph):
                piece = paragraph[start:start + chunk_size].strip()
                if piece:
                    chunks.append({"source": path.name, "text": piece})
                start += chunk_size

        if current:
            chunks.append({"source": path.name, "text": current})

        sources.append(path.name)

    return chunks, sorted(set(sources))


def _tokenize_for_kb(text: str) -> list[str]:
    """Tokenizador simple para retrieval léxico robusto en español/inglés."""
    return re.findall(r"[a-zA-Z0-9áéíóúñüÁÉÍÓÚÑÜ]{3,}", (text or "").lower())


def _retrieve_athlete_knowledge(
    query: str,
    chunks: list[dict[str, str]],
    top_k: int = _KB_MAX_CHUNKS,
) -> list[dict[str, str]]:
    """Recupera los fragmentos más relevantes de la base del atleta."""
    if not chunks:
        return []

    query_tokens = set(_tokenize_for_kb(query))
    if not query_tokens:
        return chunks[: min(top_k, len(chunks))]

    scored: list[tuple[int, int, dict[str, str]]] = []
    for idx, chunk in enumerate(chunks):
        text = chunk.get("text", "")
        text_tokens = set(_tokenize_for_kb(text))
        overlap = len(query_tokens & text_tokens)
        if overlap <= 0:
            continue
        # Ranking estable: más solape, y en empate mantener orden de carga.
        scored.append((overlap, -idx, chunk))

    if not scored:
        return chunks[: min(top_k, len(chunks))]

    scored.sort(reverse=True)
    return [chunk for _, _, chunk in scored[:top_k]]


def _build_athlete_knowledge_context(query: str, chunks: list[dict[str, str]]) -> str:
    """Construye el bloque de contexto RAG a inyectar en mensajes."""
    selected = _retrieve_athlete_knowledge(query, chunks)
    if not selected:
        return ""

    lines = [
        "## Base de Conocimiento del atleta (RAG)",
        "Combina estos fragmentos con el Perfil del usuario y los datos reales de Garmin.",
    ]
    for item in selected:
        source = item.get("source", "kb")
        text = item.get("text", "").strip()
        if not text:
            continue
        trimmed = text[:900]
        ellipsis = "…" if len(text) > 900 else ""
        lines.append(f"- Fuente: {source}\\n{trimmed}{ellipsis}")
    return "\n".join(lines)
