"""Which execution profile a Renode launch runs under.

Design section 4.3 defines two profiles and section 9.2 makes two of them mandatory rules:

  interactive   quantum 2 ms + SetGlobalAdvanceImmediately.   Fast (4.50x real time measured on an
                8-core x86-64 host with four nodes), NOT bit-reproducible.
  ci            quantum 2 ms + SetGlobalSerialExecution + a fixed SetSeed.   Exactly 1.0x real time,
                bit-reproducible in the UART capture.

Neither was selectable before: every scenario hard-coded the interactive pair, so G1
(SetGlobalSerialExecution) and G2 (SetSeed) governed nothing. `grep -rn SetGlobalSerialExecution`
over the tree found them only in prose.

Renode's Monitor has no conditionals, so a profile is a file included by the scenario. The path has
to be absolute: a relative `include` inside an included file resolves against Renode's own working
directory, not the including file - measured, `include @sub/inner.resc` from a script in /tmp
reports "File does not exist: sub/inner.resc". That is why this returns a `-e` override rather than
letting the scenario's default do the work.

Select with CUBERANGE_PROFILE=ci, or pass profile= explicitly.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PROFILE_DIR = REPO / "scripts" / "profiles"

DEFAULT = "interactive"
KNOWN = ("interactive", "ci")


def profile_path(profile: str | None = None) -> Path:
    """Absolute path to the profile script. Raises on an unknown name rather than falling back.

    Falling back would be the wrong failure: a typo'd profile would silently run the fast,
    non-reproducible one while the caller believed it had asked for determinism.
    """
    name = profile or os.environ.get("CUBERANGE_PROFILE", DEFAULT)
    if name not in KNOWN:
        raise ValueError(f"unknown execution profile {name!r}; known profiles are {KNOWN}")
    path = PROFILE_DIR / f"{name}.resc"
    if not path.is_file():
        raise FileNotFoundError(f"execution profile {name!r} is missing at {path}")
    return path


def profile_args(profile: str | None = None) -> list[str]:
    """The `-e` pair to place before the scenario's own include."""
    return ["-e", f"$profile=@{profile_path(profile)}"]


def active_profile(profile: str | None = None) -> str:
    return profile or os.environ.get("CUBERANGE_PROFILE", DEFAULT)
