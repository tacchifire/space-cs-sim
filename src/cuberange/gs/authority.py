"""Which ground station may ask which spacecraft to do what.

READ THIS BEFORE TRUSTING IT. Everything in this module is **bookkeeping**. It governs what an
operator at a console is offered and what the ground segment will transmit on its own initiative.
It is not access control, because nothing here is on the spacecraft, and the spacecraft is what
executes the command.

That distinction is EX-G02's whole subject, and it is the limit EX-G01's mitigation.md predicted:

    "It does not touch the operator. The policy governs imports. An operator console can still
     schedule anything ... That is the next exercise, not this one."

A matrix like this one is worth having - it stops the ordinary mistake, which is a rushed operator
on the wrong console. It stops nothing at all about an attacker on the link, a compromised backup
station, or a second ground segment nobody told you about. For those, the check has to be where
the authority is: on board, keyed on the source id the telecommand already carries. See
`firmware/apps/obc/src/main.c` under CUBERANGE_OBC_REQUIRE_AUTHORITY.

The vocabulary - ping, power, observe - is deliberately the same three actions the parallel
manifest work uses, so the two can converge without a rename.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Iterable

from ..identity import GROUND_STATIONS

#: Everything a ground station can ask for. An action outside this set is a typo, and a typo that
#: silently means "no" is a matrix that quietly stops authorising things.
ACTIONS: FrozenSet[str] = frozenset({"ping", "power", "observe"})


class AuthorityError(ValueError):
    """A grant names a station, satellite or action that does not exist."""


@dataclass(frozen=True)
class Grant:
    """One row: this station may do these things to this spacecraft."""

    station: str
    satellite: int
    actions: FrozenSet[str]

    def __post_init__(self) -> None:
        if self.station not in GROUND_STATIONS:
            raise AuthorityError(
                f"unknown ground station {self.station!r}; known: {sorted(GROUND_STATIONS)}")
        unknown = self.actions - ACTIONS
        if unknown:
            raise AuthorityError(f"unknown actions {sorted(unknown)}; known: {sorted(ACTIONS)}")


#: The range's own matrix. The backup station may look and may not touch - which is a perfectly
#: ordinary arrangement, and exactly the one the spacecraft was not enforcing.
MATRIX = (
    Grant("primary", 0, frozenset({"ping", "power", "observe"})),
    Grant("backup",  0, frozenset({"ping", "observe"})),
)


def may(station: str, satellite: int, action: str) -> bool:
    """Whether the matrix permits it. Not whether the spacecraft would refuse."""
    if action not in ACTIONS:
        raise AuthorityError(f"unknown action {action!r}; known: {sorted(ACTIONS)}")
    if station not in GROUND_STATIONS:
        raise AuthorityError(f"unknown ground station {station!r}")
    return any(g.station == station and g.satellite == satellite and action in g.actions
               for g in MATRIX)


def station_id(station: str) -> int:
    """The source id this station puts in the telecommand's secondary header."""
    try:
        return GROUND_STATIONS[station]
    except KeyError:
        raise AuthorityError(f"unknown ground station {station!r}") from None


def stations_permitted(satellite: int, action: str) -> tuple[str, ...]:
    return tuple(sorted(s for s in GROUND_STATIONS if may(s, satellite, action)))


def validate(matrix: Iterable[Grant] = MATRIX) -> None:
    """Refuse a matrix with two rows for one pair. Which row wins would be a coin toss."""
    seen = set()
    for g in matrix:
        key = (g.station, g.satellite)
        if key in seen:
            raise AuthorityError(f"two grants for {g.station} -> satellite {g.satellite}")
        seen.add(key)
