# EX-G03 mitigation — the counter belongs to the channel, not the link

## The change

`CUBERANGE_COMM_ANTIREPLAY_PER_VC=1`, and a table instead of a variable.

```c
static uint8_t last_seq_vc[64];
static uint8_t have_vc[64];
uint8_t vc = cr_tc_frame_vcid(frame, len);

if (have_vc[vc]) {
	int8_t ahead = (int8_t)(seq - last_seq_vc[vc]);
	if (ahead <= 0) { reject; }
}
```

Sixty-four entries because the VCID field is six bits, and a fixed table with no eviction because
a cache with a policy is a policy an attacker can drive. 128 bytes.

The anti-replay is ON in both halves of this pair. What differs is where the counter lives, which
is why `firmware-matrix.yml` declares the pair on `CUBERANGE_COMM_ANTIREPLAY_PER_VC` and not on
`CUBERANGE_COMM_ANTIREPLAY`.

## Why the frame already carried what was needed

`cr_tc_frame_vcid` is new; the field is not. CCSDS 232.0-B-4 4.1.2.5 puts a six-bit virtual
channel identifier in octet 2 of every TC transfer frame, and the decoder was reading the
sequence number out of octet 4 while stepping over it. COP-1 maintains that sequence number per
virtual channel — the FARM state is per VC — so the standard had already answered the question
this exercise asks.

That is the same shape as EX-G02, where the PUS source id had been arriving, being logged, and
deciding nothing. Twice now the fix has been to read a field that was already on the wire.

## What this does NOT solve

Say it plainly, because a mitigation oversold is worse than none:

- **The attacker picks the channel.** Pass `--vcid 0` and the attack works against the mitigated
  build exactly as before. The VCID is a plaintext field and nothing authenticates who may
  transmit on it, so this raises the cost from *transmit anything* to *transmit on the operator's
  own channel* — which is one number, read off the operator's own traffic. Real, and small. It is
  the same ending as EX-L01 and EX-G02, for the same reason: none of these frames are
  authenticated.
- **Sixty-four counters, and an attacker can fill them.** Nothing here is exhausted by that — the
  table is fixed-size and every entry is independent — but an attacker transmitting on all
  sixty-four channels denies all sixty-four. There is no per-channel access control, because
  there is no authentication to build one on.
- **The window is still eight bits.** A frame far enough ahead wraps to behind, as the exercise's
  own hint explains, and a reboot clears the table and makes an old recording valid again. Both
  are EX-L01's limits and this changes neither.
- **It does not detect the condition.** The console still says `REJECTED replayed frame`, now
  with a channel number. Nothing on the spacecraft notices that a channel it has never seen
  before has appeared, or that a channel has gone quiet since one arrived. A defence that could
  say *"VC 1 appeared at 14:02 and VC 0 has been rejected ever since"* would have turned this
  exercise into a five-minute diagnosis, and it would be worth more than the fix.

## What a flight design would do instead

COP-1 properly, rather than a sequence check that resembles it: FARM-1 with its sliding window,
the retransmit state machine, and CLCW telemetry reporting the receiver's expected sequence number
back to the ground. That last part is what this mitigation most conspicuously lacks — the ground
has no way to learn what the spacecraft expects, so an operator who has been locked out cannot
resynchronise without a reset.

Underneath it, authentication: CCSDS SDLS (355.0-B-2) binds the frame to a key, which is what
makes "who may transmit on this virtual channel" a question with an answer. Every exercise in this
range ends here, which is itself the lesson of the set.
