"""EX-S02: the one control here that is not attached to a path.

Five, and the shape is deliberate: the first two say what still works, so the third cannot be read
as "we broke the spacecraft to stop the attack".

  1. an authenticated ground telecommand is acted on - the normal path still works end to end;
  2. a telecommand with NO trailer is refused, so "authenticated" is a property and not a log line;
  3. EX-X01's attack, byte for byte, is refused - the same octets that worked in EX-S01 against a
     spacecraft with AES-256-GCM on its uplink, because that control was on the link and this one
     is on the request;
  4. a replayed authenticated telecommand is refused, and the counter is inside the MAC so it
     cannot be advanced;
  5. the vulnerable half of the pair still falls to the attack, which is what makes 3 a measurement
     of the flag rather than of the scenario.


Four, and the first two exist so the third cannot be read as "the crypto is broken":

  1. the uplink really is authenticated - COMM confirms AES-256-GCM against the published NIST
     vector on the target, and acts on a frame this host signed;
  2. a plain frame on that same link is refused, so "authenticated" is not decoration;
  3. EX-X01's attack, byte for byte, still switches the rail off - the crosslink carries CSP and
     SDLS is a transfer-frame protocol, so the packet did not fail a check, it never met one;
  4. EX-X01's mitigation still fixes it, unchanged - the weak control on the uncovered path is
     exactly as necessary after the strong control went in on the covered one.

The fourth is the one that keeps the exercise honest. Without it this file would read as an
argument against deploying SDLS, which is not the lesson and would be a bad one.
"""
import os
import socket
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.identity import GROUND_STATIONS, spacecraft       # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                    # noqa: E402
from cuberange.paths import out_dir                              # noqa: E402
from cuberange.ports import (crosslink_injector, link as link_port,   # noqa: E402
                             monitor as monitor_port)
from cuberange.proto import pus_auth, sdls                       # noqa: E402
from cuberange.proto.csp import encode_packet                    # noqa: E402
from cuberange.proto.frame import encode_tc_frame, wrap          # noqa: E402
from cuberange.proto.pus import PusTc                            # noqa: E402
from cuberange.proto.spacepacket import PacketType, SpacePacket  # noqa: E402
from cuberange.renode.monitor import Monitor                     # noqa: E402
from cuberange.renode.powerdomain import PowerDomain             # noqa: E402
from cuberange.renode.profile import profile_args                # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor         # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = Path(__file__).resolve().parent / "scenario.resc"
VULN = OUT / "build-obc-s02-vuln" / "zephyr" / "zephyr.elf"
HARD = OUT / "build-obc-s02-hard" / "zephyr" / "zephyr.elf"
COMM = OUT / "build-comm-s01-sat0" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))

VICTIM, PEER = spacecraft(0), spacecraft(1)
CSP_PORT_PUS, SPORT = 10, 20

pytestmark = pytest.mark.skipif(
    not COMM.exists() or not VULN.exists() or not HARD.exists(),
    reason=f"build them: make firmware-s01 firmware-s02 ({COMM})")


def _pus(service: int, subtype: int, source_id: int, app_data: bytes = b"") -> bytes:
    tc = PusTc(service=service, subtype=subtype, source_id=source_id, app_data=app_data)
    return SpacePacket(apid=VICTIM.apid, ptype=PacketType.TC, sec_hdr=True, seq_count=0,
                       data=tc.encode()).encode()


def _rail_off(source_id: int) -> bytes:
    """PUS 8,1 function 1 - EX-B01's and EX-G02's and EX-X01's command."""
    return _pus(8, 1, source_id, (1).to_bytes(2, "big") + bytes([0]))


class Range:
    def __init__(self, obc_elf: Path):
        self.obc_elf = obc_elf

    def __enter__(self):
        for name in ("s02-sat0-comm.uart", "s02-sat0-obc.uart", "s02-sat0-eps.uart",
                     "s02-sat1-comm.uart"):
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
        self._ctx = self.sup.launch(argv, OUT / "exs02-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("s02-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("s02-sat0-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")

        self.mon = Monitor(port=monitor_port()).connect()
        self.power = PowerDomain(self.mon, comm_elf=str(COMM),
                                 eps_machine="SAT0_EPS", comm_machine="SAT0_COMM")
        self.link = socket.create_connection(("127.0.0.1", link_port(0)), timeout=5)
        return self

    def __exit__(self, *exc):
        for obj, closer in ((getattr(self, "link", None), "close"),
                            (getattr(self, "mon", None), "close")):
            if obj is not None:
                getattr(obj, closer)()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def uplink(self, frame: bytes, settle: float = 4.0) -> None:
        self.link.sendall(wrap(frame))
        time.sleep(settle)

    def from_the_crosslink(self, dst: int, payload: bytes, settle: float = 5.0) -> None:
        sock = socket.create_connection(("127.0.0.1", crosslink_injector()), timeout=5)
        try:
            for f in encode_packet(src=PEER.comm, dst=dst, dport=CSP_PORT_PUS,
                                   sport=SPORT, payload=payload):
                sock.sendall(f"{f.can_id:x} {f.data.hex()}\n".encode())
        finally:
            sock.close()
        time.sleep(settle)


def _uplink_signed(r, packet: bytes, sdls_sn: int = 1, auth_seq: int = 1) -> None:
    """A telecommand the way a ground station now has to send one: signed, then framed, then
    authenticated again at the frame layer. Both, because both are deployed."""
    r.uplink(sdls.encode_tc(pus_auth.sign(packet, key=SDLS_KEY, seq=auth_seq),
                            key=SDLS_KEY, spi=SDLS_SPI, iv=bytes(range(0xA0, 0xAC)),
                            seq_num=sdls_sn, frame_seq=sdls_sn & 0xFF,
                            scid=VICTIM.scid, vcid=0))


def test_an_authenticated_telecommand_still_works():
    """Stated first: everything below is about what this control refuses, not about it refusing."""
    with Range(HARD) as r:
        _uplink_signed(r, _pus(17, 1, GROUND_STATIONS["primary"]))
        obc = r.console("s02-sat0-obc.uart")
        assert "PUS 17,1 from source" in obc, obc
        assert "PUS 17,2 report sent" in obc, (
            "the OBC verified it and did not answer\n" + obc)
        assert "REJECTED an unauthenticated" not in obc, obc


def test_a_telecommand_with_no_trailer_is_refused():
    with Range(HARD) as r:
        r.uplink(sdls.encode_tc(_pus(17, 1, GROUND_STATIONS["primary"]), key=SDLS_KEY,
                                spi=SDLS_SPI, iv=bytes(range(0xA0, 0xAC)), seq_num=1,
                                frame_seq=0, scid=VICTIM.scid, vcid=0))
        obc = r.console("s02-sat0-obc.uart")
        assert "REJECTED an unauthenticated telecommand" in obc, (
            "a telecommand with no MAC of its own reached a handler\n" + obc)
        assert "PUS 17,1 from source" not in obc, obc


def test_the_crosslink_attack_is_refused():
    """EX-X01's attack, byte for byte. It worked in EX-S01 against AES-256-GCM on the uplink."""
    with Range(HARD) as r:
        assert r.power.read_rail() is True
        r.from_the_crosslink(VICTIM.obc, _rail_off(GROUND_STATIONS["primary"]))
        obc = r.console("s02-sat0-obc.uart")
        assert f"REJECTED an unauthenticated telecommand from node {PEER.comm}" in obc, obc
        assert "PUS 8 executed" not in obc, obc
        assert r.power.read_rail() is True, (
            "the rail went off: the request-level MAC did not stop it\n" + obc)


def test_a_replayed_authenticated_telecommand_is_refused():
    """The counter is inside the MAC, so it cannot be advanced without the key."""
    with Range(HARD) as r:
        packet = pus_auth.sign(_pus(17, 1, GROUND_STATIONS["primary"]), key=SDLS_KEY, seq=9)
        for sdls_sn in (1, 2):                 # a new frame each time; the same telecommand inside
            r.uplink(sdls.encode_tc(packet, key=SDLS_KEY, spi=SDLS_SPI,
                                    iv=bytes(range(0xA0, 0xAC)), seq_num=sdls_sn,
                                    frame_seq=sdls_sn, scid=VICTIM.scid, vcid=0))
        obc = r.console("s02-sat0-obc.uart")
        assert "REPLAY - source" in obc, (
            "the same signed telecommand was accepted twice\n" + obc)
        assert obc.count("PUS 17,2 report sent") == 1, obc


def test_the_vulnerable_half_still_falls_to_it():
    """Which is what makes the test above a measurement of the flag and not of the scenario."""
    with Range(VULN) as r:
        assert r.power.read_rail() is True
        r.from_the_crosslink(VICTIM.obc, _rail_off(GROUND_STATIONS["primary"]))
        obc = r.console("s02-sat0-obc.uart")
        assert "PUS 8 executed" in obc, obc
        assert r.power.read_rail() is False, obc
