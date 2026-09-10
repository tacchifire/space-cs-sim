# EX-G02 mitigation — read the identity that was already there

## The change

`CUBERANGE_OBC_REQUIRE_AUTHORITY=1`, and one table.

```c
static const struct pus8_authority AUTHORITY_TABLE[] = {
	{ GROUND_PRIMARY_ID, FUNC_SET_COMM_RAIL },
	/* GROUND_BACKUP_ID is deliberately absent. */
};

#if CUBERANGE_OBC_REQUIRE_AUTHORITY
	if (!source_may_perform(source_id, function_id)) {
		printk("OBC: REJECTED PUS 8 function %u from source %u - not authorised\n", ...);
		return;
	}
#endif
```

`source_id` was already a local variable in `handle_space_packet`, parsed out of every
telecommand and printed to the console. The mitigation passes it one function further down and
looks at it. No new field, no new protocol, no key.

The check runs **before** the argument copy. Deciding whether to act on a request is not something
to do after the request's data is already in a local buffer.

## Why the table is absent from the vulnerable build

`#if` around the table itself, not just the check. A table compiled in and never consulted would
be a different defect and a less honest one — nobody writes an authority table and forgets to
call it. What happened here is that nobody wrote one.

## Why "one thing differs" is checkable here

`firmware-matrix.yml` declares this pair, and `tools/config_diff_gate.py` proves the two images
differ by exactly `CUBERANGE_OBC_REQUIRE_AUTHORITY` and nothing else — same 836 Kconfig symbols,
same compiler command line for every translation unit, different ELFs.

Two pairs now share one application. EX-F01 holds `CUBERANGE_OBC_REQUIRE_AUTHORITY` equal across
its halves; this pair holds `CUBERANGE_OBC_PUS8_LENGTH_CHECK` equal across its own. That is what
lets the gate say "exactly one differs" about both of them.

## Signed is not the same as permitted, and neither is authenticated

Worth separating, because the three get conflated:

- **Authentication** answers *is this really the backup station?* Nothing here does that. The
  source id is a plain field an attacker on the link writes freely.
- **Authorisation** answers *may the backup station do this?* That is what this mitigation adds.
- **Integrity** answers *did this command arrive as sent?* The FECF catches accidents; EX-L01's
  anti-replay catches a resend.

This mitigation is only the middle one. It stops a station from exceeding its own authority. It
does not stop anyone from claiming to be a station with more.

## What this does NOT solve

Say it plainly, because a mitigation oversold is worse than none:

- **The source id is not authenticated.** An attacker on the link writes `0x0042` and is the
  primary station as far as this table is concerned. This raises the cost from *use your own
  console* to *reach the link and change one octet*, which is real and small. EX-L01's subject is
  the link; combining that exercise's mitigation with this one is the first honest configuration,
  and even then the shared secret is committed in the clear.
- **The table is static and compiled in.** Real authority changes: a site goes down, a pass is
  handed over, a station is decommissioned. Changing this one is a firmware build and an upload,
  which nobody does mid-pass — so the practical answer under pressure would be to bypass it.
- **It covers PUS 8 only.** Service 17 is not gated, which is correct here — the backup station
  may ping — but nothing in the design stops the next privileged service from being added without
  a row in this table. The check is opt-in per service rather than default-deny, and default-deny
  is what a flight design would want.
- **A rejection is a printk.** It reaches the OBC console, which is a file in the emulation and
  a UART on hardware. It does not reach the ground. An operator who is being locked out by a
  misconfigured table and an attacker being refused look identical from the downlink, which is to
  say invisible. That is the next thing to build, and it is EX-G01's "nothing marks the
  transmission" arriving from the other direction.
- **Nothing bounds what an authorised station may say.** The primary station may power the rail;
  it may also power it off during an eclipse. Bounding *what a command may ask for* is EX-A01's
  subject and it is not solved here.

## What a flight design would do instead

Authority would be bound to an authenticated channel, not to a field in the packet: the identity
comes from whoever holds the key that signed the frame, and the table is keyed on that. CCSDS
SDLS (355.0-B-2) provides the frame-level security association this would sit on, and NASA
CryptoLib — already in this repository as a golden-vector oracle — implements it.

The table itself would be uploadable and versioned, with the spacecraft refusing a version older
than the one it holds, so a hand-over does not require a firmware build and a replayed old table
does not restore a decommissioned station.

And the refusal would be telemetry: a PUS 1 acceptance-failure report naming the service, the
subtype and the source, so that being locked out and being attacked are distinguishable from the
ground while the pass is still open.
