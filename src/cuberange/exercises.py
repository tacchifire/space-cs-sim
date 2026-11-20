"""Exercise metadata: the single front-matter parser for the whole repository.

Until now two copies of the same deliberately small regex parser lived side by side -
`tools/syllabus.py` (the teaching order) and `src/cuberange/gui/catalog.py` (the local GUI) -
and each kept its own field list, so a new field had to be added twice and could be added
once. This module is the one implementation both delegate to.

The parser stays a regex on purpose, for the reason syllabus.py originally gave: a real YAML
parse would also swallow the `ttp:` block, whose whole point is that it is deliberately empty
and must stay visible; and the syllabus tool must run with no third-party dependencies.

Fields
------
The curriculum fields (`id` .. `prerequisite`) plus the hardware-testbed classification:

- `targets`: the execution targets the exercise runs on, as a list - currently
  `renode, hw` for every exercise (the hardware-compatibility survey found no exercise
  that is Renode-only).
- `hw_class`: `as-is` (runs on the physical testbed unchanged) or `changed` (the learning
  goal survives on hardware but the injection method has to change). The classification's
  source of truth is `docs/testbed/hw-compat-table.md`.
- `hw_evidence`: a short phrase naming what the hardware run needs as evidence /
  equipment, condensed from that table's "Hardware features needed" column.
"""
from __future__ import annotations

import re
from pathlib import Path

EXERCISES_DIR = Path(__file__).resolve().parents[2] / "exercises"

#: Every front-matter field this parser recognises, in declaration order.
FIELDS = ("id", "title", "layer", "difficulty", "duration", "prerequisite",
          "targets", "hw_class", "hw_evidence")

#: The fields that existed before the hardware classification was added.
CURRICULUM_FIELDS = FIELDS[:6]

#: Values `hw_class` may take. `renode-only` is deliberately a valid value even though no
#: exercise currently uses it: the survey's out-of-scope class has to be representable,
#: or the next such exercise would have to be misfiled to be recorded at all.
HW_CLASSES = ("as-is", "changed", "renode-only")

#: Values a single entry of `targets` may take.
KNOWN_TARGETS = ("renode", "hw")

_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)
_FIELD_LINE = re.compile(r"^([a-z_]+):\s*(.*)$")


def _normalise(field: str, value: str):
    """`targets` is a list in the record; everything else stays the string as written."""
    if field == "targets":
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def split_front_matter(text: str) -> tuple[dict, str]:
    """Split a README into (front-matter dict, body).

    Only fields named in FIELDS are returned, and only when present - a caller asking
    `.get("prerequisite")` must still be able to tell "none" from "not parsed".
    """
    match = _FRONT_MATTER.match(text)
    if not match:
        raise ValueError("document has no front matter")
    entry = {}
    for line in match[1].splitlines():
        field = _FIELD_LINE.match(line)
        if field and field[1] in FIELDS:
            entry[field[1]] = _normalise(field[1], field[2].strip())
    return entry, text[match.end():]


def parse_front_matter(readme: Path) -> dict:
    """The front matter of one README file, as a dict (see split_front_matter)."""
    try:
        entry, _ = split_front_matter(Path(readme).read_text(encoding="utf-8"))
    except ValueError:
        raise ValueError(f"{readme} has no front matter") from None
    return entry


def exercise_record(readme: Path) -> dict:
    """One exercise as a complete record: every field in FIELDS, plus dir/body/scenario.

    Fields a README does not declare come back empty ("" - or [] for `targets`), so
    consumers never have to guess which keys exist.
    """
    readme = Path(readme)
    text = readme.read_text(encoding="utf-8")
    try:
        parsed, body = split_front_matter(text)
    except ValueError:
        raise ValueError(f"{readme} has no front matter") from None
    entry = {field: ([] if field == "targets" else "") for field in FIELDS}
    entry.update(parsed)
    entry.update(dir=readme.parent.name, body=body,
                 scenario="scenario.resc" if (readme.parent / "scenario.resc").is_file() else None)
    return entry


def all_exercises(exercises_dir: Path = EXERCISES_DIR) -> list[dict]:
    """Every exercise record, in directory order."""
    return [exercise_record(readme)
            for readme in sorted(Path(exercises_dir).glob("*/README.md"))]


def find_exercise(exercise_id: str, exercises_dir: Path = EXERCISES_DIR) -> dict | None:
    """The record for `exercise_id`, or None if no exercise declares it."""
    return next((entry for entry in all_exercises(exercises_dir)
                 if entry["id"] == exercise_id), None)
