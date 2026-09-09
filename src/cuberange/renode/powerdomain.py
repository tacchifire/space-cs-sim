"""Host-side power domain: turns an EPS load switch into a node actually losing power.

Renode has no concept of a powered-off machine, so the physical consequence has to be applied from
outside. This watches the EPS's load-switch GPIO and, when a rail drops, halts the machine on that
rail.

`cpu IsHalted true` is the only mechanism that works. Measured against the alternatives:

  machine Pause              freezes the ENTIRE time domain - every other node stops too, and in
                             the RunFor flow it is silently undone by the next RunFor
  machine Reset              deadlocks when the emulation sits in the RunFor-paused state
  emulation RemoveMachine    SIGSEGV on the next RunFor
  connector Disconnect       cuts the bus only; the node keeps executing and keeps printing

With IsHalted, the halted node's instruction counter freezes bit-exactly, it stops transmitting on
CAN and on its UARTs, and every other node plus global virtual time carries on. Repowering needs
the ELF reloaded: a bare unhalt leaves the node permanently dead, and `machine Reset` does not zero
RAM, so the reload is what actually produces a clean boot.

The GPIO is polled rather than pushed. A push hook is available and cheaper, but the emulation here
runs free rather than stepped, and events raised during free-run are not buffered. Polling ODR is
side-effect free; polling a read-to-clear register would corrupt firmware state, so the address
below must stay a GPIO output register.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .monitor import Monitor

# gpioPortD base 0x58020C00 + ODR offset 0x14. Port D is used because gpioPortB lists PB0 in
# invertedAFPins, and a rail there would read inverted. Measured: firmware "rail ON" -> 0x00000020.
GPIOD_ODR = 0x58020C14
COMM_RAIL_PIN = 5


@dataclass
class RailEvent:
    rail: str
    powered: bool


class PowerDomain:
    def __init__(self, monitor: Monitor, comm_elf: str,
                 eps_machine: str = "EPS", comm_machine: str = "COMM",
                 odr_addr: int = GPIOD_ODR, pin: int = COMM_RAIL_PIN):
        self.mon = monitor
        self.comm_elf = comm_elf
        self.eps_machine = eps_machine
        self.comm_machine = comm_machine
        self.odr_addr = odr_addr
        self.pin = pin
        self.state: Optional[bool] = None
        self.events: list = []
        self.latched_unpowered_at_start = False

    def read_rail(self) -> bool:
        # Two dependent commands, so they have to be atomic. With one satellite there was only
        # ever one caller; with two there are two PowerDomains sharing the single permitted
        # Monitor session, and a `mach set` from the other landing in between returns the WRONG
        # machine's register with no error anywhere.
        with self.mon.exclusive():
            self.mon.command(f'mach set "{self.eps_machine}"')
            return bool((self.mon.read_u32(self.odr_addr) >> self.pin) & 1)

    def _cut_power(self) -> None:
        with self.mon.exclusive():
            self.mon.command(f'mach set "{self.comm_machine}"')
            self.mon.command("cpu IsHalted true")

    def _restore_power(self) -> None:
        """Reload and unhalt the node whose rail came back.

        The global pause is unavoidable and it was worth checking rather than assuming. Measured:
        `sysbus LoadELF` fails on a running emulation even when the target CPU is already halted -
        "There was an error executing command" - while `machine RequestReset` and
        `cpu IsHalted false` both succeed without one. Only the reload needs it, and the reload is
        needed because `machine Reset` does not zero RAM, so a bare unhalt resumes into the old
        heap and the node never boots.

        What that global pause costs the OTHER spacecraft, also measured: nothing they can tell.
        `pause` stops the whole TIME DOMAIN, so during the window the other machines' instruction
        counters and elapsed virtual time are both frozen, and both advance again afterwards. The
        cost is wall clock, not virtual time - which matters only to host-side code holding a
        real-time budget across the window, and is why the window contains nothing but the reload.
        """
        with self.mon.exclusive():
            self.mon.command("pause")
            self.mon.command(f'mach set "{self.comm_machine}"')
            self.mon.command("machine Reset")
            self.mon.command(f"sysbus LoadELF @{self.comm_elf}")
            self.mon.command("cpu IsHalted false")
            self.mon.command("start")

    def poll(self) -> Optional[RailEvent]:
        """Apply any rail change since the last call. Returns the event, or None if unchanged.

        The first call only latches. That is deliberate - there is nothing to apply on the first
        observation - but it does mean `state` can read False while the node is still executing,
        because nothing has cut its power yet. Callers that treat `state is False` as "the node is
        down" are asking the wrong object; ask whether an EVENT arrived.
        """
        powered = self.read_rail()
        if self.state is None:
            self.state = powered
            if not powered:
                # A rail that is already off at the first look is not a transition, so no event is
                # raised and no machine is halted. Say so, rather than leaving a latched False that
                # reads like an applied outage.
                self.latched_unpowered_at_start = True
            return None
        if powered == self.state:
            return None

        self.state = powered
        if powered:
            self._restore_power()
        else:
            self._cut_power()
        event = RailEvent(rail=self.comm_machine, powered=powered)
        self.events.append(event)
        return event
