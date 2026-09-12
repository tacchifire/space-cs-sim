"""SDLS on the TC transfer frame: authentication, and only authentication.

CCSDS 355.0-B-2 adds a security header and a security trailer to a transfer frame. This module
implements the AUTHENTICATION service and nothing else - the payload travels in the clear:

    primary(5) | SPI(2) | IV(12) | SN(4) | PDU | MAC(16) | FECF(2)

That layout is not this project's reading of the standard. It is NASA CryptoLib's, measured:
`tools/oracles/sdls_oracle.c` hands CryptoLib a frame and prints where CryptoLib says the fields
are, and on a frame with no segment header it answers SPI at 5, IV at 7, SN at 19, PDU at 23 and
the MAC where the PDU ends. CryptoLib's own tests use frames WITH a segment header, which puts the
SPI at 8; this range emits none, and that is exactly the difference a hand-read layout gets wrong.

WHY AUTHENTICATION ONLY, and it is a choice rather than a shortcut:

  - It is a service type the standard defines. 355.0-B-2 names authentication, encryption, and
    authenticated encryption separately; a mission may run any of them.
  - A range whose frames you cannot read teaches less. Every exercise here asks the student to
    look at octets on a wire. Encryption would hide the thing being taught while adding no lesson
    this range does not already have - and the lesson that IS here, that authenticating the sender
    is a different question from authorising them, survives in the clear.
  - It is honest about what is implemented. `ASSURANCE.md` says this project claims no
    cryptographic assurance, and that sentence does not improve by encrypting more.

WHAT IS SIMPLIFIED, stated rather than buried:

  - The Authentication Bit Mask is all ones over the authenticated portion: every bit of the
    primary header is covered. A real mission masks the bits that legitimately change in transit -
    the Bypass and Control Command flags are the usual ones - because a relay that flips them
    would otherwise break the MAC. Nothing in this range flips them, so nothing here would notice,
    which is the reason to write it down.
  - One Security Association, one key, compiled in. That is not key management. EX-X01's
    mitigation already says a fixed shared secret is a thing you have rather than a thing you
    prove, and moving it under a MAC does not change that.
  - The anti-replay sequence number is carried and compared, but the window is a single counter.
    EX-G03 is about what one counter does when there are two transmitters, and putting the same
    counter inside a security header does not fix it - it moves it.

AES-GCM comes from `cryptography` (OpenSSL). `tests/golden/sdls.json` checks it against libsodium
through PyNaCl, which is a different implementation and not a different spelling of the same one,
and against the published NIST vectors, which are not an implementation at all.
"""
from __future__ import annotations

from dataclasses import dataclass

from .crc import crc16_ccsds
from .frame import FECF_LEN, MAX_FRAME_LEN, SCID, TC_HEADER_LEN, VCID, _check_identity

#: Field widths, in octets. Named because the whole point of a security header is that the lengths
#: are not in the frame - they come from the Security Association, and a receiver using the wrong
#: ones parses garbage that is still the right total length.
SPI_LEN = 2
IV_LEN = 12          #: AES-GCM's nonce. 12 is the size GCM is specified for; other sizes are hashed.
SN_LEN = 4           #: anti-replay sequence number
MAC_LEN = 16         #: the GCM tag
KEY_LEN = 32         #: AES-256

SEC_HEADER_LEN = SPI_LEN + IV_LEN + SN_LEN
OVERHEAD = SEC_HEADER_LEN + MAC_LEN

#: Offsets within the frame, for readers and for tests that assert the layout did not move.
SPI_AT = TC_HEADER_LEN
IV_AT = SPI_AT + SPI_LEN
SN_AT = IV_AT + IV_LEN
PDU_AT = SN_AT + SN_LEN


class AuthenticationError(Exception):
    """The MAC did not verify, or the frame is not shaped like an authenticated one.

    One exception for both, deliberately. A receiver that distinguishes "wrong MAC" from
    "malformed" in what it tells the sender has told an attacker which of the two they achieved.
    The spacecraft logs the difference locally; the wire does not carry it.
    """


@dataclass(frozen=True)
class Authenticated:
    """What a verified frame carried."""

    spi: int
    iv: bytes
    seq_num: int
    payload: bytes
    scid: int
    vcid: int
    frame_seq: int


def _aesgcm_tag(key: bytes, iv: bytes, aad: bytes) -> bytes:
    """The GCM tag over `aad` with no plaintext - which is what authentication-only means."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(key) != KEY_LEN:
        raise ValueError(f"AES-256 needs a {KEY_LEN}-octet key, got {len(key)}")
    if len(iv) != IV_LEN:
        raise ValueError(f"this profile fixes the IV at {IV_LEN} octets, got {len(iv)}")
    return AESGCM(key).encrypt(iv, b"", aad)


def encode_tc(payload: bytes, *, key: bytes, spi: int, iv: bytes, seq_num: int,
              frame_seq: int, scid: int = SCID, vcid: int = VCID) -> bytes:
    """One authenticated TC transfer frame.

    `frame_seq` is the TC frame sequence number in the primary header - COP-1's, per virtual
    channel, the one EX-G03 is about. `seq_num` is SDLS's own anti-replay counter in the security
    header. They are different counters at different layers and this signature keeps them apart,
    because a single `seq=` parameter would let a caller pass one where the other belongs and
    produce a frame that verifies and replays.
    """
    _check_identity(scid, vcid, scid_bits=10, vcid_bits=6)
    if not 0 <= spi < 1 << 16:
        raise ValueError(f"SPI is 16 bits: {spi}")
    if not 0 <= seq_num < 1 << (SN_LEN * 8):
        raise ValueError(f"the sequence number is {SN_LEN} octets: {seq_num}")

    total = TC_HEADER_LEN + OVERHEAD + len(payload) + FECF_LEN
    if total > MAX_FRAME_LEN:
        raise ValueError(f"authenticated TC frame of {total} octets exceeds {MAX_FRAME_LEN}")
    length_field = total - 1
    header = bytes([
        (scid >> 8) & 0x03,
        scid & 0xFF,
        ((vcid & 0x3F) << 2) | ((length_field >> 8) & 0x03),
        length_field & 0xFF,
        frame_seq & 0xFF,
    ])
    sec_header = spi.to_bytes(SPI_LEN, "big") + iv + seq_num.to_bytes(SN_LEN, "big")
    #: The authenticated portion: everything from the first octet through the payload. The FECF is
    #: not in it - it is computed over the finished frame, MAC included, and a receiver checks it
    #: before it has any reason to believe the MAC.
    aad = header + sec_header + payload
    body = aad + _aesgcm_tag(key, iv, aad)
    return body + crc16_ccsds(body).to_bytes(2, "big")


def decode_tc(frame: bytes, *, key: bytes, expect_scid: int | None = None,
              expect_vcid: int | None = None) -> Authenticated:
    """Verify and take apart one authenticated TC frame.

    The order is deliberate and is the order a spacecraft must use: shape, then length field, then
    FECF, then MAC. Verifying the MAC first would mean running a cipher over octet counts an
    attacker chose.
    """
    if len(frame) < TC_HEADER_LEN + OVERHEAD + FECF_LEN:
        raise AuthenticationError(
            f"{len(frame)} octets cannot hold a primary header, a security header, a MAC and a "
            f"FECF ({TC_HEADER_LEN + OVERHEAD + FECF_LEN} minimum)")
    declared = (((frame[2] & 0x03) << 8) | frame[3]) + 1
    if declared != len(frame):
        raise AuthenticationError(
            f"length field says {declared} octets, the frame is {len(frame)}")
    if crc16_ccsds(frame) != 0x0000:
        raise AuthenticationError("FECF residue is not zero")

    scid = ((frame[0] & 0x03) << 8) | frame[1]
    vcid = (frame[2] >> 2) & 0x3F
    if expect_scid is not None and scid != expect_scid:
        raise AuthenticationError(f"frame is for SCID 0x{scid:03X}, not 0x{expect_scid:03X}")
    if expect_vcid is not None and vcid != expect_vcid:
        raise AuthenticationError(f"frame is on VC {vcid}, not {expect_vcid}")

    mac_at = len(frame) - FECF_LEN - MAC_LEN
    if mac_at < PDU_AT:
        raise AuthenticationError("the MAC would start before the payload does")
    aad = frame[:mac_at]
    mac = frame[mac_at:mac_at + MAC_LEN]

    spi = int.from_bytes(frame[SPI_AT:SPI_AT + SPI_LEN], "big")
    iv = frame[IV_AT:IV_AT + IV_LEN]
    seq_num = int.from_bytes(frame[SN_AT:SN_AT + SN_LEN], "big")
    payload = frame[PDU_AT:mac_at]

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        AESGCM(key).decrypt(iv, mac, aad)
    except InvalidTag as exc:
        raise AuthenticationError("MAC does not verify") from exc

    return Authenticated(spi=spi, iv=iv, seq_num=seq_num, payload=payload,
                         scid=scid, vcid=vcid, frame_seq=frame[4])
