"""The port map must not hand the same number to two things.

This is cheap to test and expensive to debug: two sockets on one port means one of them fails to
bind, and in a Renode scenario a failed bind is reported once in a log nobody is reading while the
host tool connects happily to whichever listener won. It looks like a working range talking to the
wrong satellite.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports  # noqa: E402


def test_no_two_assignments_share_a_port():
    seen: dict[int, str] = {}
    for sat in range(ports.MAX_SATELLITES):
        for name, port in (("link", ports.link(sat)),
                           ("injector", ports.injector(sat)),
                           ("channel", ports.channel(sat))):
            label = f"sat{sat} {name}"
            assert port not in seen, f"{label} collides with {seen[port]} on port {port}"
            seen[port] = label
    for label, port in (("monitor", ports.monitor()), ("scratch link", ports.SCRATCH_LINK)):
        assert port not in seen, f"{label} collides with {seen[port]} on port {port}"
        seen[port] = label
    assert len(seen) == ports.MAX_SATELLITES * 3 + 2


def test_all_assigned_agrees_with_the_individual_functions():
    """A helper that disagreed with the functions would make the collision test meaningless."""
    table = ports.all_assigned()
    assert table[ports.link(0)] == "sat0 link"
    assert table[ports.injector(3)] == "sat3 injector"
    assert table[ports.channel(1)] == "sat1 channel"
    assert table[ports.monitor()] == "monitor"
    assert len(table) == ports.MAX_SATELLITES * 3 + 2


def test_the_historical_numbers_are_preserved():
    """Satellite 0 must keep the ports every existing scenario, README and write-up names.

    Renumbering them would silently invalidate every `localhost:3777` in the documentation, which
    is not the kind of change that should be possible by editing a constant.
    """
    assert ports.link(0) == 3777
    assert ports.monitor() == 3778
    assert ports.injector(0) == 3779
    assert ports.channel(0) == 3877


def test_a_second_satellite_gets_clear_air():
    assert ports.link(1) == 3787
    assert ports.injector(1) == 3789
    assert ports.channel(1) == 3887
    # and nothing in satellite 1's block lands on the Monitor
    assert ports.monitor() not in {ports.link(1), ports.injector(1), ports.channel(1)}


def test_an_out_of_range_satellite_is_refused():
    """Silently wrapping to another satellite's ports is the failure this module exists to stop."""
    for bad in (-1, ports.MAX_SATELLITES):
        with pytest.raises(ValueError, match="outside"):
            ports.link(bad)
        with pytest.raises(ValueError, match="outside"):
            ports.injector(bad)
        with pytest.raises(ValueError, match="outside"):
            ports.channel(bad)
