"""Which node is which, on which spacecraft — the host-side half of identity.cmake.

The firmware derives its CSP addresses and its SCID from `CUBERANGE_SAT_INDEX`. Until this module
existed the host did not: `cuberange.proto.csp` exposed `ADDR_OBC = 1`, `ADDR_EPS = 2` and so on as
bare constants, every solver imported them, and pointing any of that tooling at spacecraft 1 would
have addressed spacecraft 0's nodes instead. Silently — the frames are well-formed, they are simply
for somebody else.

The derivation is duplicated rather than shared because CMake and Python cannot read one source,
and `test_identity.py` asserts the two agree by parsing identity.cmake. A divergence is then a test
failure rather than a range that answers from the wrong satellite.

    satellite 0    OBC 1   EPS 2   ADCS 4   COMM 5    SCID/APID 0x0A9
    satellite 1    OBC 9   EPS 10  ADCS 12  COMM 13   SCID/APID 0x0AA
"""
from __future__ import annotations

from dataclasses import dataclass

STRIDE = 8
MAX_SATELLITES = 4          # CSP v1 addresses are five bits: 4 x 8 = 32
BASE_SCID = 0x0A9

_OFFSETS = {"obc": 1, "eps": 2, "adcs": 4, "comm": 5}

#: Ground stations, by name, as they appear in the PUS TC secondary header's source id.
#:
#: This lived as a bare `GROUND_SOURCE_ID = 0x0042` in `gs/station.py` and was copy-pasted into two
#: exercise files - three spellings of one number, which is how `ports.py` and `paths.py` each
#: started. It belongs here, beside the spacecraft identities, and `test_identity.py` compares it
#: against identity.cmake the same way.
#:
#: Unlike spacecraft identity these do not vary by index: a ground station is a ground station
#: whichever satellite it is talking to. The spacecraft is what decides whether it may.
GROUND_STATIONS = {"primary": 0x0042, "backup": 0x0043}

#: The one every existing scenario, exercise and write-up means when it says "the ground station".
GROUND_SOURCE_ID = GROUND_STATIONS["primary"]


@dataclass(frozen=True)
class Spacecraft:
    """Every identifier that distinguishes one spacecraft from another."""

    index: int
    obc: int
    eps: int
    adcs: int
    comm: int
    scid: int

    @property
    def apid(self) -> int:
        """The OBC's APID. Equal to the SCID by construction - see identity.cmake."""
        return self.scid

    def address(self, role: str) -> int:
        try:
            return getattr(self, role)
        except AttributeError:
            raise ValueError(f"unknown node role {role!r}; known roles are {sorted(_OFFSETS)}")


def spacecraft(index: int = 0) -> Spacecraft:
    """The identity of spacecraft `index`, matching what identity.cmake compiles in.

    Out-of-range indices raise. identity.cmake refuses them too, and it refuses BOTH ends: an
    earlier version checked only the upper bound, so index -1 compiled OBC -7 into the firmware
    and let it reach libcsp through an implicit conversion.
    """
    if not 0 <= index < MAX_SATELLITES:
        raise ValueError(
            f"satellite index {index} is outside 0..{MAX_SATELLITES - 1}. CSP v1 addresses are "
            f"five bits wide, so {STRIDE} per spacecraft leaves room for {MAX_SATELLITES}.")
    base = index * STRIDE
    return Spacecraft(index=index, scid=BASE_SCID + index,
                      **{role: base + off for role, off in _OFFSETS.items()})


SAT0 = spacecraft(0)
