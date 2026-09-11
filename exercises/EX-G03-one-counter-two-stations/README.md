---
id: EX-G03
title: One counter, two transmitters
layer: space link
difficulty: intermediate
duration: 45-60 min
prerequisite: EX-G02
ttp:
  # Deliberately empty. Mapping these to SPARTA and SPACE-SHIELD means checking each ID against
  # the official STIX exports, and there is no tool here that does it - so a filled-in list would
  # be a guess wearing a citation. Leave them empty until something verifies them.
  sparta: []
  space_shield: []
---

# EX-G03 — One counter, two transmitters

## The situation

EX-L01 ended with a working defence. The spacecraft refuses any telecommand frame whose sequence
number is not strictly ahead of the last one it accepted, so a recording cannot be retransmitted.
That mitigation is on in this exercise, and everything else is the hardened build: EX-B01's bus
forgery, EX-F01's overflow and EX-G02's missing authority check are all fixed.

One counter, for the whole link.

The backup station came online last week. It is a real site, with real operators, doing real
work — and since it started transmitting, the primary station's commands have been rejected. The
spacecraft's console says `REJECTED replayed frame`.

## Your objective

Two things, and the second one is the exercise.

1. Silence the operator with a single frame. Not a forged command, not a privileged one, and
   nothing captured from the link first.
2. Work out why the backup station has the same effect without meaning to, and why the console
   calls it a replay when nobody is replaying anything.

## What is running

COMM with the EX-L01 anti-replay on, and everything else hardened. The channel in front of the
spacecraft accepts several ground stations, so you can have both stations on the air at once and
watch them interfere.

| | |
| --- | --- |
| primary station | source `0x0042`, virtual channel 0 |
| backup station | source `0x0043`, virtual channel 1 |

## Run it

```bash
make firmware-g03
make verify EX=EX-G03-one-counter-two-stations

# or by hand, three terminals
make exercise EX=EX-G03-one-counter-two-stations
make channel
make gs STATION=primary                 # works
python3 exercises/EX-G03-one-counter-two-stations/solve.py
make gs STATION=primary                 # now it does not
```

## Hints

<details>
<summary>Claiming sequence 255 did nothing. Why?</summary>

The comparison is `(int8_t)(seq - last_seq)`, so the counter can wrap. That makes it a circular
window of ±127, not an ordering: against an operator at sequence 1, a frame claiming 200 computes
to **+57** — ahead — and is accepted, and the operator walks back into the window within a few
commands. There is no "far ahead" in eight bits, only "ahead until it is behind again". Claim 100
and they are locked out for ninety-nine commands.

EX-L01's mitigation notes say "eight bits is a small window". This is what that sentence costs.
</details>

<details>
<summary>Which virtual channel should the frame use?</summary>

On the vulnerable build it makes no difference whatsoever, and that is the whole finding. Try
`--vcid 63`. Then read `firmware/apps/comm/src/main.c` and count the counters.
</details>

<details>
<summary>Nothing in the log says "attack".</summary>

No. It says `REJECTED replayed frame`, which is the spacecraft correctly reporting what its rule
concluded. The rule is wrong, not the report. An operator reading that console looks for a
recording being retransmitted, and there is none — there is a colleague, or one frame from
someone who sent it thirty seconds ago and left.
</details>

## What you should conclude

**A defence can deny service by working exactly as designed.** Nothing here malfunctions. The
frames arrive intact, pass their CRC, and are discarded by a rule that is doing what it was
written to do. The bug is in the rule's assumption, and the assumption was never written down:
one counter is correct only if there is one transmitter.

**The log is accurate and misleading at the same time.** `REJECTED replayed frame` is a true
statement about what the rule decided. It sends the operator hunting a replay attacker who does
not exist, while the actual cause — a second site, or one stray frame — is not something the
message even hints at. A defence that cannot describe why it refused is a defence that costs
incident-response hours.

**The standard already said so.** CCSDS 232.0-B-4 gives the TC frame a six-bit virtual channel
identifier, and COP-1 maintains the frame sequence number per virtual channel — the FARM state is
per VC, not per link. This implementation read the sequence number out of the frame and ignored
the field next to it. Conformance is not paperwork here: the field that was skipped is precisely
the one that makes the defence survive a second ground station.

**And the cheapest attack in this range is the one that needs nothing.** EX-B01 needs bus access.
EX-L01 needs a recording. EX-F01 needs an overflow. EX-G02 needs a station's identity. This needs
one well-formed frame and no privileges of any kind.

## Then fix it

See [mitigation.md](mitigation.md).
