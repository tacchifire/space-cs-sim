---
id: EX-U01
title: The command that never landed
layer: uplink
difficulty: advanced
duration: 30-45 min
prerequisite: EX-L03
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-U01 — The command that never landed

## The situation

EX-L03 ended by naming the direction none of it watches:

> **Anything about the uplink.** The schedule watches what arrives. A spacecraft that hears
> nothing has no way to say so.

Everything this range has built for the operator looks at the downlink. The counter gaps, the
resend requests, the pass schedule, the beacon. Deny the **uplink** instead and all of it keeps
saying the link is healthy, because from the downlink's point of view it is.

And there is a gap in the spacecraft's vocabulary that has been open since EX-G04. That exercise
established that a refusal the ground cannot hear is indistinguishable from a frame that never
arrived, and the answer was PUS 1,2. **The sign was never flipped.** An operator can tell "refused"
from "nothing" and still cannot tell "accepted" from "never arrived".

## Your objective

Four commands go up. Say whether the spacecraft got them.

```bash
make exercise EX=EX-U01-the-command-that-never-landed          # a shell inside the range
python3 exercises/EX-U01-the-command-that-never-landed/solve.py --deny   # at that shell's prompt
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-U01-the-command-that-never-landed RUN='python3 exercises/EX-U01-the-command-that-never-landed/solve.py --deny'
```

## What happened

```text
acknowledgements   uplink denied   sent   acked   telemetry   pass schedule
yes                no               4       4        36         ok
yes                yes              4       0        32         ok
no                 yes              4       0        30         ok
no                 no               4       0        34         ok
```

**The pass schedule says `ok` in every row.** The beacon arrives throughout, the counter has no
gaps, and every downlink-side control this range has built reports a healthy link — because the
downlink *is* healthy.

With acknowledgements, 4 against 0 separates the attack. Without them, 0 against 0 does not.

## What to conclude

**A control watching one direction sees one direction.** That is EX-X01's sentence — a defence is
attached to a path — arriving for the fifth time, and it arrived here because five exercises of
work on telemetry never once asked what happens to a command.

**Acceptance is not execution, and an operator needs both apart.** The PUS 1,1 in this build is
sent *before* dispatch, so a command that is then REFUSED is acknowledged too. That reads wrong for
a moment and is the point: a refused command was **heard**. "It never arrived" and "it arrived and
went wrong" are different passes, and only the first is an attack on the uplink.

**And notice the asymmetry in how these two directions were treated.** Refusals were built in
EX-G04, authenticated in EX-D01, counted in EX-D02, re-requestable in EX-L02 and backstopped by a
beacon in EX-L03. Five exercises of scaffolding on the downlink. The uplink had nothing, because
nothing on the uplink ever produced an observation to build on — which is exactly why it is where
an attacker goes.

`mitigation.md` is the acknowledgement, what it costs in airtime, and what it still cannot say.
