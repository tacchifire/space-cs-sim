---
id: EX-S01
title: The key that guards one door
layer: space link
difficulty: intermediate
duration: 30-45 min
prerequisite: EX-X01
ttp:
  # Deliberately empty, as everywhere else here. Mapping these to SPARTA and SPACE-SHIELD means
  # checking each ID against the official STIX exports, and there is no tool in this repository
  # that does it - so a filled-in list would be a guess wearing a citation.
  sparta: []
  space_shield: []
---

# EX-S01 — The key that guards one door

## The situation

You have cryptography now.

EX-X01 ended by saying that authenticating the sender needs SDLS and that this range did not
implement it. It does, on the space link: COMM verifies a CCSDS 355.0-B-2 authentication MAC —
AES-256-GCM — on every uplink frame before anything else reads it, and an anti-replay counter that
lives **inside** the signed portion rather than in a header field an attacker writes.

That is not a small thing, and it is worth seeing work before seeing what it does not do. The
console says so at boot and on every frame:

```
COMM: SDLS self-test passed (NIST AES-256-GCM vector)
COMM: authenticated frame, SPI 9 seq 1, 11 octets of payload
```

`make sdls` measures four things about it: the target computes the published NIST tag; an
authenticated frame is acted on; a frame with one payload octet flipped and **the FECF repaired**
is refused; a byte-identical replay is refused. EX-L01's attack is dead. So is the forgery half of
EX-G02: the source id the authority table reads is now covered by a MAC, so a station cannot claim
another station's identity on the uplink. The control that decided what a station may do has, for
the first time, something real behind who the station is.

## Your objective

Switch satellite 0's COMM rail off.

You are on the crosslink, exactly where EX-X01 left you. One spacecraft in the constellation is
yours. You do not have the key.

```bash
make exercise EX=EX-S01-the-key-that-guards-one-door          # a shell inside the range
python3 exercises/EX-S01-the-key-that-guards-one-door/solve.py   # at that shell's prompt
```

`make exercise` puts you in a shell **inside the range's network namespace**, and the solver has
to run from there. The namespace holds only loopback (SAFE_USE.md), and a new one is created per
invocation, so a solver started in another terminal gets `ConnectionRefusedError` — the space link
and the injector do not exist outside.

To run one command instead of getting a shell:

```bash
make exercise EX=EX-S01-the-key-that-guards-one-door RUN='python3 exercises/EX-S01-the-key-that-guards-one-door/solve.py'
```

## What happened

```
COMM: authenticated frame, SPI 9 seq 1, 11 octets of payload
COMM: uplink frame seq=0 carrying 11 octets -> OBC
OBC:  APID 0x0a9 PUS 17,1 from source 66
OBC:  PUS 17,2 report sent to COMM (counter 0)

OBC:  accepted a connection from 13 on port 10
OBC:  APID 0x0a9 PUS 8,1 from source 66
OBC:  PUS 8 executed - COMM rail OFF
EPS:  COMM rail OFF (commanded)
```

The first block is the operator, authenticated. The second is you, from the crosslink, with the
same command and the same two forged octets as in EX-X01. **Nothing changed.**

## What to conclude

SDLS did everything it claims and nothing it does not.

It is a **transfer-frame** security protocol. The security header sits between the TC primary
header and the payload, and the MAC covers that frame. The crosslink does not carry transfer
frames — it carries CSP, which is what an inter-satellite bus carries — so there is no frame for
SDLS to protect and no place in the packet for a security header. Your packet did not fail a check.
It never met one.

Look at what the console shows. `accepted a connection from 13` — the OBC names the CSP address
your packet arrived from, in the same log where it then names source 66. Both numbers were on the
wire the whole time and one of them is a claim.

**The failure mode to carry away is not technical.** It is that after this deployment somebody
writes "the uplink is authenticated" in a document, and that sentence is true, and it is read as
"the spacecraft only accepts authenticated commands", which is not. A control's scope is the path
it sits on. EX-X01 said a defence is attached to a path rather than to an asset; this is the same
sentence again, with the strongest control this range has, which is the version of it that is easy
to miss.

And notice which of EX-X01's two lessons SDLS did fix. The authority table asked for something the
sender WRITES; a MAC turns that into something the sender must POSSESS — on the uplink. The EPS's
token was already possession-based and needed no help. Cryptography did not change which controls
survive a new path; it changed which category one control is in, on one path.

`mitigation.md` is what to do about it, and what that costs.
