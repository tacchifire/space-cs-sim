# EX-B01 mitigation — authenticate power commands

*日本語版: [mitigation.ja.md](mitigation.ja.md)*

## The change

One check, behind one build flag:

```c
#if CUBERANGE_EPS_REQUIRE_AUTH
	if (memcmp(&data[3], POWER_TOKEN, sizeof(POWER_TOKEN)) != 0) {
		printk("EPS: REJECTED unauthenticated rail command from node %u\n", src);
		return;
	}
#endif
```

Build both profiles:

```bash
west build -b nucleo_h753zi -d build-eps-vuln firmware/apps/eps -- -DCUBERANGE_EPS_REQUIRE_AUTH=0
west build -b nucleo_h753zi -d build-eps-hard firmware/apps/eps -- -DCUBERANGE_EPS_REQUIRE_AUTH=1
```

## Why the builds are otherwise identical

An exercise whose "vulnerability" comes from disabling a platform defence teaches something that
is not true. The two EPS images share a board, a `prj.conf`, a source file and compiler flags, and
you can check that mechanically:

```bash
make pair-gate
# EX-B01: both halves built from this source tree
# EX-B01: 836 Kconfig symbols identical
# EX-B01: only CUBERANGE_EPS_REQUIRE_AUTH differs (0 -> 1)
# EX-B01: ELFs differ (a37faa479b55... vs 1a9d8c2911ae...)
# EX-B01: CUBERANGE_EPS_REQUIRE_AUTH referenced in firmware/apps/eps
# EX-B01: 2 translation units compiled identically apart from -DCUBERANGE_EPS_REQUIRE_AUTH
```

This used to be a `diff` of the two Kconfig outputs that you had to remember to type. It proved
only the first of those six lines: nothing about compiler options, nothing about whether the flag
reached the compiler at all, nothing about whether any source reads it, and nothing about whether
the two builds came from this checkout.

Only `CUBERANGE_EPS_REQUIRE_AUTH` changes. The attack works against a normally-configured
satellite, and the mitigation is a line of application code — not a compiler flag.

## What this does NOT solve

Say it plainly, because a mitigation oversold is worse than none:

- **It is replayable.** The token is a fixed string. An attacker who ever sees one valid command
  can repeat it forever. A real design needs a counter or a challenge, which is EX-L01's subject.
- **It does not survive code execution.** The token is `static const` in `.rodata`, in flash,
  readable by anything running on the node — see EX-F02.
- **It does not authenticate the source.** It proves the sender knew a secret, not that it is the
  OBC. Every node that legitimately commands power must hold the same secret, so compromising any
  one of them is enough.
- **It protects one service.** Every other unauthenticated command on this bus is still open.

The honest description is: this raises the cost from *put a frame on the wire* to *first obtain a
secret*. That is a real improvement and a small one.

## What a flight design would do instead

Per-command authentication with a monotonic counter, keys provisioned per node rather than shared,
and the load switch for a critical rail behind an inhibit that a single bus command cannot clear.
CCSDS SDLS (355.0-B-2) covers the space link; the internal bus needs its own answer, which CSP
does not provide.
