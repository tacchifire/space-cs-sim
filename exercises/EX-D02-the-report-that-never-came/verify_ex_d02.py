"""EX-D02: the gap check and the signature check are one mitigation, not two.

Four, and the second is the one the exercise exists for:

  1. a suppressed report leaves a gap the operator can see, ON AUTHENTICATED TELEMETRY;
  2. the SAME attacker defeats it when the station is not requiring signatures - they take the
     report and put one back carrying the counter the missing one would have had, and the gap
     closes. A detector whose input the attacker controls detects nothing;
  3. an undisturbed pass produces no gaps - a station that reported one after every command would
     pass 1 and be useless;
  4. the counter arithmetic survives a wrap, because a 16-bit counter rolls over and a station
     that announced 65534 missing reports once a day would be switched off by the second day.

Test 4 is a unit test living beside three integration ones on purpose: it is the line of the
mitigation most likely to be wrong and least likely to be exercised by a 3-command pass.
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
from cuberange.identity import GROUND_STATIONS, spacecraft        # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                     # noqa: E402
from cuberange.paths import out_dir                               # noqa: E402
from cuberange.ports import (channel as channel_port, link as link_port,   # noqa: E402
                             monitor as monitor_port)
from cuberange.proto import pus_auth, sdls                        # noqa: E402
from cuberange.proto.frame import decode_tm_frame, encode_tm_frame  # noqa: E402
from cuberange.proto.pus import PusTc                             # noqa: E402
from cuberange.proto.spacepacket import PacketType, SpacePacket   # noqa: E402
from cuberange.renode.profile import profile_args                 # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor          # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
OBC = OUT / "build-obc-d01-hard" / "zephyr" / "zephyr.elf"
COMM = OUT / "build-comm-s01-sat0" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))

SAT = spacecraft(0)

pytestmark = pytest.mark.skipif(
    not OBC.exists() or not COMM.exists(),
    reason=f"build them: make firmware-d01 firmware-s01 ({OBC})")


class Range:
    """A pass with an attacker on the link, and a station configured one way or the other."""

    def __init__(self, *, require_signed: bool, take_nth: int | None, replace: bool = False):
        self.require_signed = require_signed
        self.take_nth = take_nth
        self.replace = replace
        self._seen = 0
        self._taken = None

    def _decide(self, frame: bytes) -> bool:
        self._seen += 1
        if self.take_nth is None or self._seen != self.take_nth:
            return True
        self._taken = frame
        return False

    def __enter__(self):
        for name in ("d02-sat0-comm.uart", "d02-sat0-obc.uart", "d02-sat0-eps.uart",
                     "d02-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                "-e", f"$injector=@{REPO}/attacker/TcpCanInjector.cs",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$obc=@{OBC}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exd02-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("d02-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("d02-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.channel = LinkChannel(listen_port=channel_port(0), sat_port=link_port(0),
                                   downlink_filter=self._decide).start()
        self.link = SpaceLink(port=channel_port(0))
        self.link.connect(retries=60)
        self.station = GroundStation(
            self.link, station_id=GROUND_STATIONS["primary"],
            require_signed_tm=SDLS_KEY if self.require_signed else None)
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

    def pass_of(self, commands: int = 3, settle: float = 4.0) -> None:
        """Send `commands` authenticated pings and collect whatever comes back."""
        for i in range(1, commands + 1):
            inner = SpacePacket(apid=SAT.apid, ptype=PacketType.TC, sec_hdr=True, seq_count=i,
                                data=PusTc(service=17, subtype=1,
                                           source_id=GROUND_STATIONS["primary"]).encode()).encode()
            self.link.send_frame(sdls.encode_tc(pus_auth.sign(inner, key=SDLS_KEY, seq=i),
                                                key=SDLS_KEY, spi=SDLS_SPI,
                                                iv=bytes(range(0xA0, 0xAC)), seq_num=i,
                                                frame_seq=i, scid=SAT.scid, vcid=0))
            time.sleep(settle)
            self.station.collect()
            if self.replace and self._taken is not None:
                self._put_one_back()

    def _put_one_back(self) -> None:
        """The attacker's replacement: same counter, no trailer, because none is required."""
        mc, vc, payload = decode_tm_frame(self._taken, expect_scid=SAT.scid)
        inner = pus_auth.verify(payload, key=SDLS_KEY).packet
        self.channel.transmit_to_ground(
            encode_tm_frame(inner, mc_count=mc, vc_count=vc, scid=SAT.scid))
        self._taken = None
        time.sleep(3)
        self.station.collect()


def test_a_suppressed_report_leaves_a_gap_the_operator_can_see():
    with Range(require_signed=True, take_nth=2) as r:
        r.pass_of(3)
        assert r.channel.suppressed, (
            "nothing was suppressed, so this measured an undisturbed pass")
        assert r.station.reports_missing == 1, (
            f"the operator counted {r.station.reports_missing} missing; "
            f"gaps={r.station.counter_gaps}")


def test_the_same_attacker_closes_the_gap_when_signatures_are_not_required():
    """The finding. A detector whose input the attacker controls detects nothing."""
    with Range(require_signed=False, take_nth=2, replace=True) as r:
        r.pass_of(3)
        assert r.channel.suppressed, "nothing was suppressed; the attack did not run"
        assert r.station.reports_missing == 0, (
            f"the replacement did not close the gap: {r.station.counter_gaps}. That would make "
            f"this exercise's whole point wrong, so check the replacement before the assertion.")


def test_an_undisturbed_pass_produces_no_gaps():
    """A station that announced a gap after every command would pass the first test and be useless."""
    with Range(require_signed=True, take_nth=None) as r:
        r.pass_of(3)
        assert not r.channel.suppressed
        assert r.station.reports_missing == 0, (
            f"gaps on a clean pass: {r.station.counter_gaps}")
        assert r.station.last_report_counter is not None, (
            "no report was counted at all, so the test above proves nothing")


@pytest.mark.parametrize("sequence,expect_missing", [
    ([0, 1, 2, 3], 0),
    ([0, 1, 3], 1),
    ([0, 5], 4),
    ([0xFFFD, 0xFFFE, 0xFFFF, 0, 1], 0),          # a wrap is not 65534 losses
    ([0xFFFE, 1], 2),                             # a gap ACROSS the wrap is still a gap
    ([5, 4, 5], 0),                               # reordering is not counted as loss
])
def test_the_counter_arithmetic_survives_a_wrap(sequence, expect_missing):
    st = GroundStation.__new__(GroundStation)
    st.last_report_counter = None
    st.counter_gaps = []
    for value in sequence:
        st._advance(value)
    assert st.reports_missing == expect_missing, (
        f"{sequence} -> {st.counter_gaps}")
