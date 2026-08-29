"""TC/TM transfer frames (CCSDS 232.0-B-4 / 132.0-B-3, minimal profiles) plus the CubeRange lab
framing that delimits them on a byte-stream UART.

The lab framing is ASM + u16 big-endian length + frame. It is NOT a CCSDS CLTU: CLTU is defined in
231.0-B-4 and starts EB90. Calling this "CCSDS channel conformance" would be false.
"""
import pytest

from cuberange.proto.crc import crc16_ccsds
from cuberange.proto.frame import (ASM, Deframer, decode_tc_frame, decode_tm_frame,
                                   encode_tc_frame, encode_tm_frame, wrap)


def test_tc_frame_carries_its_payload_and_a_valid_fecf():
    payload = b"\x01\x02\x03\x04"
    frame = encode_tc_frame(payload, seq=7)
    assert crc16_ccsds(frame) == 0x0000, "FECF residue must be zero over the whole frame"
    seq, got = decode_tc_frame(frame)
    assert (seq, got) == (7, payload)


def test_tc_frame_length_field_is_total_octets_minus_one():
    payload = bytes(20)
    frame = encode_tc_frame(payload, seq=0)
    declared = ((frame[2] & 0x03) << 8) | frame[3]
    assert declared == len(frame) - 1


def test_tm_frame_round_trip():
    payload = bytes(range(30))
    frame = encode_tm_frame(payload, mc_count=3, vc_count=4)
    assert crc16_ccsds(frame) == 0x0000
    assert decode_tm_frame(frame) == (3, 4, payload)


def test_decode_rejects_a_corrupted_frame():
    frame = bytearray(encode_tc_frame(b"\xAA\xBB", seq=1))
    frame[6] ^= 0xFF
    with pytest.raises(ValueError, match="FECF"):
        decode_tc_frame(bytes(frame))


def test_deframer_reassembles_across_arbitrary_chunk_boundaries():
    frames = [encode_tc_frame(bytes([i]) * (i + 1), seq=i) for i in range(4)]
    stream = b"".join(wrap(f) for f in frames)

    for chunk_size in (1, 3, 7, len(stream)):
        d = Deframer()
        out = []
        for i in range(0, len(stream), chunk_size):
            out.extend(d.feed(stream[i:i + chunk_size]))
        assert out == frames, f"failed at chunk size {chunk_size}"


def test_deframer_resynchronises_after_leading_garbage():
    """A ground station may attach mid-stream, or the link may have dropped bytes. The deframer
    must find the next ASM rather than give up."""
    good = wrap(encode_tc_frame(b"\x99", seq=2))
    d = Deframer()
    assert d.feed(b"garbage-before-the-marker" + good) == [encode_tc_frame(b"\x99", seq=2)]


def test_deframer_survives_a_byte_that_looks_like_the_start_of_an_asm():
    d = Deframer()
    good = wrap(encode_tc_frame(b"\x55", seq=3))
    assert d.feed(ASM[:3] + good) == [encode_tc_frame(b"\x55", seq=3)]
