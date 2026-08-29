"""CCSDS 133.0-B-2 primary header.

Layout, 6 octets:
  octet 0-1 : version(3) | type(1) | sec-hdr-flag(1) | APID(11)
  octet 2-3 : sequence-flags(2) | sequence-count(14)
  octet 4-5 : packet data length

s4.1.3.5.3: "C = (Total Number of Octets in the Packet Data Field) - 1". The off-by-one here is
the single most common CCSDS implementation bug, so it gets its own test.
"""
import pytest

from cuberange.proto.spacepacket import PacketType, SpacePacket


def test_encodes_the_documented_bit_layout():
    pkt = SpacePacket(apid=0x123, ptype=PacketType.TC, sec_hdr=True,
                      seq_count=5, data=b"\xAA\xBB\xCC")
    raw = pkt.encode()
    #  version 0, type 1 (TC), sec-hdr 1, APID 0x123 -> 0b000_1_1_00100100011 = 0x1923
    #  seq flags 0b11 (unsegmented), count 5        -> 0xC005
    #  data length = 3 - 1 = 2                      -> 0x0002
    assert raw[:6] == bytes.fromhex("1923C0050002")
    assert raw[6:] == b"\xAA\xBB\xCC"


def test_data_length_field_is_octets_minus_one():
    for n in (1, 2, 17, 255):
        raw = SpacePacket(apid=1, ptype=PacketType.TM, sec_hdr=False,
                          seq_count=0, data=bytes(n)).encode()
        assert int.from_bytes(raw[4:6], "big") == n - 1


def test_rejects_an_empty_data_field():
    """A zero-length data field cannot be represented: C would have to be -1."""
    with pytest.raises(ValueError):
        SpacePacket(apid=1, ptype=PacketType.TM, sec_hdr=False, seq_count=0, data=b"").encode()


def test_round_trip():
    original = SpacePacket(apid=0x7FF, ptype=PacketType.TC, sec_hdr=True,
                           seq_count=0x3FFF, data=bytes(range(40)))
    assert SpacePacket.decode(original.encode()) == original


def test_decode_rejects_a_truncated_packet():
    good = SpacePacket(apid=1, ptype=PacketType.TC, sec_hdr=True,
                       seq_count=0, data=b"\x01\x02\x03").encode()
    with pytest.raises(ValueError):
        SpacePacket.decode(good[:-1])
