"""EX-S01: the strongest control in the range is still attached to a path.

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
from cuberange.proto import sdls                                 # noqa: E402
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
VULN = OUT / "build-obc-x01-vuln" / "zephyr" / "zephyr.elf"
HARD = OUT / "build-obc-x01-hard" / "zephyr" / "zephyr.elf"
COMM = OUT / "build-comm-s01-sat0" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))

VICTIM, PEER = spacecraft(0), spacecraft(1)
CSP_PORT_PUS, SPORT = 10, 20

pytestmark = pytest.mark.skipif(
    not COMM.exists() or not VULN.exists() or not HARD.exists(),
    reason=f"build them: make firmware-s01 firmware-x01 ({COMM})")


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
        for name in ("s01-sat0-comm.uart", "s01-sat0-obc.uart", "s01-sat0-eps.uart",
                     "s01-sat1-comm.uart"):
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
        self._ctx = self.sup.launch(argv, OUT / "exs01-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("s01-sat0-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("s01-sat0-obc.uart")
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


def test_the_uplink_is_really_authenticated():
    """Stated first, because everything below is about what that does NOT cover."""
    with Range(VULN) as r:
        assert "SDLS self-test passed" in r.console("s01-sat0-comm.uart")
        r.uplink(sdls.encode_tc(_pus(17, 1, GROUND_STATIONS["primary"]), key=SDLS_KEY,
                                spi=SDLS_SPI, iv=bytes(range(0xA0, 0xAC)), seq_num=1,
                                frame_seq=0, scid=VICTIM.scid, vcid=0))
        comm, obc = r.console("s01-sat0-comm.uart"), r.console("s01-sat0-obc.uart")
        assert f"authenticated frame, SPI {SDLS_SPI}" in comm, comm
        assert "PUS 17,2 report sent" in obc, obc


def test_an_unauthenticated_uplink_frame_is_refused():
    """So that "authenticated" is a property and not a log line."""
    with Range(VULN) as r:
        r.uplink(encode_tc_frame(_pus(17, 1, GROUND_STATIONS["primary"]), seq=0,
                                 scid=VICTIM.scid, vcid=0))
        comm, obc = r.console("s01-sat0-comm.uart"), r.console("s01-sat0-obc.uart")
        assert "did not authenticate" in comm, comm
        assert "PUS 17,1 from source" not in obc, obc


def test_the_crosslink_attack_is_unaffected_by_the_uplink_mac():
    """EX-X01's attack, byte for byte, against a spacecraft with cryptography on its uplink."""
    with Range(VULN) as r:
        assert r.power.read_rail() is True
        r.from_the_crosslink(VICTIM.obc, _rail_off(GROUND_STATIONS["primary"]))
        comm, obc = r.console("s01-sat0-comm.uart"), r.console("s01-sat0-obc.uart")
        #: The packet never reached the MAC check, so COMM has nothing to say about it at all.
        #: That silence is the finding: it did not fail a check, it never met one.
        assert "did not authenticate" not in comm, (
            "the crosslink packet met the uplink's MAC check, which would mean SDLS covers a "
            "path this test says it does not\n" + comm)
        assert f"from source {GROUND_STATIONS['primary']}" in obc, obc
        assert "PUS 8 executed" in obc, obc
        assert r.power.read_rail() is False, (
            "the rail is still on: the attack did not land\n" + obc)


def test_ex_x01s_mitigation_still_fixes_it():
    """Without this, the file reads as an argument against deploying SDLS. It is not one."""
    with Range(HARD) as r:
        assert r.power.read_rail() is True
        r.from_the_crosslink(VICTIM.obc, _rail_off(GROUND_STATIONS["primary"]))
        obc = r.console("s01-sat0-obc.uart")
        assert "arrived from node" in obc, obc
        assert "PUS 8 executed" not in obc, obc
        assert r.power.read_rail() is True, obc

        #: And the authenticated uplink still works on the mitigated build - a build that closed
        #: the crosslink by closing everything would pass every test above.
        r.uplink(sdls.encode_tc(_pus(17, 1, GROUND_STATIONS["primary"]), key=SDLS_KEY,
                                spi=SDLS_SPI, iv=bytes(range(0xA0, 0xAC)), seq_num=1,
                                frame_seq=0, scid=VICTIM.scid, vcid=0))
        obc = r.console("s01-sat0-obc.uart")
        assert "PUS 17,2 report sent" in obc, (
            "the mitigated build stopped answering its own authenticated ground station\n" + obc)
