"""TCP port assignment for the range.

Until now these were copy-pasted literals: 3777, 3778, 3779 and 3877 appeared across twenty files
with nothing that could tell you what they were for or whether a new one collided. That is
survivable with one satellite and not with two, because the second satellite needs its own space
link and its own injector while sharing the emulation - and the first symptom of getting it wrong
is a host tool that connects successfully to the WRONG spacecraft.

The layout, and why:

    satellite N   link      3777 + 10N      Renode's socket terminal on that COMM's usart2
                  injector  3779 + 10N      the attacker's TcpCanInjector on that CAN hub
                  channel   3877 + 10N      the host-side proxy a ground station connects to

    monitor       3778                      ONE per Renode process, not per satellite

The Monitor is deliberately not indexed. Rule L1 of the design permits exactly one control client
per emulation, and both the Monitor and the External Control server have a listener backlog of 1,
so a second satellite in the same process does not get a second Monitor. It gets `mach set`.

Ten apart rather than three, so a satellite can grow another socket without renumbering the ones
after it.

WHAT THESE BIND TO. Not loopback. Renode 1.16.1 has no bind-address option, so its listeners are on
0.0.0.0 whatever this module says - measured, and recorded in SAFE_USE.md. Only `channel()` is
served by our own code, which does default to 127.0.0.1.
"""
from __future__ import annotations

BASE = 3777
SPACING = 10
MAX_SATELLITES = 8

_CHANNEL_OFFSET = 100
_MONITOR = BASE + 1

# For a test that needs a link nobody else in the suite is using. Placed well clear of the
# satellite block: test_determinism.py used to sit on 3877, which is satellite 0's channel port,
# and would have collided the moment anything ran alongside it.
SCRATCH_LINK = BASE + 900


def _check(sat: int) -> None:
    if not 0 <= sat < MAX_SATELLITES:
        raise ValueError(
            f"satellite index {sat} is outside 0..{MAX_SATELLITES - 1}. Raise MAX_SATELLITES and "
            f"check that the new range does not overlap SCRATCH_LINK.")


def link(sat: int = 0) -> int:
    """Renode's socket terminal on satellite `sat`'s COMM.usart2."""
    _check(sat)
    return BASE + sat * SPACING


def injector(sat: int = 0) -> int:
    """The TcpCanInjector attached to satellite `sat`'s CAN hub."""
    _check(sat)
    return BASE + sat * SPACING + 2


def channel(sat: int = 0) -> int:
    """The host-side LinkChannel a ground station connects to for satellite `sat`."""
    _check(sat)
    return BASE + _CHANNEL_OFFSET + sat * SPACING


def monitor() -> int:
    """The Renode Monitor. One per emulation, however many satellites are in it."""
    return _MONITOR


def all_assigned(satellites: int = MAX_SATELLITES) -> dict[int, str]:
    """Every port this module hands out, mapped to what it is. Used by the collision test."""
    out: dict[int, str] = {monitor(): "monitor", SCRATCH_LINK: "scratch link"}
    for sat in range(satellites):
        for name, port in (("link", link(sat)), ("injector", injector(sat)),
                           ("channel", channel(sat))):
            out[port] = f"sat{sat} {name}"
    return out
