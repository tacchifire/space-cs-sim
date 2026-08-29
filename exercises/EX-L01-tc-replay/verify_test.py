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

from cuberange.channel.link_channel import LinkChannel          # noqa: E402
from cuberange.gs.link import SpaceLink                          # noqa: E402
from cuberange.gs.station import GroundStation                   # noqa: E402
from cuberange.renode.monitor import Monitor                     # noqa: E402
from cuberange.renode.powerdomain import PowerDomain             # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor         # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = Path(os.environ.get("OUT", "/tmp/cuberange"))
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"

SAT_LINK_PORT = 3777      # Renode's socket terminal on COMM.usart2
GS_LINK_PORT = 3877       # what the ground station connects to, via the channel
MONITOR_PORT = 3778
BOOT_SETTLE_S = 4.0

# The EPS restores the rail 30 s after it goes off; allow for emulation running at ~2.3x.
FDIR_WAIT_S = 45.0


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
        time.sleep(BOOT_SETTLE_S)
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
