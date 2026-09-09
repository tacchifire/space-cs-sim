---
id: EX-A01
title: Spin the satellite up without it noticing
layer: internal bus
difficulty: introductory
duration: 30-45 min
prerequisite: EX-B01
ttp:
  # Deliberately empty. Mapping these to SPARTA and SPACE-SHIELD means checking each ID against
  # the official STIX exports, and there is no tool here that does it - so a filled-in list would
  # be a guess wearing a citation. Leave them empty until something verifies them.
  sparta: []
  space_shield: []
---

# EX-A01 — Spin the satellite up without it noticing

*日本語版: [README.ja.md](README.ja.md)*

## The situation

You are on the satellite's CAN bus again, in the same position as EX-B01. Something has changed
since then: the EPS now refuses power commands that do not carry the right token, so the trick that
silenced the radio no longer works.

Good. That control was never the whole problem.

The ADCS owns the magnetorquers. Its command carries a torque in milli-newton-metres as a signed
16-bit integer, so the wire format allows anything from -32768 to +32767. The coil can deliver 20.

## Your objective

Leave the spacecraft alive, answering, and unable to make power. Success is
`ADCS: TUMBLING - panels off sun` on the ADCS console, with the ground station still getting
telemetry the whole time.

## What is running

```
ground station --TCP--> COMM.usart2 --CSP/CAN--> OBC
                          COMM  OBC  EPS  ADCS  attacker  ------  one CAN hub
                                          |
                               gpioPortD.6 = tumble indicator
```

The EPS here is the **hardened** build from EX-B01. Four satellite nodes now, not three.

## Run it

```bash
make firmware-a01
make exercise EX=EX-A01-adcs-tumble               # runs the scenario and leaves it up
python3 exercises/EX-A01-adcs-tumble/solve.py     # the model answer
make verify EX=EX-A01-adcs-tumble
```

## Hints

<details><summary>Hint 1 — where to look</summary>

The ADCS listens on CSP port 12. Its command is eight octets: opcode, axis, a 16-bit torque, and
four reserved. Read `firmware/apps/adcs/src/main.c` and find the number the coil can actually
deliver. Then look at what the vulnerable build checks before integrating it.
</details>

<details><summary>Hint 2 — getting a frame onto the bus</summary>

The same injector as EX-B01, on the same port, with the same line protocol
`"<hex can id> <hex data>"`. `src/cuberange/proto/csp.py` builds the packet and fragments it.

The two mistakes that silently produce nothing are also the same: a standard 11-bit frame instead
of an extended 29-bit one, and injecting while the emulation is paused.
</details>

<details><summary>Hint 3 — what to watch</summary>

Not the telemetry. The satellite answers throughout, and that is the point. Watch
`$(make -s out)/adcs.uart`, or read the indicator pin the way the verification does:
`gpioPortD` ODR at `0x58020C14`, bit 6, on the ADCS machine.
</details>

## What you should conclude

Two things, and the second is the one worth carrying away.

**Authentication was not the missing control.** EX-B01's fix works exactly as designed here: the
EPS rejects your forged power command. It does nothing for this attack, because the ADCS command is
not implausible — it is well-formed, correctly addressed, and asks for a torque the protocol says
is legal. A command from the real operator with the same value would be accepted too. Knowing *who*
is asking does not tell you whether *what* they asked for is possible.

**The failure is quiet, and quiet is worse.** EX-B01 ends in silence: telemetry stops, and an
operator knows within one pass that something is wrong. This one ends with every subsystem
reporting nominal. The radio works. The OBC answers. Housekeeping arrives on schedule. The first
evidence reaches the operator hours later as a battery trend that nobody has a reason to look at
yet, and by then the spacecraft has been off-sun for several orbits.

A range where every exercise ends in silence teaches operators to read "responding" as "healthy".
This is the counterexample, which is why the verification asserts that the satellite is **still
alive** after the attack lands.

Willbold et al. (*Space Odyssey*, IEEE S&P 2023) found this shape repeatedly in real satellite
firmware: telecommand handlers that validate structure and provenance and never validate that the
requested value is one the hardware can carry out.

## Then fix it

See [mitigation.md](mitigation.md). The verification asserts all three directions:

| Test | Asserts |
| --- | --- |
| `test_attack_succeeds_against_the_vulnerable_adcs` | the forged torque tumbles the spacecraft, and it keeps answering |
| `test_mitigation_rejects_the_out_of_authority_torque` | the same command is refused and the attitude holds |
| `test_the_mitigation_does_not_break_legitimate_slews` | a torque inside the actuator's authority is still executed |

The third one matters as much as the first two. An ADCS that refuses every attitude command is not
a mitigated ADCS, it is a dead one.
