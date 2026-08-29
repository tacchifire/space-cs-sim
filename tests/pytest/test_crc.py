"""FECF CRC, checked against the CCSDS text and an independent implementation.

CCSDS 132.0-B-3 section 4.1.6.2.2 gives G(X) = X^16 + X^12 + X^5 + 1 and notes that the
X^(n-16).L(X) term "has the effect of presetting the shift register to all '1' state". That is
poly 0x1021, init 0xFFFF, no reflection, xorout 0 - i.e. CRC-16/IBM-3740, NOT the XMODEM or
KERMIT variants that also get called "CCITT".
"""
import pytest

from cuberange.proto.crc import crc16_ccsds

# Catalogue check value for CRC-16/IBM-3740. Confirmed against crcmod below; an earlier draft
# of this test asserted 0xE5CC, which belongs to no variant here - the oracle caught it.
CHECK_VECTOR = (b"123456789", 0x29B1)


def test_catalogue_check_vector():
    data, expected = CHECK_VECTOR
    assert crc16_ccsds(data) == expected


def test_empty_input_is_the_init_value():
    assert crc16_ccsds(b"") == 0xFFFF


def test_residue_is_zero_when_the_crc_is_included():
    """Appending the CRC big-endian and re-running must yield 0. This is the property a receiver
    uses, so if it does not hold the codec is wrong even if the forward direction looks right."""
    frame = bytes(range(32))
    fecf = crc16_ccsds(frame)
    assert crc16_ccsds(frame + fecf.to_bytes(2, "big")) == 0x0000


def test_differs_from_the_variants_it_is_often_confused_with():
    """Guards against silently switching to XMODEM (init 0) or KERMIT (reflected)."""
    assert crc16_ccsds(b"123456789") != 0x31C3   # XMODEM
    assert crc16_ccsds(b"123456789") != 0x2189   # KERMIT


def test_agrees_with_an_independent_implementation():
    """`crcmod` is a third-party implementation with its own parameter tables. If our hand-rolled
    loop and crcmod agree on random inputs, a transcription error in either is unlikely."""
    crcmod = pytest.importorskip("crcmod")
    reference = crcmod.mkCrcFun(0x11021, initCrc=0xFFFF, rev=False, xorOut=0x0000)
    for n in (0, 1, 2, 7, 8, 63, 255):
        payload = bytes((i * 7 + 3) & 0xFF for i in range(n))
        assert crc16_ccsds(payload) == reference(payload), f"mismatch at length {n}"
