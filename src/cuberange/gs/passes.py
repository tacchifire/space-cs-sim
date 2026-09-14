"""What the ground expected to hear, and when — the PASS schedule, not the command schedule.

Named `passes` and not `schedule` because `gs/schedule.py` already existed and holds EX-G01's
command plan store. The first version of this file was written straight over it, and what caught
that was an ImportError from EX-G01's own tests rather than anything in the new code. A module
name is a namespace with one occupant.

EX-D02 built a detector on the report counter and EX-L02 taught it to tell loss from denial. Both
need two reports for a gap to sit between. An attacker who denies EVERYTHING produces a link with
no gaps in it at all, and EX-L02's own write-up said where that leaves you:

    "Total denial. If nothing arrives, there are no gaps and nothing to ask about. A silent pass
     is not a gap, and noticing it needs a schedule."

This is the schedule. It is the only thing in this range that lives entirely on the ground and
knows something the spacecraft cannot tell it: WHEN IT SHOULD HAVE BEEN TALKING.

WHAT A PASS IS HERE. A window with a start and an end, and an expectation of how often the
spacecraft speaks inside it. Real pass prediction is orbital mechanics against a station mask and
this is not that - there is no propagation, no elevation, no horizon (SAFE_USE.md lists what the
channel does not model). A window here is a decision somebody wrote down, which is also what a
pass plan is by the time an operator reads one.

AND A CLOCK WARNING, because it bit this file. A rate-based expectation - "a beacon every two
seconds" - is a statement in the spacecraft's clock, checked in the operator's. In this range
those run at different speeds: the execution profiles let emulated time advance as fast as the
host allows, so a 2000 ms beacon period was measured arriving every ~400 ms of wall clock, 52
transmissions in a window whose rate predicted 10. Use `expect_at_least` and ask the question that
survives the disagreement. A real mission reconciles the two clocks deliberately; that is what
time correlation is for, and this range does not implement it.

WHAT IT BUYS, precisely, because it is less than it sounds. A silent window tells you that you
heard nothing when you expected to. It does not tell you whether the spacecraft was denied, was
off, was pointed the wrong way, or whether the prediction was wrong. It converts "I know nothing"
into "I know I heard nothing when I should have", and that is a smaller step than it feels - and
it is the step that gets somebody to look.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Pass:
    """One expected contact window.

    `expect_every_s` is how often the spacecraft is expected to say something unprompted - its
    beacon period. Zero means "no expectation", and a window with no expectation cannot be silent
    in any interesting sense: a spacecraft that only speaks when spoken to is quiet by design.
    """

    name: str
    start: float
    end: float
    expect_every_s: float = 0.0
    #: How many transmissions this window must carry, overriding the rate. Set this when the two
    #: clocks do not agree - which in this range they do not, and that is worth stating rather
    #: than working around: the execution profiles advance emulated time as fast as the host
    #: allows (design 4.3, rule G1), so a spacecraft's 2000 ms beacon arrives every ~400 ms of the
    #: operator's. Measured: 52 beacons in a 20-second window whose rate predicted 10.
    #:
    #: A rate-based expectation needs a clock both ends agree on. A real mission has one - that is
    #: what time correlation is for - and this range does not, so the exercise built on this asks
    #: the question that survives the disagreement: was there ANYTHING?
    expect_at_least: int = 0

    def contains(self, when: float) -> bool:
        return self.start <= when < self.end

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def expected(self) -> int:
        """How many unprompted transmissions this window should have carried."""
        if self.expect_at_least:
            return self.expect_at_least
        if self.expect_every_s <= 0:
            return 0
        return int(self.duration // self.expect_every_s)


@dataclass
class PassLog:
    """What actually arrived, judged against what was expected."""

    heard: list = field(default_factory=list)          #: timestamps

    def record(self, when: float | None = None) -> None:
        self.heard.append(time.time() if when is None else when)

    def in_window(self, window: Pass) -> int:
        return sum(1 for t in self.heard if window.contains(t))

    def verdict(self, window: Pass, *, tolerance: float = 0.5) -> str:
        """One of "ok", "quiet", "silent" - and the three are different findings.

        "silent" means nothing at all arrived in a window where something was expected. That is the
        one worth waking somebody for, and it is the only one a counter-gap detector cannot see.

        "quiet" means less arrived than expected, by more than `tolerance` of the expectation. On a
        lossy link that happens without an attacker, which is EX-L02's whole subject - so this is
        a prompt to ask, not a finding.

        `tolerance` defaults to half: fewer than half the expected transmissions is worth a
        sentence, and a window that merely ran short is not. The number is a judgement and is
        written down here rather than buried, because an operator who cannot see the threshold
        cannot argue with it.
        """
        if window.expected == 0:
            return "ok"
        got = self.in_window(window)
        if got == 0:
            return "silent"
        if got < window.expected * tolerance:
            return "quiet"
        return "ok"
