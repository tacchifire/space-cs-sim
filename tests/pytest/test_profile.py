"""The execution profiles, and the rules they are supposed to carry.

Design section 9.2 states two rules as mandatory: G1, SetGlobalSerialExecution, and G2, a fixed
SetSeed. Before `scripts/profiles/` existed those two appeared nowhere but prose - every scenario
hard-coded the interactive pair, and `grep -rn SetGlobalSerialExecution` over the tree found only
the sentences claiming they were enforced (design section 16). The files now exist, and nothing
checked that they still say what the rules require.

A profile is a handful of lines. That is exactly why it can be edited without anyone noticing: a
dropped SetSeed does not fail a determinism run that happens not to depend on randomness, and the
run stays reproducible until the day it does not.
"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.renode.profile import (DEFAULT, KNOWN, PROFILE_DIR,   # noqa: E402
                                      active_profile, profile_args, profile_path)


def body(name: str) -> str:
    """The profile's COMMANDS, with comments and the :name:/:description: lines removed.

    Not the raw text. ci.resc explains in prose why it does not set AdvanceImmediately, and a
    naive search finds the word in that sentence and concludes the opposite of what the file
    does - which is what the first version of this file did.
    """
    lines = (PROFILE_DIR / f"{name}.resc").read_text().splitlines()
    return "\n".join(ln for ln in lines
                      if ln.strip() and not ln.lstrip().startswith(("#", ":")))


# --------------------------------------------------------------------------- selection

def test_an_unknown_profile_is_refused_rather_than_falling_back(monkeypatch):
    """The wrong failure would be silent: a typo running the fast, non-reproducible profile
    while the caller believed it had asked for determinism."""
    with pytest.raises(ValueError):
        profile_path("determinstic")          # a plausible typo
    monkeypatch.setenv("CUBERANGE_PROFILE", "nosuchprofile")
    with pytest.raises(ValueError):
        profile_path()


def test_the_environment_selects_the_profile(monkeypatch):
    monkeypatch.setenv("CUBERANGE_PROFILE", "ci")
    assert profile_path().name == "ci.resc"
    assert active_profile() == "ci"
    assert profile_path("interactive").name == "interactive.resc", (
        "an explicit argument must beat the environment, or a test cannot pin its own profile")


def test_the_default_is_the_interactive_one(monkeypatch):
    monkeypatch.delenv("CUBERANGE_PROFILE", raising=False)
    assert active_profile() == DEFAULT == "interactive"


def test_the_override_is_an_absolute_path():
    """Measured, and the reason this indirection exists at all: a relative `include` inside an
    included file resolves against Renode's working directory, not the including file."""
    args = profile_args("ci")
    assert args[0] == "-e"
    assert args[1].startswith("$profile=@/"), args[1]
    assert Path(args[1].split("@", 1)[1]).is_absolute()


def test_every_known_profile_exists_on_disk():
    for name in KNOWN:
        assert profile_path(name).is_file()


def test_no_profile_file_is_unknown_to_the_module():
    """A third .resc in that directory would be selectable by nobody and maintained by nobody."""
    on_disk = {p.stem for p in PROFILE_DIR.glob("*.resc")}
    assert on_disk == set(KNOWN), f"on disk {sorted(on_disk)}, known {sorted(KNOWN)}"


# --------------------------------------------------------------------------- the rules themselves

def test_the_ci_profile_carries_rules_g1_and_g2():
    text = body("ci")
    assert "SetGlobalSerialExecution true" in text, "G1 is gone from the ci profile"
    assert "SetSeed" in text, "G2 is gone from the ci profile"
    seed = [ln for ln in text.splitlines() if "SetSeed" in ln][0]
    assert seed.split()[-1].isdigit(), f"the seed is not a fixed number: {seed!r}"


def test_the_ci_profile_does_not_race_ahead():
    """SetGlobalAdvanceImmediately removes the real-time throttle and with it reproducibility."""
    assert "AdvanceImmediately" not in body("ci"), (
        "the ci profile advances immediately, so it is the interactive profile wearing another "
        "name and `make determinism` is measuring nothing")


def test_the_interactive_profile_is_the_fast_one():
    assert "AdvanceImmediately true" in body("interactive")
    assert "SetSeed" not in body("interactive"), (
        "the interactive profile pins a seed, which makes the two profiles harder to tell apart "
        "than the design says they are")


@pytest.mark.parametrize("name", KNOWN)
def test_both_profiles_pin_the_measured_quantum(name):
    """2 ms is the largest quantum with byte-identical firmware output, and it is pinned in
    CLAUDE.md. Above it, timing drifts silently and duration-dependently."""
    assert 'SetGlobalQuantum "0.002"' in body(name), (
        f"the {name} profile no longer sets the pinned 2 ms quantum")


@pytest.mark.parametrize("name", KNOWN)
def test_a_profile_sets_nothing_a_scenario_would_have_to_undo(name):
    """A profile is included FIRST, so anything it starts is hard to take back."""
    text = body(name)
    for forbidden in ("mach create", "start", "sysbus LoadELF", "include"):
        assert forbidden not in text, (
            f"the {name} profile issues `{forbidden}`, which belongs to the scenario")
