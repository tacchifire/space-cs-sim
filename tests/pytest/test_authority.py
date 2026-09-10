"""The ground segment's authorisation matrix, and what it is not.

EX-G02's whole lesson is that this module is bookkeeping: it decides what a console offers, not
what the spacecraft will do. The tests below are about the bookkeeping being correct, and one of
them is about the lesson - a matrix that quietly permitted the thing the exercise calls
unauthorised would make EX-G02 measure nothing, and it would still pass its Renode assertions,
because the spacecraft is not consulting this file either way.

Until now this module was exercised only through EX-G02's emulator run, where a mistake here
would surface as a firmware that refused the wrong station.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs import authority                      # noqa: E402
from cuberange.identity import GROUND_STATIONS          # noqa: E402


def test_the_matrix_is_internally_consistent():
    authority.validate()


def test_two_grants_for_one_pair_are_refused():
    """Which row wins would be a coin toss, and the losing row would look effective."""
    doubled = list(authority.MATRIX) + [authority.Grant("backup", 0, frozenset({"power"}))]
    with pytest.raises(authority.AuthorityError):
        authority.validate(doubled)


def test_a_grant_naming_an_unknown_station_is_refused():
    with pytest.raises(authority.AuthorityError):
        authority.Grant("nosuchstation", 0, frozenset({"ping"}))


def test_a_grant_naming_an_unknown_action_is_refused():
    """A typo that silently meant "no" would be a matrix quietly withdrawing permission."""
    with pytest.raises(authority.AuthorityError):
        authority.Grant("primary", 0, frozenset({"powr"}))


def test_an_unknown_action_is_refused_at_the_query_too():
    with pytest.raises(authority.AuthorityError):
        authority.may("primary", 0, "powr")
    with pytest.raises(authority.AuthorityError):
        authority.may("nosuchstation", 0, "ping")


def test_the_matrix_says_what_ex_g02_depends_on():
    """The exercise's premise, asserted here as well as in its own verification.

    If someone grants the backup station power, EX-G02's Renode assertions still pass - the
    vulnerable spacecraft obeys everyone and the mitigated one obeys the table it was compiled
    with - and the exercise silently stops being about an unauthorised command.
    """
    assert authority.may("primary", 0, "power")
    assert not authority.may("backup", 0, "power")
    assert authority.may("backup", 0, "observe")
    assert authority.may("backup", 0, "ping")
    assert authority.stations_permitted(0, "power") == ("primary",)


def test_a_station_with_no_grant_for_a_satellite_may_do_nothing_to_it():
    """Absence is denial, not silence. There is no satellite 1 row."""
    for action in sorted(authority.ACTIONS):
        assert not authority.may("primary", 1, action), (
            f"primary may {action} satellite 1 with no grant for it")


def test_station_id_matches_the_identity_module():
    """The number the spacecraft compares against comes from one place."""
    for name, value in GROUND_STATIONS.items():
        assert authority.station_id(name) == value
    with pytest.raises(authority.AuthorityError):
        authority.station_id("nosuchstation")


def test_every_station_in_the_matrix_exists_as_an_identity():
    """A grant for a station the spacecraft has no id for is a row that can never match."""
    for grant in authority.MATRIX:
        assert grant.station in GROUND_STATIONS


def test_the_module_says_out_loud_that_it_is_not_access_control():
    """The docstring is load-bearing here.

    Somebody reading only the code would see a permission check and reasonably assume it enforces
    something. It does not, and EX-G02 exists because that assumption is the vulnerability.
    """
    doc = authority.__doc__ or ""
    assert "bookkeeping" in doc.lower()
    assert "not access control" in doc.lower()
