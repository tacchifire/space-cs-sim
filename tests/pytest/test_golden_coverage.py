"""Every committed golden vector must be read by something.

`tests/golden/crc.json` and `space_packet.json` sat in the tree consumed by nothing: the tests that
were supposed to use them called `pytest.importorskip` on a live library and recomputed instead. So
the files were not evidence, they were decoration, and the conformance layer they represented was
conditional on a pip install - which is exactly how it silently vanished from a run before (design
section 16, W26).

A vector nobody reads is worse than no vector, because it looks like coverage. This is the guard.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GOLDEN_DIR = REPO / "tests" / "golden"
TEST_DIR = REPO / "tests" / "pytest"


def _all_test_source() -> str:
    """Every test's source EXCEPT this file's.

    Including it would make the coverage check self-satisfying: the parametrize list below names
    all four files, so every one of them would appear in the corpus and "is read by a test" would
    be true because this test mentions it. That is precisely the shape of vacuous pass the file
    exists to prevent, and the first version of it had the bug.
    """
    return "\n".join(p.read_text() for p in sorted(TEST_DIR.glob("*.py"))
                     if p.name != Path(__file__).name)


def test_the_golden_directory_is_not_empty():
    files = sorted(GOLDEN_DIR.glob("*.json"))
    assert files, (
        f"{GOLDEN_DIR} has no vectors. Rebuild them: tools/oracles/build.sh, then "
        f"tools/gen_golden.py.")
    # The four gen_golden.py emits. A missing one is a layer with no outside opinion.
    names = {p.name for p in files}
    assert names >= {"crc.json", "csp.json", "pus.json", "space_packet.json"}, (
        f"missing golden files: {sorted({'crc.json', 'csp.json', 'pus.json', 'space_packet.json'} - names)}")


@pytest.mark.parametrize("name", ["crc.json", "csp.json", "pus.json", "space_packet.json"])
def test_each_golden_file_is_read_by_a_test(name):
    source = _all_test_source()
    assert name in source, (
        f"{name} is committed and no test in tests/pytest reads it. Either a test should consume "
        f"it or it should not be in the tree - a vector nobody checks against looks like coverage "
        f"and is not.")


@pytest.mark.parametrize("name", ["crc.json", "csp.json", "pus.json", "space_packet.json"])
def test_each_golden_file_records_who_produced_it(name):
    """A vector with no stated provenance cannot be told apart from our own output."""
    doc = json.loads((GOLDEN_DIR / name).read_text())
    blob = json.dumps(doc).lower()
    assert any(o in blob for o in ("oracle", "libcsp", "spacepackets", "crcmod", "cryptolib")), (
        f"{name} does not say which implementation produced it")


def test_no_conformance_test_can_skip_itself_out_of_existence():
    """importorskip in a conformance test removes the layer without failing anything.

    `make check` asserts the oracles import before running, which patches the symptom. The cure is
    that the tests reading committed vectors need no library at all - so this asserts the two files
    that carry the committed-vector layer do not reach for one.
    """
    import ast

    for name in ("test_golden_frame.py", "test_oracle_pus.py", "test_golden_coverage.py"):
        tree = ast.parse((TEST_DIR / name).read_text())
        # Parsed rather than grepped. The first version searched the text and tripped on the word
        # appearing in a docstring that explains why it is banned.
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "importorskip"]
        assert not calls, (
            f"{name} calls importorskip at line {calls[0].lineno}. It reads committed vectors and "
            f"must run everywhere, or the conformance layer is conditional again.")
