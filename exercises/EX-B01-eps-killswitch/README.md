---
id: EX-B01
title: Kill the radio from the internal bus
layer: internal bus
difficulty: introductory
duration: 30-45 min
ttp:
  # Deliberately empty. Mapping these to SPARTA and SPACE-SHIELD means checking each ID against
  # the official STIX exports, and there is no tool here that does it - so a filled-in list would
  # be a guess wearing a citation. Leave them empty until something verifies them.
  sparta: []
  space_shield: []
---

# EX-B01 — Kill the radio from the internal bus

## The situation

You are on the satellite's CAN bus. You do not own any subsystem; you can only put frames on the
wire — the position a compromised payload, a malicious component, or anyone with access to the
harness would be in.

The EPS owns the load switches. One of them powers COMM, the radio. Nothing on this bus
authenticates a command.

## Your objective

Silence the satellite. Success is the ground station losing telemetry, and the EPS console
reporting `COMM rail OFF`.

## What is running

```
ground station --TCP--> COMM.usart2 --CSP/CAN--> OBC        (PUS 17 round trip)
                          COMM  OBC  EPS  attacker  ------  one CAN hub
                                     |
                          gpioPortD.5 = COMM power rail
```

The attacker is a bare Renode machine hosting a runtime-compiled C# `ICAN` peripheral with a TCP
listener. Write `"<hex can id> <hex data>"` to its port and a raw CAN frame appears on the bus. No
privileges, no SocketCAN, no firmware of your own.

A host-side power domain watches the EPS rail GPIO and halts the COMM machine when it drops —
Renode has no notion of an unpowered machine, so the physical consequence is applied from outside.

## Run it

```bash
make firmware-p1
make exercise EX=EX-B01-eps-killswitch      # runs the scenario and leaves it up
python3 exercises/EX-B01-eps-killswitch/solve.py     # the model answer
```

## Hints

<details><summary>Hint 1 — where to look</summary>

The EPS listens on CSP port 11. Its command is seven octets: opcode, rail, state, then four more.
Read `firmware/apps/eps/src/main.c` and decide which of those seven the vulnerable build actually
inspects.
</details>

<details><summary>Hint 2 — getting a frame onto the bus</summary>

`src/cuberange/proto/csp.py` builds CSP v1 packets and fragments them into CFP-over-CAN frames.
The injector's line protocol is `"<hex can id> <hex data>"`, one frame per line.

Two things silently produce nothing: sending standard 11-bit frames instead of extended 29-bit
ones, and injecting while the emulation is paused. Both were mistakes made while building this
exercise.
</details>

<details><summary>Hint 3 — the source address</summary>

Set the CSP source to 1 and claim to be the OBC. Then ask yourself what, anywhere in the system,
would have noticed.
</details>

## What you should conclude

The interesting property is not that the EPS has a bug. It is that the EPS is behaving exactly as
designed, and the design has no way to tell an operator's command from a stranger's. A bus with no
authentication makes every node on it as trustworthy as the least trustworthy node — and the
attacker here was never even a node.

This mirrors what Willbold et al. found in real satellite firmware (*Space Odyssey*, IEEE S&P
2023): missing or bypassable telecommand authentication, and custom protocols treated as if
obscurity were access control.

## Then fix it

See [mitigation.md](mitigation.md), then run the verification, which asserts all three directions:

```bash
make verify EX=EX-B01-eps-killswitch
```

| Test | Asserts |
| --- | --- |
| `test_attack_succeeds_against_the_vulnerable_eps` | the forged frame silences the satellite |
| `test_mitigation_blocks_the_forged_command` | the mitigated build rejects it and stays up |
| `test_the_mitigation_does_not_break_legitimate_commands` | an authenticated command still works |

The third one matters as much as the first two. A control that also blocks the real operator is
not a mitigation, it is an outage.
