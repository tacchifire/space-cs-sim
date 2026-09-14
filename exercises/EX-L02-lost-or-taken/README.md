---
id: EX-L02
title: Lost, or taken
layer: space link
difficulty: advanced
duration: 45-60 min
prerequisite: EX-D02
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-L02 — Lost, or taken

## The situation

EX-D02 built a detector and its own write-up named the thing it had never met:

> **False positives on a real link.** A lossy channel drops frames, and a real operator would see
> gaps that no attacker caused. This range has no bit errors, so every gap here is somebody's
> decision.

The channel can lose frames now. `frame_loss` drops downlink frames at random, independently,
with a fixed seed — which is not a radio model and does not pretend to be one: no modulation, no
coding, no burst structure. It is enough to ask the question EX-D02 could not.

Twenty commands over a link that loses one frame in ten:

```text
no attacker at all              gaps [(11,13,1), (16,18,1)]              missing 2
one report taken as well        gaps [(3,5,1), (11,13,1), (16,18,1)]     missing 3
```

The detector is correct in both runs. It has simply stopped answering the question.

## Your objective

Three reports are missing. **Say which of them somebody took.**

```bash
make exercise EX=EX-L02-lost-or-taken          # a shell inside the range
python3 exercises/EX-L02-lost-or-taken/solve.py   # at that shell's prompt
```text

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-L02-lost-or-taken RUN='python3 exercises/EX-L02-lost-or-taken/solve.py'
```

## What happened

```text
link lost 2, attacker took 1
operator sees gaps: [(3,5,1), (11,13,1), (16,18,1)]   (missing 3)
asked for [4, 12, 17]
the spacecraft resent [12, 17]
never came back: [4]
```

Ask for all three. Two come back — those were the link. One never does, however many times you
ask, because the thing taking it takes it again. **The one that never comes back is the attack.**

## What to conclude

**A gap is a question, and a detector that cannot ask it is producing numbers.** EX-D02's counter
told the operator that something was missing. On a clean link that was the whole answer; on a real
one it is the start of one. What finishes it is a channel back to the spacecraft and a spacecraft
that can answer.

**Retransmission is a security control here, and that is not where anyone files it.** A report
buffer is ordinarily a reliability feature - reviewed for size, for overflow, for whether the
counter wraps. It is the only thing in this range that can tell an attack apart from weather.

**And notice what the vulnerable half does, because it is worse than nothing.** With no store, you
ask for all three and get silence for all three:

```text
the spacecraft resent []
never came back: [4, 12, 17]
```

An operator following the same procedure now concludes that three reports were taken. The
procedure did not fail safe; it failed confident. A detection step whose negative result is
indistinguishable from "I cannot check" is a step that manufactures findings.

`mitigation.md` is the buffer, what it costs, and where asking stops working.
