---
id: EX-D01
title: A refusal nobody sent
layer: downlink
difficulty: advanced
duration: 30-45 min
prerequisite: EX-S02
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-D01 — A refusal nobody sent

## The situation

Every attack in this range so far has gone at the spacecraft. This one goes at the operator.

EX-G04 is the exercise that built the reporting path. Its finding was that a control which refuses
a command and says so only on a console nobody is holding is a control the ground cannot use — from
the ground, a refusal and a frame that was never received are the same observation. So the
spacecraft learned to send PUS 1,2: the request id, verbatim, and a reason.

EX-S02 then put a MAC on every telecommand, so the spacecraft only acts on requests it can
authenticate. Both directions got attention. Only one of them got a key.

Nothing authenticates a report.

## Your objective

Make the operator believe a command they sent was refused.

You are on the crosslink, where EX-X01, EX-S01 and EX-S02 all left you. You do not have the key —
and this time you do not need one.

```bash
make exercise EX=EX-D01-a-refusal-nobody-sent          # a shell inside the range
python3 exercises/EX-D01-a-refusal-nobody-sent/solve.py   # at that shell's prompt
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-D01-a-refusal-nobody-sent RUN='python3 exercises/EX-D01-a-refusal-nobody-sent/solve.py'
```

## What happened

```
operator console:  refused APID 0x0A9 seq 0: not authorised (to station 0x0042)
COMM console:      downlink 22 octets from node 13
OBC console:       (nothing)
```

The OBC never saw a command, never refused one, and never sent a report. The operator has a
refusal with the right APID, the right sequence number, their own station id, and a reason that
fits.

The path is one function. COMM's downlink task binds CSP port 10, accepts a connection from **any**
source, and frames whatever arrives into a TM transfer frame — it even logs which node it came
from, and does not check it. A peer on the crosslink hands the victim's own COMM a telemetry
packet and the victim transmits it on the operator's downlink.

## What to conclude

**Authentication is directional, and one direction is easy to forget.** EX-S02's write-up said the
reports carry no trailer and named it as unfinished. This is what unfinished looks like from the
operator's chair.

**Think about what a forged refusal actually does.** It does not switch anything off. It tells an
operator that the thing they are trying does not work, in the vocabulary their own console uses,
about a command they really did send. They stop. Or they spend the pass debugging an authority
table that is working correctly. EX-G04 gave the ground a channel it would trust, and a channel
the ground trusts is worth attacking.

**And the corollary, which is worse.** With this available, an attacker can also make a *real*
refusal invisible by flooding plausible ones, or make a successful command look refused. The
report the spacecraft actually sent is indistinguishable from the ones it did not.

`mitigation.md` is the fix, and the part of it that is not on the spacecraft.
