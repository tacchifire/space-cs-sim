---
id: EX-S03
title: The key you retired
layer: space-link
difficulty: advanced
duration: 30-45 min
prerequisite: EX-U02
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-S03 — The key you retired

## The situation

EX-U02's intruder had your key, and every cryptographic control on board let them in because
**cryptography cannot tell a key from the person holding it**. The answer to that is to change the
key, and this exercise is about what "changing the key" actually consists of.

CCSDS 355.0-B-2 puts the key in a **Security Association** along with a cipher mode, an anti-replay
sequence number and a **state**. Retiring a key is *deactivating its SA*. A rotation therefore has
two halves — activate the new association, deactivate the old one — and **only the first half has a
symptom**. Do the first half alone and the operator is commanding on new key material, every
command works, every report comes back, and the thief is still holding a key that opens the door.

This range had one Security Association until now, and COMM's own source said so in the comment
where it checked the SPI. It has two.

## Your objective

Your commands on the new key work. **Show that the old key no longer does.**

```bash
make exercise EX=EX-S03-the-key-you-retired          # a shell inside the range
python3 exercises/EX-S03-the-key-you-retired/solve.py   # at that shell's prompt
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-S03-the-key-you-retired RUN='python3 exercises/EX-S03-the-key-you-retired/solve.py --no-retired-test'
```

## What happened

```text
retired SA     tested   SPI 10 acked   SPI 9 acked   received   refused   last refused SPI
deactivated    yes           4              0            8         4            9
deactivated    no            4              -            4         0            0
operational    yes           4              4            8         0            0
operational    no            4              -            4         0            0
```

**Rows 2 and 4 are the same observation.** One is a spacecraft that retired the key and one is a
spacecraft that did not, and an operator who never transmits on the old association cannot tell
them apart. Nothing is broken in either. Nothing is missing from either console. **The difference
between a completed rotation and an abandoned one is invisible until you go looking for it**, and
transmitting on a key you have just retired is not an obvious thing to do — which is precisely why
rotations go untested.

**Row 1 is the proof, and the SPI is what makes it proof.** Four frames refused would be
consistent with a bad link, a mangled frame, or somebody guessing. `last refused SPI = 9` says the
association you retired was the one turned away. The console says why in words: *that security
association is DEACTIVATED*, which is a different sentence from *the MAC does not verify* and
sends an operator somewhere different.

**Row 3 is the failure, and look at what it does NOT look like.** Eight commands accepted, zero
refused, nothing in telemetry, nothing on the console. The retired key works and the spacecraft is
perfectly happy about it.

## What to conclude

**A control you have not tested is a control you are assuming.** This is the whole exercise. The
rotation was performed, the new key was verified, everything worked — and the verification tested
the half of the change that has a symptom. The half that matters is the *refusal*, and a refusal
can only be observed by asking to be refused.

**Negative tests are the only evidence a deactivation produces.** An activation announces itself:
commands start working. A deactivation is silence in a direction nobody transmits. The only way to
distinguish "refuses the old key" from "has never been asked" is to ask.

**And a refusal needs a reason to be evidence.** EX-U03 built the radio's ability to say *how many*
frames it threw away. This exercise needed it to say *which association*, because "somebody is
using the key you retired" and "somebody has no key at all" are different incidents with different
responses, and a count carries neither.

`mitigation.md` is the SA table, the anti-replay fix it brought with it, the key that was *not*
rotated, and the telecommand that could deactivate your own association.
