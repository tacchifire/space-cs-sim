"""Electronic Cats FlatSat primary header, 4-byte MET, and packet CRC.

Profile reference: ElectronicCats/flatsat-ground-station at
3a2d7b9500520015232cd9d6fc7b0d1916ee866c, modules/core/ccsds.py.
This is not CubeRange's PUS/transfer-frame format or Pwnsat's AA55 USB framing.
"""
from __future__ import annotations

from ..proto.crc import crc16_ccsds
from ..proto.spacepacket import PacketType, SpacePacket

MAX_PACKET_SIZE = 237
PING_APID = 0x020
PING_OPCODE = 0x10


def build_ping(seq_count: int = 1, timestamp: int = 0) -> bytes:
    """Build a plaintext PING packet for inspection only; never transmits."""
    if not 0 <= timestamp <= 0xFFFFFFFF:
        raise ValueError("timestamp must fit in 32 bits")
    data = timestamp.to_bytes(4, "big") + bytes([PING_OPCODE])
    # The CRC is part of the data field and is covered by the declared packet length.
    raw = SpacePacket(apid=PING_APID, ptype=PacketType.TC, sec_hdr=True,
                      seq_count=seq_count, data=data + b"\x00\x00").encode()[:-2]
    return raw + crc16_ccsds(raw).to_bytes(2, "big")


def decode_packet(raw: bytes) -> dict:
    """Validate a complete profile packet; keep a bad CRC visible as crc_valid=False.

    Payload remains raw: no encryption level or sensor layout is guessed.
    """
    if not 8 <= len(raw) <= MAX_PACKET_SIZE:
        raise ValueError(f"FlatSat packet size must be 8..{MAX_PACKET_SIZE} bytes")
    if raw[0] >> 5:
        raise ValueError("unsupported CCSDS packet version")
    packet = SpacePacket.decode(raw)
    min_data = 6 if packet.sec_hdr else 2
    if len(packet.data) < min_data:
        raise ValueError("truncated secondary header or packet CRC")
    offset = 4 if packet.sec_hdr else 0
    return {
        "apid": packet.apid, "packet_type": packet.ptype.name,
        "seq_count": packet.seq_count, "seq_flags": raw[2] >> 6,
        "sec_hdr": packet.sec_hdr,
        "timestamp": int.from_bytes(packet.data[:4], "big") if packet.sec_hdr else None,
        "payload_hex": packet.data[offset:-2].hex(),
        "crc_valid": crc16_ccsds(raw[:-2]) == int.from_bytes(raw[-2:], "big"),
        "raw_hex": raw.hex(),
    }
