"""EX-D01: nobody authenticated the channel EX-G04 built.

Five. The middle one is the attack and the two on each side are what make it mean something:

  1. a peer on the crosslink puts a PUS 1,2 in the operator's console for a command the spacecraft
     never saw - and the OBC's console says nothing at all, which is how you know;
  2. the mitigated build plus a station that REQUIRES a trailer refuses it, and says why;
  3. the mitigated build's real reports still arrive and still verify - a station that rejected
     everything would pass 2 and be useless;
  4. the mitigated build with a station that does NOT require a trailer is still fooled, because
     optional authentication is defeated by not attaching any;
  5. COMM transmits the forgery either way. The fix is at the receiver, and this asserts that the
     spacecraft's radio is NOT where it lives - so nobody reads test 2 as "COMM now filters".
"""
import os
import socket
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.link import SpaceLink                           # noqa: E402
from cuberange.gs.station import GroundStation, PUS_TM_TIME_LEN   # noqa: E402
from cuberange.identity import GROUND_STATIONS, spacecraft        # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                     # noqa: E402
from cuberange.paths import out_dir                               # noqa: E402
from cuberange.ports import (crosslink_injector, link as link_port,   # noqa: E402
                             monitor as monitor_port)
from cuberange.proto import pus_auth, sdls                        # noqa: E402
from cuberange.proto.csp import encode_packet                     # noqa: E402
from cuberange.proto.pus import (FAILURE_NOT_AUTHORISED, PusTc, PusTm,   # noqa: E402
                                 request_id, SERVICE_VERIFICATION,
                                 SUBTYPE_ACCEPTANCE_FAILURE)
from cuberange.proto.spacepacket import PacketType, SpacePacket   # noqa: E402
from cuberange.renode.profile import profile_args                 # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor          # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
VULN = OUT / "build-obc-d01-vuln" / "zephyr" / "zephyr.elf"
HARD = OUT / "build-obc-d01-hard" / "zephyr" / "zephyr.elf"
COMM = OUT / "build-comm-s01-sat0" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))

VICTIM, PEER = spacecraft(0), spacecraft(1)
CSP_PORT_PUS, SPORT = 10, 20

pytestmark = pytest.mark.skipif(
    not VULN.exists() or not HARD.exists() or not COMM.exists(),
    reason=f"build them: make firmware-d01 firmware-s01 ({VULN})")


def forged_report(seq: int = 0, code: int = FAILURE_NOT_AUTHORISED) -> bytes:
    """A PUS 1,2 the spacecraft never sent, addressed to the primary station."""
    app = request_id(VICTIM.apid, seq) + bytes([code])
    tm = PusTm(service=SERVICE_VERIFICATION, subtype=SUBTYPE_ACCEPTANCE_FAILURE,
               dest_id=GROUND_STATIONS["primary"], time=bytes(PUS_TM_TIME_LEN), app_data=app)
    return SpacePacket(apid=VICTIM.apid, ptype=PacketType.TM, sec_hdr=True, seq_count=0,
                       data=tm.encode()).encode()


class Range:
    def __init__(self, obc_elf: Path, require_signed: bool):
        self.obc_elf = obc_elf
        self.require_signed = require_signed

    def __enter__(self):
        for name in ("d01-sat0-comm.uart", "d01-sat0-obc.uart", "d01-sat0-eps.uart",
                     "d01-sat1-comm.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                "-e", f"$injector=@{REPO}/attacker/TcpCanInjector.cs",
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"$obc=@{self.obc_elf}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "exd01-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("d01-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("d01-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.link = SpaceLink(port=link_port(0))
        self.link.connect(retries=60)
        self.station = GroundStation(
            self.link, station_id=GROUND_STATIONS["primary"],
            require_signed_tm=SDLS_KEY if self.require_signed else None)
        return self

    def __exit__(self, *exc):
        link = getattr(self, "link", None)
        if link is not None:
            link.close()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def forge(self, settle: float = 6.0) -> None:
        sock = socket.create_connection(("127.0.0.1", crosslink_injector()), timeout=5)
        try:
            for f in encode_packet(src=PEER.comm, dst=VICTIM.comm, dport=CSP_PORT_PUS,
                                   sport=SPORT, payload=forged_report()):
                sock.sendall(f"{f.can_id:x} {f.data.hex()}\n".encode())
        finally:
            sock.close()
        time.sleep(settle)
        self.station.collect()

    def real_ping(self, settle: float = 6.0) -> list:
        """An authenticated PUS 17,1 the OBC answers - both layers, because both are deployed."""
        inner = SpacePacket(apid=VICTIM.apid, ptype=PacketType.TC, sec_hdr=True, seq_count=0,
                            data=PusTc(service=17, subtype=1,
                                       source_id=GROUND_STATIONS["primary"]).encode()).encode()
        self.link.send_frame(sdls.encode_tc(pus_auth.sign(inner, key=SDLS_KEY, seq=1),
                                            key=SDLS_KEY, spi=SDLS_SPI,
                                            iv=bytes(range(0xA0, 0xAC)), seq_num=1,
                                            frame_seq=0, scid=VICTIM.scid, vcid=0))
        time.sleep(settle)
        return self.station.collect()


def test_a_peer_puts_a_refusal_in_the_operators_console():
    with Range(VULN, require_signed=False) as r:
        r.forge()
        assert r.station.refusals, (
            "the operator saw nothing; the forged report did not reach them\n"
            + r.console("d01-sat0-comm.uart"))
        got = r.station.refusals[0]
        assert got.apid == VICTIM.apid and got.dest_id == GROUND_STATIONS["primary"]
        #: The OBC is the thing that would have sent a real one. It has nothing to say.
        obc = r.console("d01-sat0-obc.uart")
        assert "acceptance failure reported" not in obc, (
            "the OBC really did refuse something; this test forged nothing\n" + obc)
        assert "REJECTED" not in obc, obc


def test_the_signed_build_and_a_requiring_station_refuse_it():
    with Range(HARD, require_signed=True) as r:
        r.forge()
        assert not r.station.refusals, (
            f"the operator still believes a forged refusal: {r.station.refusals}")
        assert r.station.unauthenticated, (
            "nothing was rejected and nothing was believed - the frame may never have arrived, "
            "which would make this test vacuous")


def test_the_real_reports_still_arrive_and_verify():
    """A station that rejected everything would pass the test above and be useless."""
    with Range(HARD, require_signed=True) as r:
        before = len(r.station.unauthenticated)
        mine = r.real_ping()
        assert mine, (
            "the operator got no answer to an authenticated command\n"
            + r.console("d01-sat0-obc.uart"))
        assert len(r.station.unauthenticated) == before, (
            f"the spacecraft's own report was rejected: {r.station.unauthenticated[before:]}")


def test_optional_authentication_is_no_authentication():
    """The signed build, and a station that verifies only when a trailer happens to be there."""
    with Range(HARD, require_signed=False) as r:
        r.forge()
        assert r.station.refusals, (
            "this should still be fooled: the attacker attaches no trailer, and a station that "
            "does not require one has nothing to check")


def test_comm_transmits_the_forgery_either_way():
    """So nobody reads the mitigation as "COMM now filters". It does not, and cannot.

    COMM frames whatever arrives on its PUS port because that is what a radio does. The thing that
    can tell a real report from a forged one is the thing that holds the key.
    """
    for elf, label in ((VULN, "vulnerable"), (HARD, "signed")):
        with Range(elf, require_signed=False) as r:
            r.forge()
            comm = r.console("d01-sat0-comm.uart")
            assert f"downlink 22 octets from node {PEER.comm}" in comm, (
                f"{label} build: COMM did not transmit the forged frame, so this exercise is "
                f"measuring something other than what it says\n{comm}")
