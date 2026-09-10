"""The TM and TC transfer frame primary headers, against implementations that are not ours.

This is the layer ASSURANCE.md named as unverified. The FECF had four independent opinions and the
Space Packet header had two; the TRANSFER FRAME headers had none, so their field layout rested on
this project's reading of CCSDS 232.0-B-4 and 132.0-B-3 alone. Design section 9.3 opens by saying
why that is not evidence: two implementations that share a misreading agree perfectly and are both
wrong on the wire.

What each half rests on now:

  TC - the octets in tests/golden/transfer_frame.json were packed field by field from the standard
       in tools/gen_golden.py, then handed to NASA CryptoLib, which parsed them with its own code
       and reported the fields it found. CryptoLib is a flight implementation, not a codec written
       for this repository. It also checks `frame length field + 1 == octets`, so the length
       convention is confirmed by something other than us.

  TM - spacepackets packs the header from named fields, which is a second implementation outright.

Neither the generator nor these vectors ever import cuberange. What this file adds is the third
link: our encoder must reproduce those octets exactly.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.proto.frame import (FECF_LEN, TC_HEADER_LEN, TM_HEADER_LEN,  # noqa: E402
                                   decode_tc_frame, decode_tm_frame, encode_tc_frame,
                                   encode_tm_frame, tc_frame_identity, tm_frame_identity)

GOLDEN = REPO / "tests" / "golden" / "transfer_frame.json"


@pytest.fixture(scope="module")
def doc() -> dict:
    if not GOLDEN.is_file():
        pytest.fail(
            f"{GOLDEN} is missing. Rebuild the oracles with tools/oracles/build.sh and regenerate "
            f"with tools/gen_golden.py. Skipping would delete the only outside opinion this layer "
            f"has ever had.")
    return json.loads(GOLDEN.read_text())


def test_the_tc_vectors_name_an_implementation_that_is_not_a_hand_decode(doc):
    """A hand decode alone is still one reading of the standard. CryptoLib is the second opinion."""
    oracles = doc["tc"]["oracles"]
    assert len(oracles) >= 2, oracles
    assert any("CryptoLib" in o for o in oracles), (
        "CryptoLib is the only TC oracle here that is a flight implementation rather than a "
        "reading of the text; without it this layer is back to one opinion")


def test_the_tm_vectors_come_from_a_second_implementation(doc):
    assert any("spacepackets" in o for o in doc["tm"]["oracles"]), doc["tm"]["oracles"]


def test_cryptolib_recovered_every_tc_field_we_encoded(doc):
    """The agreement itself, field by field, rather than a summary flag."""
    for v in doc["tc"]["vectors"]:
        c = v["cryptolib"]
        assert c["scid"] == v["scid"], f"SCID: we meant 0x{v['scid']:03X}, CryptoLib read 0x{c['scid']:03X}"
        assert c["vcid"] == v["vcid"], f"VCID: we meant {v['vcid']}, CryptoLib read {c['vcid']}"
        assert c["fsn"] == v["fsn"], f"frame sequence number: {v['fsn']} vs {c['fsn']}"
        assert c["frame_length_field"] == v["frame_length_field"]
        assert c["tfvn"] == 0 and c["bypass"] == 0 and c["cc"] == 0 and c["spare"] == 0, (
            "a bit outside the fields we set came back non-zero, so the packing overlaps a "
            "neighbouring field")


def test_cryptolib_agrees_the_length_field_is_total_minus_one(doc):
    """The easiest field in the header to get wrong, and the one nothing else would catch.

    An encoder that wrote the payload length, or the total, round-trips against its own decoder
    perfectly and is rejected by every real receiver.
    """
    assert "minus 1" in doc["tc"]["length_convention"]
    for v in doc["tc"]["vectors"]:
        assert v["cryptolib"]["length_field_plus_one_equals_octets"], (
            f"CryptoLib rejected the length of the {v['total_octets']}-octet vector; it checks "
            f"`fl + 1 == len` and disagreed")
        assert v["frame_length_field"] == v["total_octets"] - 1


def test_our_encoder_reproduces_every_tc_frame(doc):
    for v in doc["tc"]["vectors"]:
        ours = encode_tc_frame(bytes.fromhex(v["payload"]), seq=v["fsn"],
                               scid=v["scid"], vcid=v["vcid"])
        assert ours.hex().upper() == v["frame"], (
            f"TC frame for scid=0x{v['scid']:03X} vcid={v['vcid']} fsn={v['fsn']} differs:\n"
            f"  ours   {ours.hex().upper()}\n  oracle {v['frame']}")


def test_our_encoder_reproduces_every_tm_header(doc):
    for v in doc["tm"]["vectors"]:
        ours = encode_tm_frame(bytes.fromhex(v["payload"]), mc_count=v["mc_count"],
                               vc_count=v["vc_count"], scid=v["scid"], vcid=v["vcid"])
        assert ours[:TM_HEADER_LEN].hex().upper() == v["header"], (
            f"TM header for scid=0x{v['scid']:03X} vcid={v['vcid']} differs:\n"
            f"  ours   {ours[:TM_HEADER_LEN].hex().upper()}\n  oracle {v['header']}")
        assert ours.hex().upper() == v["frame"]


def test_our_decoder_reads_the_oracle_s_own_octets(doc):
    """Encoding agreement is half of it. A decoder can agree with our encoder and nothing else."""
    for v in doc["tc"]["vectors"]:
        frame = bytes.fromhex(v["frame"])
        assert tc_frame_identity(frame) == (v["scid"], v["vcid"])
        seq, payload = decode_tc_frame(frame, expect_scid=v["scid"], expect_vcid=v["vcid"])
        assert seq == v["fsn"]
        assert payload.hex().upper() == v["payload"]

    for v in doc["tm"]["vectors"]:
        frame = bytes.fromhex(v["frame"])
        assert tm_frame_identity(frame) == (v["scid"], v["vcid"])
        mc, vc, payload = decode_tm_frame(frame, expect_scid=v["scid"], expect_vcid=v["vcid"])
        assert (mc, vc) == (v["mc_count"], v["vc_count"])
        assert payload.hex().upper() == v["payload"]


def test_the_field_widths_are_exercised_at_their_limits(doc):
    """A vector set that never reaches a field's maximum cannot catch a width error.

    What this does NOT catch, stated because the first version of this docstring claimed it did:
    widening the TM VCID mask from 3 bits to 6 - the obvious copy-paste from the TC header - passes
    every vector here. It has to: the vectors cannot use a VCID above 7, so 0x7 and 0x3F mask
    identically, and a value above 7 never reaches the mask because _check_identity rejects it
    first. The range check is the guard, not the mask, and
    test_an_out_of_range_identity_is_refused_rather_than_masked is where that is tested.
    """
    tc = doc["tc"]["vectors"]
    assert 0x3FF in {v["scid"] for v in tc}, "no TC vector uses the maximum 10-bit SCID"
    assert 63 in {v["vcid"] for v in tc}, "no TC vector uses the maximum 6-bit VCID"
    assert 255 in {v["fsn"] for v in tc}, "no TC vector uses the maximum 8-bit sequence number"
    assert 0 in {v["scid"] for v in tc} and 0 in {v["vcid"] for v in tc}

    tm = doc["tm"]["vectors"]
    assert 0x3FF in {v["scid"] for v in tm}, "no TM vector uses the maximum 10-bit SCID"
    assert 7 in {v["vcid"] for v in tm}, "no TM vector uses the maximum 3-bit VCID"
    assert 255 in {v["mc_count"] for v in tm} and 255 in {v["vc_count"] for v in tm}


def test_the_two_headers_are_not_the_same_length(doc):
    """A guard against the vectors being regenerated from one code path for both."""
    assert doc["tc"]["header_len"] == TC_HEADER_LEN == 5
    assert doc["tm"]["header_len"] == TM_HEADER_LEN == 6
    assert FECF_LEN == 2


def test_an_out_of_range_identity_is_refused_rather_than_masked():
    """The actual guard on the field widths.

    Masking would make encode_tm_frame(vcid=8) emit VCID 0 and a receiver would accept a frame
    addressed to the wrong virtual channel with nothing logged anywhere. The widths differ between
    the two headers - TC VCID is 6 bits, TM VCID is 3 - so each needs its own case.
    """
    for kwargs in ({"scid": 0x400}, {"vcid": 64}):
        with pytest.raises(ValueError):
            encode_tc_frame(b"", seq=0, **kwargs)
    encode_tc_frame(b"", seq=0, scid=0x3FF, vcid=63)          # the maxima must still be allowed

    for kwargs in ({"scid": 0x400}, {"vcid": 8}):
        with pytest.raises(ValueError):
            encode_tm_frame(b"", mc_count=0, vc_count=0, **kwargs)
    encode_tm_frame(b"", mc_count=0, vc_count=0, scid=0x3FF, vcid=7)
