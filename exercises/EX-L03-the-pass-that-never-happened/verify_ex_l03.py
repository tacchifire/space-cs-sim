"""EX-L03: total denial makes no gaps, and a schedule needs something to expect.

Four passes and a unit test. The fourth pass is the finding:

  1. beacon, no attacker  -> heard, no gaps, "ok";
  2. beacon, all denied   -> nothing heard, STILL no gaps, "silent". The counter detector that
     EX-D02 and EX-L02 built cannot see this, which is asserted rather than described;
  3. no beacon, all denied -> "silent" as well, so far so good;
  4. NO BEACON, NO ATTACKER -> also "silent". The schedule fires on a pass nobody attacked,
     because a spacecraft that speaks only when spoken to is silent by design. The ground-side
     control and the spacecraft-side behaviour are one mitigation, not two.

The unit test covers the verdict boundaries, which a 15-second pass exercises one of.
"""
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.channel.link_channel import LinkChannel            # noqa: E402
from cuberange.gs.link import SpaceLink                           # noqa: E402
from cuberange.gs.passes import Pass, PassLog                   # noqa: E402
from cuberange.gs.station import GroundStation                    # noqa: E402
from cuberange.identity import GROUND_STATIONS                    # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                     # noqa: E402
from cuberange.paths import out_dir                               # noqa: E402
from cuberange.ports import (channel as channel_port, link as link_port,   # noqa: E402
                             monitor as monitor_port)
from cuberange.renode.profile import profile_args                 # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor          # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
VULN = OUT / "build-obc-l03-vuln" / "zephyr" / "zephyr.elf"      # no beacon
HARD = OUT / "build-obc-l03-hard" / "zephyr" / "zephyr.elf"      # beacon
COMM = OUT / "build-comm-s01-sat0" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))
WINDOW_S = 12.0

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not COMM.exists(),
    reason=f"build them: make firmware-l03 firmware-s01 ({VULN})")


class PassRun:
    def __init__(self, obc: Path, *, deny: bool):
        self.obc = obc
        self.deny = deny

    def __enter__(self):
        for name in ("l03-sat0-comm.uart", "l03-sat0-obc.uart", "l03-sat0-eps.uart",
                     "l03-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                "-e", f"$injector=@{REPO}/attacker/TcpCanInjector.cs",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$obc=@{self.obc}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exl03-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("l03-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("l03-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.channel = LinkChannel(listen_port=channel_port(0), sat_port=link_port(0),
                                   downlink_filter=(lambda _f: False) if self.deny else None
                                   ).start()
        self.link = SpaceLink(port=channel_port(0))
        self.link.connect(retries=60)
        self.station = GroundStation(self.link, station_id=GROUND_STATIONS["primary"],
                                     require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY,
                                     sdls_spi=SDLS_SPI)
        return self

    def __exit__(self, *exc):
        for obj, closer in ((getattr(self, "link", None), "close"),
                            (getattr(self, "channel", None), "stop")):
            if obj is not None:
                getattr(obj, closer)()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def fly(self):
        start = time.time()
        window = Pass("pass", start=start, end=start + WINDOW_S, expect_at_least=1)
        log = PassLog()
        while time.time() < window.end:
            time.sleep(0.4)
            self.station.collect()
            while len(log.heard) < len(self.station.telemetry):
                log.record()
        return window, log


def test_a_beaconing_spacecraft_fills_a_window_nobody_attacks():
    with PassRun(HARD, deny=False) as r:
        window, log = r.fly()
        assert "beacon" in r.console("l03-sat0-obc.uart"), (
            "the beacon build did not beacon; the rest of this file measures nothing")
        assert log.in_window(window) > 0
        assert log.verdict(window) == "ok"


def test_total_denial_produces_no_counter_gaps_at_all():
    """The reason EX-L02's detector cannot see this, asserted rather than described."""
    with PassRun(HARD, deny=True) as r:
        window, log = r.fly()
        assert r.channel.suppressed, "nothing was denied"
        assert log.in_window(window) == 0
        assert r.station.counter_gaps == [], (
            f"total denial produced gaps: {r.station.counter_gaps}. If that is true the exercise's "
            f"premise is wrong and the write-up has to change.")
        assert log.verdict(window) == "silent"


def test_the_schedule_is_silent_without_a_beacon_even_with_no_attacker():
    """The finding. A schedule with nothing to expect reports a silent pass every time."""
    with PassRun(VULN, deny=False) as r:
        window, log = r.fly()
        assert not r.channel.suppressed, "this run was supposed to have no attacker"
        assert "beacon" not in r.console("l03-sat0-obc.uart")
        assert log.in_window(window) == 0
        assert log.verdict(window) == "silent", (
            "a spacecraft that only speaks when spoken to was not silent, so the pair's flag is "
            "doing something other than what this exercise says")


def test_the_beaconless_build_is_silent_under_denial_too():
    """Which is what makes the row above a false positive rather than a different situation."""
    with PassRun(VULN, deny=True) as r:
        window, log = r.fly()
        assert log.verdict(window) == "silent"
        assert r.station.counter_gaps == []


@pytest.mark.parametrize("heard,expect_at_least,verdict", [
    ([], 1, "silent"),
    ([1.0], 1, "ok"),
    ([], 0, "ok"),                 # no expectation, so nothing to be silent about
    ([1.0, 2.0], 10, "quiet"),     # fewer than half of ten
    ([1.0] * 6, 10, "ok"),         # more than half is not a finding
])
def test_the_verdict_boundaries(heard, expect_at_least, verdict):
    window = Pass("w", start=0.0, end=10.0, expect_at_least=expect_at_least)
    assert PassLog(heard=list(heard)).verdict(window) == verdict
