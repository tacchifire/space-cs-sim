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

#: Service 1, request verification (ECSS-E-ST-70-41C 6.1). The spacecraft saying out loud what it
#: did with a telecommand, which is the thing EX-G02 and EX-G03 both end by saying it cannot do:
#: their refusals are printk on a console the ground never sees, so an operator being locked out
#: and an operator being attacked look identical from the downlink, which is to say invisible.
SERVICE_VERIFICATION = 1
SUBTYPE_ACCEPTANCE_SUCCESS = 1
SUBTYPE_ACCEPTANCE_FAILURE = 2

#: Failure codes are mission-defined; ECSS specifies the field, not the values. Small and named,
#: because a report carrying only "refused" sends the operator to the same place a silent refusal
#: does - looking for the reason somewhere else.
FAILURE_NOT_AUTHORISED = 1
FAILURE_UNKNOWN_FUNCTION = 2
FAILURE_MALFORMED = 3

FAILURE_NAMES = {
    FAILURE_NOT_AUTHORISED: "not authorised",
    FAILURE_UNKNOWN_FUNCTION: "unknown function",
    FAILURE_MALFORMED: "malformed request",
}


def request_id(apid: int, seq_count: int, ptype: int = 1, sec_hdr: bool = True) -> bytes:
    """The four octets ECSS-E-ST-70-41C uses to name the telecommand a report is about.

    It is the failed packet's own primary header, first two words: version, type, secondary
    header flag and APID, then sequence flags and count. Not an invented identifier - the point
    is that the ground can match the report to a request it has a copy of, without the spacecraft
    having to remember anything.
    """
    if not 0 <= apid <= 0x7FF:
        raise ValueError(f"APID {apid} does not fit in 11 bits")
    if not 0 <= seq_count <= 0x3FFF:
        raise ValueError(f"sequence count {seq_count} does not fit in 14 bits")
    word0 = (0 << 13) | ((ptype & 1) << 12) | ((1 if sec_hdr else 0) << 11) | apid
    word1 = (0b11 << 14) | seq_count          # unsegmented, as everything here is
    return word0.to_bytes(2, "big") + word1.to_bytes(2, "big")


def parse_request_id(raw: bytes) -> tuple:
    """(apid, seq_count) out of those four octets."""
    if len(raw) < 4:
        raise ValueError(f"a request id is four octets, got {len(raw)}")
    word0 = int.from_bytes(raw[0:2], "big")
    word1 = int.from_bytes(raw[2:4], "big")
    return word0 & 0x7FF, word1 & 0x3FFF


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
