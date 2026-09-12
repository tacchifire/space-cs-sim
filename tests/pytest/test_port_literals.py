"""The port map is only a map if nothing routes around it.

`test_ports.py` proves `ports.py` is internally consistent: no two assignments collide, a second
satellite gets clear air, an out-of-range index is refused. It cannot see a file that writes 3877
by hand — and seventeen of them did, while `tests/e2e/test_determinism.py` picked 3877 as "a port
nothing else in the suite uses" when 3877 is `channel(0)`, the ground-station side of EX-L01's
channel. The map was right and unenforced, which is the same shape as the build directory that was
correct in one file and copy-pasted into ten.

Two rules, because the two file types can obey in different ways:

  - Python asks `cuberange.ports`.
  - A `.resc` cannot import Python, so it declares `$name?=NNNN` and the harness overrides it. The
    default still has to agree with the map, or a scenario run without an override listens
    somewhere the harness is not connecting.
"""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports  # noqa: E402

#: Every port the map hands out, and what it is called.
ASSIGNED = ports.all_assigned()

#: Files allowed to write the numbers: the map itself, and the test that pins them so a
#: renumbering has to be deliberate.
EXEMPT = {"src/cuberange/ports.py", "tests/pytest/test_ports.py",
          "tests/pytest/test_port_literals.py"}

#: Scenarios that legitimately open no sockets. Named rather than skipped: a skip for "declares no
#: ports" also covers a scenario that LOST its declarations, and the two look identical in a run.
NO_PORTS = {"csp_ping.resc", "interactive.resc", "ci.resc"}

#: `.resc` variable name -> the map entry its default must equal.
RESC_DEFAULTS = {
    "linkport": ports.link(0), "injport": ports.injector(0),
    "link0": ports.link(0), "link1": ports.link(1),
    "inj0": ports.injector(0), "inj1": ports.injector(1),
    #: The crosslink injector is NOT indexed - the crosslink is one bus for the constellation,
    #: the way the Monitor is one per emulation. A scenario that spelled it `injport` would be
    #: checked against satellite 0's own injector and be wrong in a way nobody would read.
    "xlinkport": ports.crosslink_injector(),
}


def _python_files():
    for p in sorted(REPO.glob("**/*.py")):
        if ".git" in p.parts or "__pycache__" in p.parts:
            continue
        if str(p.relative_to(REPO)) in EXEMPT:
            continue
        yield p


def test_no_python_file_writes_an_assigned_port_by_hand():
    offenders = []
    for p in _python_files():
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            for port in ASSIGNED:
                if re.search(rf"(?<![\w.]){port}(?![\w])", line):
                    offenders.append(f"{p.relative_to(REPO)}:{n}  {ASSIGNED[port]} ({port})")
    assert not offenders, (
        "these lines write a port the map already assigns. Call cuberange.ports instead - a "
        "literal here and a change there is how tests/e2e/test_determinism.py ended up squatting "
        "on EX-L01's channel port:\n  " + "\n  ".join(offenders))


@pytest.mark.parametrize("resc", sorted(REPO.glob("**/*.resc")), ids=lambda p: p.name)
def test_every_scenario_default_agrees_with_the_map(resc: Path):
    checked = 0
    for n, line in enumerate(resc.read_text().splitlines(), 1):
        m = re.match(r"\s*\$(\w+)\s*\?=\s*(\d+)\s*$", line)
        if not m:
            continue
        name, value = m.group(1), int(m.group(2))
        if name not in RESC_DEFAULTS:
            continue
        checked += 1
        assert value == RESC_DEFAULTS[name], (
            f"{resc.relative_to(REPO)}:{n} defaults ${name} to {value}, but the map says "
            f"{RESC_DEFAULTS[name]}. Run without an override and the scenario listens where "
            f"nothing connects.")
    if checked == 0:
        assert resc.name in NO_PORTS, (
            f"{resc.relative_to(REPO)} declares no port defaults and is not listed in NO_PORTS. "
            f"Either it opens no sockets - say so by adding it - or its declarations were lost, "
            f"in which case the harness and the scenario now disagree about where to connect.")


def test_the_scan_would_notice_a_violation():
    """The corpus and the pattern, checked against a line that must be caught.

    A scan whose glob matched nothing, or whose regex never fired, would pass silently - and this
    file exists because a check that covered nothing looked exactly like a check that passed.
    """
    assert sum(1 for _ in _python_files()) > 40, "the Python corpus is suspiciously small"
    port = ports.channel(0)
    assert re.search(rf"(?<![\w.]){port}(?![\w])", f"LINK_PORT = {port}")
    assert not re.search(rf"(?<![\w.]){port}(?![\w])", f"LINK_PORT = {port}9")
    assert not re.search(rf"(?<![\w.]){port}(?![\w])", f"x.{port}")
