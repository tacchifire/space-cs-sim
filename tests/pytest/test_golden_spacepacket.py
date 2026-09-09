"""The Space Packet primary header, checked against committed vectors.

`test_oracle_spacepacket.py` already compares this codec with spacepackets - but through
`pytest.importorskip`, so on a host without that library the whole comparison disappears and the
run still reports success. `make check` patches the symptom by asserting the import first; the
committed vectors remove the condition entirely, and they carry a THIRD opinion the live comparison
does not: ccsdspy decoded every one of them, and a hand decode from the CCSDS 133.0-B-2 field table
produced the same fields.

tests/golden/space_packet.json was in the tree, generated, and read by nothing.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.proto.spacepacket import PacketType, SpacePacket  # noqa: E402

GOLDEN = REPO / "tests" / "golden" / "space_packet.json"
HEADER_LEN = 6


@pytest.fixture(scope="module")
def golden() -> dict:
    if not GOLDEN.is_file():
        pytest.fail(f"{GOLDEN} is missing. Regenerate with tools/gen_golden.py.")
    return json.loads(GOLDEN.read_text())


def test_more_than_one_implementation_produced_these(golden):
    oracles = golden["oracles"]
    assert len(oracles) >= 2, f"only {len(oracles)} oracles: {oracles}"
    assert any("spacepackets" in o for o in oracles)
    assert any("ccsdspy" in o for o in oracles), (
        "ccsdspy is the second decoder; without it these vectors rest on one library")


def test_our_encoder_reproduces_every_header(golden):
    for v in golden["vectors"]:
        expected = bytes.fromhex(v["hdr"])
        assert len(expected) == HEADER_LEN

        # data_len_field is C from CCSDS 133.0-B-2 4.1.3.5.3: octets in the packet data field,
        # minus one. Our encoder takes the data itself, so the payload is one octet longer.
        payload = bytes(v["data_len_field"] + 1)
        ours = SpacePacket(
            apid=v["apid"],
            ptype=PacketType.TC if v["packet_type"] else PacketType.TM,
            sec_hdr=bool(v["sec_hdr_flag"]),
            seq_count=v["seq_count"],
            data=payload).encode()

        if v["seq_flags"] != 3:
            # Our codec always emits UNSEGMENTED (0b11); a vector with any other value is testing
            # a segmentation feature this project does not implement, so compare everything but
            # those two bits rather than pretending to support it.
            mask = bytes([0xFF, 0xFF, 0x3F, 0xFF, 0xFF, 0xFF])
            got = bytes(a & m for a, m in zip(ours[:HEADER_LEN], mask))
            want = bytes(b & m for b, m in zip(expected, mask))
            assert got == want, (
                f"APID 0x{v['apid']:03X} seq {v['seq_count']} differs outside the segmentation "
                f"flags:\n  ours   {ours[:HEADER_LEN].hex().upper()}\n"
                f"  oracle {expected.hex().upper()}")
            continue

        assert ours[:HEADER_LEN] == expected, (
            f"APID 0x{v['apid']:03X} seq {v['seq_count']} header differs:\n"
            f"  ours   {ours[:HEADER_LEN].hex().upper()}\n  oracle {expected.hex().upper()}")


def test_our_decoder_reads_every_header(golden):
    for v in golden["vectors"]:
        packet = bytes.fromhex(v["hdr"]) + bytes(v["data_len_field"] + 1)
        decoded = SpacePacket.decode(packet)
        assert decoded.apid == v["apid"]
        assert decoded.seq_count == v["seq_count"]
        assert int(decoded.ptype) == v["packet_type"]
        assert int(decoded.sec_hdr) == v["sec_hdr_flag"]
        assert len(decoded.data) == v["data_len_field"] + 1


def test_the_total_length_convention_is_the_standard_s(golden):
    """The one field that is a convention rather than a layout, and the easiest to get wrong."""
    assert "minus 1" in golden["length_convention"] or "- 1" in golden["length_convention"]
    for v in golden["vectors"]:
        assert v["total_packet_octets"] == HEADER_LEN + v["data_len_field"] + 1


def test_the_extremes_are_covered(golden):
    """A vector set that never exercises a field's limits cannot catch a width error."""
    apids = {v["apid"] for v in golden["vectors"]}
    seqs = {v["seq_count"] for v in golden["vectors"]}
    assert 0x7FF in apids, "no vector uses the maximum 11-bit APID"
    assert 0x3FFF in seqs, "no vector uses the maximum 14-bit sequence count"
    assert 0 in apids and 0 in seqs
