"""The path the READMEs tell a reader to take, run exactly as they write it.

Every exercise README says: start the range with `make exercise`, then run the solver. Fourteen
files say it, in two languages. It did not work. `make exercise` puts Renode in a network
namespace that contains only loopback - a NEW namespace per invocation - so a solver started
anywhere else gets ConnectionRefusedError on the space link and on the injector.

Nothing caught it, because no gate ran a reader's path. `make verify` launches Renode from inside
pytest, which is already inside the namespace, so the tested path and the documented path were
different paths that happened to share a scenario file.

This file runs `make exercise` with its RUN= hook, which is the same shell the interactive form
drops into. If the documented workflow breaks again, this is what fails.

It is deliberately NOT run inside $(ISOLATE), because the namespace this test is about is the one
`make exercise` creates. Wrapping it would still work - `cuberange.safety.isolate` now runs the
command directly when it is already inside a loopback-only namespace, so nesting is a no-op rather
than an error - but it would mean the test entered a namespace the reader never enters.

That no-op is itself part of the fix. The raw backends cannot nest here (measured: bwrap inside
bwrap is "No permissions to create a new namespace", unshare is EPERM, both from
kernel.apparmor_restrict_unprivileged_userns=1), and even where they can, a nested namespace is a
DIFFERENT loopback. `make exercise` in one terminal and `make channel` in another gave two ranges
that could not see each other, which is what EX-G03 and EX-G04 told their readers to set up.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.paths import out_dir     # noqa: E402

OUT = out_dir()

#: One exercise, not all of them: this is about the launcher, and every exercise uses the same
#: launcher. EX-B01 is the cheapest - three machines, an injector, and a solver that prints what
#: it injected.
EXERCISE = "EX-B01-eps-killswitch"

pytestmark = pytest.mark.skipif(
    not (OUT / "build-comm" / "zephyr" / "zephyr.elf").exists(),
    reason="build the firmware first: make firmware-all")


def _make(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ, OUT=str(OUT))
    return subprocess.run(["make", "-C", str(REPO), *args], capture_output=True,
                          text=True, timeout=timeout, env=env)


def test_the_solver_reaches_the_range_the_way_the_readme_says():
    """`make exercise` then the solver - the two commands, in that order, as written."""
    solve = f"python3 exercises/{EXERCISE}/solve.py"
    r = _make("exercise", f"EX={EXERCISE}", f"RUN={solve}")
    assert r.returncode == 0, (
        f"the documented workflow failed (exit {r.returncode}).\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    assert "injected" in r.stdout, (
        "the solver ran but injected nothing - it did not reach the range\n" + r.stdout)
    assert "ConnectionRefused" not in r.stdout + r.stderr, r.stdout + r.stderr


def test_every_readme_that_names_the_two_commands_names_them_in_the_form_that_works():
    """The instruction and the launcher have to agree, in both languages.

    A README that tells the reader to open a second terminal is telling them to do the thing that
    returns ConnectionRefusedError, and no amount of working launcher fixes that sentence.
    """
    offenders = []
    for readme in sorted(REPO.glob("exercises/*/README*.md")):
        text = readme.read_text()
        if "make exercise" not in text or "solve.py" not in text:
            continue
        #: The solver must appear inside a block that also establishes it runs in the range's
        #: shell - either the RUN= form, or after the banner's prompt.
        if not re.search(r"RUN=|cuberange:[\w-]+\$", text):
            offenders.append(str(readme.relative_to(REPO)))
    assert not offenders, (
        "these tell the reader to run the solver without saying where, and 'somewhere else' is "
        "the one place it cannot run: " + ", ".join(offenders))
