# EX-X01 — Mitigation

## The fix

`CUBERANGE_OBC_CROSSLINK_ORIGIN=1`. One condition:

```c
static bool origin_permits_claim(uint16_t source_id, uint16_t via)
{
	if (source_id != GROUND_PRIMARY_ID && source_id != GROUND_BACKUP_ID) {
		return true;
	}
	return via == COMM_ADDR;
}
```

`via` is `csp_conn_src(conn)` — the CSP address the packet actually arrived from, which the
router filled in from the connection, not from anything the sender wrote in the PUS header.

A packet claiming to come from a ground station has to have come from this spacecraft's own COMM.
A peer may talk to us. A peer may not be the ground.

Measured, same attack, same moment, the two builds side by side:

```
CUBERANGE_OBC_CROSSLINK_ORIGIN=0   OBC: PUS 8 executed - COMM rail OFF
                                   EPS: COMM rail OFF (commanded)

CUBERANGE_OBC_CROSSLINK_ORIGIN=1   OBC: REJECTED a packet claiming source 66 that arrived
                                        from node 13
                                   OBC: PUS 1,2 acceptance failure reported to source 66 (code 4)
```

## Why this shape and not another

The obvious fix is "authenticate the sender", and this range cannot do it. SDLS is not
implemented — `ASSURANCE.md` says so and will keep saying so until it is true — and a lab
implementation of it would not make the claim any more true.

So the control here checks something else: **where the packet entered**, which the attacker does
not choose, instead of **who sent it**, which the attacker writes. That is a topology check. It is
weaker than authentication and it is available today, which is a trade this write-up would rather
state than hide.

Note what it does NOT do: it does not stop satellite 1 from talking to satellite 0. Peers have
business with each other, and the relay in the opening of the exercise is exactly that business.
It refuses one specific claim — that a packet arriving from outside this spacecraft is the ground.

## What it does not solve

- **An attacker who compromises our own COMM.** Then `via` is COMM_ADDR and the check passes.
  The control believes the COMM, because at some point something has to be believed. Moving the
  trust boundary is not the same as removing it, and this moves it to the node that already had
  to be trusted to deframe the uplink.
- **Anyone who can transmit on the space link.** This control says nothing about the uplink. That
  is EX-L01's ground, and EX-L01's mitigation notes are equally clear that its own answer —
  a sequence counter — does not authenticate either.
- **A peer commanding us as a peer.** Nothing here gates what a legitimate neighbour may ask for.
  The authority table is keyed on ground station ids; there is no equivalent for spacecraft, and
  building one would repeat this exercise's mistake unless the identity in it were bound to
  something better than a field in a header.
- **Reaching internal nodes directly.** The crosslink still routes to satellite 0's EPS and ADCS.
  `verify_ex_x01.py` measures that the EPS refuses it on BOTH builds - `REJECTED unauthenticated
  rail command from node 13` - and that is the EPS's own control working, not this one. Nothing
  stopped the packet from arriving. The ADCS torque limit from EX-A01 is likewise a check on the
  command, not on its origin. Both survive the new path for the same reason: they check a property
  of the request rather than a claim about the requester.
- **A second crosslink attachment.** The check compares against COMM_ADDR, one address. A
  spacecraft with two radios would need both, and a list of trusted attachments is a list of
  things to get wrong.

## The thing worth carrying out of this

Two controls were in the way and one of them worked.

The EPS asks for a token — something the attacker must **have**. It survived a path its author
never considered, because possession does not depend on how you arrived.

The OBC asked for a source id — something the attacker **writes**. It did not survive, and it did
not survive on the first day a second path existed.

When you add a way in, the controls that fail are the ones keyed on assertions. Working out which
of your controls are which is a cheaper exercise than discovering it the way this one does.

## Detection, if you cannot fix it

The hardened build's refusal does not go back to the attacker. `send_acceptance_failure` connects
to this spacecraft's own COMM, so the PUS 1,2 goes **down the space link to the real ground
station** — which learns that a command it never sent was refused in its name.

That is a better signal than it first looks. A ground station that receives a refusal for a
request id it has no record of issuing is being told, by its own spacecraft, that somebody else is
using its identity. Nothing else in this range produces that.

## A note on the plumbing, because it failed silently

The crosslink attachment has a CSP address of its own — `8i + 6`, alongside the four node roles
— and that is not cosmetic. libcsp's split horizon appears three times in `csp_io.c` and asks the
same question each time:

```c
if (csp_iflist_is_within_subnet(iface->addr, routed_from)) continue;
```

It compares the OUTGOING interface's address against the INCOMING interface's subnet. Register
both of a router's interfaces with the node's own address, as the obvious first version of this
did, and that is true for every netmask — `csp_iflist.c:21` builds the mask from the netmask and
netmask 0 builds mask 0, under which every address equals every other. The router then forwards
nothing. There is no error, no log line and no counter: the packet arrives on fdcan2 and stops.

It was found by injecting a frame and watching the EPS say nothing, which is also what the EPS
says when the frame never arrives — the same observation EX-G04 is about, one layer down.
