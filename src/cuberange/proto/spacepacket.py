"""CCSDS Space Packet Protocol, 133.0-B-2. Primary header only; the secondary header is PUS
and lives in cuberange.proto.pus."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

PRIMARY_HEADER_LEN = 6
SEQ_FLAGS_UNSEGMENTED = 0b11


class PacketType(IntEnum):
    TM = 0
    TC = 1


@dataclass(frozen=True)
class SpacePacket:
    apid: int
    ptype: PacketType
    sec_hdr: bool
    seq_count: int
    data: bytes

    def encode(self) -> bytes:
        if not 0 <= self.apid <= 0x7FF:
            raise ValueError(f"APID {self.apid} does not fit in 11 bits")
        if not 0 <= self.seq_count <= 0x3FFF:
            raise ValueError(f"sequence count {self.seq_count} does not fit in 14 bits")
        if len(self.data) == 0:
            raise ValueError("packet data field must contain at least one octet")

        word0 = (int(self.ptype) << 12) | (int(self.sec_hdr) << 11) | self.apid
        word1 = (SEQ_FLAGS_UNSEGMENTED << 14) | self.seq_count
        word2 = len(self.data) - 1
        return (word0.to_bytes(2, "big") + word1.to_bytes(2, "big")
                + word2.to_bytes(2, "big") + self.data)

    @staticmethod
    def decode(raw: bytes) -> "SpacePacket":
        if len(raw) < PRIMARY_HEADER_LEN:
            raise ValueError(f"space packet shorter than a primary header: {len(raw)} octets")
        word0 = int.from_bytes(raw[0:2], "big")
        word1 = int.from_bytes(raw[2:4], "big")
        data_len = int.from_bytes(raw[4:6], "big") + 1
        body = raw[PRIMARY_HEADER_LEN:]
        if len(body) != data_len:
            raise ValueError(
                f"declared data length {data_len} but {len(body)} octets follow the header")
        return SpacePacket(
            apid=word0 & 0x7FF,
            ptype=PacketType((word0 >> 12) & 0x1),
            sec_hdr=bool((word0 >> 11) & 0x1),
            seq_count=word1 & 0x3FFF,
            data=body,
        )
