# EX-U01 — Mitigation

## The fix

`CUBERANGE_OBC_ACK_COMMANDS=1`. PUS 1,1 on acceptance, carrying the accepted packet's own first
four octets as the request id — the same field the failure report carries, so the ground matches
it against a command it already has a copy of.

It is sent **before dispatch**, which is where ECSS puts acceptance and for the reason ECSS puts it
there: acceptance is a statement about the packet arriving and being well-formed enough to hand to
a handler, and execution is a different statement. A command that is accepted and then refused
produces both a 1,1 and a 1,2, and an operator who receives neither has learned something a
refusal alone could never tell them.

## What it costs

- **A downlink packet per telecommand, forever.** That is the reason missions do not acknowledge
  everything: the PUS acknowledgement flags in the TC secondary header exist precisely so an
  operator can ask for acceptance on the commands that matter and not on the rest. This range sets
  those flags and ignores them — a simplification, named here rather than left to be found.
- **A standing oracle.** Anyone who can transmit learns whether a packet was well-formed. On a
  link where telecommands are authenticated (EX-S02) they have to forge the MAC first, so the
  oracle is bounded; on one where they are not, it is a free format checker.

## What it does not solve

- **Denial of the acknowledgement.** The attacker who denies the uplink can deny the 1,1 as well,
  and then the operator is back where EX-D02 and EX-L02 left them: a missing report, a counter gap,
  and a resend request that answers it. The difference is that there is now something to be
  missing, which is the whole of the improvement.
- **Which direction failed.** Silence after a command means the command did not arrive OR the
  acknowledgement did not come back. Those are different attacks with the same symptom, and
  nothing here separates them. A mission would use the report counter — a gap says the downlink
  ate something — but a gap needs two reports around it and an acknowledgement that never existed
  leaves none.
- **Execution.** Acceptance says the packet was well-formed. EX-F01 is an entire exercise about a
  well-formed packet doing something nobody wanted.
- **The uplink counter.** There is none. The spacecraft has no way to say "I have heard 40
  commands from you" and the ground has no way to notice it should have been 41. That is the
  uplink's version of EX-D02, and it is not implemented.

## The thing worth carrying out of this

Nine exercises, and this one is where the pattern stops being a pattern and starts being a
checklist.

EX-X01: a defence is attached to a path, not to an asset.
EX-G04: a control that cannot report is a control the ground cannot use.
EX-S01: the strongest control in the system is still attached to a path.
EX-S02: so bind it to the thing you actually care about.
EX-D01: and then check that you did it in both directions.
EX-D02: and check what your detection is standing on.
EX-L02: and check that it can tell your adversary from your weather.
EX-L03: and check that the thing it is watching for actually happens.
EX-U01: **and check the direction nobody built anything for.**

Five exercises of scaffolding went onto the downlink because the downlink produced observations to
build on. The uplink produced none, and so got none, and that is not a coincidence — it is the
mechanism. **Attention follows evidence, and an attacker goes where there is no evidence to follow.**
