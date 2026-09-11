---
id: EX-G04
title: A refusal nobody hears
layer: ground segment
difficulty: intermediate
duration: 30-45 min
prerequisite: EX-G03
ttp:
  # Deliberately empty. Mapping these to SPARTA and SPACE-SHIELD means checking each ID against
  # the official STIX exports, and there is no tool here that does it - so a filled-in list would
  # be a guess wearing a citation. Leave them empty until something verifies them.
  sparta: []
  space_shield: []
---

# EX-G04 — A refusal nobody hears

## The situation

Nothing is broken.

EX-G02's authority check is on and working: a station that is not granted `power` sends the
command anyway, and the spacecraft refuses it. The on-board console says
`REJECTED PUS 8 function 1 from source 67 - not authorised`. The control does exactly what it was
built to do.

The operator does not have the console. The operator has the downlink, and on the downlink the
refusal looks like this:

```
```

That is the whole exercise.

## Your objective

Establish, from the ground and during the pass, which of these happened:

- the spacecraft refused the command;
- the frame was corrupted in flight and dropped by the CRC;
- the uplink never transmitted;
- the spacecraft is not listening.

Then decide what would have to change on board for that to be possible.

## What is running

The same three nodes, with EX-G02's authority check **on** in both builds of this pair. The only
difference is whether the spacecraft says anything about what it refused.

| build | authority check | reports refusals |
| --- | --- | --- |
| `build-obc-g04-vuln` | yes | no |
| `build-obc-g04-hard` | yes | PUS 1,2 |

## Run it

```bash
make firmware-g04
make verify EX=EX-G04-a-refusal-nobody-hears

# or by hand
make exercise EX=EX-G04-a-refusal-nobody-hears
make channel
python3 exercises/EX-G04-a-refusal-nobody-hears/solve.py
```

## Hints

<details>
<summary>How do I tell a refusal from a dropped frame?</summary>

On the vulnerable build you cannot, and that is the finding rather than a gap in the hint. Try it:
send the refused command, then send a frame with one octet flipped so COMM's FECF discards it.
Compare what the ground saw. They are the same observation, not similar ones.
</details>

<details>
<summary>What does the spacecraft already know that it is not saying?</summary>

Everything needed. It knows which request it refused — it is holding the packet — and it knows
why. ECSS-E-ST-70-41C service 1 exists for exactly this: 1,2 is an acceptance failure report, and
it names the request by the failed packet's own first four octets, so the ground can match the
report to a command in its own log without the spacecraft remembering anything at all.
</details>

<details>
<summary>Why not just log it and downlink the log later?</summary>

Because "later" is after the pass. The value of the report is that it arrives while the operator
still has a link and can act - send it from the other station, fix the table, or stop trying. A
log downlinked at the next contact tells you what you could have done.
</details>

## What you should conclude

**A control that cannot report is half a control.** The authority check here is correct, tested,
and gated. It also produced an outage that the ground would spend a pass misdiagnosing, and the
two facts sit in the same firmware without contradicting each other. Whether a security control
can be *observed working* is a property of the control, not of the logging.

**Silence is the most ambiguous signal there is.** A refused command, a corrupted frame, an
unpointed antenna and a dead receiver all produce it. Anything that resolves silence into one of
those is worth more than it looks, and it is cheap here: four octets of request id and one octet
of reason.

**The spacecraft was never missing information.** It held the packet it refused and the reason it
refused it. This is the third exercise in a row where the fix was to use something already
present — EX-G02's source id was arriving and being ignored, EX-G03's virtual channel was being
stepped over, and here the refused request was in a local variable. None of the three needed a new
field on the wire.

**And an attacker benefits from silence twice.** Once because a refusal that nobody sees cannot be
alerted on, and once because an operator hunting the wrong fault is an operator not looking at
them. EX-G03's single-counter denial of service is exactly this: the console says *replay*, the
operator hunts a replay, and the frame that caused it was sent thirty seconds ago by someone who
has left.

## Then fix it

See [mitigation.md](mitigation.md).
