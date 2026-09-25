"""The range's SDLS key. One key, written down, and that is the point being made.

This is NOT key management, and no amount of wrapping makes it so. It is a 32-octet constant in a
file in a public repository, mirrored into the firmware, and `test_keys.py` asserts the two copies
agree the same way `test_identity.py` does for the addresses.

WHY THAT IS THE HONEST SHAPE HERE:

  - The range's lesson about keys is not "keep them secret" - every exercise write-up already says
    a fixed shared secret is a thing you HAVE rather than a thing you prove. It is that a control
    keyed on possession survives a new path while a control keyed on an assertion does not, and
    EX-X01 measures exactly that.
  - A key store, a rekey protocol and a hardware root of trust are each a project. Writing a
    weak one and calling the problem solved would be the strawman `firmware-matrix.yml` exists to
    prevent, one layer up.
  - Anyone who reads this file can forge an authenticated telecommand to this range's spacecraft.
    That is stated in SAFE_USE.md and it is a property of a teaching range, not a defect.

The value is arbitrary and was chosen to be obviously synthetic rather than to look real.
"""
from __future__ import annotations

#: 32 octets, AES-256. Deliberately a recognisable pattern: a key that looks like a key invites
#: someone to wonder whether it is one.
SDLS_KEY = bytes.fromhex(
    "6375626572616e67652d73646c732d746573742d6b65792d6e6f742d7265616c"
)

#: The Security Parameter Index this range's one association answers to. SPI 0 and 65535 are
#: avoided: CCSDS reserves neither, but NASA CryptoLib refuses SPI_MIN and SPI_MAX outright, and a
#: range whose frames an independent implementation cannot parse has given up an oracle for
#: nothing. See tests/golden/sdls.json.
SDLS_SPI = 9

#: A SECOND association, and the reason there are now two.
#:
#: COMM verified against one SPI and one key until EX-S03, and its own source said so: "one
#: association is all this range has". CCSDS 355.0-B-2 puts a key, a cipher mode, a sequence
#: number and a STATE in a Security Association, and a mission has several because that is how a
#: key is retired - you activate the new SA, you deactivate the old one, and a frame on the old
#: SPI stops opening the door.
#:
#: Both halves of that are controls and only the first one is fun to do. EX-S03 is about the
#: second: a rotation that activated a new key and left the old SA operational has moved the
#: operator onto new material and has retired nothing.
#:
#: SPI 10, next to 9, and a key that is obviously the same family of synthetic constant. Nothing
#: in this file is a secret and keys.py's header says at length why.
SDLS_KEY_ROTATED = bytes.fromhex(
    "6375626572616e67652d73646c732d726f74617465642d6b65792d6e6f74726c"
)

#: The association the operator rotates TO. SPI 9 stays where it is so every exercise written
#: before this one keeps measuring what it measured.
SDLS_SPI_ROTATED = 10

#: A THIRD association, and a DIFFERENT party, which is the whole of EX-S04.
#:
#: SPI 9 and SPI 10 are both the operator's - a key and the key it rotates to. This one belongs to
#: someone else: a partner or cross-support station that legitimately holds its own Security
#: Association on the same space link. Shared and cross-supported ground networks are ordinary, and
#: CCSDS 355.0-B-2 has several SAs precisely so several parties can each hold one.
#:
#: It exists so that "a valid MAC" and "the operator" can be two different things. EX-S04 is SA
#: management by telecommand - CCSDS 355.1 Extended Procedures, the STOP_SA directive - and the
#: lesson is EX-G02's, one layer down in key management: authenticating the sender (this station
#: holds SPI 11's key and its frames verify) is a different question from authorising it (may it
#: retire the OPERATOR's SPI 9?). A control that answers only the first lets a partner switch the
#: operator off with a frame that passes every cryptographic check on board.
SDLS_KEY_PARTNER = bytes.fromhex(
    "6375626572616e67652d73646c732d706172746e65722d6e6f742d7265616c21"
)

#: SPI 11, next to 9 and 10, and a key that is obviously the same family of synthetic constant.
SDLS_SPI_PARTNER = 11

#: SA owners, mirrored into cuberange_keys.h. An owner is the party a Security Association belongs
#: to, and it is the whole of EX-S04's authorisation check: the hardened build lets a STOP_SA
#: directive retire an SA only when the frame carrying it authenticated under an SA with the SAME
#: owner. The values are arbitrary tags; only equality is ever compared.
SA_OWNER_OPERATOR = 1     #: SPI 9 and SPI 10 - the operator's key and the one it rotates to
SA_OWNER_PARTNER = 2      #: SPI 11 - the partner / cross-support station

#: Which owner each SPI belongs to. keys.py is the host-side mirror of the firmware's sa_table[],
#: and a solver reads this to know which SA is authorised to retire which.
SA_OWNERS = {SDLS_SPI: SA_OWNER_OPERATOR,
             SDLS_SPI_ROTATED: SA_OWNER_OPERATOR,
             SDLS_SPI_PARTNER: SA_OWNER_PARTNER}

assert len(SDLS_KEY) == 32, "AES-256 needs 32 octets"
assert len(SDLS_KEY_ROTATED) == 32, "AES-256 needs 32 octets"
assert len(SDLS_KEY_PARTNER) == 32, "AES-256 needs 32 octets"
assert SDLS_KEY_ROTATED != SDLS_KEY, "a rotation to the same octets is not a rotation"
assert len({SDLS_KEY, SDLS_KEY_ROTATED, SDLS_KEY_PARTNER}) == 3, "three SAs need three keys"
assert len({SDLS_SPI, SDLS_SPI_ROTATED, SDLS_SPI_PARTNER}) == 3, "three SAs need three SPIs"
assert SA_OWNER_OPERATOR != SA_OWNER_PARTNER, "the partner is a different party or there is no EX-S04"
