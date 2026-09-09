"""EX-B01 verification, in both directions.

An exercise that only proves the attack works is half a lesson and a quarter of a product. These
tests assert all three things that matter:

  1. against the vulnerable EPS, a forged bus frame silences the satellite
  2. against the mitigated EPS, the same frame is rejected and the satellite stays up
  3. against the mitigated EPS, a properly authenticated command STILL cuts the rail

Without (3) a mitigation that simply broke the feature would pass.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports  # noqa: E402
from cuberange.paths import out_dir                             # noqa: E402
from cuberange.gs.link import SpaceLink                            # noqa: E402
from cuberange.gs.station import GroundStation                     # noqa: E402
from cuberange.proto.csp import ADDR_EPS, encode_packet            # noqa: E402
from cuberange.renode.profile import profile_args
from cuberange.renode.monitor import Monitor                       # noqa: E402
from cuberange.renode.powerdomain import PowerDomain               # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor           # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
INJECTOR = REPO / "attacker" / "TcpCanInjector.cs"

LINK_PORT, MONITOR_PORT, INJ_PORT = ports.link(0), ports.monitor(), ports.injector(0)
# Upper bound on how long the nodes may take to come up, not a fixed wait - the code waits for
# EPS's own readiness line and only uses this to give up. Raise it on a slow host.
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "30"))
POWER_TOKEN = bytes([0x5A, 0xC3, 0x11, 0xE7])
CSP_PORT_POWER = 11


def _forge_power_off(token: bytes) -> None:
    """Put a rail-off command on the bus, forging the OBC's source address."""
    import socket

    payload = bytes([1, 0, 0]) + token         # opcode=set rail, rail=COMM, state=off
    frames = encode_packet(src=1, dst=ADDR_EPS, dport=CSP_PORT_POWER, sport=20,
                           payload=payload, transfer_id=0x2A)
    sock = socket.create_connection(("127.0.0.1", INJ_PORT), timeout=5)
    try:
        for frame in frames:
            sock.sendall(f"{frame.can_id:x} {frame.data.hex()}\n".encode())
    finally:
        sock.close()


class Range:
    """One running scenario with the ground station and power domain attached."""

    def __init__(self, eps_elf: Path):
        self.eps_elf = eps_elf
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=180, rss_ceiling_mb=2048)
        self._ctx = None

    def __enter__(self):
        for name in ("comm.uart", "obc.uart", "eps.uart"):
            (OUT / name).unlink(missing_ok=True)
        # No --hide-log: on a security range the Renode log is evidence. The injector reports
        # every accepted connection and transmitted frame there, and boot-time register warnings
        # cost under 9% because they are boot-only.
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(MONITOR_PORT),
                "-e", f"$eps=@{self.eps_elf}",
                "-e", f"$injector=@{INJECTOR}",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exb01-renode.log")
        self.run = self._ctx.__enter__()

        self.link = SpaceLink(port=LINK_PORT)
        self.link.connect(retries=60)
        self.station = GroundStation(self.link)
        self.mon = Monitor(port=MONITOR_PORT).connect()
        self.power = PowerDomain(self.mon, comm_elf=str(OUT / "build-comm/zephyr/zephyr.elf"))
        # Wait for EPS to announce itself instead of sleeping a fixed interval. The power commands
        # this exercise sends - legitimate and forged alike - go to EPS and are transmitted once
        # with no retry, so one sent before EPS's CAN interface is up is silently dropped and the
        # rail simply never moves. A fixed sleep gets worse on a slower host, not better.
        #
        # Tear down explicitly on failure: __exit__ is not called when __enter__ raises, so a bare
        # assert here would leave a supervised Renode running and holding its ports.
        if not self.wait_console("eps.uart", "EPS listening", seconds=BOOT_TIMEOUT_S):
            console = self.console("eps.uart")
            self.__exit__(None, None, None)
            raise AssertionError(
                f"EPS never reported ready within {BOOT_TIMEOUT_S}s:\n{console}")
        self.power.poll()          # latch the initial rail state
        return self

    def __exit__(self, *exc):
        for closer in (getattr(self, "mon", None), getattr(self, "link", None)):
            if closer is not None:
                closer.close()
        self._ctx.__exit__(*exc)

    def wait_console(self, name: str, needle: str, seconds: float) -> bool:
        """Poll a node's console until it prints `needle`."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if needle in self.console(name):
                return True
            time.sleep(0.2)
        return False

    def alive(self, timeout: float = 8.0) -> bool:
        return self.station.ping(timeout=timeout) is not None

    def settle_power(self, seconds: float = 6.0):
        """Poll the rail until it changes or the budget runs out. Returns the event, or None."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            event = self.power.poll()
            if event is not None:
                return event
            time.sleep(0.3)
        return None

    def console(self, name: str) -> str:
        path = OUT / name
        return path.read_text(errors="replace") if path.exists() else ""


def _require(elf: Path):
    if not elf.exists():
        pytest.skip(f"{elf} missing - run 'make firmware-p1' first")


def test_attack_succeeds_against_the_vulnerable_eps():
    eps = OUT / "build-eps-vuln" / "zephyr" / "zephyr.elf"
    _require(eps)
    with Range(eps) as r:
        assert r.alive(), "the satellite was not answering before the attack"
        assert r.power.state is True, "the COMM rail should start powered"

        _forge_power_off(token=b"\x00\x00\x00\x00")

        event = r.settle_power()
        assert event is not None and event.powered is False, (
            f"the rail never dropped; EPS console said:\n{r.console('eps.uart')}")
        assert "COMM rail OFF" in r.console("eps.uart")

        assert not r.alive(timeout=8), (
            "the satellite still answered after its radio lost power")


def test_mitigation_blocks_the_forged_command():
    eps = OUT / "build-eps-hard" / "zephyr" / "zephyr.elf"
    _require(eps)
    with Range(eps) as r:
        assert r.alive(), "the satellite was not answering before the attack"

        _forge_power_off(token=b"\x00\x00\x00\x00")

        assert r.settle_power(seconds=5) is None, "the rail dropped despite authentication"
        assert "REJECTED unauthenticated" in r.console("eps.uart"), (
            f"EPS did not log a rejection:\n{r.console('eps.uart')}")
        assert r.alive(), "the satellite went quiet even though the command was rejected"


def test_the_mitigation_does_not_break_legitimate_commands():
    """A control that also blocks the real operator is not a mitigation, it is an outage."""
    eps = OUT / "build-eps-hard" / "zephyr" / "zephyr.elf"
    _require(eps)
    with Range(eps) as r:
        assert r.alive(), "the satellite was not answering before the command"

        _forge_power_off(token=POWER_TOKEN)     # same frame, correct token

        event = r.settle_power()
        assert event is not None and event.powered is False, (
            f"an authenticated rail command was not honoured:\n{r.console('eps.uart')}")
        assert not r.alive(timeout=8)
