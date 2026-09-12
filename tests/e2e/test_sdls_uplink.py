"""An authenticated uplink, on the spacecraft, measured rather than asserted.

Four things, and the first one is the one that would otherwise be taken on faith:

  1. AES-256-GCM computes the published NIST tag ON THE TARGET. Renode models registers rather
     than physics and mbedTLS here is software, but "the library built" and "the library gets the
     right answer on this target" are different claims and only one of them is checked by the
     build succeeding. COMM prints the result at boot.
  2. A frame this host authenticated is verified, unwrapped and acted on: the OBC answers the
     PUS 17,1 inside it.
  3. The same frame with one payload octet flipped AND THE FECF REPAIRED is refused. Without
     repairing the FECF this would pass against a spacecraft with no MAC at all, because the CRC
     would have caught it - which is exactly the confusion SDLS exists to end.
  4. A plain, unauthenticated frame - a valid one, correct FECF, the format every other build in
     this range speaks - is refused. An authenticated frame and a plain one are two formats, and
     a build that accepted both would let an attacker choose which to send.

WHY THE STACK SIZES IN sdls.conf ARE NOT DECORATION. This file was written after a COMM that
booted, printed, passed its own crypto self-test, started its link thread, and then never
received one octet. sizeof(mbedtls_gcm_context) is 424 and Zephyr's default main stack is 1024;
the self-test overflowed it and corrupted a kernel object, silently. Four hypotheses were measured
and killed before that one. Test 1 below is what makes the next occurrence visible.
"""
import os
import socket
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.identity import GROUND_STATIONS, spacecraft     # noqa: E402
from cuberange.keys import SDLS_KEY, SDLS_SPI                  # noqa: E402
from cuberange.paths import out_dir                            # noqa: E402
from cuberange.ports import link as link_port, monitor as monitor_port   # noqa: E402
from cuberange.proto import sdls                               # noqa: E402
from cuberange.proto.crc import crc16_ccsds                    # noqa: E402
from cuberange.proto.frame import encode_tc_frame, wrap        # noqa: E402
from cuberange.proto.pus import PusTc                          # noqa: E402
from cuberange.proto.spacepacket import PacketType, SpacePacket  # noqa: E402
from cuberange.renode.profile import profile_args              # noqa: E402
from cuberange.renode.supervisor import RenodeSupervisor       # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = out_dir()
SCENARIO = REPO / "scripts" / "multi-node" / "sdls.resc"
COMM = OUT / "build-comm-sdls" / "zephyr" / "zephyr.elf"
BOOT_TIMEOUT_S = float(os.environ.get("CUBERANGE_BOOT_TIMEOUT_S", "40"))

SAT = spacecraft(0)

pytestmark = pytest.mark.skipif(
    not COMM.exists(), reason=f"build it: make firmware-sdls ({COMM})")


def _pus17() -> bytes:
    """A connection test, which the OBC answers - so acceptance is observable, not inferred."""
    tc = PusTc(service=17, subtype=1, source_id=GROUND_STATIONS["primary"], app_data=b"")
    return SpacePacket(apid=SAT.apid, ptype=PacketType.TC, sec_hdr=True, seq_count=0,
                       data=tc.encode()).encode()


class Range:
    def __enter__(self):
        for name in ("sdls-comm.uart", "sdls-obc.uart", "sdls-eps.uart"):
            (OUT / name).unlink(missing_ok=True)
        self.sup = RenodeSupervisor(cwd=RENODE_DIR, timeout_s=240, rss_ceiling_mb=2048)
        argv = ["./renode", "--disable-xwt", "--plain", "--hide-analyzers",
                "--port", str(monitor_port()),
                *profile_args(),
                "-e", f"$out=@{OUT}",
                "-e", f"include @{SCENARIO}",
                "-e", "start"]
        self._ctx = self.sup.launch(argv, OUT / "sdls-renode.log")
        self.run = self._ctx.__enter__()

        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if "OBC listening" in self.console("sdls-obc.uart"):
                break
            time.sleep(0.2)
        else:
            text = self.console("sdls-obc.uart")
            self.__exit__(None, None, None)
            raise AssertionError(f"OBC never reported ready:\n{text}")
        time.sleep(1.5)
        self.sock = socket.create_connection(("127.0.0.1", link_port(0)), timeout=5)
        return self

    def __exit__(self, *exc):
        sock = getattr(self, "sock", None)
        if sock is not None:
            sock.close()
        self._ctx.__exit__(*exc)

    def console(self, name: str) -> str:
        p = OUT / name
        return p.read_text(errors="replace") if p.exists() else ""

    def send(self, frame: bytes, settle: float = 4.0) -> None:
        self.sock.sendall(wrap(frame))
        time.sleep(settle)


def test_the_target_computes_the_published_nist_tag():
    """Before anything depends on the answer."""
    with Range() as r:
        comm = r.console("sdls-comm.uart")
        assert "SDLS self-test passed" in comm, (
            "COMM did not confirm AES-256-GCM on the target. A FAILED line here means mbedTLS "
            f"got the wrong answer; no line at all means main() did not reach it.\n{comm}")
        assert "SELFTEST FAILED" not in comm, comm


def test_an_authenticated_frame_is_verified_and_acted_on():
    with Range() as r:
        frame = sdls.encode_tc(_pus17(), key=SDLS_KEY, spi=SDLS_SPI,
                               iv=bytes(range(0xA0, 0xAC)), seq_num=1, frame_seq=0,
                               scid=SAT.scid, vcid=0)
        r.send(frame)
        comm, obc = r.console("sdls-comm.uart"), r.console("sdls-obc.uart")
        assert f"authenticated frame, SPI {SDLS_SPI}" in comm, comm
        assert "did not authenticate" not in comm, comm
        assert "PUS 17,1 from source" in obc, obc
        assert "PUS 17,2 report sent" in obc, (
            "the OBC received it and did not answer\n" + obc)


def test_a_tampered_payload_is_refused_with_the_fecf_repaired():
    with Range() as r:
        frame = bytearray(sdls.encode_tc(_pus17(), key=SDLS_KEY, spi=SDLS_SPI,
                                         iv=bytes(range(0xA0, 0xAC)), seq_num=1, frame_seq=0,
                                         scid=SAT.scid, vcid=0))
        frame[sdls.PDU_AT + 1] ^= 0x01                      # a different APID
        frame[-2:] = crc16_ccsds(bytes(frame[:-2])).to_bytes(2, "big")
        assert crc16_ccsds(bytes(frame)) == 0x0000, "the repaired FECF is invalid; test is inert"

        r.send(bytes(frame))
        comm, obc = r.console("sdls-comm.uart"), r.console("sdls-obc.uart")
        assert "did not authenticate" in comm, (
            "a frame with a valid FECF and an invalid MAC was accepted\n" + comm)
        assert "PUS 17,1 from source" not in obc, (
            "the OBC acted on a frame the MAC should have stopped\n" + obc)


def test_a_replayed_authenticated_frame_is_refused():
    """The reason SDLS is worth having over EX-L01's counter.

    EX-L01's mitigation reads the frame sequence number out of the primary header - a field the
    attacker writes, outside anything signed - and its own notes say a recording can be replayed
    with that counter advanced. The SDLS sequence number is inside the authenticated portion, so
    advancing it invalidates the MAC, and the MAC cannot be recomputed without the key.

    The recording here is BYTE-IDENTICAL, which is the whole point: a replay does not need to
    change anything.
    """
    with Range() as r:
        frame = sdls.encode_tc(_pus17(), key=SDLS_KEY, spi=SDLS_SPI,
                               iv=bytes(range(0xA0, 0xAC)), seq_num=5, frame_seq=0,
                               scid=SAT.scid, vcid=0)
        r.send(frame)
        comm = r.console("sdls-comm.uart")
        assert "authenticated frame" in comm, comm
        assert "REPLAY" not in comm, "the first transmission was called a replay\n" + comm

        r.send(frame)                                   # the same octets, again
        comm = r.console("sdls-comm.uart")
        assert "REPLAY - authenticated frame with sequence 5" in comm, (
            "a byte-identical authenticated frame was accepted twice\n" + comm)


def test_the_replay_check_does_not_block_the_next_real_command():
    """A build that rejected everything after the first frame would pass the test above."""
    with Range() as r:
        for sn in (1, 2, 3):
            r.send(sdls.encode_tc(_pus17(), key=SDLS_KEY, spi=SDLS_SPI,
                                  iv=bytes(range(0xA0, 0xAC)), seq_num=sn, frame_seq=sn,
                                  scid=SAT.scid, vcid=0), settle=3.0)
        comm, obc = r.console("sdls-comm.uart"), r.console("sdls-obc.uart")
        assert comm.count("authenticated frame") == 3, (
            f"{comm.count('authenticated frame')} of 3 advancing frames were accepted\n" + comm)
        assert "REPLAY" not in comm, comm
        assert obc.count("PUS 17,1 from source") == 3, obc


def test_a_plain_unauthenticated_frame_is_refused():
    """The format every other build in this range speaks, and this one does not."""
    with Range() as r:
        r.send(encode_tc_frame(_pus17(), seq=0, scid=SAT.scid, vcid=0))
        comm, obc = r.console("sdls-comm.uart"), r.console("sdls-obc.uart")
        assert "did not authenticate" in comm, (
            "an unauthenticated frame reached the OBC on an SDLS build\n" + comm)
        assert "PUS 17,1 from source" not in obc, obc
