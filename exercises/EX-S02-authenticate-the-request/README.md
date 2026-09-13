---
id: EX-S02
title: Authenticate the request, not the road
layer: application
difficulty: advanced
duration: 45-60 min
prerequisite: EX-S01
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-S02 — Authenticate the request, not the road

## The situation

EX-S01's mitigation named three ways to close the gap it demonstrates and said the third was the
one that actually answers it:

> Authentication at the application layer. The PUS packet carries its own MAC. Then it does not
> matter which link it arrived on — which is the property the other two do not have.

This is that, implemented. The telecommand carries a trailer inside the Space Packet, and the
packet's own length field covers it:

```
primary(6) | PUS TC secondary(5) | application data | SEQ(4) | MAC(16)
```

AES-256-GCM over everything up to the MAC, including the source id and the sequence number. The
OBC verifies it before dispatch, and nothing downstream knows that happened — the parser sees
exactly the octets that were signed.

**The trailer is mission-defined and has no outside oracle.** ECSS-E-ST-70-41C defines no
authentication field for a TC packet, and CCSDS puts security at the transfer-frame layer, which
is the layer this exists to stop depending on. `tests/golden/pus_auth.json` says that in its
`oracles` list rather than naming something that merely resembles one; what is checked from
outside is the primitive, against libsodium and the NIST vectors.

## Your objective

Switch satellite 0's COMM rail off.

Same position as EX-X01 and EX-S01: the crosslink, a compromised peer, no key.

```bash
make exercise EX=EX-S02-authenticate-the-request          # a shell inside the range
python3 exercises/EX-S02-authenticate-the-request/solve.py   # at that shell's prompt
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError`.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-S02-authenticate-the-request RUN='python3 exercises/EX-S02-authenticate-the-request/solve.py'
```

## What happened

```
OBC: accepted a connection from 5 on port 10
OBC: APID 0x0a9 PUS 17,1 from source 66
OBC: PUS 17,2 report sent to COMM (counter 0)

OBC: accepted a connection from 13 on port 10
OBC: REJECTED an unauthenticated telecommand from node 13
```

The rail stays on. The attack that SDLS on the uplink could not touch — because SDLS is a
transfer-frame protocol and the crosslink carries CSP — is refused here, and the reason is that
the control moved off the link and onto the request.

Notice what did **not** have to change. The solver is the same file EX-X01 and EX-S01 use. The
crosslink is the same bus. The OBC's authority table, refusal reports and length check are all
still on and all still doing exactly what they did. One flag, one control, and the path stopped
mattering.

Also notice the cost, which is visible on the wire: the same PUS 17,1 that was 11 octets of
payload is now 31. Twenty octets on every telecommand, forever.

## What to conclude

**A control's scope is whatever it is bound to.** Bind it to a link and it covers that link — that
was EX-S01, with the strongest link-layer control this range has. Bind it to the request and it
covers the request, wherever the request goes. That is not a statement about cryptography; the
same sentence is true of the EPS's token, which is why that control survived a path its author
never considered while the authority table did not.

**And notice what is still unsolved, because it is the same thing every time.** Authentication
answers *who*. It has never answered *what*. EX-G02's authority table still decides whether an
authenticated station may switch a rail off, and if that table is wrong then a perfectly
authenticated command does the wrong thing. A range that deployed this and stopped would still
have EX-G02, exactly as it did before any of the crypto existed.

`mitigation.md` is what this costs and what it does not fix.
