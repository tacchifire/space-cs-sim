"""The SDLS key, in the two places it has to live, asserted equal.

CMake and Python cannot read one source, so the key is written twice - `src/cuberange/keys.py`
and `firmware/common/cuberange_keys.h` - exactly as the CSP addresses are. A divergence there
produces a spacecraft that refuses every telecommand and a ground station that cannot tell a
wrong key from a wrong MAC from a frame that never arrived, which is EX-G04's problem with a
cryptographic layer on top.

So the two copies are compared here, by parsing the header rather than by trusting a comment.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HEADER = REPO / "firmware" / "common" / "cuberange_keys.h"

from cuberange.keys import (SDLS_KEY, SDLS_KEY_PARTNER, SDLS_KEY_ROTATED,   # noqa: E402
                            SDLS_SPI, SDLS_SPI_PARTNER, SDLS_SPI_ROTATED)


def _header_key(name: str = "cr_sdls_key") -> bytes:
    text = HEADER.read_text()
    m = re.search(rf"static const uint8_t {name}\[32\] = \{{(.*?)\}};", text, re.S)
    assert m, f"{name} is not in cuberange_keys.h in the shape this test parses"
    octets = re.findall(r"0x([0-9A-Fa-f]{2})", m.group(1))
    return bytes(int(o, 16) for o in octets)


def test_the_firmware_and_the_host_hold_the_same_key():
    assert _header_key() == SDLS_KEY, (
        f"the key differs.\n  header: {_header_key().hex()}\n  keys.py: {SDLS_KEY.hex()}")


def test_the_key_is_the_right_length_for_aes_256():
    assert len(SDLS_KEY) == 32
    assert len(_header_key()) == 32, "the header declares [32] but does not list 32 octets"


def test_the_firmware_and_the_host_agree_on_the_spi():
    text = HEADER.read_text()
    m = re.search(r"#define CR_SDLS_SPI\s+(\d+)", text)
    assert m, "CR_SDLS_SPI is not in cuberange_keys.h"
    assert int(m.group(1)) == SDLS_SPI


def test_the_key_says_what_it_is():
    """A teaching range's key should be unmistakable as one.

    Not decoration. SAFE_USE.md says anyone who reads the repository can forge an authenticated
    telecommand to these spacecraft; a key that looked random would invite somebody to treat it
    as a secret, and then to wonder whether this range is safe to point at something real.
    """
    assert b"not-real" in SDLS_KEY, (
        "the key no longer spells out that it is not a real one; keys.py explains why it should")


def test_the_firmware_and_the_host_hold_the_same_rotated_key():
    """The second association, checked the same way and for the same reason.

    A rotation whose two ends disagree about the new key is a spacecraft that refuses everything
    after the rotation and an operator whose only evidence is silence - which is the failure
    EX-G04 is about, arriving at the worst possible moment.
    """
    assert _header_key("cr_sdls_key_rotated") == SDLS_KEY_ROTATED, (
        f"the rotated key differs.\n  header: {_header_key('cr_sdls_key_rotated').hex()}"
        f"\n  keys.py: {SDLS_KEY_ROTATED.hex()}")
    assert len(SDLS_KEY_ROTATED) == 32


def test_the_firmware_and_the_host_agree_on_the_rotated_spi():
    m = re.search(r"#define CR_SDLS_SPI_ROTATED\s+(\d+)", HEADER.read_text())
    assert m, "CR_SDLS_SPI_ROTATED is not in cuberange_keys.h"
    assert int(m.group(1)) == SDLS_SPI_ROTATED


def test_the_firmware_and_the_host_hold_the_same_partner_key():
    """The partner association, checked the same way. EX-S04.

    The partner is a DIFFERENT party holding its own SA on the same link, and the exercise turns on
    its frames verifying while its authority does not extend to the operator's SA. If the two copies
    of this key disagreed, the partner's frames would fail the MAC and the exercise would measure a
    wrong key rather than a wrong authority - which are different lessons and different fixes.
    """
    assert _header_key("cr_sdls_key_partner") == SDLS_KEY_PARTNER, (
        f"the partner key differs.\n  header: {_header_key('cr_sdls_key_partner').hex()}"
        f"\n  keys.py: {SDLS_KEY_PARTNER.hex()}")
    assert len(SDLS_KEY_PARTNER) == 32


def test_the_firmware_and_the_host_agree_on_the_partner_spi():
    m = re.search(r"#define CR_SDLS_SPI_PARTNER\s+(\d+)", HEADER.read_text())
    assert m, "CR_SDLS_SPI_PARTNER is not in cuberange_keys.h"
    assert int(m.group(1)) == SDLS_SPI_PARTNER


def test_the_firmware_and_the_host_agree_on_the_sa_owners():
    """The owner tags decide EX-S04's authorisation, so a divergence is a spacecraft that authorises
    the wrong party. Compared by parsing the header, like the keys and SPIs above."""
    from cuberange.keys import SA_OWNER_OPERATOR, SA_OWNER_PARTNER
    text = HEADER.read_text()
    for name, want in (("CR_SDLS_OWNER_OPERATOR", SA_OWNER_OPERATOR),
                       ("CR_SDLS_OWNER_PARTNER", SA_OWNER_PARTNER)):
        m = re.search(rf"#define {name}\s+(\d+)", text)
        assert m, f"{name} is not in cuberange_keys.h"
        assert int(m.group(1)) == want


def test_the_three_associations_are_actually_different():
    """A rotation to the same octets, or to the same SPI, is not a rotation - and a partner that
    shares the operator's key or SPI is not a different party."""
    assert SDLS_KEY_ROTATED != SDLS_KEY
    assert SDLS_SPI_ROTATED != SDLS_SPI
    assert _header_key("cr_sdls_key_rotated") != _header_key("cr_sdls_key")
    assert len({SDLS_KEY, SDLS_KEY_ROTATED, SDLS_KEY_PARTNER}) == 3
    assert len({SDLS_SPI, SDLS_SPI_ROTATED, SDLS_SPI_PARTNER}) == 3
    assert len({_header_key("cr_sdls_key"), _header_key("cr_sdls_key_rotated"),
                _header_key("cr_sdls_key_partner")}) == 3
