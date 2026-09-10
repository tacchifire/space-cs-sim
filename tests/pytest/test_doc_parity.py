"""Every Japanese document must keep its English counterpart's structure.

This repository ships each document twice. Two spellings of one fact drift, and a translation that
drifts loses whole sections without anything reporting it: `ASSURANCE.ja.md` was missing
"Where the claims come from" entirely — the section that says where the project's claims come from,
absent from the assurance document, in the language most of its readers use.

Headings are compared by LEVEL, not by text: the text is translated, the structure is not. A
missing, added or re-nested section changes the sequence and fails here.

Headings alone were not enough. With every heading matching, `ASSURANCE.ja.md` still claimed PUS-C
had no independent oracle and that the golden vectors were not committed - both long since untrue -
and `SECURITY.ja.md` was missing three of the nine deliberate weaknesses the English version
discloses, including the privileged handler EX-F01 targets and the ground segment's unchecked
import path. A security disclosure that discloses less in one language is not a translation
problem. Table rows are compared too, because both drifts showed up there.

Then a third drift appeared that rows could not see either: `README.ja.md` was missing EX-G01
from its exercise list entirely and still said four attack origins were covered when there were
five. A list item is as countable as a table row, so those are compared as well. Between
headings, table rows and list items, a section, a row or a bullet cannot go missing silently -
which is not the same as the two versions saying the same thing, and this file does not claim it
is.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Documents with no `.ja.md`, and why. Naming them here means a NEW document without a translation
#: fails this file rather than joining a silent backlog.
NO_JA_COUNTERPART = {
    "docs/superpowers/specs/2026-08-28-cuberange-design.md": "already written in Japanese",
    "docs/superpowers/plans/2026-08-29-cuberange-p0.md": "already written in Japanese",
    "docs/HANDOFF-raspberry-pi.md": "a handoff note between sessions, not a published document",
}


def headings(text: str) -> list[tuple[int, str]]:
    """Headings outside fenced code blocks.

    The fence handling is not decoration. A naive `^#+` match counts `#if CUBERANGE_EPS_REQUIRE_AUTH`
    and `# no output: not one Zephyr option differs` inside the mitigation write-ups' code blocks,
    and a first pass reported EX-B01 as drifted when the two versions are structurally identical.
    A check that cries wolf gets switched off.
    """
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if fence is None and re.match(r"^(```|~~~)", stripped):
            fence = stripped[:3]
            continue
        if fence and stripped.startswith(fence):
            fence = None
            continue
        if fence:
            continue
        if m := re.match(r"^(#{1,6}) +(\S.*)$", line):
            out.append((len(m.group(1)), m.group(2)))
    return out


def _doc_pairs() -> list[tuple[Path, Path]]:
    pairs = []
    for ja in sorted(REPO.glob("**/*.ja.md")):
        if ".git" in ja.parts:
            continue
        pairs.append((ja.with_name(ja.name.replace(".ja.md", ".md")), ja))
    return pairs


@pytest.mark.parametrize("en,ja", _doc_pairs(), ids=lambda p: p.name)
def test_the_two_versions_have_the_same_sections(en: Path, ja: Path):
    assert en.is_file(), f"{ja.name} has no English counterpart at {en}"
    a, b = headings(en.read_text()), headings(ja.read_text())
    if [lvl for lvl, _ in a] == [lvl for lvl, _ in b]:
        return

    # Say WHICH section, not just that the counts differ. "en has 5 headings, ja has 4" sends the
    # reader to diff two files by hand.
    detail = ["  en:"] + [f"    {'#' * l} {t}" for l, t in a] + ["  ja:"] + [f"    {'#' * l} {t}" for l, t in b]
    raise AssertionError(
        f"{ja.relative_to(REPO)} and {en.relative_to(REPO)} no longer have the same section "
        f"structure. Headings are compared by level because the text is translated; a section was "
        f"added, dropped or re-nested in one of them.\n" + "\n".join(detail))


def test_every_document_either_has_a_translation_or_is_listed():
    """A new English document with no Japanese version must be a decision, not an oversight."""
    missing = []
    for en in sorted(REPO.glob("**/*.md")):
        if ".git" in en.parts or en.name.endswith(".ja.md") or ".pytest_cache" in en.parts:
            continue
        rel = str(en.relative_to(REPO))
        if rel in NO_JA_COUNTERPART:
            continue
        if not en.with_name(en.name.replace(".md", ".ja.md")).is_file():
            missing.append(rel)
    assert not missing, (
        "these documents have no .ja.md and are not listed in NO_JA_COUNTERPART: "
        + ", ".join(missing)
        + ". Translate them, or add them to the list with the reason.")


def test_the_listed_exemptions_still_exist():
    """An exemption for a deleted file is a comment pretending to be a decision."""
    gone = [rel for rel in NO_JA_COUNTERPART if not (REPO / rel).is_file()]
    assert not gone, f"NO_JA_COUNTERPART names files that no longer exist: {gone}"


def test_the_pairs_list_is_not_empty():
    """A glob that matches nothing would make every test above pass by vacuum."""
    assert len(_doc_pairs()) >= 15, f"only {len(_doc_pairs())} document pairs found"


def _countable(text: str, pattern: str) -> int:
    """Count lines matching `pattern` outside fenced code blocks.

    Fences matter for the same reason they matter for headings: a code block full of pipes or
    dashes would be counted, and the two versions' code blocks legitimately differ.
    """
    count, fence = 0, None
    for line in text.splitlines():
        stripped = line.strip()
        if fence is None and re.match(r"^(```|~~~)", stripped):
            fence = stripped[:3]
            continue
        if fence and stripped.startswith(fence):
            fence = None
            continue
        if fence:
            continue
        if re.match(pattern, stripped):
            count += 1
    return count


@pytest.mark.parametrize("en,ja", _doc_pairs(), ids=lambda p: p.name)
def test_the_two_versions_have_the_same_list_items(en: Path, ja: Path):
    """The drift that headings and table rows both missed.

    README.ja.md had lost an entire exercise from its list - EX-G01, the one the next exercise
    builds on - while every heading matched and every table matched.
    """
    pattern = r"^[-*] "
    a = _countable(en.read_text(), pattern)
    b = _countable(ja.read_text(), pattern)
    assert a == b, (
        f"{ja.relative_to(REPO)} has {b} top-level list items against "
        f"{en.relative_to(REPO)}'s {a}. Something was dropped or added; find which rather than "
        f"padding the count.")


def _table_rows(text: str) -> int:
    """Table rows outside fenced code blocks.

    Fences matter here for the same reason they matter for headings: a code block full of pipe
    characters would be counted as a table, and the two versions' code blocks legitimately differ.
    """
    count, fence = 0, None
    for line in text.splitlines():
        stripped = line.strip()
        if fence is None and re.match(r"^(```|~~~)", stripped):
            fence = stripped[:3]
            continue
        if fence and stripped.startswith(fence):
            fence = None
            continue
        if fence:
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            count += 1
    return count


@pytest.mark.parametrize("en,ja", _doc_pairs(), ids=lambda p: p.name)
def test_the_two_versions_have_the_same_table_rows(en: Path, ja: Path):
    """Content parity where content happens to be structural.

    This is not a general content check - it cannot be, the text is translated. It is the one
    place where a dropped row is countable, and both of the real drifts found so far were rows: a
    verification-status table that had gone stale, and three undisclosed weaknesses.
    """
    a, b = _table_rows(en.read_text()), _table_rows(ja.read_text())
    assert a == b, (
        f"{ja.relative_to(REPO)} has {b} table rows against {en.relative_to(REPO)}'s {a}. A row "
        f"was dropped or added in one of them - check which, rather than padding the count.")
