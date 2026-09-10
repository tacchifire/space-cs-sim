"""Every defect this repository names must be in the register the documents point at.

CLAUDE.md ends its list of silent failure modes with "The full register is section 3.2 of the
design." That is a claim about a document, and it was false: D22 - `cpu TranslateAddress` caching
by address rather than by access type, which is why EX-F01 is code reuse and not shellcode -
existed in probe.sh and in CLAUDE.md and had never been added to section 3.2. A reader following
the pointer would have found twenty-one entries and concluded that was all of them.

WHAT THIS DOES NOT CHECK, said plainly because the obvious stronger version would be wrong: it
does not require every defect to have a probe. probe.sh reproduces the CAPABILITIES the design
relies on, plus a handful of negative assertions pinning defects that a future Renode might fix.
Several register entries are avoided by design rather than probed - D18's answer to "CANHub does
not simulate bus ACK" is that no exercise depends on it, and there is nothing to run. Asserting a
probe per defect would be inventing a rule the project never made, which is the shape of mistake
section 16 keeps recording.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DESIGN = REPO / "docs" / "superpowers" / "specs" / "2026-08-28-cuberange-design.md"

#: Files that may name a defect. Restricted so a "D3" inside an unrelated identifier elsewhere
#: does not become a phantom register entry.
SOURCES = ("CLAUDE.md", "CLAUDE.ja.md", "tools/renode-probe/probe.sh",
           "ASSURANCE.md", "ASSURANCE.ja.md")

DEFECT = re.compile(r"(?<![\w.])D([1-9][0-9]?)(?![\w])")


def registered() -> dict[int, str]:
    """The register's own rows, in order."""
    rows = {}
    for m in re.finditer(r"^\| D(\d+) \| (.{0,60})", DESIGN.read_text(), re.M):
        rows[int(m.group(1))] = m.group(2)
    return rows


def referenced() -> dict[int, list[str]]:
    """Defect numbers named outside the register, and where."""
    found: dict[int, list[str]] = {}
    for rel in SOURCES:
        path = REPO / rel
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for m in DEFECT.finditer(line):
                found.setdefault(int(m.group(1)), []).append(f"{rel}:{n}")
    return found


def test_the_register_is_not_empty():
    """A regex that stopped matching would make every check below pass by vacuum."""
    assert len(registered()) >= 20, f"only {len(registered())} register rows found"


def test_every_defect_named_anywhere_is_in_the_register():
    rows = registered()
    missing = {d: where for d, where in referenced().items() if d not in rows}
    assert not missing, (
        "these defect numbers are used in the tree and absent from section 3.2, which CLAUDE.md "
        "calls the full register:\n  "
        + "\n  ".join(f"D{d} ({', '.join(w[:3])})" for d, w in sorted(missing.items())))


def test_the_register_is_contiguous_and_has_no_duplicates():
    """A gap means an entry was deleted rather than superseded; a duplicate means two truths."""
    text = DESIGN.read_text()
    numbers = [int(m.group(1)) for m in re.finditer(r"^\| D(\d+) \|", text, re.M)]
    assert len(numbers) == len(set(numbers)), (
        f"duplicate defect numbers: {sorted(n for n in numbers if numbers.count(n) > 1)}")
    assert numbers == sorted(numbers), f"the register is out of order: {numbers}"
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"the register skips numbers: {sorted(set(range(1, max(numbers) + 1)) - set(numbers))}")


def test_the_newest_defect_is_pinned_by_something_that_can_fail():
    """D22 in particular, because its whole value is being a negative assertion.

    It says a future Renode that fixes the cache must make the probe FAIL rather than quietly
    change what EX-F01's write-ups mean. That is only true while probe.sh actually asserts it.
    """
    probe = (REPO / "tools" / "renode-probe" / "probe.sh").read_text()
    assert "D22" in probe, "D22 is no longer pinned in probe.sh"
    assert re.search(r"TranslateAddress", probe), (
        "probe.sh mentions D22 but no longer queries TranslateAddress, so the pin is a comment")


@pytest.mark.parametrize("doc", ["CLAUDE.md", "CLAUDE.ja.md"])
def test_the_working_notes_still_point_at_the_register(doc):
    """If the pointer goes, the guard above is guarding a claim nobody makes any more."""
    text = (REPO / doc).read_text()
    assert re.search(r"3\.2", text), (
        f"{doc} no longer points at section 3.2 as the full register; either restore the pointer "
        f"or delete this file, but do not leave a check for a claim that is gone")
