---
id: EX-D02
title: The report that never came
layer: downlink
difficulty: advanced
duration: 30-45 min
prerequisite: EX-D01
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-D02 — The report that never came

## The situation

EX-D01 ended by naming what its own fix does not cover:

> Authentication makes a forged report detectable. It does nothing about a real report that never
> arrives, which is EX-G04's problem and is still there: the ground cannot tell a suppressed
> refusal from a command that was accepted. A signed report proves what the spacecraft said; it
> cannot prove the spacecraft said nothing.

The spacecraft signs its reports. The operator verifies them. And an attacker who takes one away
leaves an operator with a shorter list and no reason to think anything is missing.

## Your objective

This one is different from every other exercise here. **You are not asked to break anything.**

Three commands are sent. Some number of reports come back. Decide — from what the operator's
console can see — whether the spacecraft answered all three.

```bash
make exercise EX=EX-D02-the-report-that-never-came          # a shell inside the range
python3 exercises/EX-D02-the-report-that-never-came/solve.py --drop &
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-D02-the-report-that-never-came RUN='python3 exercises/EX-D02-the-report-that-never-came/solve.py --drop'
```

The solver sits between the operator and the spacecraft and takes one report. That position is
not invented for the exercise: it is where a compromised ground-segment front end sits, which is
the same supply-chain position EX-G01 attacks. A jammer has less precision; a compromised ground
segment has more.

## What happened

Two configurations, same attack:

```
station requiring signed telemetry     gaps [(0, 2, 1)]   reports missing: 1
station not requiring it, --replace    gaps []            reports missing: 0
```

The counter the spacecraft puts in every report is what makes the first line possible. The second
line is the same attacker, in the same place, with the station's signature check turned off — they
take the report AND put one back carrying the counter value the missing one would have had. The
hole closes.

## What to conclude

**The gap check and the signature check are not two mitigations. They are one.**

A detector whose input the attacker controls detects nothing. The report counter is a good signal
precisely and only while an attacker cannot write it — which is what the trailer buys, and which
is why this exercise's fix is not "also check the counter" but "check the counter *on telemetry
you have authenticated*".

This is worth stating in the general form, because it is not about telemetry. **Detection built
on unauthenticated evidence is decoration.** Log lines an attacker can write, counters an attacker
can set, heartbeats an attacker can send: each one looks like a control and is a place to look
while nothing is being seen.

**And what remains, because something always does.** Authenticated counters tell you a report is
missing. They do not tell you what was in it. An operator who sees a gap knows only that the
spacecraft said something they did not hear — which is better than not knowing, and is not the
same as knowing.

`mitigation.md` is the fix, what it costs, and the part of this nothing fixes.
