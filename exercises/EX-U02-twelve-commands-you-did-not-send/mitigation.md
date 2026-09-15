# EX-U02 — the counter, and what it cannot tell you

## What the mitigated build does

`CUBERANGE_OBC_TC_COUNTERS=1` adds two sixteen-bit counters to the OBC and four octets to the
beacon:

```c
static uint16_t tc_accepted;   /* every telecommand this spacecraft HEARD */
static uint16_t tc_rejected;   /* how many of those it refused */
```

`tc_accepted` is incremented where ECSS acceptance is decided — after the packet's shape and
length field check out, before any handler runs. `tc_rejected` is incremented at each refusal.
**A refused command counts in both**, which reads wrong for a moment and is deliberate: it is the
same semantics `send_acceptance_success` already had, and for the same reason. A refused command
was *heard*, and "did it arrive" is the question.

They are reported in the housekeeping beacon (PUS 3,25) alongside the uptime that was already
there, so the counters cost **four octets per beacon and no packet that was not already being
sent**. That is the cheapest detector in this range by a wide margin.

The station reads **differences between two reports**, never the value. Sixteen bits wrap; an
absolute read is a detector that is wrong once every 65536 commands. A spacecraft reboot resets
the counters and shows up as a large negative step, which is honest — a station reporting "someone
else sent 65000 commands" after a reboot would not be.

## What it costs

Four octets of airtime per beacon, two `uint16_t` of RAM, and two increments on the telecommand
path. Nothing measurable.

The real cost is on the ground and it is not small: **the station has to keep a baseline across
passes**. A counter is only readable against a previous reading, so an operator who loses their
record of where it was loses the detector until the next pass establishes a new one — and an
intrusion inside that window is invisible in exactly the way it was before. `GroundStation` takes
its baseline at acquisition of signal, before commanding, which is also what makes the arithmetic
exact: a baseline taken after the first command has already been counted measures from an origin
one command in, and `commands_heard` then reads 15 where 16 is true. Measured; that is where the
number came from.

## What it still cannot do

- **Tell an attacker from a colleague.** `unexplained_commands` says *you are not alone*. It never
  says *you are under attack*. A second legitimate station produces exactly the reading row 1
  produces, which is EX-G03's whole subject arriving in a new place. An operator who does not know
  their own constellation will investigate a teammate.
- **Say which commands.** It is a count, not a log. Sixteen heard and eight sent tells you eight
  telecommands you did not send were accepted; it does not tell you what they asked for. A real
  spacecraft would carry the last N telecommand request ids — the report store EX-L02 built,
  pointed at the uplink — and this range does not.
- **Survive being read as a single number.** It is a NET. The intrusion in row 1 *caused* the
  denial that subtracts from it, so twelve probes read as +8. `commands_heard` and `commands_sent`
  are exposed beside it because the net hides its own terms, and an operator reading +8 as a small
  intrusion has understated it by a third.
- **Count what COMM refused.** The counters are on the OBC, which is *behind* the link-layer
  authentication. A forged frame, a replayed frame, a frame with a bad MAC — none of them reach
  this counter, so an attacker who does **not** have the key is invisible to it while hammering the
  uplink. That is the opposite blind spot from the one this exercise is about and it is the more
  common attacker. The counter belongs on both nodes; here it is on one.
- **Refuse an unimplemented service.** This OBC answers PUS 3,1 with a printk and counts it as
  accepted. Real ECSS acceptance would *refuse* an unimplemented service, which would put the
  intruder's twelve probes in `tc_rejected` instead — a **stronger** detector, because
  "twelve refused" is a sentence about an intruder and "twelve accepted" needs subtraction first.

## The second Security Association

Row 1's lockout is not staged and it is worth reading twice.

COMM keeps **one** anti-replay counter, because this range has **one** Security Association. The
firmware has said so in a comment since EX-S01 and said the fix was not implemented. This exercise
is the first thing to measure what that costs: the intruder's twelve probes advance the counter,
the operator's next four frames arrive behind it, and COMM refuses them **at the link layer** — so
the OBC never sees those commands, and no acceptance and no refusal comes back. From the ground,
an intruder using your key is indistinguishable from EX-U01's denied uplink until you look at the
sign.

CCSDS 355.0-B-2 puts anti-replay state in the SA, and an SA belongs to a virtual channel. Two
transmitters get two SAs and two counters and do not collide. Implementing that would remove the
lockout, which would make the intruder quieter still: no symptom at all, and the counter the only
evidence that anything happened. **The mitigation in this file would matter more, not less.**

## The thing worth carrying out of this

EX-U01: check the direction nobody built anything for.
EX-U02: **and check whether anything you built can tell your traffic from someone else's.**

Every control in this range asks a question about a frame. A MAC asks whether the sender has a
key. An authority table asks whether a source id is on a list. An anti-replay counter asks whether
a sequence number is fresh. All three are satisfied by an intruder holding the key, because none
of them is a question about *whose* traffic this is — and the first control here that asks that
question is a two-octet counter costing four octets of airtime.
