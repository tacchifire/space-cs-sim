# EX-A01 mitigation — bound the command by what the actuator can do

*日本語版: [mitigation.ja.md](mitigation.ja.md)*

## The change

One check, behind one build flag:

```c
#if CUBERANGE_ADCS_TORQUE_LIMIT
	if (torque > TORQUE_AUTHORITY_MNM || torque < -TORQUE_AUTHORITY_MNM) {
		printk("ADCS: REJECTED out-of-authority torque %d mNm from node %u (limit +-%d)\n",
		       torque, src, TORQUE_AUTHORITY_MNM);
		return;
	}
#endif
```

Build both profiles:

```bash
west build -b nucleo_h753zi -d build-adcs-vuln firmware/apps/adcs -- -DCUBERANGE_ADCS_TORQUE_LIMIT=0
west build -b nucleo_h753zi -d build-adcs-hard firmware/apps/adcs -- -DCUBERANGE_ADCS_TORQUE_LIMIT=1
```

## Refused, not clamped

The obvious alternative is to clamp the value to the authority and carry on. Do not.

Clamping executes a command the operator did not give. When the anomaly investigation starts, the
log says the ADCS performed a 20 mNm slew and the operator is certain they commanded 30000, and the
disagreement costs a day. Refusing produces an event with the offending value in it, which is the
difference between a confusing anomaly and a diagnosable one.

The general rule: a value outside the physical envelope is not a value to be trimmed, it is
evidence that whoever sent it was wrong about the spacecraft.

## Why the builds are otherwise identical

The two ADCS images share a board, a `prj.conf`, a source file and compiler flags. Unlike EX-B01,
you do not have to run the `diff` yourself — `tools/config_diff_gate.py` does it for every declared
pair and is part of `make check`:

```bash
python3 tools/config_diff_gate.py
# EX-A01: 836 Kconfig symbols identical
# EX-A01: only CUBERANGE_ADCS_TORQUE_LIMIT differs (0 -> 1)
# EX-A01: ELFs differ
# EX-A01: 2 translation units compiled identically apart from -DCUBERANGE_ADCS_TORQUE_LIMIT
```

The last line is the one EX-B01's manual `diff` could not give you: Kconfig says nothing about
compiler options, so a pair could pass a clean `.config` diff while the vulnerable side had been
built with a protection switched off at the command line.

## What this does NOT solve

Say it plainly, because a mitigation oversold is worse than none:

- **It bounds one field of one command.** Every other command on this bus still accepts whatever
  the wire format allows. The bug is not "the torque check was missing", it is "nobody derived the
  command envelope from the hardware", and that survives fixing this one line.
- **It bounds magnitude, not accumulation.** Twenty in-authority commands in a row still spin the
  spacecraft up. Nothing here rate-limits, and nothing tracks total momentum. An attacker who is
  patient, or a control loop with a sign error, gets the same result more slowly.
- **It does not detect the attempt.** The rejection goes to a console. No event reaches the ground,
  no counter increments in housekeeping, so an operator learns nothing about somebody probing the
  bus. That is a detection exercise this range does not have yet.
- **It does not authenticate anything.** The check does not care who sent the command, and it
  should not — but that means a compromised OBC can still slew the spacecraft anywhere inside the
  envelope, which is quite far enough to break a pointing requirement.

The honest description: this closes the *implausible* commands and leaves every *plausible* abuse
open.

## What a flight design would do instead

Command envelopes derived from the hardware datasheet and generated into both the flight software
and the ground checker from one source, so the two cannot drift. Momentum accounting with a
budget, not just an instantaneous limit. A rejection that raises a telemetry event rather than a
console line. And attitude commands routed through a mode manager that knows whether a slew is
permitted at all in the current mode — most real ADCS incidents come from a command that was
individually legal and wrong for the situation.
