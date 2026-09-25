---
id: EX-S04
title: A retirement you did not order
layer: space-link
difficulty: advanced
duration: 30-45 min
prerequisite: EX-S03
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-S04 — A retirement you did not order

## The situation

EX-S03 retired a key by *deactivating its Security Association*, and it did it by rebuilding the
firmware — the SA table was `const`, compiled in. Its `mitigation.md` said the next thing out loud:
a real mission deactivates an SA by **telecommand**, and *a telecommand that can deactivate the
operator's own SA is a denial of service with a valid MAC on it.* This exercise builds that
telecommand and points it at the operator.

SA management on the wire is **CCSDS 355.1 Extended Procedures**. This range implements one
directive, `STOP_SA`, carried as the authenticated payload of a transfer frame on a reserved
control virtual channel — so COMM, which holds the Security Associations and does not parse PUS,
acts on it itself.

You are not the operator. You are a **partner station**: a second party that legitimately holds its
own Security Association (SPI 11) on this space link, the way a cross-supported ground network does.
Your frames verify. You were never given the operator's key, and you will not need it.

## Your objective

**Retire the operator's key (SPI 9) with a directive it never authorised**, and show the operator
falls off the air — then look at the one build that refuses you.

```bash
make exercise EX=EX-S04-a-retirement-you-did-not-order          # a shell inside the range
python3 exercises/EX-S04-a-retirement-you-did-not-order/solve.py   # at that shell's prompt
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has to
run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To run the legitimate retirement instead — the operator retiring its own key, which the hardened
build allows — pass `--legitimate`:

```bash
make exercise EX=EX-S04-a-retirement-you-did-not-order RUN='python3 exercises/EX-S04-a-retirement-you-did-not-order/solve.py --legitimate'
```

## What happened

```text
build         STOP_SA sent by        SPI 9 before   SPI 9 after   last refused SPI   console says
vulnerable    partner  (SPI 11)           4              0               9            SPI 9 DEACTIVATED by SPI 11
hardened      partner  (SPI 11)           4              4              11            SA STOP REFUSED — SPI 11 may not retire SPI 9
hardened      operator (SPI 10)           4              0               9            SPI 9 DEACTIVATED by SPI 10
```

**Row 1 is the attack, and nothing about the frame is wrong.** It carries a valid MAC — the
partner really does hold SPI 11 — its anti-replay counter is fresh, and it names a directive the
spacecraft implements. The vulnerable build asks whether the frame is *authentic* and never whether
the sender is *authorised* over the SA it named, so it retires SPI 9. The operator's next commands
come back refused, `DEACTIVATED`. A party that was never given the operator's key just took the
operator off the air.

**Row 2 is the mitigation, and it refuses the directive rather than the operator.** The hardened
build checks that the retiring frame authenticated under an SA with the **same owner** as the one it
is retiring. SPI 11 is a different owner, so the STOP is refused — and the radio names **SPI 11**,
the party that overreached, not SPI 9. The operator never notices; its commands keep working.

**Row 3 is the feature, and the mitigation had to leave it standing.** The operator retires its own
SPI 9, authenticated under its *other* association SPI 10 — same owner — and the hardened build
allows it. That is EX-S03's rotation, performed on the wire instead of by a rebuild. A mitigation
that had simply forbidden `STOP_SA` would have failed this row, which is why it is measured.

## What to conclude

**Authenticating the sender is not authorising them.** This is EX-G02's lesson, one layer down, in
key management. A MAC proves possession of *a* key; it says nothing about which Security
Associations that key's holder may administer. The control that survives is the one keyed on *owns
this SA*, not the one keyed on *holds a valid key*.

**From the ground, row 1 and row 3 are the same event.** Look at the `SPI 9 before/after` and
`last refused SPI` columns for both: `4 → 0`, refused SPI `9`, identical. "Someone retired my key"
and "I retired my key" produce the same observation, because the operator only ever sees the
*effect* — SPI 9 stopped answering. The single place the difference exists is the COMM console —
`by SPI 11` versus `by SPI 10` — and it never leaves the spacecraft.

That gap is what `mitigation.md` ends on, and it is the next exercise: authentication answered *who
framed it*, authorisation answered *whether they may*, and neither is **attribution** of a
deactivation the operator lives with the consequences of. There is also an availability question
this exercise deliberately did not touch — what stops a directive from retiring the *last*
operational SA, or arriving before the SA it depends on — and `mitigation.md` says why those are
left for later rather than folded in here.
