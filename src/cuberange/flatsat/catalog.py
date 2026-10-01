"""Read the repository's existing curriculum for the local console.

The canonical front-matter parser and teaching order are reused from tools/syllabus.py.
The console presents their facts and README excerpts; it never starts an exercise.
"""
from __future__ import annotations

from functools import lru_cache
import importlib.util
from pathlib import Path
import re

from ..paths import REPO


@lru_cache(maxsize=1)
def _syllabus():
    spec = importlib.util.spec_from_file_location(
        "_cuberange_console_syllabus", REPO / "tools" / "syllabus.py")
    if spec is None or spec.loader is None:
        raise ValueError("The exercise catalog is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(directory: str, lang: str) -> str:
    if lang not in ("ja", "en"):
        raise ValueError("Document language must be ja or en")
    root = (REPO / "exercises").resolve()
    name = "README.ja.md" if lang == "ja" else "README.md"
    path = (root / directory / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Exercise document is outside the repository")
    return path.read_text(encoding="utf-8")


def _objective(text: str, lang: str) -> str:
    """Keep the first objective paragraph, with Markdown left as plain text."""
    headings = ("あなたの目的", "目的") if lang == "ja" else ("Your objective",)
    for heading in headings:
        match = re.search(r"^## " + re.escape(heading) + r"\s*\n(.*?)(?=^## |\Z)",
                          text, re.M | re.S)
        if match:
            paragraph = match.group(1).strip().split("\n\n", 1)[0]
            return " ".join(paragraph.split())
    return ""


def list_exercises() -> list[dict]:
    syllabus = _syllabus()
    found = syllabus.exercises()
    sequence = syllabus.order(found)
    sequence.extend(sorted(set(found) - set(sequence)))
    items = []
    for identifier in sequence:
        entry = found[identifier]
        if not re.fullmatch(r"EX-[A-Z][0-9]{2}", identifier):
            raise ValueError("Invalid exercise identifier in the curriculum")
        directory = entry["dir"]
        ja_text = _read(directory, "ja")
        ja = syllabus.front_matter(REPO / "exercises" / directory / "README.ja.md")
        items.append({
            "id": identifier, "directory": directory,
            "title": ja.get("title") or entry["title"], "title_en": entry["title"],
            "description": _objective(ja_text, "ja"),
            "layer": entry["layer"], "difficulty": entry["difficulty"],
            "duration": entry["duration"], "prerequisite": entry.get("prerequisite") or None,
            "command": f"make exercise EX={directory}",
            "readme_url": f"/api/exercises/{identifier}/readme?lang=ja",
        })
    return items


def read_exercise(identifier: str, lang: str = "ja") -> str:
    if not isinstance(identifier, str) or not re.fullmatch(r"EX-[A-Z][0-9]{2}", identifier):
        raise ValueError("Unknown exercise")
    found = _syllabus().exercises()
    if identifier not in found:
        raise ValueError("Unknown exercise")
    return _read(found[identifier]["dir"], lang)
