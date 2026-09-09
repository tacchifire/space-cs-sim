---
id: EX-G01
title: Command a spacecraft you cannot reach
layer: ground segment
difficulty: intermediate
duration: 45-60 min
prerequisite: EX-F01
ttp:
  # Deliberately empty. Mapping these to SPARTA and SPACE-SHIELD means checking each ID against
  # the official STIX exports, and there is no tool here that does it - so a filled-in list would
  # be a guess wearing a citation. Leave them empty until something verifies them.
  sparta: []
  space_shield: []
---

# EX-G01 — Command a spacecraft you cannot reach

*日本語版: [README.ja.md](README.ja.md)*

## The situation

You have no radio. You are not on the bus. You have no operator account, and you never touch the
spacecraft.

What you have is the ability to write a file into the ground segment's plugin directory — the
position of anyone who authors a mission tool, compromises one, or simply gets one installed. Pass
predictors, conjunction screeners, payload schedulers: a ground segment integrates several, and
they all write their output somewhere.

The ground segment ingests schedule files from that same directory.

## Your objective

Silence the satellite without transmitting anything. Success is `EPS: COMM rail OFF (commanded)`
with the operator's own ground station having sent the command, from a plan you wrote.

## What is running

```
   plugins/ ────────► ground segment ──► COMM.usart2 ──CSP/CAN──► OBC ──► EPS
   you are here        imports, then       every spacecraft-side control
                       transmits           is the HARDENED build
```

The EPS demands its token from EX-B01. The COMM rejects replays from EX-L01. The OBC bounds its
PUS 8 copy from EX-F01. All three work perfectly while this happens.

## Run it

```bash
make firmware-g01
python3 exercises/EX-G01-schedule-poisoning/solve.py --dir "$(make -s out)"/gs-plugins
make verify EX=EX-G01-schedule-poisoning
```

## Hints

<details><summary>Hint 1 — where the trust boundary should have been</summary>

Read `Scheduler.import_pending` in `src/cuberange/gs/schedule.py`, then ask two questions about the
directory it reads: who else writes there, and what is checked about a file found in it.
</details>

<details><summary>Hint 2 — what a plan looks like</summary>

`src/cuberange/gs/import_policy.py` has both policies side by side. `AcceptAnything` is what the
vulnerable ground segment uses; the fields it reads are the fields your file needs. The scheduler
transmits a PUS service and subtype, so anything the ground station can send, a plan can ask for.
</details>

<details><summary>Hint 3 — which command</summary>

The one the operator uses to switch the COMM rail: PUS 8,1, function 1, state 0. You are not
forging it — you are asking the ground segment to send the real thing.
</details>

## What you should conclude

**Every control worked.** That is the whole exercise. The EPS authenticated the command and it was
authentic. The OBC bounded the copy and the copy fitted. The COMM checked for a replay and it was
not one. The frame's checksum was right because the ground station computed it. Four exercises of
spacecraft-side hardening, all functioning, and the radio still went off — because none of them was
ever about *who decided what to send*.

**Authorisation is not a property of a message.** EX-B01 asked "does the sender know the secret".
EX-L01 asked "have I seen this before". EX-A01 asked "can the hardware do this". EX-F01 asked "does
this fit". A command that satisfies all four can still be one nobody was entitled to schedule, and
the only place that question can be asked is where the plan is assembled.

**One directory, two trust levels.** The flaw is not that the importer is careless in the abstract;
it is that the same path is both an output sink for third-party tools and an input source for the
command pipeline. That is a mundane, extremely common shape, and it is the reason this exercise is
not the tautology an earlier draft of the design was — "whoever can write the database can write
the database" teaches nothing, and section 16 of the design records that being thrown out.

**The transmitted command carries no mark of its origin.** `ScheduledCommand.origin` records where
an entry came from, and nothing puts that on the wire. From the spacecraft's point of view, and
from the point of view of anyone reading the downlink afterwards, a poisoned plan and an operator's
keystroke are the same event.

Willbold et al. (*Space Odyssey*, IEEE S&P 2023) is about firmware, and this exercise is the part
their scope excludes: the ground segment is where most real incidents begin, and it is the least
examined half of the system.

## Then fix it

See [mitigation.md](mitigation.md). The verification asserts all three directions, end to end
against the emulated spacecraft:

| Test | Asserts |
| --- | --- |
| `test_the_attack_works_against_the_permissive_importer` | the plugin's file becomes a transmission, the rail drops, and the EPS did NOT reject anything |
| `test_the_mitigation_refuses_the_plugin_s_file` | the importer records a rejection and the rail stays up |
| `test_the_mitigation_still_transmits_the_operator_s_own_plan` | a signed, permitted plan is still imported and still sent |

The first one asserts `"REJECTED" not in` the EPS console as well as the rail dropping. Without it,
a run where the EPS refused the command would look the same as one where the poisoned plan was
honoured, and only the second is this exercise.

`tests/pytest/test_schedule_policy.py` carries fourteen more, including the anti-strawman proof:
the scheduler is forbidden to mention either policy class, so "the two halves differ only in the
policy object" is checked rather than promised.
