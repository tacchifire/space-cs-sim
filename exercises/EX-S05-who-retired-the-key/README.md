---
id: EX-S05
title: Who retired the key
layer: space-link
difficulty: advanced
duration: 30-45 min
prerequisite: EX-S04
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-S05 — Who retired the key

## The situation

EX-S04 stopped an unauthorised retirement, and its `mitigation.md` ended on the gap it did not
close: **from the ground, a stolen retirement and the operator's own rotation are the same event.**
Both are SPI 9 going dark. The only record of *who* retired the association is a line on the COMM
console — `by SPI 11` versus `by SPI 10` — and that console never leaves the spacecraft. An operator
who finds their key retired cannot tell, from anything they can see, whether they did it or an
adversary did.

This is the detection half. It does not re-fight EX-S04's prevention: **authority is deliberately
OFF here in both builds**, so a partner really can retire the operator's SA — which is the point,
because a retirement has to happen for there to be anything to attribute. What differs is whether
the spacecraft **reports who did it**. The report rides EX-D01's signed beacon, so the attribution
is authenticated rather than asserted, and the requester SPI is what the SDLS MAC *proved*.

## Your objective

Retire the operator's SPI 9 — as the partner, then as the operator — and read the beacon. **Show
that only the reporting build lets the ground name who did it.**

```bash
make exercise EX=EX-S05-who-retired-the-key          # a shell inside the range
python3 exercises/EX-S05-who-retired-the-key/solve.py   # at that shell's prompt
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has to
run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To retire it as the operator instead of the partner — the rotation that should read as *self* —
pass `--legitimate`:

```bash
make exercise EX=EX-S05-who-retired-the-key RUN='python3 exercises/EX-S05-who-retired-the-key/solve.py --legitimate'
```

## What happened

```text
build         STOP_SA sent by        SPI 9 after   beacon: retired SA   beacon: retired by
vulnerable    partner  (SPI 11)          dark          (nothing)            (nothing)
vulnerable    operator (SPI 10)          dark          (nothing)            (nothing)
hardened      partner  (SPI 11)          dark          SPI 9                SPI 11
hardened      operator (SPI 10)          dark          SPI 9                SPI 10
```

**The two vulnerable rows are the same observation.** A theft by the partner and a rotation by the
operator both leave SPI 9 dark and the beacon empty. Nothing distinguishes them from the ground,
which is EX-S04's blind spot exactly: the retirement happened, the console recorded who, and the
operator will never see it.

**The hardened rows separate them, and the separating value is the requester SPI.** `retired by SPI
11` is a partner who was never the operator; `retired by SPI 10` is the operator's own second key.
The operator reads the beacon and knows which of the two just happened — the one thing the console
knew all along and could not say.

**The fourth row is why the mitigation had to name the *actual* retirer, not just raise an alarm.**
A report that flagged every retirement as suspicious would be useless the first time the operator
rotated a key on purpose. Attribution that says *SPI 10 — you* is what makes it a control rather
than a smoke detector wired to the doorbell.

## What to conclude

**A control that works has to be heard working.** This is EX-G04 and EX-U03's lesson, arriving for
SA management. EX-S04's authorisation genuinely refuses the wrong party — but a refusal, or a
retirement, that reaches only a console nobody reads is a decision the operator cannot act on.
Attribution is the difference between "SPI 9 stopped answering" and "SPI 11 retired SPI 9".

**Attribution is a name bound to a proof, not a label.** The requester SPI on the beacon is the SA
whose key verified the directive's MAC — possession the attacker had to have, not a field they
filled in. And the beacon carries it under EX-D01's signature, so the report itself cannot be forged
or suppressed by an attacker who does not hold the downlink key. An unauthenticated attribution
would be EX-D02's detector all over again: a witness whose testimony the accused writes.

`mitigation.md` is the beacon field, why the length is the feature test, why the report leans on
EX-D01 rather than signing itself, and the one thing it still cannot do — carry more than the *last*
retirement, so a burst of SA-management events collapses to one, exactly as EX-S03's refused-SPI
does.
