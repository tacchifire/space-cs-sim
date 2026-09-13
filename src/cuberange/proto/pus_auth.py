"""Authentication on the TELECOMMAND, not on the link it arrived over.

EX-S01 ends by naming three ways to close the gap it demonstrates, and says the third is the one
that actually answers it and is the most work:

    Authentication at the application layer. The PUS packet carries its own MAC. Then it does not
    matter which link it arrived on - which is the property the other two do not have.

This is that. A mission-defined authentication trailer inside the Space Packet:

    primary(6) | PUS TC secondary(5) | application data | SEQ(4) | MAC(16)

and the Space Packet's own data-field length covers the trailer, so it travels with the packet
through any framing: a TC transfer frame on the space link, a CSP packet on the crosslink, a raw
CAN payload on the internal bus. The OBC verifies the request it is about to act on rather than
the road it came down.

WHAT IS AND IS NOT STANDARD HERE, stated rather than implied.

  - The MAC is AES-256-GCM over `packet[0:mac]` with no plaintext - the same construction and the
    same key length as the SDLS one in `sdls.py`, checked against libsodium and the NIST vectors.
    That part has independent oracles.
  - The TRAILER LAYOUT IS MISSION-DEFINED AND HAS NO ORACLE. ECSS-E-ST-70-41C defines no
    authentication field for a TC packet; CCSDS puts security at the transfer-frame layer, which
    is exactly the layer this bypasses. So there is nothing outside this repository to check the
    field order against, and `tests/golden/pus_auth.json` says so in its `oracles` list instead of
    listing something that only looks like one. `ASSURANCE.md` carries the same sentence.
  - A real mission doing this would put it in its own ICD and would have that reviewed. The
    honest claim is "this range defines a trailer", not "this range implements a standard".

WHY THE SEQUENCE NUMBER IS INSIDE THE MAC. Without it the trailer authenticates a request and not
an OCCASION, and a recording replays perfectly - which is EX-L01 at a different layer. It is
counted PER SOURCE ID, because two ground stations are two senders and one counter between them
is EX-G03 a third time. That is implemented here rather than written down as a limit, because by
now the range has made the same mistake twice and the third time is not a lesson.

WHAT THIS STILL DOES NOT SOLVE, and EX-S02's write-up says it at length: every node that verifies
needs the key, so a compromised spacecraft holds it; and authentication answers who, never what -
EX-G02's authority table is still the thing that decides whether an authenticated station may do
what it asked.
"""
from __future__ import annotations

from dataclasses import dataclass

from .pus import TC_SEC_HDR_LEN
from .spacepacket import PRIMARY_HEADER_LEN as SP_HEADER_LEN

SEQ_LEN = 4          #: per-source occasion counter, inside the authenticated region
MAC_LEN = 16         #: the GCM tag
KEY_LEN = 32         #: AES-256
TRAILER_LEN = SEQ_LEN + MAC_LEN

#: The nonce. GCM needs 12 octets and this profile derives them from the packet rather than
#: carrying them, because a nonce that is transmitted is 12 more octets on every packet and this
#: one is already determined by fields the MAC covers:
#:
#:     APID (2) | counterparty id (2) | sequence (4) | direction (1) | 0x00 x 3
#:
#: The rule GCM actually needs is that a (key, nonce) pair is never reused. Reusing one with GCM is
#: catastrophic - it leaks the authentication subkey, not just the plaintext - so every field here
#: is load-bearing:
#:
#:   - the counterparty id, because several ground stations share the spacecraft's APID and would
#:     otherwise collide on the same counter value;
#:   - the sequence, because otherwise two commands from one station collide, and because that is
#:     the same number the anti-replay check refuses to see twice. Those two properties being the
#:     same property is what makes the counter non-optional;
#:   - THE DIRECTION, which was added after the telemetry side was written. A telecommand from
#:     station 0x0042 with sequence 5 and a report to station 0x0042 with sequence 5 are different
#:     packets that produced the SAME nonce under the same key. One octet separates them.
#:
#: The counterparty id is read from a different offset in each direction, because PUS puts it in a
#: different place: a TC secondary header is version/service/subtype/source(2), and a TM's is
#: version/service/subtype/counter(2)/destination(2).
NONCE_LEN = 12
DIRECTION_TC = 1
DIRECTION_TM = 0

#: Where the counterparty id sits, after the Space Packet primary header, in each direction.
_PARTY_AT = {DIRECTION_TC: 3, DIRECTION_TM: 5}


class AuthenticationError(Exception):
    """The trailer is absent, malformed, or does not verify.

    One exception for all three, as in `sdls.py` and for the same reason: a receiver that tells the
    sender which of them happened has told an attacker which one they achieved.
    """


@dataclass(frozen=True)
class Verified:
    """What a verified packet carried, once it is safe to look at it."""

    source_id: int
    seq: int
    packet: bytes        #: the Space Packet with the trailer removed, ready for the normal parser


def nonce(apid: int, party_id: int, seq: int, direction: int = DIRECTION_TC) -> bytes:
    if direction not in _PARTY_AT:
        raise ValueError(f"direction must be DIRECTION_TC or DIRECTION_TM, got {direction}")
    return (apid.to_bytes(2, "big") + party_id.to_bytes(2, "big")
            + seq.to_bytes(SEQ_LEN, "big") + bytes([direction]) + bytes(NONCE_LEN - 9))


def direction_of(packet: bytes) -> int:
    """TC or TM, out of the Space Packet primary header's type bit (CCSDS 133.0-B, 4.1.2.3.2)."""
    return DIRECTION_TC if (packet[0] >> 4) & 0x01 else DIRECTION_TM


def party_of(packet: bytes) -> int:
    """The counterparty: a TC's source id, or a TM's destination id."""
    at = SP_HEADER_LEN + _PARTY_AT[direction_of(packet)]
    return int.from_bytes(packet[at:at + 2], "big")


def _tag(key: bytes, iv: bytes, aad: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(key) != KEY_LEN:
        raise ValueError(f"AES-256 needs a {KEY_LEN}-octet key, got {len(key)}")
    return AESGCM(key).encrypt(iv, b"", aad)


def sign(packet: bytes, *, key: bytes, seq: int) -> bytes:
    """Append the trailer to a finished Space Packet, fixing up its length field.

    `packet` is a complete TC Space Packet as `SpacePacket.encode()` produces it. The data-field
    length is rewritten to cover the trailer, BEFORE the MAC is computed - so the length an
    attacker would have to change to strip the trailer is itself authenticated.
    """
    if len(packet) < SP_HEADER_LEN + TC_SEC_HDR_LEN:
        raise ValueError(f"not a PUS TC Space Packet: {len(packet)} octets")
    if not 0 <= seq < 1 << (SEQ_LEN * 8):
        raise ValueError(f"the sequence number is {SEQ_LEN} octets: {seq}")

    apid = ((packet[0] << 8) | packet[1]) & 0x7FF
    direction = direction_of(packet)
    party = party_of(packet)

    body = bytearray(packet)
    total_data = len(packet) - SP_HEADER_LEN + TRAILER_LEN
    body[4:6] = (total_data - 1).to_bytes(2, "big")
    aad = bytes(body) + seq.to_bytes(SEQ_LEN, "big")
    return aad + _tag(key, nonce(apid, party, seq, direction), aad)


def verify(packet: bytes, *, key: bytes) -> Verified:
    """Check the trailer and hand back the packet without it.

    Shape, then the declared length, then the MAC - the order a receiver must use, because the
    alternative is running a cipher over octet counts an attacker chose.
    """
    minimum = SP_HEADER_LEN + TC_SEC_HDR_LEN + TRAILER_LEN
    if len(packet) < minimum:
        raise AuthenticationError(
            f"{len(packet)} octets cannot hold a header, a secondary header and a trailer "
            f"({minimum} minimum)")
    declared = ((packet[4] << 8) | packet[5]) + 1
    if declared != len(packet) - SP_HEADER_LEN:
        raise AuthenticationError(
            f"data-field length says {declared}, {len(packet) - SP_HEADER_LEN} octets follow")

    mac_at = len(packet) - MAC_LEN
    seq_at = mac_at - SEQ_LEN
    apid = ((packet[0] << 8) | packet[1]) & 0x7FF
    direction = direction_of(packet)
    party = party_of(packet)
    seq = int.from_bytes(packet[seq_at:mac_at], "big")

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        AESGCM(key).decrypt(nonce(apid, party, seq, direction), packet[mac_at:], packet[:mac_at])
    except InvalidTag as exc:
        raise AuthenticationError("MAC does not verify") from exc

    #: The packet the OBC should parse: trailer gone, length field put back to what it describes.
    inner = bytearray(packet[:seq_at])
    inner[4:6] = (len(inner) - SP_HEADER_LEN - 1).to_bytes(2, "big")
    return Verified(source_id=party, seq=seq, packet=bytes(inner))
