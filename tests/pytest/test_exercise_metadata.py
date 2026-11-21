"""The hardware classification in every exercise's front matter must stay honest.

`targets`, `hw_class` and `hw_evidence` were added so the physical-testbed work can read,
per exercise, where it runs and what a hardware run needs - instead of re-deriving it from
prose. That only helps if the values are present, valid, the same in both languages, and in
agreement with the survey they came from (`docs/testbed/hw-compat-table.md`, the
hardware-compatibility table: 16 exercises run on hardware as-is, 8 need a different
injection method, 0 are Renode-only).

The table itself lands in a separate change, so the comparison against it skips when the
file is absent rather than failing a tree that legitimately does not have it yet. The
presence/validity/parity checks below do not skip: they read only the exercise READMEs,
which are always here.
"""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

from cuberange import exercises as core  # noqa: E402
from cuberange.gui import catalog  # noqa: E402
import syllabus  # noqa: E402

RECORDS = core.all_exercises()
BY_ID = {entry["id"]: entry for entry in RECORDS}
COMPAT_TABLE = REPO / "docs" / "testbed" / "hw-compat-table.md"

NEW_FIELDS = ("targets", "hw_class", "hw_evidence")

#: The survey's class wording, as written in the table, to the front-matter value.
TABLE_CLASS = {
    "Runs on hardware as-is": "as-is",
    "Needs a different injection method": "changed",
    "Out of scope for hardware (Renode only)": "renode-only",
}


def test_there_are_24_exercises_with_metadata():
    assert len(RECORDS) == 24, f"found {len(RECORDS)} exercises, expected 24"
    assert len(BY_ID) == 24, "exercise ids are not unique"


@pytest.mark.parametrize("ex_id", sorted(BY_ID))
def test_every_exercise_declares_valid_targets_and_hw_class(ex_id):
    entry = BY_ID[ex_id]
    assert entry["targets"], f"{ex_id} declares no targets"
    unknown = set(entry["targets"]) - set(core.KNOWN_TARGETS)
    assert not unknown, f"{ex_id} declares unknown targets: {sorted(unknown)}"
    assert "renode" in entry["targets"], f"{ex_id} does not run on renode"
    assert entry["hw_class"] in core.HW_CLASSES, (
        f"{ex_id}'s hw_class is {entry['hw_class']!r}, expected one of {core.HW_CLASSES}")
    # The two fields must agree: a Renode-only exercise cannot claim the hw target,
    # and an exercise classified for hardware cannot omit it.
    assert ("hw" in entry["targets"]) == (entry["hw_class"] != "renode-only"), (
        f"{ex_id}: targets={entry['targets']} contradicts hw_class={entry['hw_class']!r}")
    assert entry["hw_evidence"].strip(), f"{ex_id} names no hardware evidence/equipment"


def test_the_classification_matches_the_survey_totals():
    """16 as-is / 8 changed / 0 renode-only, per the compatibility survey."""
    counts = {}
    for entry in RECORDS:
        counts[entry["hw_class"]] = counts.get(entry["hw_class"], 0) + 1
    assert counts == {"as-is": 16, "changed": 8}, counts


@pytest.mark.parametrize("ex_id", sorted(BY_ID))
def test_the_japanese_readme_declares_the_same_classification(ex_id):
    """Machine-readable fields are not translated; the two versions must agree exactly."""
    directory = REPO / "exercises" / BY_ID[ex_id]["dir"]
    en = core.parse_front_matter(directory / "README.md")
    ja = core.parse_front_matter(directory / "README.ja.md")
    for field in NEW_FIELDS:
        assert ja.get(field) == en.get(field), (
            f"{ex_id}: README.ja.md says {field}={ja.get(field)!r}, "
            f"README.md says {en.get(field)!r}")


def _table_classes() -> dict:
    """Exercise id -> hw_class, parsed from the compatibility table's per-exercise rows."""
    out = {}
    for line in COMPAT_TABLE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| EX-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        ex_id = re.match(r"(EX-[A-Z][0-9]{2})", cells[0]).group(1)
        wording = cells[3].strip("*")
        assert wording in TABLE_CLASS, f"{ex_id}: unrecognised table class {wording!r}"
        out[ex_id] = TABLE_CLASS[wording]
    return out


def test_hw_class_matches_the_compatibility_table():
    if not COMPAT_TABLE.is_file():
        pytest.skip("docs/testbed/hw-compat-table.md not in this tree yet (separate change)")
    table = _table_classes()
    assert len(table) == 24, f"parsed {len(table)} rows from the compatibility table"
    mismatches = {ex_id: (BY_ID[ex_id]["hw_class"], table[ex_id])
                  for ex_id in table
                  if ex_id not in BY_ID or BY_ID[ex_id]["hw_class"] != table[ex_id]}
    assert not mismatches, f"front matter (first) disagrees with the table (second): {mismatches}"


def test_the_parser_is_a_single_implementation():
    """syllabus and the GUI catalog delegate to cuberange.exercises rather than re-parsing."""
    assert syllabus.FIELDS == core.FIELDS
    assert catalog.FIELDS == core.FIELDS
    for entry in RECORDS:
        readme = REPO / "exercises" / entry["dir"] / "README.md"
        assert syllabus.front_matter(readme) == core.parse_front_matter(readme)
        found = catalog.get_exercise(entry["id"])
        for field in core.FIELDS:
            assert found[field] == entry[field]


def test_the_gui_list_exposes_the_classification():
    listed = {entry["id"]: entry for entry in catalog.list_exercises()}
    assert set(listed) == set(BY_ID)
    for ex_id, entry in listed.items():
        assert entry["targets"] == BY_ID[ex_id]["targets"]
        assert entry["hw_class"] == BY_ID[ex_id]["hw_class"]
