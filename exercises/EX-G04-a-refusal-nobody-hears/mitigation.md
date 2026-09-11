# EX-G04 mitigation — say what was refused, and why, while the pass is open

## The change

`CUBERANGE_OBC_VERIFY_REPORTS=1`, and the refusal gets a telecommand's worth of reply.

```c
static void send_acceptance_failure(uint16_t dest_id, const uint8_t *failed_packet,
				    uint8_t failure_code)
{
	...
	sec[1] = 1;                       /* service 1, request verification */
	sec[2] = 2;                       /* subtype 2, acceptance failure   */
	memcpy(app, failed_packet, 4);    /* the request id, verbatim */
	app[4] = failure_code;
}
```

ECSS-E-ST-70-41C service 1. The request id is the refused packet's **own first four octets** —
version, type, secondary header flag and APID, then sequence flags and count. Nothing is invented
and nothing is remembered: the ground matches the report against a command it already has a copy
of. `src/cuberange/proto/pus.py` has `request_id()`, and a test asserts it equals the encoded
packet's `raw[0..4]` rather than trusting that it does.

The report is addressed to the station whose command was refused, because on a channel where
every station hears every downlink, telling the other operator their command failed would be a
new way to confuse a pass.

## What had to change on the ground, and why it is not incidental

`GroundStation.ping()` drained the link before transmitting — and discarded what it drained.
A PUS 1,2 answers a PUS 8, and nothing was polling between those two, so the report the
spacecraft sent precisely so that the ground would know was being thrown away by the next ping.
`collect()` classifies instead of discarding. A drain that drops evidence is not a drain, and on
a range built to teach about evidence that is worth more than a line of code.

## What this does NOT solve

Say it plainly, because a mitigation oversold is worse than none:

- **The report is not authenticated either.** Anyone who can transmit can forge a PUS 1,2 and
  tell an operator their command was refused when it was executed, or the reverse. This is the
  fourth exercise to end here, which is the point of the set rather than an oversight in it.
- **It covers one refusal.** The authority check reports; the PUS 8 length check does not, the
  unknown-function path does not, and COMM's replay rejection — EX-G03's whole subject — does
  not, because COMM sits below the packet layer and the standards answer there is CLCW in the TM
  frame's operational control field, which is a bigger change than this one.
- **There is no success report.** ECSS 1,1 and 1,7 exist, and an operator who receives nothing
  still cannot distinguish "accepted and working" from "lost". This exercise fixes the ambiguity
  in one direction only: a refusal is now loud, and a success is still silent.
- **Nothing correlates it to a pass.** The report names the request, not the time or the
  station's own context, and the spacecraft's clock is uptime. Reconstructing a sequence of
  events after the fact still needs the ground's log.
- **And it does not detect a pattern.** One refusal is reported; a hundred refusals are reported
  a hundred times. Nothing on board notices that every command from one source has been refused
  for ten minutes, which is the observation that would actually have named EX-G03's fault.

## What a flight design would do instead

The full verification service: 1,1 and 1,2 for acceptance, 1,7 and 1,8 for completion, with the
acknowledgement flags in the telecommand's own secondary header deciding which are sent — the
`ack` field this codec already carries and nothing reads. That is the standard's answer to "which
of my commands worked", and it is four subtypes rather than one.

Underneath it, event reporting (service 5) for the conditions a single refusal cannot express: a
source that has been refused repeatedly, a virtual channel that has appeared, a counter that has
jumped. And underneath that, authentication, so a report means something about who sent it —
which is where every exercise in this range ends, for the same reason.
