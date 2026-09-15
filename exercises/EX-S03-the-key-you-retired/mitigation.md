# EX-S03 — the Security Association table, and the key nobody rotated

## What the mitigated build does

Both halves of this pair carry the table. **The table is not the difference.**

```c
struct cr_sdls_sa {
        uint16_t spi;
        const uint8_t *key;
        bool operational;          /* the state field, reduced to the one bit this range needs */
};

static const struct cr_sdls_sa sa_table[] = {
        { CR_SDLS_SPI,         cr_sdls_key,         /* deactivated in EX-S03's hard half */ },
        { CR_SDLS_SPI_ROTATED, cr_sdls_key_rotated, true },
};
```

`CUBERANGE_COMM_SA_DEACTIVATION=1` marks the retired association non-operational, and that one bit
is the pair's whole difference. A build that carried only the new key would be a spacecraft that
*cannot speak* the old association rather than one that *refuses* it — those look the same from a
single frame and are not the same control, and the pair gate would have happily called either one
"the mitigation".

The state is checked **before the MAC, not after**. A deactivated SA's key must not be used to
verify anything: checking the MAC first would mean the retired key still decides whether a frame is
well-formed, and an attacker holding it could tell a deactivated association from a nonexistent one
by how long the answer took.

Refusals now carry a **reason**, one of four, distinct because they send an operator to four
different places:

```
not a well-formed authenticated frame      the deframer or the layout
no such security association               a frame for somebody else's spacecraft, or a guess
that security association is DEACTIVATED   somebody is using a key you retired
the MAC does not verify                    somebody without the key
```

The SPI of the last refused frame goes to the OBC with the counts EX-U03 built and rides the
beacon, which is now fourteen octets. **Not for a malformed frame**: a frame that failed its layout
check has an SPI field full of whatever octets happened to be there, and reporting it would have
the ground investigating an association nobody used.

## What came with it, unasked

**Anti-replay is now per Security Association**, which is where CCSDS 355.0-B-2 puts it and what
COMM's source has said was unimplemented since EX-S01. EX-U02 measured what the single counter
cost: an intruder holding the operator's key advanced it, the operator's next frames arrived
behind it, and COMM refused the *legitimate* station at the link layer with no acceptance and no
refusal reaching the ground.

Note carefully what this does **not** fix. Two transmitters sharing **one** SA still collide,
because they share its counter — and that is EX-U02 exactly, whose intruder stole the key to the
association the operator was using. **A stolen key does not give the thief a second SA.** The fix
is real and it is not a fix for that.

## What it still cannot do

- **Rotate the other key.** This range has *two* keys and the rotation moved one. The mission-layer
  telecommand authentication (EX-S02) still verifies with the original constant, and an attacker
  holding it can still sign a telecommand — they just cannot get a frame past the radio to deliver
  it. That is defence in depth working as intended and it is also **an un-rotated key still in
  circulation**. A rotation that covers one of two layers is half a rotation, and this exercise
  would report it as a success.
- **Deactivate an SA in flight.** The table is `const` and compiled in, so this rotation was flown
  as a software update. A real mission deactivates an SA by *telecommand* — and **a telecommand
  that can deactivate the operator's own association is a denial of service with a valid MAC on
  it.** Build that and the next exercise writes itself: who is authorised to retire a key, what
  happens if the deactivation arrives before the activation, and what the spacecraft does if it is
  left with no operational SA at all. None of that is implemented here.
- **Say which association a refusal burst belonged to.** One value, the last one. Refusals on two
  SPIs report only the second, and a real mission would carry a small per-SA histogram. Enough to
  tell "the key you retired" from "no key at all", which is the question a rotation raises, and not
  enough for an incident timeline.
- **Distinguish a deactivated SA from an expired one.** CryptoLib carries four SA states — unkeyed,
  keyed, operational, expired — and the difference matters for key management this range does not
  do. One bit here, named as one bit.
- **Prove a rotation happened at all.** The negative test proves the old key is refused *now*. It
  says nothing about the window between the new SA becoming operational and the old one being
  deactivated, and in a real rotation that window is hours or days long and is when the interesting
  thing happens.

## The thing worth carrying out of this

EX-U01: check the direction nobody built anything for.
EX-U02: and check whether anything you built can tell your traffic from someone else's.
EX-U03: and check whether the controls that already work can say that they did.
EX-S03: **and check that the control you turned off is off, by asking it to let you in.**

Every exercise before this one is about a control that was missing or that could not report. This
one is about a control that was *performed*, *verified* and *incomplete* — and it passed its
verification because the verification exercised the half of the change that makes noise. **An
activation announces itself. A deactivation is silence in a direction nobody transmits.**
