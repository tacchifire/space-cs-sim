"""The instruction and the launcher have to agree - checked statically, in the fast suite.

`make exercise` puts the reader in a shell inside the range's network namespace, which holds only
loopback and is created fresh per invocation. A solver started anywhere else gets
ConnectionRefusedError. So a README that names both `make exercise` and `solve.py` must say WHERE
the solver runs: the `RUN=` form, or after the range shell's prompt.

This check used to live in tests/e2e/test_documented_workflow.py, beside its dynamic twin - which
really does launch the range and really does need firmware. This half needs neither: it reads
files. But the module's firmware skip gated it anyway, and `make workflow` is the last step of
`make check`. So a README missing its RUN= line was invisible to the fast suite a contributor runs
locally, and CI reported it as "CI FAILED after 52m01s" (EX-S06's first run) - a regex that takes
0.16 s, waiting behind every Renode test in the range. Here it fails in the gate's first two
minutes, and before a push. The dynamic half stays in e2e, where it belongs.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The solver must appear inside a block that also establishes it runs in the range's shell -
#: either the RUN= form, or after the banner's prompt.
IN_THE_RANGE = re.compile(r"RUN=|cuberange:[\w-]+\$")


def readmes_naming_both_commands() -> list[tuple[Path, str]]:
    out = []
    for readme in sorted(REPO.glob("exercises/*/README*.md")):
        text = readme.read_text()
        if "make exercise" in text and "solve.py" in text:
            out.append((readme, text))
    return out


def test_every_readme_that_names_the_two_commands_names_them_in_the_form_that_works():
    """The instruction and the launcher have to agree, in both languages.

    A README that tells the reader to open a second terminal is telling them to do the thing that
    returns ConnectionRefusedError, and no amount of working launcher fixes that sentence.
    """
    offenders = [str(readme.relative_to(REPO)) for readme, text in readmes_naming_both_commands()
                 if not IN_THE_RANGE.search(text)]
    assert not offenders, (
        "these tell the reader to run the solver without saying where, and 'somewhere else' is "
        "the one place it cannot run: " + ", ".join(offenders))


def test_the_check_has_something_to_check():
    """A guard over zero READMEs agrees with any README - and moving a glob to a new file is
    exactly how one ends up rooted somewhere with nothing under it.

    At least one README per exercise directory must name both commands; today it is 40 of 44, the
    four that do not being EX-G01's and EX-L01's, which launch differently.
    """
    #: glob, not iterdir: a mis-rooted REPO should FAIL this assertion, not raise before reaching it.
    exercises = [p for p in REPO.glob("exercises/EX-*") if p.is_dir()]
    found = readmes_naming_both_commands()
    assert exercises, f"no exercise directories under {REPO / 'exercises'}"
    assert len(found) >= len(exercises), (
        f"only {len(found)} README(s) name both `make exercise` and `solve.py` across "
        f"{len(exercises)} exercises - the check above would pass against almost anything")
