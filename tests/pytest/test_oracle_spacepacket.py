"""Independent oracle. Our codec and spacepackets are separate implementations of the same book;
if they agree byte-for-byte, a shared misreading is unlikely. spacepackets is Apache-2.0 and is a
TEST dependency only - it must never be imported by shipped ground-station code."""
import pytest

from cuberange.proto.spacepacket import PacketType, SpacePacket

pytest.importorskip("spacepackets")
from spacepackets.ccsds.spacepacket import (  # noqa: E402
    PacketType as OraclePacketType,
    SpacePacketHeader,
)


@pytest.mark.parametrize("apid,seq,payload_len",
                         [(0x001, 0, 1), (0x123, 5, 3), (0x7FF, 0x3FFF, 64)])
def test_tc_primary_header_matches_the_oracle(apid, seq, payload_len):
    payload = bytes((i * 3) & 0xFF for i in range(payload_len))
    ours = SpacePacket(apid=apid, ptype=PacketType.TC, sec_hdr=True,
                       seq_count=seq, data=payload).encode()
    theirs = SpacePacketHeader(
        packet_type=OraclePacketType.TC, apid=apid, seq_count=seq,
        data_len=payload_len - 1, sec_header_flag=True,
    ).pack()
    assert ours[:6] == theirs


@pytest.mark.parametrize("apid,seq,payload_len", [(0x0A9, 0, 11), (0x0A9, 1, 40)])
def test_tm_primary_header_matches_the_oracle(apid, seq, payload_len):
    payload = bytes(payload_len)
    ours = SpacePacket(apid=apid, ptype=PacketType.TM, sec_hdr=True,
                       seq_count=seq, data=payload).encode()
    theirs = SpacePacketHeader(
        packet_type=OraclePacketType.TM, apid=apid, seq_count=seq,
        data_len=payload_len - 1, sec_header_flag=True,
    ).pack()
    assert ours[:6] == theirs


def test_the_oracle_also_decodes_what_we_produce():
    """Encoding agreement could still hide a shared misreading of the length field. Round-tripping
    through the oracle's decoder checks the other direction too."""
    payload = b"\xDE\xAD\xBE\xEF"
    ours = SpacePacket(apid=0x0A9, ptype=PacketType.TC, sec_hdr=True,
                       seq_count=9, data=payload).encode()
    parsed = SpacePacketHeader.unpack(ours[:6])
    assert parsed.apid == 0x0A9
    assert parsed.seq_count == 9
    assert parsed.data_len == len(payload) - 1
