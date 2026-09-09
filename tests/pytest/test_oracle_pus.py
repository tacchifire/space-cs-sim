"""PUS-C secondary headers, checked against spacepackets rather than against ourselves.

ASSURANCE.md used to say "the codecs are checked against independent implementations", full stop.
It was true of the Space Packet header, the FECF CRC and CSP, and false of PUS and the transfer
frames - those were checked only against this project's own second implementation, which section
9.3 of the design opens by saying is not evidence. `tools/gen_golden.py` could already generate
these vectors; nobody had committed them and no test read them.

PUS is the layer that most needs an outside opinion. The ECSS standard itself requires registration
and was not obtainable, so `pus.py`'s own docstring says its layout rests on two implementations
agreeing rather than on the primary source. That makes spacepackets the closest thing to an
authority available here, and a transposed field - the TM message-type counter and the destination
ID are adjacent 16-bit values - would round-trip through our own codec perfectly.

The oracle's vectors are complete Space Packets: primary header, secondary header, source data and
a packet error control field. This project's codec produces the secondary header and the data, and
does not implement PEC. The comparison is therefore over the bytes both actually claim to produce,
and the test asserts the slice boundaries rather than trusting them.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.proto.pus import (PUS_VERSION, PusTc, PusTm,          # noqa: E402
                                 TC_SEC_HDR_LEN, TM_SEC_HDR_LEN)
from cuberange.proto.spacepacket import SpacePacket                  # noqa: E402

GOLDEN = REPO / "tests" / "golden" / "pus.json"
SP_HEADER_LEN = 6
PEC_LEN = 2


@pytest.fixture(scope="module")
def golden() -> dict:
    if not GOLDEN.is_file():
        pytest.fail(
            f"{GOLDEN} is missing. Regenerate it with tools/oracles/build.sh and "
            f"tools/gen_golden.py - a conformance test with no oracle is not a conformance test, "
            f"and skipping here is how the whole layer vanished from `make check` before.")
    return json.loads(GOLDEN.read_text())


def test_the_vectors_come_from_an_independent_implementation(golden):
    """The file must say whose opinion it is, or it is just more of our own output."""
    assert "spacepackets" in golden["oracle"], (
        f"pus.json's oracle is {golden['oracle']!r}; if these vectors were produced by this "
        f"project's own codec they establish nothing")
    assert golden["tc_vectors"] and golden["tm_vectors"]


def test_the_tc_secondary_header_matches_the_oracle(golden):
    for v in golden["tc_vectors"]:
        packet = bytes.fromhex(v["packet"])
        app_data = bytes.fromhex(v["app_data"])
        # The oracle's packet is primary header + secondary header + data + PEC.
        expected = packet[SP_HEADER_LEN:SP_HEADER_LEN + TC_SEC_HDR_LEN]
        assert len(expected) == TC_SEC_HDR_LEN

        ours = PusTc(service=v["service"], subtype=v["subtype"],
                     source_id=v["source_id"], ack=v["ack"], app_data=app_data).encode()
        assert ours[:TC_SEC_HDR_LEN] == expected, (
            f"TC {v['service']},{v['subtype']} secondary header differs from spacepackets:\n"
            f"  ours   {ours[:TC_SEC_HDR_LEN].hex().upper()}\n"
            f"  oracle {expected.hex().upper()}")
        assert ours[TC_SEC_HDR_LEN:] == app_data


def test_the_tm_secondary_header_matches_the_oracle(golden):
    for v in golden["tm_vectors"]:
        packet = bytes.fromhex(v["packet"])
        expected = packet[SP_HEADER_LEN:SP_HEADER_LEN + TM_SEC_HDR_LEN]
        assert len(expected) == TM_SEC_HDR_LEN

        ours = PusTm(service=v["service"], subtype=v["subtype"],
                     msg_counter=v["msg_counter"], dest_id=v["dest_id"],
                     time=bytes.fromhex(v["time_cds"]),
                     app_data=bytes.fromhex(v["source_data"])).encode()
        assert ours[:TM_SEC_HDR_LEN] == expected, (
            f"TM {v['service']},{v['subtype']} secondary header differs from spacepackets:\n"
            f"  ours   {ours[:TM_SEC_HDR_LEN].hex().upper()}\n"
            f"  oracle {expected.hex().upper()}")


def test_the_message_counter_and_destination_id_are_not_transposed(golden):
    """The specific error this oracle exists to catch.

    They are adjacent 16-bit big-endian fields in the TM secondary header, so swapping them
    round-trips through our own encoder and decoder perfectly and shows up nowhere else. A vector
    where the two differ is the only thing that tells them apart.
    """
    distinguishing = [v for v in golden["tm_vectors"] if v["msg_counter"] != v["dest_id"]]
    assert distinguishing, (
        "every TM vector has msg_counter == dest_id, so this file cannot detect a transposition. "
        "Add a vector where they differ to tools/gen_golden.py.")
    for v in distinguishing:
        packet = bytes.fromhex(v["packet"])
        sec = packet[SP_HEADER_LEN:SP_HEADER_LEN + TM_SEC_HDR_LEN]
        assert int.from_bytes(sec[3:5], "big") == v["msg_counter"]
        assert int.from_bytes(sec[5:7], "big") == v["dest_id"]


def test_our_decoder_reads_the_oracle_s_bytes_back(golden):
    """Encoding to the same bytes is half of it; reading theirs is the other half."""
    for v in golden["tc_vectors"]:
        packet = bytes.fromhex(v["packet"])
        body = packet[SP_HEADER_LEN:len(packet) - PEC_LEN]
        tc = PusTc.decode(body)
        assert (tc.service, tc.subtype, tc.source_id, tc.ack) == (
            v["service"], v["subtype"], v["source_id"], v["ack"])
        assert tc.app_data == bytes.fromhex(v["app_data"])

    for v in golden["tm_vectors"]:
        packet = bytes.fromhex(v["packet"])
        body = packet[SP_HEADER_LEN:len(packet) - PEC_LEN]
        tm = PusTm.decode(body, time_len=len(bytes.fromhex(v["time_cds"])))
        assert (tm.service, tm.subtype, tm.msg_counter, tm.dest_id) == (
            v["service"], v["subtype"], v["msg_counter"], v["dest_id"])
        assert tm.time == bytes.fromhex(v["time_cds"])
        assert tm.app_data == bytes.fromhex(v["source_data"])


def test_the_primary_headers_agree_too(golden):
    """The oracle's packet carries a Space Packet header; ours must read the same one.

    Decoded WHOLE, with the packet error control field still attached. The primary header's data
    length counts the PEC - CCSDS 133.0-B-2 4.1.3.5.3 makes it the octet count of the entire packet
    data field minus one - so stripping the PEC first and then decoding fails the length check.
    That is our decoder being right and this test having been wrong.
    """
    for v in golden["tc_vectors"]:
        packet = bytes.fromhex(v["packet"])
        decoded = SpacePacket.decode(packet)
        assert decoded.apid == v["apid"]
        assert decoded.seq_count == v["seq"]
        # And the declared length really does include the two PEC octets.
        assert len(decoded.data) == len(packet) - SP_HEADER_LEN
        assert decoded.data.endswith(packet[-PEC_LEN:])


def test_the_pus_version_is_the_one_the_oracle_used(golden):
    """PUS-C is version 2. A vector built with version 1 would silently rewrite our expectations."""
    for v in golden["tc_vectors"]:
        sec = bytes.fromhex(v["packet"])[SP_HEADER_LEN:]
        assert sec[0] >> 4 == PUS_VERSION
