"""ECSS-E-ST-70-41C PUS-C secondary headers, TC and TM.

PUS is an application-layer convention carried inside a CCSDS Space Packet. It does not define
RF modulation, transfer framing, COP-1 or SDLS; keep those concerns out of this module.

Provenance note: the ECSS standard itself requires registration and was not obtainable, so these
layouts rest on two independent implementations agreeing (spacepackets and FSFW) rather than on
the primary source. Do not describe them as verified against the standard.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PUS_VERSION = 2          # PUS-C
TC_SEC_HDR_LEN = 5
TM_SEC_HDR_LEN = 7

SERVICE_TEST = 17
SUBTYPE_CONNECTION_TEST = 1
SUBTYPE_CONNECTION_TEST_REPORT = 2


@dataclass(frozen=True)
class PusTc:
    service: int
    subtype: int
    source_id: int = 0
    ack: int = 0b1111
    app_data: bytes = b""

    def encode(self) -> bytes:
        if not 0 <= self.ack <= 0xF:
            raise ValueError("ack flags must fit in 4 bits")
        return (bytes([(PUS_VERSION << 4) | self.ack, self.service, self.subtype])
                + self.source_id.to_bytes(2, "big") + self.app_data)

    @staticmethod
    def decode(raw: bytes) -> "PusTc":
        if len(raw) < TC_SEC_HDR_LEN:
            raise ValueError(f"PUS TC shorter than a secondary header: {len(raw)} octets")
        version = raw[0] >> 4
        if version != PUS_VERSION:
            raise ValueError(f"unsupported PUS version {version}")
        return PusTc(service=raw[1], subtype=raw[2],
                     source_id=int.from_bytes(raw[3:5], "big"),
                     ack=raw[0] & 0xF, app_data=raw[TC_SEC_HDR_LEN:])


@dataclass(frozen=True)
class PusTm:
    service: int
    subtype: int
    msg_counter: int = 0
    dest_id: int = 0
    time: bytes = b""
    app_data: bytes = field(default=b"")

    def encode(self) -> bytes:
        return (bytes([PUS_VERSION << 4, self.service, self.subtype])
                + self.msg_counter.to_bytes(2, "big")
                + self.dest_id.to_bytes(2, "big")
                + self.time + self.app_data)

    @staticmethod
    def decode(raw: bytes, time_len: int) -> "PusTm":
        need = TM_SEC_HDR_LEN + time_len
        if len(raw) < need:
            raise ValueError(f"PUS TM shorter than header+time: {len(raw)} < {need}")
        version = raw[0] >> 4
        if version != PUS_VERSION:
            raise ValueError(f"unsupported PUS version {version}")
        return PusTm(service=raw[1], subtype=raw[2],
                     msg_counter=int.from_bytes(raw[3:5], "big"),
                     dest_id=int.from_bytes(raw[5:7], "big"),
                     time=raw[TM_SEC_HDR_LEN:need], app_data=raw[need:])
