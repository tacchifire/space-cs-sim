"""The teaching order is a graph, and a graph can be broken without anyone noticing.

Every exercise declares `prerequisite:` in its front matter. Six correct edges, and until
tools/syllabus.py nothing read them - the curriculum was true and unreachable, spread across six
files a student opens one at a time.

Reading it is not enough on its own. A prerequisite naming an exercise that does not exist sends a
student looking for a lesson nobody wrote. A cycle sends them round one. An exercise no path
reaches is one nobody is ever told to take. None of those three announce themselves, and all three
are cheap to detect.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import syllabus                                            # noqa: E402

FOUND = syllabus.exercises()

#: Front-matter fields that are facts about the curriculum, not prose. The two language versions
#: must agree on all of them; `title` is the one that is meant to differ.
SHARED = ("id", "layer", "difficulty", "duration", "prerequisite")

DIFFICULTY = {"introductory": 0, "intermediate": 1, "advanced": 2}


def test_there_are_exercises_to_order():
    assert len(FOUND) >= 5, f"only {len(FOUND)} exercises found; this file is checking nothing"


def test_every_prerequisite_names_an_exercise_that_exists():
    dangling = {i: fm["prerequisite"] for i, fm in FOUND.items()
                if fm.get("prerequisite") and fm["prerequisite"] not in FOUND}
    assert not dangling, (
        f"these name a prerequisite that is not an exercise here: {dangling}. A student following "
        f"it goes looking for a lesson nobody wrote.")


def test_the_graph_has_no_cycle():
    """A cycle is not a curriculum, and neither exercise in one can ever be started."""
    for start in FOUND:
        seen, node = set(), start
        while node:
            if node in seen:
                pytest.fail(f"prerequisite cycle reachable from {start}: {sorted(seen)}")
            seen.add(node)
            node = FOUND[node].get("prerequisite") or None


def test_at_least_one_exercise_needs_nothing():
    roots = [i for i, fm in FOUND.items() if not fm.get("prerequisite")]
    assert roots, "every exercise has a prerequisite, so there is nowhere to begin"


def test_every_exercise_is_reachable_from_a_root():
    """An unreachable exercise is one no student is ever told to take."""
    reached = set(syllabus.order(FOUND))
    assert reached == set(FOUND), f"unreachable: {sorted(set(FOUND) - reached)}"


def test_difficulty_never_decreases_along_an_edge():
    """Not a law of nature, but it is what this set was built to do, and a reversal is a signal
    that an exercise was slotted in where it does not belong."""
    for i, fm in FOUND.items():
        pre = fm.get("prerequisite")
        if not pre:
            continue
        a, b = DIFFICULTY.get(FOUND[pre]["difficulty"]), DIFFICULTY.get(fm["difficulty"])
        assert a is not None and b is not None, f"unknown difficulty on {i} or {pre}"
        assert b >= a, (
            f"{i} ({fm['difficulty']}) comes after {pre} ({FOUND[pre]['difficulty']})")


def test_every_duration_is_a_range_in_minutes():
    for i, fm in FOUND.items():
        assert re.fullmatch(r"\d+-\d+ min", fm.get("duration", "")), (
            f"{i}'s duration is {fm.get('duration')!r}; an instructor plans a session from these")


@pytest.mark.parametrize("ex_id", sorted(FOUND))
def test_the_two_languages_declare_the_same_curriculum(ex_id):
    """A Japanese reader must not get a different order, or different prerequisites.

    Only `title` may differ - it is the one field meant to be translated.
    """
    d = REPO / "exercises" / FOUND[ex_id]["dir"]
    ja = syllabus.front_matter(d / "README.ja.md")
    en = syllabus.front_matter(d / "README.md")
    for field in SHARED:
        assert ja.get(field) == en.get(field), (
            f"{ex_id}: README.ja.md says {field}={ja.get(field)!r}, README.md says "
            f"{en.get(field)!r}")
    assert ja.get("title") and ja.get("title") != en.get("title"), (
        f"{ex_id}'s Japanese title is missing or untranslated")


def test_the_tool_runs_and_lists_every_exercise():
    """The renderer, not just the model behind it."""
    proc = subprocess.run([sys.executable, str(REPO / "tools" / "syllabus.py")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    for ex_id, fm in FOUND.items():
        assert ex_id in proc.stdout
        assert f"make exercise EX={fm['dir']}" in proc.stdout, (
            f"the syllabus does not tell a student how to start {ex_id}")


def test_the_dot_output_has_an_edge_for_every_prerequisite():
    proc = subprocess.run([sys.executable, str(REPO / "tools" / "syllabus.py"), "--dot"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    edges = sum(1 for ln in proc.stdout.splitlines() if "->" in ln)
    assert edges == sum(1 for fm in FOUND.values() if fm.get("prerequisite"))
