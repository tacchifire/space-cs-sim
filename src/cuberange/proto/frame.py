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

# Satellite 0's identity. It is a DEFAULT, not a constant of the range: a second spacecraft needs
# its own, and until these were parameters both would have emitted and accepted byte-identical
# frame identities. A ground station wired to satellite 2's link would have accepted satellite 1's
# telemetry without a word - the quietest possible failure, and the one that makes a multi-satellite
# range teach something false.
SCID = 0x0A9
VCID = 0

TC_HEADER_LEN = 5
TM_HEADER_LEN = 6
FECF_LEN = 2
FIRST_HEADER_POINTER = 0          # a packet starts at the first octet of the data field
MAX_FRAME_LEN = 1024


def _check_identity(scid: int, vcid: int, scid_bits: int, vcid_bits: int) -> None:
    """Refuse an identity that does not fit, rather than masking it into a different spacecraft.

    The encoders used to mask: `scid=0x4A9` went out as 0x0A9. A ground station that kept the
    untruncated value would then transmit to one spacecraft and reject every reply from it, which
    presents as a satellite that has gone silent.
    """
    if not 0 <= scid < (1 << scid_bits):
        raise ValueError(
            f"SCID 0x{scid:X} does not fit in {scid_bits} bits; masking it would address a "
            f"different spacecraft")
    if not 0 <= vcid < (1 << vcid_bits):
        raise ValueError(f"VCID {vcid} does not fit in {vcid_bits} bits")


def encode_tc_frame(payload: bytes, seq: int, scid: int = SCID, vcid: int = VCID) -> bytes:
    # TC primary header: SCID is 10 bits split across octets 0-1, VCID is 6 bits in octet 2.
    _check_identity(scid, vcid, scid_bits=10, vcid_bits=6)
    total = TC_HEADER_LEN + len(payload) + FECF_LEN
    if total > MAX_FRAME_LEN:
        raise ValueError(f"TC frame of {total} octets exceeds the {MAX_FRAME_LEN} limit")
    length_field = total - 1
    header = bytes([
        (scid >> 8) & 0x03,
        scid & 0xFF,
        ((vcid & 0x3F) << 2) | ((length_field >> 8) & 0x03),
        length_field & 0xFF,
        seq & 0xFF,
    ])
    body = header + payload
    return body + crc16_ccsds(body).to_bytes(2, "big")


def tc_frame_identity(frame: bytes) -> tuple[int, int]:
    """(scid, vcid) out of a TC primary header, without validating anything else."""
    if len(frame) < TC_HEADER_LEN:
        raise ValueError(f"TC frame too short to carry an identity: {len(frame)} octets")
    return ((frame[0] & 0x03) << 8) | frame[1], (frame[2] >> 2) & 0x3F


def decode_tc_frame(frame: bytes, expect_scid: int | None = None,
                    expect_vcid: int | None = None) -> tuple[int, bytes]:
    """Decode a TC frame, optionally requiring it to be addressed to a particular spacecraft.

    `expect_scid` defaults to None - accept anything - because the FRAMING layer genuinely does not
    know which spacecraft the caller meant. The caller does, and a ground station that omits it is
    choosing to accept every satellite in earshot.
    """
    if len(frame) < TC_HEADER_LEN + FECF_LEN:
        raise ValueError(f"TC frame too short: {len(frame)} octets")
    if crc16_ccsds(frame) != 0x0000:
        raise ValueError("FECF check failed")
    declared = (((frame[2] & 0x03) << 8) | frame[3]) + 1
    if declared != len(frame):
        raise ValueError(f"TC frame declares {declared} octets but is {len(frame)}")
    scid, vcid = tc_frame_identity(frame)
    if expect_scid is not None and scid != expect_scid:
        raise ValueError(f"TC frame is for spacecraft 0x{scid:03X}, expected 0x{expect_scid:03X}")
    if expect_vcid is not None and vcid != expect_vcid:
        raise ValueError(f"TC frame is on VC {vcid}, expected {expect_vcid}")
    return frame[4], frame[TC_HEADER_LEN:-FECF_LEN]


def encode_tm_frame(payload: bytes, mc_count: int, vc_count: int,
                    scid: int = SCID, vcid: int = VCID) -> bytes:
    # TM primary header: SCID is 10 bits and VCID only 3, both inside the first 16-bit word.
    _check_identity(scid, vcid, scid_bits=10, vcid_bits=3)
    total = TM_HEADER_LEN + len(payload) + FECF_LEN
    if total > MAX_FRAME_LEN:
        raise ValueError(f"TM frame of {total} octets exceeds the {MAX_FRAME_LEN} limit")
    word0 = ((scid & 0x3FF) << 4) | ((vcid & 0x7) << 1)      # ocf flag = 0
    header = (word0.to_bytes(2, "big")
              + bytes([mc_count & 0xFF, vc_count & 0xFF])
              + FIRST_HEADER_POINTER.to_bytes(2, "big"))
    body = header + payload
    return body + crc16_ccsds(body).to_bytes(2, "big")


def tm_frame_identity(frame: bytes) -> tuple[int, int]:
    """(scid, vcid) out of a TM primary header, without validating anything else."""
    if len(frame) < 2:
        raise ValueError(f"TM frame too short to carry an identity: {len(frame)} octets")
    word0 = (frame[0] << 8) | frame[1]
    return (word0 >> 4) & 0x3FF, (word0 >> 1) & 0x7


def decode_tm_frame(frame: bytes, expect_scid: int | None = None,
                    expect_vcid: int | None = None) -> tuple[int, int, bytes]:
    """Decode a TM frame, optionally requiring it to come from a particular spacecraft."""
    if len(frame) < TM_HEADER_LEN + FECF_LEN:
        raise ValueError(f"TM frame too short: {len(frame)} octets")
    if crc16_ccsds(frame) != 0x0000:
        raise ValueError("FECF check failed")
    scid, vcid = tm_frame_identity(frame)
    if expect_scid is not None and scid != expect_scid:
        raise ValueError(f"TM frame is from spacecraft 0x{scid:03X}, expected 0x{expect_scid:03X}")
    if expect_vcid is not None and vcid != expect_vcid:
        raise ValueError(f"TM frame is on VC {vcid}, expected {expect_vcid}")
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
