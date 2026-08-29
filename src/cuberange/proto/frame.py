"""TC and TM transfer frames, minimal profiles, plus the CubeRange lab framing.

TC primary header, 5 octets (CCSDS 232.0-B-4):
  [0] tf-version(2) | bypass(1) | ctrl-cmd(1) | reserved(2) | scid-high(2)
  [1] scid-low(8)
  [2] vcid(6) | frame-length-high(2)
  [3] frame-length-low(8)          frame length = total octets - 1
  [4] frame sequence number(8)

TM primary header, 6 octets (CCSDS 132.0-B-3):
  [0:2] tf-version(2) | scid(10) | vcid(3) | ocf-flag(1)
  [2]   master channel frame count
  [3]   virtual channel frame count
  [4:6] data field status: sec-hdr(1) | sync(1) | packet-order(1) | seg-len-id(2) | fhp(11)

Both are followed by the data field and a 2-octet FECF. No OCF, no secondary header, no COP-1.

The outer delimiter below is CubeRange lab framing, NOT a CCSDS CLTU. CLTU is CCSDS 231.0-B-4 and
starts EB90; we do not implement it, and this must never be described as CCSDS channel conformance.
"""
from __future__ import annotations

from .crc import crc16_ccsds

ASM = b"\x1a\xcf\xfc\x1d"
SCID = 0x0A9
VCID = 0

TC_HEADER_LEN = 5
TM_HEADER_LEN = 6
FECF_LEN = 2
FIRST_HEADER_POINTER = 0          # a packet starts at the first octet of the data field
MAX_FRAME_LEN = 1024


def encode_tc_frame(payload: bytes, seq: int) -> bytes:
    total = TC_HEADER_LEN + len(payload) + FECF_LEN
    if total > MAX_FRAME_LEN:
        raise ValueError(f"TC frame of {total} octets exceeds the {MAX_FRAME_LEN} limit")
    length_field = total - 1
    header = bytes([
        (SCID >> 8) & 0x03,
        SCID & 0xFF,
        ((VCID & 0x3F) << 2) | ((length_field >> 8) & 0x03),
        length_field & 0xFF,
        seq & 0xFF,
    ])
    body = header + payload
    return body + crc16_ccsds(body).to_bytes(2, "big")


def decode_tc_frame(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < TC_HEADER_LEN + FECF_LEN:
        raise ValueError(f"TC frame too short: {len(frame)} octets")
    if crc16_ccsds(frame) != 0x0000:
        raise ValueError("FECF check failed")
    declared = (((frame[2] & 0x03) << 8) | frame[3]) + 1
    if declared != len(frame):
        raise ValueError(f"TC frame declares {declared} octets but is {len(frame)}")
    return frame[4], frame[TC_HEADER_LEN:-FECF_LEN]


def encode_tm_frame(payload: bytes, mc_count: int, vc_count: int) -> bytes:
    total = TM_HEADER_LEN + len(payload) + FECF_LEN
    if total > MAX_FRAME_LEN:
        raise ValueError(f"TM frame of {total} octets exceeds the {MAX_FRAME_LEN} limit")
    word0 = ((SCID & 0x3FF) << 4) | ((VCID & 0x7) << 1)      # ocf flag = 0
    header = (word0.to_bytes(2, "big")
              + bytes([mc_count & 0xFF, vc_count & 0xFF])
              + FIRST_HEADER_POINTER.to_bytes(2, "big"))
    body = header + payload
    return body + crc16_ccsds(body).to_bytes(2, "big")


def decode_tm_frame(frame: bytes) -> tuple[int, int, bytes]:
    if len(frame) < TM_HEADER_LEN + FECF_LEN:
        raise ValueError(f"TM frame too short: {len(frame)} octets")
    if crc16_ccsds(frame) != 0x0000:
        raise ValueError("FECF check failed")
    return frame[2], frame[3], frame[TM_HEADER_LEN:-FECF_LEN]


def wrap(frame: bytes) -> bytes:
    """CubeRange lab framing: ASM + u16 big-endian length + frame."""
    return ASM + len(frame).to_bytes(2, "big") + frame


class Deframer:
    """Byte-stream to frame reassembler.

    A UART hands us arbitrary chunks, so the deframer holds partial state between feeds and hunts
    for the next ASM after any corruption. It never raises: a link that has seen garbage should
    keep working once the next good frame arrives.
    """

    def __init__(self, max_frame_len: int = MAX_FRAME_LEN):
        self._buf = bytearray()
        self._max = max_frame_len

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buf.extend(chunk)
        out: list[bytes] = []
        while True:
            start = self._buf.find(ASM)
            if start < 0:
                # Keep the last few bytes: they may be a partial ASM split across chunks.
                if len(self._buf) > len(ASM) - 1:
                    del self._buf[:len(self._buf) - (len(ASM) - 1)]
                return out
            del self._buf[:start]
            if len(self._buf) < len(ASM) + 2:
                return out
            length = int.from_bytes(self._buf[len(ASM):len(ASM) + 2], "big")
            if length == 0 or length > self._max:
                # Bogus length: skip this ASM and look for the next one.
                del self._buf[:len(ASM)]
                continue
            end = len(ASM) + 2 + length
            if len(self._buf) < end:
                return out
            out.append(bytes(self._buf[len(ASM) + 2:end]))
            del self._buf[:end]
