"""Exercise metadata using the same deliberately small regex parser as tools/syllabus.py."""
from pathlib import Path
import re

EXERCISES = Path(__file__).resolve().parents[3] / "exercises"
FIELDS = ("id", "title", "layer", "difficulty", "duration", "prerequisite")


def exercises() -> list[dict]:
    found = []
    for readme in sorted(EXERCISES.glob("*/README.md")):
        text = readme.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not match:
            raise ValueError(f"{readme} has no front matter")
        entry = dict.fromkeys(FIELDS, "")
        for line in match[1].splitlines():
            field = re.match(r"^([a-z_]+):\s*(.*)$", line)
            if field and field[1] in FIELDS:
                entry[field[1]] = field[2].strip()
        entry.update(dir=readme.parent.name, body=text[match.end():],
                     scenario="scenario.resc" if (readme.parent / "scenario.resc").is_file() else None)
        found.append(entry)
    return found


def list_exercises() -> list[dict]:
    return [{key: entry[key] for key in (*FIELDS[:-1], "dir")} for entry in exercises()]


def get_exercise(exercise_id: str) -> dict | None:
    return next((entry for entry in exercises() if entry["id"] == exercise_id), None)
