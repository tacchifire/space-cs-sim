"""The host's idea of who is who must match the firmware's.

Two derivations of the same thing exist because CMake and Python cannot read one source:
`firmware/common/identity.cmake` compiles addresses into the images, and `cuberange.identity`
tells host tooling what to address. If they drift, every frame the ground sends is well-formed and
for the wrong node — no error, on either side.

So this test parses the CMake and checks it against the Python, rather than asserting the Python
against numbers typed here twice.
"""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange import identity  # noqa: E402

CMAKE = REPO / "firmware" / "common" / "identity.cmake"


def cmake_derivation() -> dict:
    """The arithmetic identity.cmake performs, read out of the file itself."""
    text = CMAKE.read_text()
    out = {}
    for m in re.finditer(r'math\(EXPR _cr_(\w+)\s+"([^"]+)"\)', text):
        out[m.group(1)] = m.group(2)
    return out


def test_the_cmake_still_derives_what_this_module_assumes():
    d = cmake_derivation()
    assert d["base"] == "${CUBERANGE_SAT_INDEX} * 8", (
        f"identity.cmake's stride changed to {d['base']!r}; cuberange.identity.STRIDE is "
        f"{identity.STRIDE} and the two now disagree")
    for role, offset in (("obc", 1), ("eps", 2), ("adcs", 4), ("comm", 5)):
        assert d[role] == "${_cr_base} + %d" % offset, (
            f"identity.cmake puts {role} at {d[role]!r}, cuberange.identity puts it at +{offset}")
    assert d["scid"].startswith("169 +"), (
        f"identity.cmake's SCID base is {d['scid']!r}, cuberange.identity uses "
        f"0x{identity.BASE_SCID:03X} = {identity.BASE_SCID}")
    assert identity.BASE_SCID == 169


def test_both_ends_refuse_the_same_range():
    text = CMAKE.read_text()
    assert "LESS 0" in text and "GREATER 3" in text, (
        "identity.cmake no longer bounds the satellite index at both ends; a negative index "
        "compiles negative CSP addresses into the firmware")
    for bad in (-1, identity.MAX_SATELLITES):
        with pytest.raises(ValueError, match="outside"):
            identity.spacecraft(bad)


def test_satellite_zero_keeps_the_numbers_everything_else_names():
    sat = identity.spacecraft(0)
    assert (sat.obc, sat.eps, sat.adcs, sat.comm) == (1, 2, 4, 5)
    assert sat.scid == 0x0A9 and sat.apid == 0x0A9


def test_satellite_one_shares_nothing_with_satellite_zero():
    a, b = identity.spacecraft(0), identity.spacecraft(1)
    assert a.scid != b.scid
    assert not ({a.obc, a.eps, a.adcs, a.comm} & {b.obc, b.eps, b.adcs, b.comm}), (
        "the two spacecraft share a CSP address, so a command for one is answered by the other")


def test_every_address_fits_in_five_bits():
    """CSP v1 masks with 0x1F. An address above 31 wraps onto another spacecraft's node."""
    for i in range(identity.MAX_SATELLITES):
        sat = identity.spacecraft(i)
        for role in ("obc", "eps", "adcs", "comm"):
            addr = sat.address(role)
            assert 0 <= addr <= 0x1F, f"sat{i} {role} is {addr}, which does not fit in CSP v1"


def test_no_two_spacecraft_collide_on_any_address():
    seen = {}
    for i in range(identity.MAX_SATELLITES):
        sat = identity.spacecraft(i)
        for role in ("obc", "eps", "adcs", "comm"):
            addr = sat.address(role)
            assert addr not in seen, f"sat{i} {role} collides with {seen[addr]} on address {addr}"
            seen[addr] = f"sat{i} {role}"
        assert sat.scid not in {s.scid for s in map(identity.spacecraft, range(i))}


def test_an_unknown_role_is_refused():
    with pytest.raises(ValueError, match="unknown node role"):
        identity.spacecraft(0).address("payload")
