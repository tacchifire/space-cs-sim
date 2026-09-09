"""The Makefile and paths.py derive the build directory twice; they must agree.

Two derivations of one fact are a liability unless something compares them - `test_identity.py`
exists for exactly this reason about `identity.cmake`, and this is the same shape. The Makefile
cannot call Python (every `make help` would pay for an interpreter start) and the Python code
cannot call Make, so the string is written twice and checked here.

The underlying incident: `OUT` defaulted to a bare `/tmp/cuberange` in every checkout. A second
working copy of this repository on the same machine was running `west build -p always` into the
same `build-obc/`, so the pair gate compared one checkout's vulnerable image against the other's
mitigated image. What it reported was four unexplained cache variables. What was true was that the
artifacts belonged to somebody else.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.paths import TAG_LEN, default_out, out_dir  # noqa: E402

MAKEFILE = REPO / "Makefile"


def _make_prints_out() -> str:
    """Ask Make itself, rather than reimplementing its expansion rules and trusting the copy.

    A parse that agrees with my own reading of $(patsubst)/$(notdir)/$(abspath) and disagrees with
    Make proves nothing. `make` is required by tools/ci.sh's environment check, so its absence is a
    broken environment rather than a reason to skip - and a skip here would delete the only
    assertion that touches the real derivation.
    """
    if not shutil.which("make"):
        pytest.fail(
            "make is not on PATH, so the Makefile's own value of OUT cannot be read and this "
            "check would silently cover nothing. Run tools/ci.sh --env-only.")
    tmp = tempfile.mkdtemp(prefix="cuberange-showout.")
    helper = Path(tmp) / "show_out.mk"
    helper.write_text("show-out:\n\t@echo $(OUT)\n")
    try:
        # --no-print-directory and the MAKE* scrub are both needed under `make check`: pytest runs
        # as a child of make there, so this inner make inherits MAKELEVEL and prints "Entering
        # directory"/"Leaving directory" around the one line we want. The first version of this
        # test passed standalone and failed inside the gate for exactly that reason.
        drop = {"OUT", "MAKEFLAGS", "MAKELEVEL", "MFLAGS", "MAKE_TERMOUT", "MAKE_TERMERR"}
        proc = subprocess.run(
            ["make", "--no-print-directory", "-f", str(MAKEFILE), "-f", str(helper), "show-out"],
            cwd=REPO, capture_output=True, text=True, timeout=60,
            # A stray OUT in the ambient environment would make Make echo that instead of its
            # default, and the test would pass while proving nothing about the default.
            env={k: v for k, v in os.environ.items() if k not in drop})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert proc.returncode == 0, f"make failed:\n{proc.stderr}"
    return proc.stdout.strip()


def test_make_and_python_derive_the_same_directory():
    assert _make_prints_out() == str(default_out()), (
        "the Makefile's OUT default and src/cuberange/paths.py disagree. They are two spellings "
        "of one rule; change both or neither.")


def test_the_default_is_not_shared_between_checkouts():
    """The property the whole change exists for.

    Asserting only "the name contains a hash" would pass against a hash of a constant.
    """
    a = default_out(Path("/home/someone/space-cs-sim"))
    b = default_out(Path("/home/someone/elsewhere/space-cs-sim"))
    assert a != b, (
        f"two checkouts sharing a basename resolved to the same build directory ({a}); that is "
        f"the exact collision this default was introduced to prevent")
    assert a.name.startswith("cuberange-space-cs-sim-"), a
    assert len(a.name.rsplit("-", 1)[1]) == TAG_LEN, a


def test_the_makefile_default_is_not_a_bare_shared_path():
    """A clear failure if somebody reverts the line, rather than a confusing gate report later."""
    line = next((l for l in MAKEFILE.read_text().splitlines()
                 if l.startswith("OUT") and "?=" in l), None)
    assert line is not None, "the Makefile no longer defines a default OUT"
    assert line.split("?=")[1].strip() != "/tmp/cuberange", (
        "OUT is back to a path every checkout of this repository shares. Two working copies on "
        "one machine will overwrite each other's firmware and the pair gate will report it as a "
        "flag mismatch. See src/cuberange/paths.py.")


def test_out_overrides_and_an_empty_out_does_not_mean_the_source_tree(monkeypatch):
    """`Path("")` is `.`, and a clean target that removed `.` would remove the checkout."""
    monkeypatch.setenv("OUT", "/tmp/somewhere-else")
    assert out_dir() == Path("/tmp/somewhere-else")

    monkeypatch.setenv("OUT", "")
    assert out_dir() == default_out(), "an empty OUT was taken literally"
    assert out_dir() != Path("."), "an empty OUT resolved to the source tree"


def test_the_digest_is_of_the_resolved_path():
    """A relative path and its absolute form must not produce two directories for one checkout."""
    assert default_out(REPO) == default_out(Path(str(REPO) + "/."))
    expected = hashlib.md5(str(REPO).encode()).hexdigest()[:TAG_LEN]
    assert default_out(REPO).name.endswith(expected)


def test_nothing_hardcodes_the_old_shared_default():
    """The fallback used to be copy-pasted into ten files; each copy was a place to miss."""
    offenders = []
    for path in sorted(REPO.glob("**/*.py")):
        if path.name == Path(__file__).name or "/.git/" in str(path):
            continue
        text = path.read_text(errors="replace")
        if '"/tmp/cuberange"' in text or '"/tmp/cuberange/' in text:
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, (
        "these files hardcode the shared default instead of calling cuberange.paths.out_dir(): "
        + ", ".join(offenders))


# --------------------------------------------------------------------------- $out in scenarios
#
# The scenarios used to hardcode /tmp/cuberange for every firmware image and UART capture, and the
# exercises overrode only the ONE image under test. So the node being attacked came from this
# checkout and the nodes around it came from whatever was in the shared directory - another
# checkout's build, in the case that produced this work. The scenario now derives every path from
# $out and has no default for it, so an unset $out stops Renode with "No such variable: $out"
# instead of quietly booting somebody else's firmware.

RESC_FILES = sorted(p for p in REPO.glob("**/*.resc") if ".git" not in p.parts)
LAUNCHERS = sorted(
    p for p in list(REPO.glob("tests/e2e/*.py")) + list(REPO.glob("exercises/**/verify_*.py"))
    if 'f"include @' in p.read_text())


@pytest.mark.parametrize("resc", RESC_FILES, ids=lambda p: p.name)
def test_no_scenario_hardcodes_an_absolute_build_path(resc):
    offenders = [f"{n}: {line.strip()}"
                 for n, line in enumerate(resc.read_text().splitlines(), 1)
                 if "@/tmp/" in line and not line.lstrip().startswith("#")]
    assert not offenders, (
        f"{resc.relative_to(REPO)} names an absolute build path. Derive it from $out:\n  "
        + "\n  ".join(offenders))


@pytest.mark.parametrize("launcher", LAUNCHERS, ids=lambda p: p.name)
def test_every_scenario_launcher_passes_out(launcher):
    text = launcher.read_text()
    assert '"$out=@' in text, (
        f"{launcher.relative_to(REPO)} includes a scenario without setting $out. The scenario has "
        f"no default for it, so this run would stop at 'No such variable: $out' - which is the "
        f"intended outcome, but it means this launcher never worked.")


def test_the_launcher_list_is_not_empty():
    """A glob that matched nothing would make the check above pass for every file it never saw."""
    assert len(LAUNCHERS) >= 8, f"only {len(LAUNCHERS)} scenario launchers found: {LAUNCHERS}"
    assert len(RESC_FILES) >= 8, f"only {len(RESC_FILES)} scenarios found"
