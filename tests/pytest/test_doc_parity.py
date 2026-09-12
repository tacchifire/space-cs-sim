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
headings, table rows and list items, a section, a row or a bullet cannot go missing silently.

All three compare structure, and a fourth drift slipped under all of them: `CONTRIBUTING.md` told
an English reader to verify a vuln/hardened pair by typing a `diff` of two Kconfig files, while
the Japanese version had already been rewritten around the gate that checks six things and runs
in `make check`. Same headings, same rows, same bullets, different instructions. So the commands
inside the code blocks are compared too - those are the part of a document that does not
translate. That is still not the same as the two versions saying the same thing, and this file
does not claim it is.
"""
import re
import shutil
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


# --------------------------------------------------------------------------------------------
# Commands
#
# Headings, rows and bullets compare STRUCTURE. Two documents can match on all three and still
# tell the reader to run different things, because the thing the reader runs lives inside a code
# block and code blocks were never compared.
#
# That is not hypothetical. `CONTRIBUTING.md` told an English reader to verify a vuln/hardened
# pair with a `diff` of two Kconfig files typed by hand; the Japanese version had already been
# rewritten around `tools/config_diff_gate.py`, which checks six things instead of that one and
# runs inside `make check`. `EX-B01`'s mitigation had the same split, and its English half went
# on to say "only the length check changes" about an exercise whose flag is
# CUBERANGE_EPS_REQUIRE_AUTH — the length check belongs to EX-F01. Every structural check passed
# throughout.
#
# Commands are the part of a document that does not translate. Prose does, comments do,
# placeholders do; `make pair-gate` does not.

def _fenced_blocks(text: str) -> list[tuple[str, str]]:
    """(info string, body) for each fenced block."""
    out: list[tuple[str, str]] = []
    fence: str | None = None
    info = ""
    buf: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if fence is None and re.match(r"^(```|~~~)", stripped):
            fence, info, buf = stripped[:3], stripped[3:].strip(), []
            continue
        if fence and stripped.startswith(fence):
            out.append((info, "\n".join(buf)))
            fence = None
            continue
        if fence:
            buf.append(line)
    return out


def _is_command(token: str) -> bool:
    """Whether `token` names something runnable.

    Probed, not guessed: `shutil.which` for PATH, the filesystem for repo-relative scripts. The
    alternative is an allowlist of command names, which would have to be extended every time a
    document mentions a new tool and would fail open until someone noticed.

    This is what keeps ASCII diagrams out. The exercise READMEs draw their topology inside
    unlabelled code blocks - `COMM  OBC  EPS  attacker  ------  one CAN hub`, `you are here` -
    and those lines are prose that happens to be monospaced. `COMM` and `you` are not on PATH.
    """
    if token.startswith("./") or token.startswith("tools/"):
        return (REPO / token.removeprefix("./")).is_file()
    if "/" in token or "=" in token:
        return False
    return shutil.which(token) is not None


def _strip_trailing_prose(line: str) -> str:
    """Drop the trailing comment and any column-aligned description.

    Both are translated on purpose and neither changes what runs:

        make exercise EX=EX-A01-adcs-tumble    # runs the scenario / シナリオを起動したままにする
        tools/setup-toolchain.sh               Zephyr + SDK, no root / root 不要、Docker 不要

    The `#` scan honours quotes so that `grep '#define'` keeps its argument. The column split is
    on a run of two or more spaces, which is how this repository aligns those descriptions; real
    commands separate their arguments with one.
    """
    out: list[str] = []
    quote: str | None = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return re.split(r"\s{2,}", "".join(out).strip())[0].strip()


def commands(text: str) -> list[str]:
    """Runnable lines, in order, with translated decoration removed."""
    found: list[str] = []
    for info, body in _fenced_blocks(text):
        if info not in ("bash", "sh", "console", "shell", ""):
            continue
        for raw in body.splitlines():
            line = re.sub(r"^\$\s+", "", raw.strip())
            if not line or line.startswith("#"):
                continue
            head = line.split()[0]
            if not _is_command(head):
                continue
            #: `<command>` and `<コマンド>` are the same placeholder.
            cleaned = re.sub(r"<[^>]*>", "<>", _strip_trailing_prose(line))
            if cleaned:
                found.append(cleaned)
    return found


def test_command_extractor_keeps_commands_and_drops_decoration():
    """The extractor itself, on input where the answer is known.

    Without this, `commands()` returning [] for every document would make the comparison below
    pass on all 24 pairs while comparing nothing - the failure mode of W42, where a check that
    never ran was documented as running.
    """
    kept = commands(
        "```bash\n"
        "make pair-gate\n"
        "python3 -m cuberange.safety.isolate -- <command>\n"
        "tools/setup-toolchain.sh        Zephyr + SDK, no root\n"
        "grep '#define' firmware/common/cuberange_proto.h\n"
        "```\n"
    )
    assert kept == [
        "make pair-gate",
        "python3 -m cuberange.safety.isolate -- <>",
        "tools/setup-toolchain.sh",
        "grep '#define' firmware/common/cuberange_proto.h",
    ], kept

    dropped = commands(
        "```\n"
        "COMM  OBC  EPS  attacker  ------  one CAN hub\n"
        "you are here                            the target\n"
        "gpioPortD.5 = COMM power rail\n"
        "# a comment line\n"
        "```\n"
        "```c\n"
        "make this_is_c_not_shell\n"
        "```\n"
    )
    assert dropped == [], dropped

    #: The two spellings of a translated line must collapse to the same command...
    en = commands("```bash\nmake exercise EX=EX-B01      # runs the scenario\n```\n")
    ja = commands("```bash\nmake exercise EX=EX-B01      # シナリオを起動する\n```\n")
    assert en == ja == ["make exercise EX=EX-B01"]

    #: ...and a changed command must not.
    assert commands("```bash\nmake pair-gate\n```\n") != commands(
        "```bash\npython3 tools/config_diff_gate.py\n```\n"
    )


@pytest.mark.parametrize("en,ja", _doc_pairs(), ids=lambda p: p.name)
def test_both_languages_tell_the_reader_to_run_the_same_thing(en: Path, ja: Path):
    a, b = commands(en.read_text()), commands(ja.read_text())
    assert a == b, (
        f"{en.relative_to(REPO)} and {ja.relative_to(REPO)} give different instructions.\n"
        f"  en only: {[x for x in a if x not in b]}\n"
        f"  ja only: {[x for x in b if x not in a]}\n"
        "A reader following one of these does something the other reader does not."
    )
