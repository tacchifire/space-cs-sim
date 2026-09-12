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

from cuberange.keys import SDLS_KEY, SDLS_SPI   # noqa: E402


def _header_key() -> bytes:
    text = HEADER.read_text()
    m = re.search(r"static const uint8_t cr_sdls_key\[32\] = \{(.*?)\};", text, re.S)
    assert m, "cr_sdls_key is not in cuberange_keys.h in the shape this test parses"
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
