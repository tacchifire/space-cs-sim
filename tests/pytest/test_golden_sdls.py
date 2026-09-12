"""The SDLS security header, against two oracles that answer two different questions.

WHERE the fields are is NASA CryptoLib's answer: `tools/oracles/sdls_oracle.c` hands it frames it
did not produce and prints the offsets its own arithmetic implies. That matters because
CryptoLib's own tests carry a segment header, which puts the SPI at 8, and this range emits none,
which should put it at 5 - the single likeliest place for a hand-read layout to be quietly wrong.

WHAT the MAC is has two answers that are not this codec's: libsodium through PyNaCl, which is a
different AES-GCM implementation from the OpenSSL one `cryptography` wraps, and the published NIST
vectors, which are not an implementation at all.

The vectors also carry one CryptoLib DECLINED, and that is deliberate. Its in-memory SA store
holds 64 associations and refuses the first and last, so it can speak about SPIs 1..62; the SDLS
SPI field is sixteen bits. A vector at 0xFFFE exercises the codec and must not be allowed to look
like agreement, so the generator records the refusal and this file asserts the refusal is there.
"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((REPO / "tests" / "golden" / "sdls.json").read_text())

from cuberange.proto import sdls   # noqa: E402

KEY = bytes(range(32))      #: the key tools/gen_golden.py used; see its SDLS section


def _vectors():
    return GOLDEN["vectors"]


@pytest.mark.parametrize("v", _vectors(), ids=lambda v: f"spi{v['spi']}-scid{v['scid']:03X}")
def test_the_codec_reproduces_the_committed_frame(v):
    """Byte for byte, including a MAC that came out of libsodium rather than out of this codec."""
    frame = sdls.encode_tc(bytes.fromhex(v["payload"]), key=KEY, spi=v["spi"],
                           iv=bytes.fromhex(v["iv"]), seq_num=v["seq_num"],
                           frame_seq=v["frame_seq"], scid=v["scid"], vcid=v["vcid"])
    assert frame.hex() == v["frame"].lower(), (
        f"the codec and the committed vector disagree.\n"
        f"  codec:  {frame.hex()}\n  vector: {v['frame'].lower()}")


@pytest.mark.parametrize("v", _vectors(), ids=lambda v: f"spi{v['spi']}-scid{v['scid']:03X}")
def test_the_codec_verifies_and_takes_apart_the_committed_frame(v):
    got = sdls.decode_tc(bytes.fromhex(v["frame"]), key=KEY)
    assert got.spi == v["spi"]
    assert got.iv.hex() == v["iv"].lower()
    assert got.seq_num == v["seq_num"]
    assert got.payload.hex() == v["payload"].lower()
    assert got.scid == v["scid"] and got.vcid == v["vcid"]
    assert got.frame_seq == v["frame_seq"]


@pytest.mark.parametrize("v", _vectors(), ids=lambda v: f"spi{v['spi']}-scid{v['scid']:03X}")
def test_cryptolib_puts_the_fields_where_this_codec_does(v):
    """The offsets, from an implementation that has never seen this repository."""
    c = v["cryptolib"]
    if "declined" in c:
        assert not (1 <= v["spi"] <= 62), (
            f"CryptoLib declined SPI {v['spi']}, which is inside the range it holds - that is a "
            f"disagreement, not a limit: {c['declined']}")
        return
    assert c["spi"] == v["spi"]
    assert c["spi_at"] == sdls.SPI_AT
    assert c["iv_at"] == sdls.IV_AT and c["iv_len"] == sdls.IV_LEN
    assert c["sn_at"] == sdls.SN_AT and c["sn_len"] == sdls.SN_LEN
    assert c["pdu_at"] == sdls.PDU_AT
    assert c["iv"].lower() == v["iv"].lower()
    assert c["sn"].lower() == v["seq_num"].to_bytes(sdls.SN_LEN, "big").hex()
    assert c["pdu_len"] == len(bytes.fromhex(v["payload"]))
    assert c["mac_len"] == sdls.MAC_LEN
    #: Two derivations of the same offset - forward from CryptoLib's own tc_pdu_len, and backward
    #: from the frame end. They agree, or the payload length and the MAC position disagree and one
    #: of them is wrong.
    assert c["mac_at"] == c["mac_at_from_frame_end"]


def test_at_least_one_vector_is_outside_what_cryptolib_can_hold():
    """A golden set that only contains cases the oracle likes has stopped testing the codec.

    The SDLS SPI is sixteen bits and CryptoLib's store is 64 associations deep. Without a vector
    past that, nothing here would notice a codec that silently masked the SPI to six bits.
    """
    declined = [v for v in _vectors() if "declined" in v["cryptolib"]]
    assert declined, "every vector is inside CryptoLib's SPI range; add one past it"
    assert any(v["spi"] > 63 for v in declined)


@pytest.mark.parametrize("case", GOLDEN["nist_aes256_gcm"], ids=lambda c: c["key"][:8])
def test_the_primitive_matches_the_published_nist_vectors(case):
    """AES-256-GCM itself, against numbers nobody in this repository produced."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    tag = AESGCM(bytes.fromhex(case["key"])).encrypt(
        bytes.fromhex(case["iv"]), b"", bytes.fromhex(case["aad"]))
    assert tag.hex() == case["tag"].lower()


def test_a_tampered_payload_is_refused_even_with_the_fecf_repaired():
    """The FECF is arithmetic an attacker can redo. The MAC is not.

    Without repairing the FECF this test would pass against a codec with no MAC at all, because
    the CRC would have caught the flip - which is exactly the confusion SDLS exists to end.
    """
    from cuberange.proto.crc import crc16_ccsds

    v = _vectors()[0]
    frame = bytearray(bytes.fromhex(v["frame"]))
    frame[sdls.PDU_AT] ^= 0x01
    frame[-2:] = crc16_ccsds(bytes(frame[:-2])).to_bytes(2, "big")
    assert crc16_ccsds(bytes(frame)) == 0x0000, "the repaired FECF is not valid; test is inert"
    with pytest.raises(sdls.AuthenticationError, match="MAC"):
        sdls.decode_tc(bytes(frame), key=KEY)


def test_a_frame_authenticated_under_another_key_is_refused():
    v = _vectors()[0]
    with pytest.raises(sdls.AuthenticationError):
        sdls.decode_tc(bytes.fromhex(v["frame"]), key=bytes(32))
