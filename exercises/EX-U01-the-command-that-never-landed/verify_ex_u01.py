"""EX-U01: every downlink control reports a healthy link while no command arrives.

Four passes:

  1. acknowledgements, no attacker  -> four sent, four acknowledged;
  2. acknowledgements, uplink denied -> four sent, ZERO acknowledged, and the pass schedule still
     says ok. That last clause is the exercise: the beacon arrives throughout and the counter has
     no gaps, so every control built for the downlink reports health - truthfully, about the only
     direction it can see;
  3. no acknowledgements, uplink denied -> zero, indistinguishable from
  4. no acknowledgements, NO attacker -> also zero. A spacecraft that never acknowledges is
     silent about commands by design, which is what makes rows 2 and 4 the same observation on
     the vulnerable build.
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
from cuberange.gs.passes import Pass, PassLog                     # noqa: E402
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
VULN = OUT / "build-obc-u01-vuln" / "zephyr" / "zephyr.elf"       # no acknowledgements
HARD = OUT / "build-obc-u01-hard" / "zephyr" / "zephyr.elf"       # acknowledgements
COMM = OUT / "build-comm-s01-sat0" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))
COMMANDS, WINDOW_S = 4, 12.0

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not COMM.exists(),
    reason=f"build them: make firmware-u01 firmware-s01 ({VULN})")


class UplinkPass:
    def __init__(self, obc: Path, *, deny: bool):
        self.obc = obc
        self.deny = deny

    def __enter__(self):
        for name in ("u01-sat0-comm.uart", "u01-sat0-obc.uart", "u01-sat0-eps.uart",
                     "u01-sat1-comm.uart"):
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
        self._ctx = self.sup.launch(argv, OUT / "exu01-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("u01-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("u01-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.channel = LinkChannel(listen_port=channel_port(0), sat_port=link_port(0),
                                   uplink_filter=(lambda _f: False) if self.deny else None
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
        log, sent = PassLog(), 0
        gap = WINDOW_S / (COMMANDS + 1)
        while time.time() < window.end:
            if sent < COMMANDS and (time.time() - start) > sent * gap:
                self.station.send_connection_test()
                sent += 1
            time.sleep(0.4)
            self.station.collect()
            while len(log.heard) < len(self.station.telemetry):
                log.record()
        assert sent == COMMANDS, f"only {sent} of {COMMANDS} commands were sent"
        return window, log


def test_acknowledgements_arrive_when_nobody_denies_the_uplink():
    with UplinkPass(HARD, deny=False) as p:
        window, log = p.fly()
        assert len(p.station.acknowledged) == COMMANDS, (
            f"{len(p.station.acknowledged)} of {COMMANDS} acknowledged\n"
            + p.console("u01-sat0-obc.uart")[-600:])
        assert log.verdict(window) == "ok"


def test_denying_the_uplink_stops_the_acknowledgements_and_nothing_else():
    """The exercise. Every downlink-side control still reports health, truthfully."""
    with UplinkPass(HARD, deny=True) as p:
        window, log = p.fly()
        assert len(p.channel.uplink_suppressed) == COMMANDS, (
            f"took {len(p.channel.uplink_suppressed)} uplink frames of {COMMANDS}")
        assert p.station.acknowledged == [], (
            f"the spacecraft acknowledged something it never received: {p.station.acknowledged}")
        #: And the controls from five previous exercises all say the link is fine.
        assert log.in_window(window) > 0, "the beacon stopped, so this is not an uplink-only denial"
        assert log.verdict(window) == "ok"
        assert p.station.counter_gaps == [], (
            f"the downlink had gaps: {p.station.counter_gaps}. This exercise is about a denial "
            f"that leaves the downlink untouched, so the attack did more than it should have.")


def test_the_vulnerable_build_is_silent_about_commands_under_denial():
    with UplinkPass(VULN, deny=True) as p:
        window, log = p.fly()
        assert p.station.acknowledged == []
        assert log.verdict(window) == "ok"


def test_the_vulnerable_build_is_equally_silent_with_no_attacker():
    """Which is what makes the row above indistinguishable rather than merely quiet."""
    with UplinkPass(VULN, deny=False) as p:
        window, log = p.fly()
        assert not p.channel.uplink_suppressed, "this run was supposed to have no attacker"
        assert p.station.acknowledged == [], (
            "the vulnerable build acknowledged a command, so the pair's flag is doing something "
            "other than what this exercise says")
        assert log.verdict(window) == "ok"
