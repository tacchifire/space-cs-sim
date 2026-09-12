---
id: EX-X01
title: A peer with the ground's name
layer: crosslink
difficulty: intermediate
duration: 45-60 min
prerequisite: EX-G04
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-X01 — A peer with the ground's name

## The situation

Two spacecraft, and this time they can talk to each other.

Everything up to here has had exactly one way in. A telecommand reached a spacecraft by arriving
on its space link, being deframed by its COMM, and being handed to its OBC. Every defence this
range has built sits on that path: EX-L01's anti-replay, EX-G03's per-virtual-channel sequence
numbers, EX-G02's authority table, EX-G04's refusal reports. `make constellation` even proves the
spacecraft are apart — `test_the_buses_are_isolated` asserts that a frame on satellite 0's bus
never appears on satellite 1's.

A constellation whose members cannot talk to each other is four satellites, not a constellation.
So this scenario adds the link that makes it one: fdcan2 on each COMM, joined to a single shared
bus. It is a real capability with a real purpose — a spacecraft over the ocean, out of contact
with every ground station, is reachable through a neighbour that is not.

Start it and watch it work:

```bash
make exercise EX=EX-X01-a-peer-with-the-grounds-name
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError` — the space link
and the injector do not exist outside. `make channel`, `make gs` and `make verify` work from that
shell too, and are the same three commands they always were.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-X01-a-peer-with-the-grounds-name RUN='python3 exercises/EX-X01-a-peer-with-the-grounds-name/solve.py'
```

Both COMM consoles say so, within a few seconds of boot:

```
CUBERANGE: COMM (addr 5) booting
CUBERANGE: COMM crosslink up on can@4000a400, /2 local, default out
COMM: crosslink reached COMM 13 in 1 ms
```

Satellite 0's COMM is address 5. Satellite 1's is 13. They are on separate internal CAN buses,
in separate machines, and a CSP packet crossed between them.

That is the feature.

## Your objective

Switch satellite 0's COMM rail off. You are on the crosslink and nowhere else.

The premise is that one spacecraft in the constellation is already yours. How you got there is
another exercise's subject — EX-F01 ends with code execution on an OBC — and this one begins
after it. What the range gives you is an injector on the crosslink bus, which is what a
compromised spacecraft's radio would be.

Everything is hardened. The EPS has EX-B01's token check. The OBC has EX-F01's length check,
EX-G02's authority table and EX-G04's verification reports, all switched **on**.

## What to try, in order

Run each, then read the console it names. Write down which worked before reading the next section.

1. **The direct route.** Address satellite 0's EPS and tell it to drop the rail — EX-B01's
   command, sent from here.

   ```bash
   python3 exercises/EX-X01-a-peer-with-the-grounds-name/solve.py --direct
   ```

2. **The OBC, honestly.** The same objective through the OBC, using your own identity: PUS 8
   function 1, source id `0x0AA`, which is satellite 1.

   ```bash
   python3 exercises/EX-X01-a-peer-with-the-grounds-name/solve.py --honest
   ```

3. **The OBC, as somebody else.** The same command with two octets changed.

   ```bash
   python3 exercises/EX-X01-a-peer-with-the-grounds-name/solve.py
   ```

## What happened

```
1.  EPS: REJECTED unauthenticated rail command from node 13
2.  OBC: REJECTED PUS 8 function 1 from source 170 - not authorised
3.  OBC: PUS 8 executed - COMM rail OFF
    EPS: COMM rail OFF (commanded)
```

## What to conclude

**The direct route failed, and the reason is worth stating precisely.** The EPS asks for
something the sender has to *possess*. A shared token is a poor secret — its own mitigation
write-up says so, and says it is replayable and does not survive an attacker who can read the
OBC's flash — but a poor secret is still a thing you either have or do not, and arriving by a
new road does not give it to you.

**The honest route failed too.** The authority table has no entry for satellite 1, and
`source_may_perform` returns false for a source it does not know. It fails closed, which is
correct, and it is why step 3 is only two octets away.

**The third worked.** The authority table asks for something the sender *writes*: the source id
in the TC secondary header is a field, and you filled it in with `0x0042`. There is nothing in
the packet to check it against. EX-G02 built a control that decides what a station may do, and
it is right about that; it was never able to decide who a station is, and nothing said so out
loud until there was a second way in.

**There is a second thing here, and it is larger than the first.** Not one of the link-layer
defences this range has built was in the way. They are all inside `on_tc_frame` in the COMM
firmware, and `on_tc_frame` runs on bytes arriving from the space link. Your packet never went
near it. A defence is attached to a path, not to an asset. Four exercises' worth of work on the
uplink protects the uplink.

**And the relay is not a separate thing from the attack.** The crosslink forwards packets for
addresses that are not local — that is the whole feature, and it is what carried the ping in the
opening. Routing between an outside bus and an inside bus is what makes every internal node
externally reachable. The operator got a spacecraft they could not otherwise reach. So did you.

`mitigation.md` is what to do about it, and what it does not fix.
