# EX-S02 — Mitigation

## The fix

`CUBERANGE_OBC_REQUIRE_PUS_AUTH=1`. The OBC verifies the telecommand's own MAC before it dispatches
anything, and strips the trailer in place so the parser below sees exactly the signed octets:

```c
if (!pus_auth_ok(packet->data, &plen)) {
        printk("OBC: REJECTED an unauthenticated telecommand from node %u\n", csp_conn_src(conn));
        csp_buffer_free(packet);
        continue;
}
handle_space_packet(packet->data, plen, csp_conn_src(conn));
```

No handler below it knows. That is deliberate: a control every handler had to remember to call
would be forgotten by one of them, and the one that forgot would be the interesting one.

Three pieces make it work and each is load-bearing:

- **The MAC covers the source id.** That is the field EX-X01 forges, and it is now covered
  wherever the packet arrives from.
- **The sequence number is inside the authenticated region.** Advancing it invalidates the MAC, so
  a recording cannot be replayed by bumping a counter — EX-L01's attack, one layer up.
- **The length field is rewritten to cover the trailer before the MAC is computed.** So the number
  an attacker would change to cut the trailer off is itself authenticated.

The anti-replay counter is **per source id**, not one counter for the link. Two ground stations are
two senders and one counter between them is EX-G03 a third time. That is implemented here rather
than written down as a limit, because by now this range has made the same mistake twice and a
third would not be teaching anything.

## What it costs

- **Twenty octets on every telecommand.** Measured: the same PUS 17,1 goes from 11 octets of
  payload to 31. On a link measured in hundreds of bits per second that is not free, and it is
  the reason a real mission might put the MAC at the frame layer instead — which is what CCSDS
  did, and which is exactly the decision EX-S01 shows the consequence of.
- **Every node that verifies needs the key.** The OBC holds it now. A payload computer that also
  accepted telecommands would need it too, and so would anything that forwarded on their behalf.
  The set of things that must be trusted grew.
- **The nonce.** GCM is catastrophic under nonce reuse — it leaks the authentication subkey, not
  just a plaintext — and this profile derives the nonce from APID, source id and sequence rather
  than transmitting it. Within one key that repeats only if a source reuses a sequence number,
  which is the event the replay check refuses, so the two properties are the same property. It
  is written down in `pus_auth.py`, in the golden file and here because a future change that
  relaxes the counter would silently break the cipher, not just the replay check.

## What it does not solve

- **A peer with the key.** Any shared secret in a constellation is held by every member, and a
  compromised spacecraft is a member. `keys.py` says at length why one written-down key is the
  honest shape for a teaching range; the sentence that survives is that a fixed shared secret is
  a thing you HAVE rather than a thing you prove.
- **Authorisation.** EX-G02 is untouched, and this is the most important line in this file.
  Authentication answers who; it has never answered what. An authenticated station asking for
  something it should not have is EX-G02, and it is still there after all of this.
- **Telemetry.** The reports going the other way carry no trailer. EX-G04 established that a
  refusal the ground cannot hear is useless; a refusal the ground cannot AUTHENTICATE is one an
  attacker can forge, and an operator who can be told "your command was refused" by anybody is an
  operator who can be made to stop trying. Not implemented; named.
- **Key distribution, rotation, revocation.** None of it. One constant in a header file.

## The thing worth carrying out of this

Four exercises end at one sentence now, and EX-S02 is the one that inverts it.

EX-X01: a defence is attached to a path, not to an asset.
EX-G04: a control that cannot report is a control the ground cannot use.
EX-S01: the strongest control in the system is still attached to a path.
EX-S02: **so bind it to the thing you actually care about.**

The request is what the spacecraft acts on. It was always the right thing to authenticate, and the
reason it usually is not is that the frame layer is cheaper and the standard is already written.
That is a real engineering trade and this exercise does not pretend otherwise — it shows what the
trade buys and what it costs, in octets, on the wire.
