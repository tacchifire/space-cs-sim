---
id: EX-L03
title: The pass that never happened
layer: space link
difficulty: advanced
duration: 30-45 min
prerequisite: EX-L02
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-L03 — The pass that never happened

## The situation

EX-L02 ended by naming the case its own fix cannot reach:

> **Total denial.** If nothing arrives, there are no gaps and nothing to ask about. A silent pass
> is not a gap, and noticing it needs a schedule.

A counter gap needs two reports for it to sit between. Deny everything and the operator's console
shows an empty list of gaps — which is exactly what a perfect pass shows.

## Your objective

The attacker denies the whole pass. Say so.

```bash
make exercise EX=EX-L03-the-pass-that-never-happened          # a shell inside the range
python3 exercises/EX-L03-the-pass-that-never-happened/solve.py   # at that shell's prompt
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-L03-the-pass-that-never-happened RUN='python3 exercises/EX-L03-the-pass-that-never-happened/solve.py'
```

## What happened

Four runs, and the fourth is the one to sit with:

```text
beacon   attacker    heard   counter gaps   schedule says
yes      none         36        []          ok
yes      all           0        []          silent
no       all           0        []          silent
no       none          0        []          silent      <- nobody attacked anything
```

**The counter gaps are empty in every row.** Total denial makes no gaps, and neither does a
perfect pass, and neither does a spacecraft with nothing to say.

The schedule separates the first two. It does not separate the last two, and that is the finding.

## What to conclude

**A schedule needs something to expect.** On the build with no beacon, the spacecraft speaks only
when spoken to — so silence is its normal condition, the window fires every time, and an operator
who followed this procedure would open an investigation on every quiet pass until they stopped
following it.

The ground-side control and the spacecraft-side behaviour are **one mitigation, not two**. That is
EX-D02's sentence again, from the other side: there the detector failed *open* because its input
was forgeable; here it fails *closed* because its input does not exist. Both are the same mistake,
which is building a detector without asking what it is standing on.

**And notice how little the working version buys.** A silent window tells you that you heard
nothing when you expected to. It does not tell you whether the spacecraft was denied, was off, was
pointed the wrong way, or whether the prediction was wrong. It converts "I know nothing" into "I
know I heard nothing when I should have" — a smaller step than it feels, and the step that gets
somebody to look.

`mitigation.md` is the beacon, what it costs, and the clock problem underneath all of it.
