---
id: EX-S06
title: The retirement before yours
layer: space-link
difficulty: advanced
duration: 30-45 min
prerequisite: EX-S05
ttp:
  sparta: []
  space_shield: []
---

# EX-S06 — The retirement before yours

## The situation

EX-S05 gave the ground a name for a retirement: the beacon now says *who* retired *which* SA. But
its `mitigation.md` ended on the limit this exercise takes — it carries only the **last** one. A
single value, overwritten every time COMM records a new SA-management event, exactly the
single-value limit EX-S03's refused-SPI has, one field over.

This is a **detection** exercise, like EX-S05. **Authority is held OFF in both builds on purpose**,
so the retirements land and there is a sequence to record. What differs is only whether COMM reports
the whole SA-management **log** or only its most recent entry.

The sequence is two retirements of the **same** association, SPI 9:

1. a **partner** (SPI 11) sends `STOP_SA(9)` — a theft; the operator's primary SA goes dark;
2. the **operator** (SPI 10) sends `STOP_SA(9)` — a planned rotation that formally retires the same
   SPI, arriving *after* the theft.

## Your objective

Send the two retirements, read the beacon, and show that **only the build that reports the log lets
the ground see the theft the operator's own retirement overwrote**.

```bash
make exercise EX=EX-S06-the-retirement-before-yours          # a shell inside the range
python3 exercises/EX-S06-the-retirement-before-yours/solve.py   # at that shell's prompt
```

`make exercise` drops you into a shell **inside the range's network namespace**, and the solver runs
from there. The namespace is loopback-only (SAFE_USE.md) and created fresh per invocation, so a
solver launched in another terminal gets `ConnectionRefusedError`.

## What happened

```text
build        SA-management events (in order)          beacon carries
vulnerable   SPI 9 by SPI 11,  then SPI 9 by SPI 10   SPI 9 by SPI 10           (last only)
hardened     SPI 9 by SPI 11,  then SPI 9 by SPI 10   SPI 9 by SPI 11;
                                                      SPI 9 by SPI 10           (both, oldest first)
```

**The vulnerable row is the finding.** Both retirements happened — the COMM console records both —
but the beacon carries only the last. The theft by SPI 11 is not merely lost: the surviving entry
attributes SPI 9 to the operator's own SPI 10, so from the ground it is a **benign self-rotation**.
An attacker who retires an SA the operator is about to retire anyway is invisible, and the record
that would have caught them is overwritten by the operator's own legitimate action.

**The hardened row separates them, and the separating value is the order.** `SPI 9 by SPI 11` is the
first entry — the one the snapshot overwrote — and `SPI 9 by SPI 10` is the operator's own. The
operator reads the log and sees a partner got there first, which is a sign the key is compromised
that the snapshot could never show.

**Runs 3 and 4 are why the mitigation has to be a log and not an alarm.** A single legitimate
retirement logs exactly one entry, and a spacecraft that retired nothing sends the short beacon with
no log at all — a report that always showed two entries, or padded one in, would pass run 2 and be
useless.

## In conclusion

**A control that reports has to report the sequence, not just the latest state.** This is EX-S05's
attribution arriving at its own limit: attribution of *only the last* event is defeated by a second
event, and here the second event is the operator's own routine rotation, so the defeat looks like
normal operations. The fix is the log — the same shape as a real mission's SA-management audit
trail, which this range had only ever kept as a snapshot.

**Each entry is a name bound to proof, not a label.** The requester SPI in every log entry is the SA
whose MAC verified the directive — possession, not a header — and the beacon is carried under
EX-D01's signature, so a keyless attacker can neither forge nor suppress the log.

`mitigation.md` states the log's shape, why its length is the feature test, why it rides EX-D01
rather than signing itself, and what it still does **not** solve — the ring is bounded, so a sequence
longer than it still loses its oldest events, the single-value limit's descendant one size up.
