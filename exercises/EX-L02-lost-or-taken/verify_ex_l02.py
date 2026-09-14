"""EX-L02: a gap is a question, and only one of these builds can answer it.

Four:

  1. on a lossy link with no attacker the detector still fires - two gaps, nobody's doing;
  2. with an attacker as well it fires three times, and NOTHING in the gap list separates them.
     That is the problem statement, asserted rather than described;
  3. the mitigated build answers: asked for all three, it resends the two the link lost and never
     resends the one that is being taken;
  4. the vulnerable build resends NONE of them, so the same procedure concludes that all three
     were taken. It fails confident, not safe, and that is its own assertion because a reader who
     skipped it would take "asking works" as the lesson rather than "asking works if somebody can
     answer".

The loss is seeded (LinkChannel takes a fixed seed) so these numbers are the same every run. A
test that fails one run in twenty is a test people re-run rather than read - W48.
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
VULN = OUT / "build-obc-l02-vuln" / "zephyr" / "zephyr.elf"
HARD = OUT / "build-obc-l02-hard" / "zephyr" / "zephyr.elf"
COMM = OUT / "build-comm-s01-sat0" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))

#: Twenty commands at 10% loss. Twelve was not enough: the seeded stream's first twelve draws are
#: all above 0.10, so a shorter pass measured a clean link and said nothing. The expectation is
#: 2.0 losses and the seed delivers 2 - checked before this file was written, not after it failed.
COMMANDS, LOSS, TAKE_NTH = 20, 0.10, 5

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not COMM.exists(),
    reason=f"build them: make firmware-l02 firmware-s01 ({VULN})")


class Pass:
    def __init__(self, obc: Path, *, attacker: bool):
        self.obc = obc
        self.attacker = attacker
        self._seen = 0

    def _decide(self, frame: bytes) -> bool:
        self._seen += 1
        #: Every time, not once. An attacker who takes it once looks like the link, which the
        #: mitigation write-up says plainly is not caught.
        return not (self.attacker and self._seen == TAKE_NTH)

    def __enter__(self):
        for name in ("l02-sat0-comm.uart", "l02-sat0-obc.uart", "l02-sat0-eps.uart",
                     "l02-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=300, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                "-e", f"$injector=@{REPO}/attacker/TcpCanInjector.cs",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$obc=@{self.obc}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exl02-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("l02-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("l02-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.channel = LinkChannel(listen_port=channel_port(0), sat_port=link_port(0),
                                   frame_loss=LOSS, downlink_filter=self._decide).start()
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

    def fly(self) -> None:
        for _ in range(COMMANDS):
            self.station.send_connection_test()
            time.sleep(1.8)
            self.station.collect()

    def missing(self) -> list:
        return [c for last, cur, _ in self.station.counter_gaps for c in range(last + 1, cur)]

    def ask_for(self, counters) -> list:
        """Returns the counters the spacecraft actually resent."""
        back = []
        for counter in counters:
            self.station.request_resend(counter)
            time.sleep(4)
            self.station.collect()
            if f"resent report {counter} " in self.console("l02-sat0-obc.uart"):
                back.append(counter)
        return back


def test_a_lossy_link_makes_the_detector_fire_with_no_attacker():
    with Pass(HARD, attacker=False) as p:
        p.fly()
        assert p.channel.lost, "the seeded link lost nothing; this pass measured a clean link"
        assert not p.channel.suppressed
        assert p.station.reports_missing == len(p.channel.lost), (
            f"the operator counted {p.station.reports_missing} missing and the link lost "
            f"{len(p.channel.lost)}: {p.station.counter_gaps}")


def test_the_gap_list_does_not_say_which_is_which():
    """The problem statement. Three gaps, one attacker, and nothing to tell them apart by."""
    with Pass(HARD, attacker=True) as p:
        p.fly()
        assert p.channel.suppressed, "the attacker took nothing"
        assert p.station.reports_missing == len(p.channel.lost) + len(p.channel.suppressed), (
            f"gaps={p.station.counter_gaps} lost={len(p.channel.lost)} "
            f"taken={len(p.channel.suppressed)}")
        #: Every gap is one report wide and carries no other distinguishing field. If that ever
        #: stops being true the exercise has a different problem statement and should say so.
        assert all(missing == 1 for _, _, missing in p.station.counter_gaps)


def test_asking_again_separates_the_link_from_the_attacker():
    with Pass(HARD, attacker=True) as p:
        p.fly()
        missing = p.missing()
        assert len(missing) >= 2, f"need a lost one and a taken one to separate: {missing}"
        back = p.ask_for(missing)
        never = [c for c in missing if c not in back]
        assert len(back) == len(p.channel.lost), (
            f"resent {back}, link lost {len(p.channel.lost)}")
        assert len(never) == len(p.channel.suppressed), (
            f"never came back {never}, attacker took {len(p.channel.suppressed)}")


def test_the_vulnerable_build_answers_nothing_and_looks_certain():
    """It fails confident, not safe - and a reader who skips this takes the wrong lesson."""
    with Pass(VULN, attacker=True) as p:
        p.fly()
        missing = p.missing()
        assert missing, "nothing was missing, so there was nothing to ask"
        back = p.ask_for(missing)
        assert back == [], (
            f"a build with no report store resent {back}; the pair's flag is doing nothing")
        #: The finding: the same procedure now names every gap as an attack, including the ones
        #: the link lost.
        assert len(missing) > len(p.channel.suppressed), (
            "this pass had no link losses, so it cannot show the false conclusion")
