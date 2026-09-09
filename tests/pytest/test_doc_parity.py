"""Every Japanese document must keep its English counterpart's structure.

This repository ships each document twice. Two spellings of one fact drift, and a translation that
drifts loses whole sections without anything reporting it: `ASSURANCE.ja.md` was missing
"Where the claims come from" entirely — the section that says where the project's claims come from,
absent from the assurance document, in the language most of its readers use.

Headings are compared by LEVEL, not by text: the text is translated, the structure is not. A
missing, added or re-nested section changes the sequence and fails here.
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
