"""EX-U02: an intruder holding the key, and the sign of one number.

The premise is key compromise, which no control in this range covers: the SDLS MAC verifies, the
telecommand authentication verifies, the anti-replay counter is fresh. Cryptography cannot tell a
key from the person holding it.

Five runs. The first four are the pair, and the fifth is the one the exercise is for:

  1. counters, an intruder transmitting while we are out of view -> +8, and the 8 is the finding.
     Twelve probes went up; the count is a NET, and the intruder's own lockout ate four commands
     of ours, which subtract. Sixteen heard against eight sent. An operator who reads +8 as
     "eight intruder commands" has the wrong number for the same reason they would have the
     wrong number reading a bank balance as a deposit;
  2. counters, nobody else transmitting -> 0. Which is what makes row 1 a measurement rather than
     an artefact of the arithmetic;
  3. no counters, an intruder transmitting -> the station cannot tell, and None is not 0;
  4. no counters, nobody transmitting -> also cannot tell. Rows 3 and 4 are the same observation,
     which is the definition of a blind spot;
  5. counters, NO intruder, and the uplink denied in the second pass -> -4.

Rows 1 and 5 produce the SAME SYMPTOM: our commands stop being acknowledged. One is an antenna
and one is an incident, and they need opposite responses. The sign of the counter is the only
thing in this range that separates them.

Row 1's lockout is not staged. This range has one Security Association, so its SDLS anti-replay
is one counter for the link - COMM's own source says so and says it is not fixed. The intruder
advancing it puts the legitimate station BEHIND, and COMM refuses those frames at the link layer,
so the OBC never sees them and no acceptance and no refusal comes back. EX-G03 is the same shape
one layer down; nobody had measured it here.
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
from cuberange.identity import GROUND_STATIONS, GROUND_VCIDS      # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                     # noqa: E402
from cuberange.paths import out_dir                               # noqa: E402
from cuberange.ports import (channel as channel_port, link as link_port,   # noqa: E402
                             monitor as monitor_port)
from cuberange.renode.profile import profile_args                 # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor          # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from solve import INTRUDER_SOURCE_ID, INTRUDER_VCID, PROBE_SERVICE, PROBE_SUBTYPE  # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
VULN = OUT / "build-obc-u02-vuln" / "zephyr" / "zephyr.elf"       # no telecommand counters
HARD = OUT / "build-obc-u02-hard" / "zephyr" / "zephyr.elf"       # counters in the beacon
COMM = OUT / "build-comm-s01-sat0" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))
COMMANDS, PROBES, PASS_S = 4, 12, 8.0

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not COMM.exists(),
    reason=f"build them: make firmware-u02 firmware-s01 ({VULN})")


class Intrusion:
    def __init__(self, obc: Path, *, intruder: bool, deny_second_pass: bool = False):
        self.obc = obc
        self.intruder = intruder
        self.deny_second_pass = deny_second_pass

    def __enter__(self):
        for name in ("u02-sat0-comm.uart", "u02-sat0-obc.uart", "u02-sat0-eps.uart",
                     "u02-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$obc=@{self.obc}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exu02-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("u02-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("u02-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.in_view = {"now": True}
        self.uplink_open = {"now": True}
        self.channel = LinkChannel(listen_port=channel_port(0), sat_port=link_port(0),
                                   downlink_filter=lambda _f: self.in_view["now"],
                                   uplink_filter=lambda _f: self.uplink_open["now"]).start()
        self.link = SpaceLink(port=channel_port(0))
        self.link.connect(retries=60)
        self.station = GroundStation(self.link, station_id=GROUND_STATIONS["primary"],
                                     vcid=GROUND_VCIDS["primary"],
                                     require_signed_tm=SDLS_KEY, uplink_key=SDLS_KEY,
                                     sdls_spi=SDLS_SPI)
        self.intruder_link = SpaceLink(port=channel_port(0))
        self.intruder_link.connect(retries=60)
        self.thief = GroundStation(self.intruder_link, station_id=INTRUDER_SOURCE_ID,
                                   vcid=INTRUDER_VCID, require_signed_tm=SDLS_KEY,
                                   uplink_key=SDLS_KEY, sdls_spi=SDLS_SPI)
        return self

    def __exit__(self, *exc):
        for obj, closer in ((getattr(self, "intruder_link", None), "close"),
                            (getattr(self, "link", None), "close"),
                            (getattr(self, "channel", None), "stop")):
            if obj is not None:
                getattr(obj, closer)()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def _pass_over(self, send: int) -> int:
        start, sent = time.time(), 0
        while time.time() - start < PASS_S:
            if sent < send and (time.time() - start) > sent * (PASS_S / (send + 1)):
                self.station.send_connection_test()
                sent += 1
            time.sleep(0.3)
            self.station.collect()
        return sent

    def _acquire(self, seconds: float = 6.0) -> None:
        """Receive before transmitting, until the counter has a baseline.

        A real pass starts at acquisition of signal and the operator listens before commanding.
        Here it is also what makes the arithmetic exact: a baseline taken after the first command
        has already been counted measures from an origin one command in, and `commands_heard`
        then reads 15 where 16 is true. Measured - that is where the number came from.

        On the build with no counters nothing sets the baseline and this simply spends the time
        collecting, which is what a station with nothing to establish would do.
        """
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.station.collect()
            if self.station._tc_baseline is not None:
                return
            time.sleep(0.2)

    def fly(self) -> int:
        """One ordinary pass, a gap the intruder uses, and a second ordinary pass.

        Returns how many telecommands the intruder transmitted. The baseline is taken during the
        first pass and it is what makes the second one readable: a station that never saw the
        counter before cannot say where it should be now.
        """
        self._acquire()
        sent = self._pass_over(COMMANDS)
        assert sent == COMMANDS, f"only {sent} of {COMMANDS} commands were sent in pass 1"

        self.in_view["now"] = False
        probes = 0
        if self.intruder:
            #: Listen before transmitting. One Security Association means one anti-replay counter
            #: for the whole link, so an intruder who starts from sequence 1 is refused by a
            #: control aimed at somebody else. `observe_uplink_sequence` is that listening, and
            #: the frames it reads are the operator's own, captured off the link.
            assert self.channel.uplink_frames, "nothing to listen to; pass 1 transmitted nothing"
            self.thief.observe_uplink_sequence(self.channel.uplink_frames[-1])
            for _ in range(PROBES):
                self.thief.send_pus(PROBE_SERVICE, PROBE_SUBTYPE)
                probes += 1
                time.sleep(0.15)
        time.sleep(2.0)

        self.in_view["now"] = True
        if self.deny_second_pass:
            self.uplink_open["now"] = False
        sent = self._pass_over(COMMANDS)
        assert sent == COMMANDS, f"only {sent} of {COMMANDS} commands were sent in pass 2"
        return probes


def test_the_counter_reports_telecommands_this_station_never_sent():
    """The exercise. Twelve, and not one of them refused."""
    with Intrusion(HARD, intruder=True) as p:
        probes = p.fly()
        assert probes == PROBES
        #: PROBES - COMMANDS, and the subtraction is the lesson rather than an adjustment. The
        #: intruder's twelve advanced the link's one anti-replay counter, which locked our second
        #: pass out, so four commands we sent were never heard. A net of +8 out of sixteen heard
        #: and eight sent.
        assert p.station.unexplained_commands == PROBES - COMMANDS, (
            f"net {p.station.unexplained_commands}: the spacecraft heard "
            f"{p.station.commands_heard} telecommands and this station sent "
            f"{p.station.commands_sent}\n" + p.console("u02-sat0-obc.uart")[-900:])
        assert p.station.commands_heard == PROBES + COMMANDS, (
            f"{p.station.commands_heard} heard, expected {PROBES} probes and pass 1's {COMMANDS}")
        #: Refused none of them, which is worth as much as the count. An intruder enumerating a
        #: service tree has not tried to do anything yet, and the pair of numbers says which it is.
        assert p.station.commands_refused == 0, (
            f"{p.station.commands_refused} refused, so the probes reached the authority check - "
            f"this exercise is about commands that do not")
        #: And the symptom the operator actually notices: their own second pass got nothing back,
        #: because the intruder advanced the one anti-replay counter this link has and COMM now
        #: refuses the legitimate station at the link layer. No acceptance, no refusal.
        assert len(p.station.acknowledged) == COMMANDS, (
            f"{len(p.station.acknowledged)} acknowledged; pass 1's {COMMANDS} were expected and "
            f"pass 2's were expected to be refused by COMM before reaching the OBC")
        assert "REPLAY" in p.console("u02-sat0-comm.uart"), (
            "COMM never refused a frame, so the lockout this test asserts did not happen for the "
            "reason it says it did")
        #: The downlink HAS a gap, and it is ours: the beacon ran through a window we were not
        #: receiving in. Row 2 has no intruder and the same gap, so the gap says nothing about
        #: the intrusion - a detector that fires on this station's own pass schedule cannot tell
        #: an operator anything about who else is transmitting.
        assert p.station.reports_missing > 0, (
            "no reports missing, so the out-of-view window did not happen and this run did not "
            "measure what it says it measured")


def test_the_same_station_reports_zero_when_nobody_else_transmits():
    """Which is what stops row 1 being an artefact of the arithmetic."""
    with Intrusion(HARD, intruder=False) as p:
        assert p.fly() == 0
        assert p.station.unexplained_commands == 0, (
            f"{p.station.unexplained_commands} telecommands unaccounted for on a run with no "
            f"intruder - the detector counts something other than what it says it counts")
        assert p.station.commands_refused == 0
        assert len(p.station.acknowledged) == 2 * COMMANDS, (
            "with nobody else on the link both passes should have been acknowledged")
        #: And the gap is here too, identical in kind to row 1's. Named in an assertion rather
        #: than in prose because "the same in both" is a claim, and a claim gets measured.
        assert p.station.reports_missing > 0


def test_a_denied_uplink_moves_the_same_number_the_other_way():
    """Rows 1 and 5 are the same symptom. The sign is the whole difference.

    Nobody is transmitting at this spacecraft but us, and our second pass is being eaten. The
    operator sees precisely what row 1's operator sees: four acknowledged, then nothing. A
    negative count says the spacecraft heard FEWER than we sent, which no intruder can cause and
    no key rotation will fix.
    """
    with Intrusion(HARD, intruder=False, deny_second_pass=True) as p:
        assert p.fly() == 0
        assert len(p.station.acknowledged) == COMMANDS, (
            "this run is only meaningful if the second pass got nothing back, exactly as row 1's "
            "did")
        assert p.station.unexplained_commands == -COMMANDS, (
            f"expected -{COMMANDS}, got {p.station.unexplained_commands}")
        assert "REPLAY" not in p.console("u02-sat0-comm.uart"), (
            "COMM refused a frame, so this run's silence has a second cause and the comparison "
            "with row 1 is not clean")


def test_the_vulnerable_build_cannot_tell_and_says_so():
    with Intrusion(VULN, intruder=True) as p:
        assert p.fly() == PROBES
        assert p.station.unexplained_commands is None, (
            "the vulnerable build produced a count, so the pair's flag does something other than "
            "what this exercise says")
        assert p.station.commands_refused is None
        #: The intrusion happened and every other control passed it. What the operator has is a
        #: second pass with no answers - which is EX-U01's picture exactly, and EX-U01's answer to
        #: it was "your uplink is being denied".
        assert len(p.station.acknowledged) == COMMANDS
        assert p.station.reports_missing > 0


def test_the_vulnerable_build_says_exactly_the_same_thing_with_nobody_there():
    """Rows 4 and 3 being identical in the one number that matters is the blind spot."""
    with Intrusion(VULN, intruder=False) as p:
        assert p.fly() == 0
        assert p.station.unexplained_commands is None
        assert len(p.station.acknowledged) == 2 * COMMANDS
