"""EX-A01 verification, in both directions.

  1. against the vulnerable ADCS, a forged torque command spins the spacecraft up
  2. against the mitigated ADCS, the same command is refused and the attitude holds
  3. against the mitigated ADCS, a torque INSIDE the actuator's authority is still executed

Without (3) a mitigation that simply refused every attitude command would pass, and the spacecraft
would be safe in the way a brick is safe.

This exercise also asserts something EX-B01 does not: that the satellite stays ALIVE through the
attack. That is not a detail, it is the lesson. A range whose every exercise ends in silence
teaches operators to equate "responding" with "healthy", and this is the counterexample.
"""
import os
import socket
import struct
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.link import SpaceLink                            # noqa: E402
from cuberange.gs.station import GroundStation                     # noqa: E402
from cuberange.proto.csp import ADDR_ADCS, encode_packet           # noqa: E402
from cuberange.renode.monitor import Monitor                       # noqa: E402
from cuberange.renode.profile import profile_args                  # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor           # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = Path(os.environ.get("OUT", "/tmp/cuberange"))
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
INJECTOR = REPO / "attacker" / "TcpCanInjector.cs"

LINK_PORT, MONITOR_PORT, INJ_PORT = 3777, 3778, 3779
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "45"))

CSP_PORT_ATT = 12
TORQUE_AUTHORITY_MNM = 20        # firmware/apps/adcs/src/main.c
FORGED_TORQUE_MNM = 30000        # 1500x the authority, and inside the int16 wire field
LEGIT_TORQUE_MNM = 15            # comfortably inside the authority

# gpioPortD base 0x58020C00 + ODR offset 0x14, pin 6. Port D because stm32h743.repl lists PB0 and
# five other port-B pins in invertedAFPins, where a signal reads inverted from what the firmware
# wrote. Reading ODR is side-effect free; a read-to-clear register would corrupt firmware state.
GPIOD_ODR = 0x58020C14
TUMBLE_PIN = 6


def _forge_torque(torque_mnm: int) -> None:
    """Put an attitude command on the bus, forging the OBC's source address.

    The forged source is incidental here, and saying so is half the exercise: the same command from
    the real OBC would also be accepted. EX-B01's fix was authentication; it does not help at all
    against a command that is authentic and simply outside what the hardware can do.
    """
    payload = bytes([1, 0]) + struct.pack(">h", torque_mnm) + b"\x00\x00\x00\x00"
    frames = encode_packet(src=1, dst=ADDR_ADCS, dport=CSP_PORT_ATT, sport=21,
                           payload=payload, transfer_id=0x2B)
    sock = socket.create_connection(("127.0.0.1", INJ_PORT), timeout=5)
    try:
        for frame in frames:
            sock.sendall(f"{frame.can_id:x} {frame.data.hex()}\n".encode())
    finally:
        sock.close()


class Range:
    """One running scenario: four satellite nodes, a ground station, and the bus attacker."""

    def __init__(self, adcs_elf: Path):
        self.adcs_elf = adcs_elf
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2560)
        self._ctx = None

    def __enter__(self):
        for name in ("comm.uart", "obc.uart", "eps.uart", "adcs.uart"):
            (OUT / name).unlink(missing_ok=True)
        # No --hide-log: the Renode log is evidence, and it is where the injector reports every
        # accepted connection and transmitted frame.
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(MONITOR_PORT),
                "-e", f"$adcs=@{self.adcs_elf}",
                "-e", f"$injector=@{INJECTOR}",
                *profile_args(),
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exa01-renode.log")
        self.run = self._ctx.__enter__()

        self.link = SpaceLink(port=LINK_PORT)
        self.link.connect(retries=60)
        self.station = GroundStation(self.link)
        self.mon = Monitor(port=MONITOR_PORT).connect()

        # Wait for ADCS's own readiness line rather than sleeping. The torque command is
        # transmitted once with no retry, so one sent before the CAN interface is up is silently
        # dropped and the attitude simply never moves - which would read as a working mitigation.
        #
        # Tear down explicitly on failure: __exit__ is not called when __enter__ raises, and a bare
        # assert would leave a supervised Renode holding its ports.
        if not self.wait_console("adcs.uart", "ADCS listening", seconds=BOOT_TIMEOUT_S):
            console = self.console("adcs.uart")
            self.__exit__(None, None, None)
            raise AssertionError(
                f"ADCS never reported ready within {BOOT_TIMEOUT_S}s:\n{console}")
        return self

    def __exit__(self, *exc):
        for closer in (getattr(self, "mon", None), getattr(self, "link", None)):
            if closer is not None:
                closer.close()
        self._ctx.__exit__(*exc)

    def wait_console(self, name: str, needle: str, seconds: float) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if needle in self.console(name):
                return True
            time.sleep(0.2)
        return False

    def console(self, name: str) -> str:
        path = OUT / name
        return path.read_text(errors="replace") if path.exists() else ""

    def alive(self, timeout: float = 8.0) -> bool:
        return self.station.ping(timeout=timeout) is not None

    def read_tumble(self) -> bool:
        """The tumble indicator, read from the pin the ADCS firmware drove.

        Deliberately not read from telemetry. An attacker who can put frames on this bus can also
        put housekeeping on it, so a range that scored this exercise from a TM packet would be
        scoring something the attacker controls.
        """
        self.mon.command('mach set "ADCS"')
        return bool((self.mon.read_u32(GPIOD_ODR) >> TUMBLE_PIN) & 1)

    def wait_tumble(self, want: bool, seconds: float = 12.0) -> bool:
        """Poll the indicator until it reads `want`, or the budget runs out."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.read_tumble() == want:
                return True
            time.sleep(0.3)
        return False


def _require(elf: Path):
    if not elf.exists():
        pytest.skip(f"{elf} missing - run 'make firmware-a01' first")


def test_attack_succeeds_against_the_vulnerable_adcs():
    adcs = OUT / "build-adcs-vuln" / "zephyr" / "zephyr.elf"
    _require(adcs)
    with Range(adcs) as r:
        assert r.alive(), "the satellite was not answering before the attack"
        assert r.read_tumble() is False, "the attitude indicator should start clear"

        _forge_torque(FORGED_TORQUE_MNM)

        assert r.wait_tumble(True), (
            f"the spacecraft never left its attitude; ADCS console said:\n"
            f"{r.console('adcs.uart')}")
        assert "TUMBLING" in r.console("adcs.uart")

        # The lesson. Every other symptom still looks healthy: the radio answers, the OBC replies,
        # the round trip completes. Only the pin the ADCS drove says anything is wrong.
        assert r.alive(), (
            "the satellite stopped answering, which makes this the same lesson as EX-B01. "
            "The point of EX-A01 is a failure that telemetry does not reveal.")


def test_mitigation_rejects_the_out_of_authority_torque():
    adcs = OUT / "build-adcs-hard" / "zephyr" / "zephyr.elf"
    _require(adcs)
    with Range(adcs) as r:
        assert r.alive(), "the satellite was not answering before the attack"

        _forge_torque(FORGED_TORQUE_MNM)

        # The indicator must stay clear for the whole budget, not merely be clear at the end.
        deadline = time.time() + 8.0
        while time.time() < deadline:
            assert r.read_tumble() is False, (
                f"the attitude moved despite the authority check:\n{r.console('adcs.uart')}")
            time.sleep(0.5)

        assert "REJECTED out-of-authority" in r.console("adcs.uart"), (
            f"ADCS did not log a rejection, so the command may simply never have arrived - "
            f"which would make this test pass for the wrong reason:\n{r.console('adcs.uart')}")
        assert r.alive(), "the satellite went quiet even though the command was rejected"


def test_the_mitigation_does_not_break_legitimate_slews():
    """A control that refuses every attitude command is not a mitigation, it is a dead ADCS."""
    adcs = OUT / "build-adcs-hard" / "zephyr" / "zephyr.elf"
    _require(adcs)
    with Range(adcs) as r:
        assert r.alive(), "the satellite was not answering before the command"

        _forge_torque(LEGIT_TORQUE_MNM)

        assert r.wait_console("adcs.uart", f"torque {LEGIT_TORQUE_MNM} mNm accepted",
                              seconds=10), (
            f"a torque inside the actuator's authority was not executed:\n"
            f"{r.console('adcs.uart')}")
        # And it must not trip the indicator: a legitimate slew at full authority reaches
        # 20 x 10 x 20 = 4000 mdps against a 20000 mdps threshold, a 5x margin.
        assert r.read_tumble() is False, (
            "a legitimate slew tripped the tumble indicator; the margin in the attitude model is "
            "too small for the exercise to distinguish an attack from normal operation")
        assert r.alive()
