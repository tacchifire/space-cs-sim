# EX-L01 mitigation — reject frames that are not moving forward

## The change

The TC transfer frame already carries an 8-bit sequence number. The mitigated COMM accepts a frame
only if that number is ahead of the last one it accepted:

```c
#if CUBERANGE_COMM_ANTIREPLAY
	if (have_last) {
		int8_t ahead = (int8_t)(seq - last_seq);

		if (ahead <= 0) {
			printk("COMM: REJECTED replayed frame seq=%u (last accepted %u)\n", seq, last_seq);
			return;
		}
	}
	have_last = 1;
	last_seq = seq;
#endif
```

The signed cast is the whole trick: an 8-bit counter wraps, and `seq > last_seq` would reject every
frame for half of each cycle.

```bash
west build ... firmware/apps/comm -- -DCUBERANGE_COMM_ANTIREPLAY=1
```

As with EX-B01, the two COMM images differ by one build flag and nothing else — no Zephyr option
changes between them.

## What this does NOT solve

- **It is not authentication.** Anyone who can transmit can still send a *new* command with a
  higher sequence number. This stops a recording, not a forger. Doing better needs the frame
  authenticated to a key, which is what CCSDS SDLS (355.0-B-2) is for.
- **An attacker who can suppress the real uplink wins.** Jam the operator, then transmit with a
  sequence number far ahead. The satellite accepts yours and rejects the operator's for the rest of
  the cycle — a denial of service built out of the defence itself.
- **Eight bits is a small window.** With 256 values and no persistence across reset, a reboot
  resets the state and an old recording becomes valid again. Try it: power-cycle COMM and replay.
- **It protects the link, not the bus.** EX-B01's attacker is untouched by this.

The honest description: this raises the cost from *record and retransmit* to *transmit at the right
moment, ahead of the operator*. Real, and much smaller than it looks.

## What a flight design would do instead

SDLS with a security association per virtual channel, AES-256-GCM, and an anti-replay window over a
sequence number that is authenticated rather than merely present — so that moving the counter
forward requires the key. That is Phase 2's subject, and it is also why the design refuses to
describe the current lab framing as CCSDS channel conformance.
