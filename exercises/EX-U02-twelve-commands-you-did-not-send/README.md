---
id: EX-U02
title: Twelve commands you did not send
layer: uplink
difficulty: advanced
duration: 30-45 min
prerequisite: EX-U01
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-U02 — Twelve commands you did not send

## The situation

EX-U01 ended by naming what it did not have:

> **The uplink counter.** There is none. The spacecraft has no way to say "I have heard 40
> commands from you" and the ground has no way to notice it should have been 41.

This exercise is that counter, and the reason to build it is not the one EX-U01 gave.

**Somebody else has the key.** How is not this exercise's subject — EX-F01 ends with code execution
on an OBC, and a key on a laptop in an operations centre is a shorter story than that. What matters
is what every control in this range does about it, which is nothing, correctly. The SDLS MAC
verifies. The telecommand authentication verifies. The anti-replay sequence is fresh. **Cryptography
cannot tell a key from the person holding it**, and nothing built in EX-S01, EX-S02 or EX-D01 was
ever going to.

They transmit while you are not over the spacecraft.

## Your objective

You command in one pass, you are out of view for a while, you command again. Say what happened
while you were away.

```bash
make exercise EX=EX-U02-twelve-commands-you-did-not-send          # a shell inside the range
python3 exercises/EX-U02-twelve-commands-you-did-not-send/solve.py   # at that shell's prompt
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-U02-twelve-commands-you-did-not-send RUN='python3 exercises/EX-U02-twelve-commands-you-did-not-send/solve.py --no-intruder'
```

## What happened

```text
counters   intruder   our second pass   acked   heard   sent   unexplained   refused
yes        yes        -                 4/8      16      8        +8            0
yes        no         -                 8/8       8      8         0            0
yes        no         denied            4/8       4      8        -4            0
no         yes        -                 4/8       -      8       cannot tell    -
no         no         -                 8/8       -      8       cannot tell    -
```

**Rows 1 and 3 are the same symptom.** Four commands acknowledged, then a pass where nothing comes
back. One is an intruder holding your key; the other is your uplink being eaten. They call for
opposite responses — a key rotation and an incident, or an antenna and a link budget — and *the
sign of one number is the only thing in this range that separates them*.

**+8, not +12.** Twelve probes went up. The count is a **net**: the intruder's own transmissions
advanced the link's single anti-replay counter, which put your second pass *behind* it, so COMM
refused four commands of yours at the link layer and the OBC never heard them. Sixteen heard
against eight sent. An operator who reads +8 as "eight intruder commands" has the wrong number.

**Zero refused, and that is half the finding.** Not one probe reached the authority table. They are
service/subtype combinations this OBC does not implement, answered with a line on a console nobody
off the spacecraft can read. **The intruder is not doing anything yet. They are finding out what is
here**, and the pair of numbers — many heard, none refused — is what says so.

**The counter gap says nothing.** Fifteen reports missing in row 1, four in row 2. That is the
beacon running while you were out of view, in both, and it is your own pass schedule rather than
anybody's attack. EX-L02 and EX-L03 built that detector and it is working exactly as specified.

## What to conclude

**Every control here answers "is this frame legitimate". None answers "is this frame mine".** A MAC
proves possession of a key. An authority table proves a source id is on a list. An anti-replay
counter proves a sequence number is fresh. An intruder holding the key satisfies all three, because
all three are questions about the *frame*, and there is no question in this range about the *set of
frames* until somebody counts them.

**The witness has to be on board.** Everything else the operator has reads the live downlink, and a
live reading only ever sees what you were present for. The intruder transmitted into an empty sky
and the acknowledgements their probes provoked were received by nobody. The count was still there
on the next pass, because a counter is state and a stream is not.

**Attention follows evidence** — EX-U01's sentence, and it is why this is the sixteenth exercise
rather than the second. Nothing on the uplink ever produced an observation, so nothing on the
uplink ever got built, so the uplink is where an intruder with your key goes.

`mitigation.md` is the counter, what it cannot distinguish, and the second Security Association
this range still does not have.
