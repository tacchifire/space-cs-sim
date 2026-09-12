"""Numbers written in prose must match what is actually there.

Four instances of one defect turned up in a single session's work:

  - tools/ci.sh said "golden vectors (4 files)" while the list beside it had five;
  - CLAUDE.md said "Five exercises ... 17 assertions" after a sixth was added;
  - README.ja.md said four attack origins were covered while listing five in English;
  - firmware-matrix.yml's header described four checks after the gate had grown to six.

Each was correct when written. Counts in prose are a claim like any other, and this project's rule
is that a claim comes with the command that establishes it - so here is the command.

WHAT THIS DOES NOT COVER, because a guard oversold is worse than none: only counts that can be
derived cheaply and exactly. "836 Kconfig symbols" needs a build; "40 probe checks" needs a
six-minute Renode run; "13% of launches" is a measurement nobody can re-derive from the tree. Those
stay prose, and stay the author's responsibility.
"""
import ast
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- what is actually there

def exercise_dirs() -> list[Path]:
    return sorted(p for p in (REPO / "exercises").iterdir()
                  if p.is_dir() and p.name.startswith("EX-"))


def exercise_assertions() -> int:
    """Test functions across every verify_ex_*.py, counted from the AST rather than by grep.

    Grep would count the word `def test_` inside a docstring explaining what a test does, which is
    the kind of thing these files are full of.
    """
    total = 0
    for path in sorted(REPO.glob("exercises/*/verify_ex_*.py")):
        tree = ast.parse(path.read_text())
        total += sum(1 for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name.startswith("test_"))
    return total


def golden_files() -> int:
    return len(list((REPO / "tests" / "golden").glob("*.json")))


def firmware_pairs() -> int:
    doc = yaml.safe_load((REPO / "firmware-matrix.yml").read_text())
    return len(doc["pairs"])


ACTUAL = {
    "exercises": lambda: len(exercise_dirs()),
    "exercise files": lambda: len(EXERCISE_FILES),
    "assertions": exercise_assertions,
    "golden files": golden_files,
    "firmware pairs": firmware_pairs,
}


# --------------------------------------------------------------------------- what the prose claims

#: (label, file, regex). The regex must capture exactly one number, and it must be specific enough
#: that it cannot match an unrelated sentence - a loose pattern here produces failures nobody can
#: act on, and a guard people cannot act on gets deleted.
CLAIMS = [
    ("exercises",      "CLAUDE.md",            r"(\w+) exercises work today"),
    ("assertions",     "CLAUDE.md",            r"(\d+) assertions, all measured"),
    ("exercises",      "CLAUDE.ja.md",         r"現在 (\d+) つの演習が動く"),
    ("assertions",     "CLAUDE.ja.md",         r"アサーションは (\d+)、すべて実測である"),
    ("exercise files", "CONTRIBUTING.md",    r"An exercise is (\w+) files"),
    ("exercise files", "CONTRIBUTING.ja.md", r"演習は(\w+)つのファイル"),
    ("firmware pairs", "tools/config_diff_gate.py", None),   # checked below, not by regex
]

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _as_int(text: str) -> int:
    return WORDS.get(text.lower(), None) if not text.isdigit() else int(text)


@pytest.mark.parametrize("label,rel,pattern",
                         [(a, b, c) for a, b, c in CLAIMS if c is not None],
                         ids=lambda v: str(v))
def test_a_documented_count_matches_reality(label, rel, pattern):
    text = (REPO / rel).read_text()
    matches = re.findall(pattern, text)
    assert matches, (
        f"{rel} no longer contains a sentence matching {pattern!r}. Either the wording changed - "
        f"update this pattern - or the claim was deleted, in which case delete the row.")
    assert len(matches) == 1, f"{rel} states the {label} count {len(matches)} times: {matches}"

    claimed = _as_int(matches[0])
    assert claimed is not None, f"{rel} spells the count as {matches[0]!r}, which is not a number"
    actual = ACTUAL[label]()
    assert claimed == actual, (
        f"{rel} says {matches[0]} {label}; there are {actual}. The prose was right when it was "
        f"written, which is exactly why this test exists.")


def test_the_gate_documents_as_many_checks_as_it_runs():
    """config_diff_gate.py's docstring enumerates its checks. It described four while running six."""
    source = (REPO / "tools" / "config_diff_gate.py").read_text()
    # The numbered comments inside check_pair are the checks themselves.
    performed = set(re.findall(r"^    # (\d)\. ", source, re.M))
    assert performed, "check_pair no longer numbers its checks; this guard cannot see them"
    documented = set(re.findall(r"^(\d)\. ", (REPO / "CLAUDE.md").read_text(), re.M))
    assert performed <= documented, (
        f"config_diff_gate.py performs checks {sorted(performed)} and CLAUDE.md documents "
        f"{sorted(documented)}. A check nobody knows about is one nobody maintains.")


#: What makes a directory an exercise. Named here rather than inline because CONTRIBUTING.md
#: states the COUNT in both languages, and CLAIMS below checks the prose against len() of this -
#: the prose said five for a long time while this required seven, the two Japanese files having
#: fallen out of the sentence in both languages.
EXERCISE_FILES = ("README.md", "README.ja.md", "mitigation.md", "mitigation.ja.md",
                  "solve.py", "scenario.resc", "verify_ex_*.py")


def test_every_exercise_directory_is_a_complete_exercise():
    """Every file in EXERCISE_FILES. A half-landed exercise counts as one here."""
    missing = []
    for d in exercise_dirs():
        for name in [n for n in EXERCISE_FILES if not n.startswith("verify_")]:
            if not (d / name).is_file():
                missing.append(f"{d.name}/{name}")
        if not list(d.glob("verify_ex_*.py")):
            missing.append(f"{d.name}/verify_ex_*.py")
    assert not missing, "incomplete exercises: " + ", ".join(missing)


def test_every_exercise_asserts_at_least_three_things():
    """The attack lands, the mitigation blocks it, and the mitigated build still does its job."""
    thin = []
    for d in exercise_dirs():
        n = 0
        for path in d.glob("verify_ex_*.py"):
            tree = ast.parse(path.read_text())
            n += sum(1 for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name.startswith("test_"))
        if n < 3:
            thin.append(f"{d.name} ({n})")
    assert not thin, (
        "these assert fewer than three things, so a mitigation that simply broke the feature "
        "would pass: " + ", ".join(thin))


def test_the_counts_are_not_all_zero():
    """A guard whose inputs all return 0 agrees with any prose that says 0."""
    for label, fn in ACTUAL.items():
        assert fn() > 0, f"{label} counted 0, so every comparison above is vacuous"


# --------------------------------------------------------------------------- pointers

WHERE_BLOCK = re.compile(r"^## (?:Where things are|どこに何があるか)\n+```\n(.*?)```",
                         re.S | re.M)


def _expand(entry: str) -> list[Path]:
    """A map entry to the paths it names.

    Brace and glob forms are deliberate in the map - `firmware/apps/{comm,obc,eps,adcs}/` says
    more to a reader than four lines would - so they are expanded rather than skipped. Skipping
    them is how `adcs` stayed missing from that very entry after the role was added.
    """
    import itertools
    forms = [entry]
    while any("{" in f for f in forms):
        out = []
        for f in forms:
            if "{" not in f:
                out.append(f)
                continue
            head, rest = f.split("{", 1)
            body, tail = rest.split("}", 1)
            out += [head + choice + tail for choice in body.split(",")]
        forms = out
    paths = []
    for f in forms:
        f = f.rstrip("/")
        paths += list(REPO.glob(f)) if any(c in f for c in "*?[") else [REPO / f]
    return paths


@pytest.mark.parametrize("doc", ["CLAUDE.md", "CLAUDE.ja.md"])
def test_every_path_the_working_notes_name_exists(doc):
    """A map that points at something that moved sends the next reader looking for it."""
    text = (REPO / doc).read_text()
    m = WHERE_BLOCK.search(text)
    assert m, f"{doc} no longer has a 'where things are' block, or its heading changed"

    entries = [line.split()[0] for line in m.group(1).splitlines() if line.strip()]
    assert len(entries) >= 20, f"{doc}'s map has only {len(entries)} entries"

    missing = []
    for entry in entries:
        found = _expand(entry)
        if not found or not all(p.exists() for p in found):
            missing.append(entry)
    assert not missing, f"{doc} names paths that do not exist: {missing}"


def test_the_two_maps_name_the_same_paths():
    """The map is a fact, not prose. Its two copies drifting is the same defect as a lost row."""
    def entries(doc):
        m = WHERE_BLOCK.search((REPO / doc).read_text())
        return [line.split()[0] for line in m.group(1).splitlines() if line.strip()]
    en, ja = entries("CLAUDE.md"), entries("CLAUDE.ja.md")
    assert en == ja, (
        "the English and Japanese maps name different paths:\n"
        f"  only in en: {[e for e in en if e not in ja]}\n"
        f"  only in ja: {[e for e in ja if e not in en]}")
