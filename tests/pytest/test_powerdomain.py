"""The rail state machine, driven directly instead of through an emulator.

PowerDomain halts CPUs and reloads ELFs. It had no unit test at all: every path through it ran
only inside a Renode exercise, where a mistake in this logic surfaces as a firmware that would not
boot or a rail that would not move - and gets debugged as a firmware problem.

The fake Monitor below is not a stand-in for Renode. It records the commands issued and answers
register reads from a value the test sets, which is exactly enough to ask what this class decides.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.renode.powerdomain import COMM_RAIL_PIN, PowerDomain  # noqa: E402


class FakeMonitor:
    """Records commands; returns whatever the test says the ODR holds."""

    def __init__(self, odr: int = 1 << COMM_RAIL_PIN):
        self.odr = odr
        self.commands: list[str] = []
        self.exclusive_depth = 0
        self.max_exclusive_depth = 0

    def command(self, text: str) -> str:
        self.commands.append(text)
        return ""

    def read_u32(self, address: int) -> int:
        self.commands.append(f"read {address:#x}")
        return self.odr

    def exclusive(self):
        monitor = self

        class _Ctx:
            def __enter__(self_inner):
                monitor.exclusive_depth += 1
                monitor.max_exclusive_depth = max(monitor.max_exclusive_depth,
                                                  monitor.exclusive_depth)
                return monitor

            def __exit__(self_inner, *exc):
                monitor.exclusive_depth -= 1
                return False

        return _Ctx()


def domain(mon: FakeMonitor) -> PowerDomain:
    return PowerDomain(mon, comm_elf="/nonexistent/zephyr.elf")


def touched_machines(mon: FakeMonitor) -> list[str]:
    """Commands that do something to a machine, as opposed to reading a register."""
    return [c for c in mon.commands
            if c.startswith("cpu ") or "LoadELF" in c or "RequestReset" in c or c == "pause"]


# --------------------------------------------------------------------------- latching

def test_the_first_poll_only_latches_and_touches_nothing():
    mon = FakeMonitor(odr=1 << COMM_RAIL_PIN)
    d = domain(mon)
    assert d.poll() is None, "the first observation is not a transition"
    assert d.state is True
    assert touched_machines(mon) == [], f"the first poll acted on a machine: {mon.commands}"


def test_a_rail_already_off_at_the_first_look_is_not_an_outage():
    """The distinction that `state is False` cannot express.

    Nothing halted the node - it was already dark when this object first looked - so there is
    nothing to restore, and treating it as an applied outage means a later poll that sees the rail
    come back will reload a machine that was never halted.
    """
    mon = FakeMonitor(odr=0)
    d = domain(mon)
    assert d.poll() is None
    assert d.state is False
    assert d.latched_unpowered_at_start is True
    assert d.outage_applied() is False, (
        "a rail that was already off reads as an outage this object applied")
    assert touched_machines(mon) == []


# --------------------------------------------------------------------------- transitions

def test_a_drop_cuts_power_and_raises_one_event():
    mon = FakeMonitor(odr=1 << COMM_RAIL_PIN)
    d = domain(mon)
    d.poll()                                   # latch powered
    mon.odr = 0
    event = d.poll()
    assert event is not None and event.powered is False
    assert d.outage_applied() is True
    assert any("IsHalted true" in c for c in mon.commands), mon.commands
    assert len(d.events) == 1


def test_an_unchanged_rail_raises_nothing_and_touches_nothing():
    mon = FakeMonitor(odr=1 << COMM_RAIL_PIN)
    d = domain(mon)
    d.poll()
    before = len(mon.commands)
    assert d.poll() is None
    assert touched_machines(mon) == []
    assert len(mon.commands) > before, "the second poll did not even read the register"


def test_a_restore_reloads_rather_than_merely_unhalting():
    """`machine Reset` does not zero RAM, so a bare unhalt resumes into the old heap.

    Measured and recorded in powerdomain.py's own comment; this is the assertion for it.
    """
    mon = FakeMonitor(odr=1 << COMM_RAIL_PIN)
    d = domain(mon)
    d.poll()
    mon.odr = 0
    d.poll()
    mon.odr = 1 << COMM_RAIL_PIN
    event = d.poll()
    assert event is not None and event.powered is True
    assert any("LoadELF" in c for c in mon.commands), (
        f"the node came back without a reload, so it resumes into its old heap: {mon.commands}")
    assert d.outage_applied() is False


# --------------------------------------------------------------------------- atomicity

def test_the_paired_commands_are_issued_under_one_exclusive_hold():
    """`mach set` then a register read is two dependent commands.

    With two spacecraft there are two PowerDomains sharing the single permitted Monitor session,
    and a `mach set` from the other landing between them returns the WRONG machine's register with
    no error anywhere.
    """
    mon = FakeMonitor()
    d = domain(mon)
    d.read_rail()
    assert mon.max_exclusive_depth >= 1, "read_rail did not take the Monitor exclusively"
    assert mon.exclusive_depth == 0, "the exclusive hold was not released"


def test_the_rail_is_read_from_the_pin_the_firmware_drives():
    """A different pin would read a neighbouring signal and report it confidently."""
    mon = FakeMonitor(odr=1 << COMM_RAIL_PIN)
    assert domain(mon).read_rail() is True
    assert domain(FakeMonitor(odr=~(1 << COMM_RAIL_PIN) & 0xFFFFFFFF)).read_rail() is False
    assert domain(FakeMonitor(odr=0)).read_rail() is False
