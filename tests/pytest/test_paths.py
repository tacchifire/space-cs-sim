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
import ast
import os
import re
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


# --------------------------------------------------------------------------- the Makefile too
#
# The Python launchers were checked above and the Makefile was not, so `make exercise` - the one
# target a student runs by hand, and the only one `make check` never touches - kept launching
# Renode directly with no $out and no containment. It hung at the Monitor prompt with an empty
# log, which is the exact failure CLAUDE.md documents, in the first command anybody types.

MAKEFILE_TEXT = (REPO / "Makefile").read_text()


def _makefile_renode_launches() -> list[tuple[int, str]]:
    """Recipe lines that start Renode, with their line numbers.

    Matches the binary being invoked rather than the word "renode": $(RENODE_DIR) appears in
    plenty of lines that only pass it to a child.
    """
    out = []
    for n, line in enumerate(MAKEFILE_TEXT.splitlines(), 1):
        if re.search(r"(?<![\w/])\./renode\b", line):
            out.append((n, line.strip()))
    return out


def _recipe_containing(line_no: int) -> str:
    """The whole recipe a line belongs to, so continuations are visible."""
    lines = MAKEFILE_TEXT.splitlines()
    start = line_no - 1
    while start > 0 and (lines[start - 1].startswith("\t")
                         or lines[start - 1].rstrip().endswith("\\")):
        start -= 1
    end = line_no
    while end < len(lines) and (lines[end].startswith("\t") or lines[end - 1].rstrip().endswith("\\")):
        end += 1
    return "\n".join(lines[start:end])


def test_the_makefile_launches_renode_somewhere():
    assert _makefile_renode_launches(), (
        "no Renode launch found in the Makefile; this guard is watching nothing")


@pytest.mark.parametrize("line_no,text", _makefile_renode_launches(),
                         ids=lambda v: str(v)[:40])
def test_every_makefile_renode_launch_is_contained_and_told_where_to_look(line_no, text):
    recipe = _recipe_containing(line_no)
    assert "safety.isolate" in recipe or "$(ISOLATE)" in recipe, (
        f"Makefile:{line_no} starts Renode outside the loopback-only namespace. A range whose "
        f"containment depends on which target you ran is not contained.\n{recipe[:400]}")
    assert "$$out=@" in recipe or "$(ISOLATE)" in recipe, (
        f"Makefile:{line_no} starts Renode without setting $out. The scenarios have no default "
        f"for it, so this aborts the -e chain at the first LoadELF and hangs at the Monitor "
        f"prompt with an empty log.\n{recipe[:400]}")


def test_the_student_facing_target_derives_its_ports_from_the_map():
    """It printed the link and monitor ports as literals, which is right until the map moves.

    The numbers are not repeated here either - test_port_literals.py forbids that, and it caught
    this docstring on the way in.
    """
    recipe = re.search(r"^exercise:.*?(?=\n[a-zA-Z])", MAKEFILE_TEXT, re.S | re.M)
    assert recipe, "the `exercise` target is gone"
    #: The recipe delegates to tools/exercise.sh, which is where the banner lives - the target
    #: became a launcher for a shell inside the namespace rather than a foreground Renode.
    launcher = REPO / "tools" / "exercise.sh"
    assert launcher.is_file(), "tools/exercise.sh is gone but `make exercise` still calls it"
    body = recipe.group(0) + launcher.read_text()
    assert "cuberange import ports" in body, (
        "`make exercise` prints port numbers that are not derived from cuberange.ports")
    # Built from the map rather than written out. Writing them here would put three port
    # literals in this file, which is the very thing test_port_literals.py forbids - and it
    # caught exactly that on the first version of this test.
    from cuberange import ports as _ports
    for port in (_ports.link(0), _ports.monitor(), _ports.injector(0),
                 _ports.crosslink_injector()):
        assert f"localhost:{port}" not in body, f"`make exercise` hardcodes localhost:{port}"


# --------------------------------------------------------------------------- targets that run nothing
#
# `make soak-p0` deselected its only test for its whole existence. pytest.ini sets
# `addopts = -m "not slow"`, the one test in that file is marked slow, and the recipe did not
# override the filter - so pytest reported "1 deselected", exited 5, and make reported an error
# that reads like a broken test. CLAUDE.md advertised the target with a runtime.

def _default_marker_filter() -> str:
    ini = (REPO / "pytest.ini").read_text()
    m = re.search(r'addopts\s*=.*?-m\s+"([^"]+)"', ini)
    return m.group(1) if m else ""


def _pytest_recipes() -> list[tuple[int, str, list[str]]]:
    """(line, recipe, target paths) for every recipe that runs pytest."""
    out = []
    lines = MAKEFILE_TEXT.splitlines()
    for n, line in enumerate(lines, 1):
        if "-m pytest" not in line:
            continue
        recipe = line
        i = n
        while recipe.rstrip().endswith("\\") and i < len(lines):
            recipe += "\n" + lines[i]
            i += 1
        after = recipe.split("-m pytest", 1)[1]
        paths = [tok for tok in after.split()
                 if tok.endswith(".py") or "/" in tok and not tok.startswith("-")]
        out.append((n, recipe, paths))
    return out


def test_the_makefile_runs_pytest_somewhere():
    assert _pytest_recipes(), "no pytest recipe found; this guard is watching nothing"


@pytest.mark.parametrize("line_no,recipe,paths", _pytest_recipes(),
                         ids=lambda v: str(v)[:30])
def test_no_target_deselects_every_test_it_means_to_run(line_no, recipe, paths):
    """A target that selects nothing exits 5 and looks like a failing test."""
    marker = _default_marker_filter()
    if not marker.startswith("not "):
        pytest.fail(f"pytest.ini's default filter is {marker!r}; this guard only understands "
                    f"'not <marker>' and must be updated rather than quietly passing")
    excluded = marker[4:].strip()

    # A recipe that names its own -m has taken responsibility for the selection.
    if re.search(r"(?<!\.)\s-m\s+(?!pytest)", recipe):
        return

    files = []
    for spec in paths:
        files += sorted(REPO.glob(spec)) if any(c in spec for c in "*?[") else [REPO / spec]

    for path in files:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text())
        tests = [n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name.startswith("test_")]
        if not tests:
            continue
        selectable = [t for t in tests
                      if not any(getattr(d, "attr", None) == excluded
                                 or getattr(getattr(d, "func", None), "attr", None) == excluded
                                 for d in t.decorator_list)]
        assert selectable, (
            f"Makefile:{line_no} runs pytest on {path.relative_to(REPO)}, every test in which is "
            f"marked '{excluded}' and therefore deselected by pytest.ini. The run selects "
            f"nothing, exits 5, and make reports an error that reads like a broken test. Pass "
            f"-m {excluded} in the recipe.")


def test_every_build_directory_named_anywhere_is_one_the_makefile_produces():
    """A scenario or a verifier pointing at a build nobody makes.

    This was made twice in one afternoon, both times by a sed that renamed more than it was aimed
    at: `s01-` to `s02-` also turned `build-comm-s01-sat0` into `build-comm-s02-sat0`. The first
    one aborted Renode's -e chain and the consoles were empty; the second skipped every test in
    the file, and `make verify` reported success until conftest.py was taught not to.

    Both are the same mistake and neither needs Renode to catch. The Makefile is the only thing
    that creates a build directory, so the set named anywhere else has to be a subset of the set
    it writes.
    """
    import re

    makefile = (REPO / "Makefile").read_text()
    produced = set(re.findall(r"-d \$\(OUT\)/(build-[\w.-]+)", makefile))
    #: Some targets build in a loop - `build-$$role-sat$(SAT)`, `build-comm-s01-sat$$sat` - so
    #: their names are patterns. Expanded over the four node roles and the four spacecraft indices
    #: identity.cmake allows, which is the same expansion the shell does.
    ROLES = ("comm", "obc", "eps", "adcs")
    for pattern in re.findall(r"-d \$\(OUT\)/(build-\S+)", makefile):
        if "$" not in pattern:
            continue
        for role in ROLES:
            for sat in range(4):
                produced.add(pattern
                             .replace("$$role", role).replace("$(ROLE)", role)
                             .replace("$$sat", str(sat)).replace("$(SAT)", str(sat)))
    assert len(produced) >= 15, f"only {len(produced)} build directories found; the regex drifted"

    named = {}
    for f in (sorted(REPO.glob("exercises/*/scenario.resc"))
              + sorted(REPO.glob("exercises/*/verify_*.py"))
              + sorted(REPO.glob("scripts/**/*.resc"))
              + sorted(REPO.glob("tests/e2e/*.py"))):
        #: Both spellings. A .resc writes `$out/build-x/zephyr/zephyr.elf`; a verifier writes
        #: `OUT / "build-x" / "zephyr"`, which has no slash in it at all - and that is exactly the
        #: one the first version of this test missed, on the day it was written to catch it.
        #: Comments that name a build are matched too, deliberately: a comment pointing at a build
        #: nobody makes is stale, and finding that is free here.
        for b in re.findall(r"\b(build-[\w.-]+)", f.read_text()):
            named.setdefault(b.rstrip("."), set()).add(str(f.relative_to(REPO)))

    missing = {b: sorted(w) for b, w in named.items() if b not in produced}
    assert not missing, (
        "these name a build directory no Makefile target writes, so the scenario aborts or every "
        "test skips:\n  "
        + "\n  ".join(f"{b}  <- {', '.join(w)}" for b, w in sorted(missing.items())))
