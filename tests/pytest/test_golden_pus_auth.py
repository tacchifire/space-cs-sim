"""The telecommand's own MAC, against the one oracle it has and about the one it does not.

WHAT IS CHECKED FROM OUTSIDE: the tag. Every committed vector's MAC came out of libsodium through
PyNaCl, which is a different AES-256-GCM implementation from the OpenSSL one the codec uses, and
the primitive itself is pinned to the published NIST vectors in sdls.json.

WHAT IS NOT: the field layout. ECSS-E-ST-70-41C defines no authentication field for a TC packet
and CCSDS puts security at the transfer-frame layer - which is the layer EX-S02 exists because a
crosslink bypasses. So the trailer's field order is this repository's, there is nothing outside to
compare it against, and `tests/golden/pus_auth.json` says exactly that in its `oracles` list.

This file asserts that the file keeps saying it. A golden set whose provenance quietly improved
from "none" to something vague would be worse than one with no oracle at all, because the second
is honest.
"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((REPO / "tests" / "golden" / "pus_auth.json").read_text())

from cuberange.proto import pus_auth   # noqa: E402

KEY = bytes.fromhex(GOLDEN["key"])


def _vectors():
    return GOLDEN["vectors"]


@pytest.mark.parametrize("v", _vectors(), ids=lambda v: f"src{v['source_id']:04X}-seq{v['seq']}")
def test_the_codec_reproduces_the_committed_packet(v):
    """Byte for byte, including a MAC that came out of libsodium rather than out of this codec."""
    got = pus_auth.sign(bytes.fromhex(v["inner"]), key=KEY, seq=v["seq"])
    assert got.hex() == v["packet"].lower(), (
        f"codec:  {got.hex()}\nvector: {v['packet'].lower()}")


@pytest.mark.parametrize("v", _vectors(), ids=lambda v: f"src{v['source_id']:04X}-seq{v['seq']}")
def test_the_codec_verifies_and_hands_back_what_was_signed(v):
    got = pus_auth.verify(bytes.fromhex(v["packet"]), key=KEY)
    assert got.source_id == v["source_id"]
    assert got.seq == v["seq"]
    #: The packet handed back must be EXACTLY the one that was signed, length field and all - the
    #: OBC parses it with the ordinary parser, which knows nothing about any of this.
    assert got.packet.hex() == v["inner"].lower()


@pytest.mark.parametrize("v", _vectors(), ids=lambda v: f"src{v['source_id']:04X}-seq{v['seq']}")
def test_the_nonce_is_derived_and_not_carried(v):
    assert pus_auth.nonce(v["apid"], v["source_id"], v["seq"]).hex() == v["nonce"].lower()
    assert v["nonce"].lower() not in v["packet"].lower().replace(v["mac"].lower(), ""), (
        "the nonce appears in the packet; this profile derives it so it does not have to be sent")


def test_the_golden_file_still_says_it_has_no_layout_oracle():
    """Provenance that quietly improves is the failure this repository keeps finding.

    ASSURANCE.md carries the same sentence. If a real oracle for this layout ever appears - a
    published ICD, another implementation - it goes in the list and this test is updated to match,
    deliberately, rather than the list drifting into something that reads better than it is.
    """
    oracles = GOLDEN["oracles"]
    assert any(o.startswith("NONE for the field layout") for o in oracles), (
        f"pus_auth.json no longer states that its layout has no oracle: {oracles}")
    assert any("libsodium" in o for o in oracles), "the tag's oracle is gone"


def test_a_source_id_change_breaks_the_mac():
    """The property EX-S02 is built on: the field EX-X01 forges is covered wherever it arrives."""
    v = _vectors()[1]
    packet = bytearray(bytes.fromhex(v["packet"]))
    packet[6 + 3] ^= 0x01                                   # the PUS source id
    with pytest.raises(pus_auth.AuthenticationError):
        pus_auth.verify(bytes(packet), key=KEY)


def test_a_sequence_change_breaks_the_mac():
    """So a recording cannot be replayed by advancing the counter - EX-L01's attack, one layer up."""
    v = _vectors()[1]
    packet = bytearray(bytes.fromhex(v["packet"]))
    packet[-pus_auth.MAC_LEN - 1] ^= 0x01
    with pytest.raises(pus_auth.AuthenticationError):
        pus_auth.verify(bytes(packet), key=KEY)


def test_stripping_the_trailer_breaks_the_mac():
    """The length field is rewritten BEFORE the MAC is computed, so shortening it is detected.

    Without that ordering an attacker could cut the trailer off, put the length field back, and
    hand the OBC a packet that looks like it was never signed - which on a build that requires
    authentication is a refusal, and on a mixed fleet is an accepted command.
    """
    v = _vectors()[1]
    inner = bytes.fromhex(v["inner"])
    with pytest.raises(pus_auth.AuthenticationError):
        pus_auth.verify(inner + bytes(pus_auth.TRAILER_LEN), key=KEY)
