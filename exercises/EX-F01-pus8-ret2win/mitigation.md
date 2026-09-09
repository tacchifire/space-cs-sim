# EX-F01 mitigation — size the copy from the buffer, not from the packet

*日本語版: [mitigation.ja.md](mitigation.ja.md)*

## The change

One check, behind one build flag:

```c
#if CUBERANGE_OBC_PUS8_LENGTH_CHECK
	if (arg_len > sizeof(args)) {
		printk("OBC: REJECTED PUS 8 argument block of %u octets (buffer is %u)\n",
		       (unsigned int)arg_len, (unsigned int)sizeof(args));
		return;
	}
#endif
	memcpy(args, &app_data[2], arg_len);
```

Build both profiles:

```bash
west build -b nucleo_h753zi -d build-obc      firmware/apps/obc -- -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=0
west build -b nucleo_h753zi -d build-obc-hard firmware/apps/obc -- -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1
```

`sizeof(args)`, not the literal 16. The two go out of sync the first time somebody resizes the
buffer, and the version that reads the size from the buffer keeps working.

## Why the builds are otherwise identical

This is the exercise where the anti-strawman proof matters most, because "the platform's defences
were switched off" is the obvious suspicion about any stack-overflow demonstration. It is not what
happened, and `tools/config_diff_gate.py` says so mechanically rather than asking you to believe it:

```
EX-F01: 836 Kconfig symbols identical
EX-F01: only CUBERANGE_OBC_PUS8_LENGTH_CHECK differs (0 -> 1)
EX-F01: ELFs differ
EX-F01: 2 translation units compiled identically apart from -DCUBERANGE_OBC_PUS8_LENGTH_CHECK
```

The last line is what a `.config` diff cannot give you: Kconfig says nothing about compiler options,
so a pair could pass a clean Kconfig comparison while the vulnerable side was built with a
protection removed at the command line.

The protections that are ON in both builds, and did not stop this: `ARM_MPU`,
`HW_STACK_PROTECTION`, `MPU_STACK_GUARD`, `SRAM_REGION_PERMISSIONS`, `XIP`. The ones that are OFF in
both are Zephyr's own defaults for this board: `STACK_CANARIES`, `STACK_SENTINEL`, `USERSPACE`,
`STACK_POINTER_RANDOM=0`.

The MPU stack guard is worth a sentence, because its name suggests it should have caught this. It is
a region placed **below** a thread's stack, to catch a stack that grows too far down. A copy running
upward through the current frame never reaches it. Zephyr's own documentation says the guard detects
overflow rather than preventing data corruption, and this is what that distinction looks like.

## What this does NOT solve

Say it plainly, because a mitigation oversold is worse than none:

- **It fixes one copy.** Every other length in this firmware that comes off the wire is still
  trusted. The bug is not "this memcpy was unbounded", it is that nothing in the build makes an
  unbounded one visible, and that survives fixing this line.
- **It does not make the frame safe.** There is still no canary, so the next unbounded write in any
  other handler reaches the same saved return address just as easily. Turning `STACK_CANARIES` on
  would raise the cost of the whole class rather than one instance — at the price of a check on
  every function return, which is a budget decision and not a free win.
- **It leaves the maintenance handler in the image.** `maintenance_inhibit_fdir` is still there, at
  a known address, with its enable bit clear. The authorisation check works; it is simply not on
  the path an attacker takes. Removing dead privileged code is a better answer than guarding it,
  and it is the one this mitigation does not make.
- **It says nothing about detection.** The rejection goes to a console. No PUS 5 event reaches the
  ground, so an operator learns nothing about somebody probing the command handler with over-long
  packets — which is exactly the reconnaissance that precedes this attack.

The honest description: this closes one overflow and leaves the conditions that made it exploitable
exactly as they were.

## What a flight design would do instead

Generate the command definitions — service, subtype, function, argument layout and its length —
from one machine-readable source, and generate both the flight parser and the ground checker from
it, so a length can only be wrong in one place and both ends notice. Reject at the framing layer
what the application layer would have to bound. Turn on stack canaries and spend the cycles. And
strip maintenance entry points from the flight image rather than disabling them, because a disabled
function is still a gadget.

CCSDS SDLS (355.0-B-2) authenticates the link and would not have helped here: the packet was
well-formed and would have been signed. That is the point of putting this exercise after EX-L01.
