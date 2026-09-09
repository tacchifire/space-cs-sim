"""EX-L01 verification, in both directions.

The chain this closes: EX-B01's mitigation authenticates power commands with a fixed token, and its
own write-up says the control is replayable. Here an attacker proves it - without ever holding the
token, without touching the internal bus, and without understanding a single field of the frame.

  1. against the vulnerable COMM, a captured telecommand replays and kills the radio again
  2. against the mitigated COMM, the same bytes are rejected
  3. against the mitigated COMM, the operator's next real command still works

The satellite recovers between the original command and the replay because the EPS restores the
COMM rail after a timeout - a real interlock, and the reason a single bad command is an outage
rather than a loss.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import ports  # noqa: E402
from cuberange.paths import out_dir                             # noqa: E402
from cuberange.channel.link_channel import LinkChannel          # noqa: E402
from cuberange.gs.link import SpaceLink                          # noqa: E402
from cuberange.gs.station import GroundStation                   # noqa: E402
from cuberange.renode.profile import profile_args
from cuberange.renode.monitor import Monitor                     # noqa: E402
from cuberange.renode.powerdomain import PowerDomain             # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor         # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
INJECTOR = REPO / "attacker" / "TcpCanInjector.cs"

SAT_LINK_PORT = ports.link(0)     # Renode's socket terminal on COMM.usart2
GS_LINK_PORT = ports.channel(0)   # what the ground station connects to, via the channel
MONITOR_PORT = ports.monitor()
# Upper bound on how long the nodes may take to come up, not a fixed wait - the code waits for
# EPS's own readiness line and only uses this to give up. Raise it on a slow host.
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "30"))

# The EPS restores the rail 30 virtual seconds after it goes off. This budget is WALL CLOCK,
# so it has to cover 30 virtual seconds at whatever speed the host actually emulates:
#   x86-64 at 2.34x -> ~13 s (45 s is generous)
#   Pi 5   at ~0.75x -> ~40 s (45 s is uncomfortably close)
#   Pi 4   at ~0.30x -> ~100 s (45 s FAILS)
# Measure the ratio with `make probe` and set this to roughly 30/ratio, plus margin.
FDIR_WAIT_S = float(os.environ.get("CUBERANGE_FDIR_WAIT_S", "45"))


class Range:
    """A running scenario with a channel between the ground station and the spacecraft."""

    def __init__(self, comm_elf: Path):
        self.comm_elf = comm_elf
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=300, rss_ceiling_mb=2048)

    def __enter__(self):
        for name in ("comm.uart", "obc.uart", "eps.uart"):
            (OUT / name).unlink(missing_ok=True)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(MONITOR_PORT),
                "-e", f"$comm=@{self.comm_elf}",
                "-e", f"$injector=@{INJECTOR}",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exl01-renode.log")
        self.run = self._ctx.__enter__()

        self.channel = LinkChannel(listen_port=GS_LINK_PORT, sat_port=SAT_LINK_PORT).start()
        self.link = SpaceLink(port=GS_LINK_PORT)
        self.link.connect(retries=60)
        self.station = GroundStation(self.link)
        self.mon = Monitor(port=MONITOR_PORT).connect()
        self.power = PowerDomain(self.mon, comm_elf=str(self.comm_elf))
        # Wait for EPS to say it is listening, rather than sleeping a fixed interval and hoping.
        #
        # The commands this exercise sends go to EPS on CSP port 11 and are transmitted once, with
        # no retry. One sent before EPS's CAN interface is up is simply dropped, and wait_rail then
        # polls a rail that is never going to change - which is how this showed up: an intermittent
        # failure of the FIRST assertion, with an eps.uart log that ends at "EPS listening" and a
        # rail that never moved. alive() does not cover it, because it pings OBC through COMM and
        # says nothing about EPS. A fixed sleep also gets worse on a slower host, not better.
        #
        # Tear down explicitly if this fails: __exit__ is not called when __enter__ raises, so a
        # bare assert here would leave a supervised Renode running and its ports held.
        if not self.wait_console("eps.uart", "EPS listening", seconds=BOOT_TIMEOUT_S):
            console = self.console("eps.uart")
            self.__exit__(None, None, None)
            raise AssertionError(
                f"EPS never reported ready within {BOOT_TIMEOUT_S}s:\n{console}")
        self.power.poll()
        return self

    def __exit__(self, *exc):
        for closer in (getattr(self, "mon", None), getattr(self, "link", None),
                       getattr(self, "channel", None)):
            if closer is not None:
                closer.stop() if hasattr(closer, "stop") else closer.close()
        self._ctx.__exit__(*exc)

    def alive(self, timeout: float = 8.0) -> bool:
        return self.station.ping(timeout=timeout) is not None

    def wait_console(self, name: str, needle: str, seconds: float) -> bool:
        """Poll a node's console until it prints `needle`."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if needle in self.console(name):
                return True
            time.sleep(0.2)
        return False

    def wait_rail(self, want: bool, seconds: float):
        """Poll until the rail reaches `want`, applying power changes as they happen."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.power.poll()
            if self.power.state is want:
                return True
            time.sleep(0.3)
        return False

    def console(self, name: str) -> str:
        path = OUT / name
        return path.read_text(errors="replace") if path.exists() else ""


def _require(elf: Path):
    if not elf.exists():
        pytest.skip(f"{elf} missing - run 'make firmware-exl01' first")


def _capture_rail_off_frame(r: Range) -> bytes:
    """Operator sends a legitimate rail-off command; the attacker keeps the frame."""
    before = len(r.channel.uplink_frames)
    r.station.set_comm_rail(False)
    assert r.channel.wait_for_uplink(before + 1, timeout=10), "the channel captured no uplink"
    frame = r.channel.uplink_frames[-1]

    assert r.wait_rail(False, seconds=15), (
        f"the operator's own command did not cut the rail:\n{r.console('eps.uart')}")
    return frame


def test_replay_succeeds_against_the_vulnerable_comm():
    comm = OUT / "build-comm" / "zephyr" / "zephyr.elf"
    _require(comm)
    with Range(comm) as r:
        assert r.alive(), "the satellite was not answering at the start"

        frame = _capture_rail_off_frame(r)
        assert len(frame) > 0

        # The satellite recovers on its own; that interlock is what makes a replay worth having.
        assert r.wait_rail(True, seconds=FDIR_WAIT_S), (
            f"FDIR never restored the rail:\n{r.console('eps.uart')}")
        assert r.alive(timeout=15), "the satellite did not come back after FDIR restored power"

        # Same bytes, sent again. The attacker never parsed them.
        r.channel.replay(frame)

        assert r.wait_rail(False, seconds=20), (
            f"the replayed command had no effect:\n{r.console('comm.uart')}")
        assert not r.alive(timeout=8), "the satellite still answered after the replay"


def test_replay_is_rejected_by_the_hardened_comm():
    comm = OUT / "build-comm-hard" / "zephyr" / "zephyr.elf"
    _require(comm)
    with Range(comm) as r:
        assert r.alive(), "the satellite was not answering at the start"

        frame = _capture_rail_off_frame(r)
        assert r.wait_rail(True, seconds=FDIR_WAIT_S), "FDIR never restored the rail"
        assert r.alive(timeout=15), "the satellite did not come back"

        r.channel.replay(frame)

        assert not r.wait_rail(False, seconds=15), "the replay cut the rail despite anti-replay"
        assert "REJECTED replayed frame" in r.console("comm.uart"), (
            f"COMM did not log a rejection:\n{r.console('comm.uart')}")
        assert r.alive(), "the satellite went quiet even though the replay was rejected"


def test_the_hardened_comm_still_accepts_new_commands():
    """Anti-replay that also rejects the operator's next command is an outage, not a control."""
    comm = OUT / "build-comm-hard" / "zephyr" / "zephyr.elf"
    _require(comm)
    with Range(comm) as r:
        assert r.alive(), "the satellite was not answering at the start"

        _capture_rail_off_frame(r)
        assert r.wait_rail(True, seconds=FDIR_WAIT_S), "FDIR never restored the rail"
        assert r.alive(timeout=15), "the satellite did not come back"

        # A brand new command, with a fresh sequence number, must be honoured.
        r.station.set_comm_rail(False)
        assert r.wait_rail(False, seconds=20), (
            f"a new operator command was rejected as a replay:\n{r.console('comm.uart')}")
