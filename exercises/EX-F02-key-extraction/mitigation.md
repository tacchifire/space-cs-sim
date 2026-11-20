# EX-F02 mitigation — serve the dump from the block it is for

*日本語版: [mitigation.ja.md](mitigation.ja.md)*

## The change

One check, behind one build flag, after the two checks every build makes on the packet itself
(memory id 0, and 1 to 128 octets):

```c
#if CUBERANGE_OBC_PUS6_ALLOWLIST
	if (!dump_allowed(address, length)) {
		printk("OBC: REJECTED PUS 6 dump of %u octets at 0x%08x "
		       "(public block is %u octets at 0x%08x)\n",
		       (unsigned int)length, (unsigned int)address,
		       (unsigned int)sizeof(obc_public_block),
		       (unsigned int)(uintptr_t)obc_public_block);
		return;
	}
#endif
	public_block_refresh();
	send_dump_report(source_id, mem_id, address, length);
```

and the check:

```c
static bool dump_allowed(uint32_t address, uint16_t length)
{
	const uint64_t start = (uintptr_t)obc_public_block;

	return address >= start && (uint64_t)address - start + length <= sizeof(obc_public_block);
}
```

Every octet of the request has to be inside the block, not just the first: a request that starts
inside the block and runs past its end is the one a start-address check serves, and the verifier
sends exactly that. The sum is an offset in 64 bits because the address comes off the wire, and
`0xFFFFFFF0 + 0x20` is `0x10` in 32.

What it allows is one thing: `obc_public_block`, 64 octets of SRAM holding a magic, the uptime and
a message counter, refreshed for every dump. Nothing secret is ever written there, and that is the
real control — the allowlist does not decide what is sensitive, the layout does, and the allowlist
holds the line drawn around it.

Build both profiles:

```bash
make firmware-f02
```

which runs, for each half, the line below with the environment the Makefile sets up around it
(libcsp as an extra module):

```bash
west build -b nucleo_h753zi -d build-obc-f02-vuln firmware/apps/obc -- \
  -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_SIGN_REPORTS=1 \
  -DCUBERANGE_OBC_PUS6_ALLOWLIST=0 -DEXTRA_CONF_FILE="$PWD/firmware/apps/obc/pus_auth.conf"
west build -b nucleo_h753zi -d build-obc-f02-hard firmware/apps/obc -- \
  -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_SIGN_REPORTS=1 \
  -DCUBERANGE_OBC_PUS6_ALLOWLIST=1 -DEXTRA_CONF_FILE="$PWD/firmware/apps/obc/pus_auth.conf"
```

## Why the builds are otherwise identical

The pair is declared in `firmware-matrix.yml`, so `make pair-gate` holds it to the same six checks
as every other pair: identical Kconfig, no other `CUBERANGE_*` variable different, ELFs that
differ, the flag referenced by the source, identical compile lines apart from the one `-D`, and that
`-D` actually reaching a translation unit. It has not been run against this pair yet — the pair was
written on a machine with no Zephyr toolchain — so there is no gate output to quote here, and this
section will not pretend otherwise.

What is held equal, and why each one is held:

- **`CUBERANGE_OBC_SIGN_REPORTS=1` in both.** The premise, not a bystander. Signing is what compiles
  `cr_sdls_key` into the OBC; a hardened half without it would refuse to dump a key that was not
  there and look like a working allowlist. The verifier's first test checks that both ELFs hold the
  key before anything is run.
- **`CUBERANGE_OBC_PUS8_LENGTH_CHECK=1` in both.** EX-F01's overflow is closed, so the dump is the
  way to the key in this pair, and nobody reads the hardened half's result as "the overflow was the
  problem".
- **`CUBERANGE_OBC_REQUIRE_PUS_AUTH=0` in both.** The OBC verifies no telecommand, so the request is
  a plain frame, as EX-F01's was.
- **The dump handler in both.** A memory dump is the service. What the flag decides is which memory
  it may read.

## The design's two mitigations, and how much of each this is

Section 6.5 of the design names two honest mitigations for a key a dump can reach — (M1) an address
allowlist on Service 6, and (M2) a short life for key material, with explicit zeroisation — and
requires saying plainly that neither stops an attacker who already runs code. That last part is the
first item of the next section.

**M1 is this exercise's flag**, all of it, as above.

**M2 is mostly not done.** What this exercise does implement of it:

- **The dump handler keeps nothing.** `send_dump_report` builds the TM 6,6 in the CSP buffer it
  leaves in and copies the octets straight from where they live: no static, no frame-local staging
  array. Dump reports are also kept out of the report store EX-L02 added, because the store keeps
  reports verbatim and a verbatim dump report is a second copy with a longer life. This is in both
  builds; it is not the difference.

What it does not:

- **The key's own life is untouched.** `cr_sdls_key` is a `static const`. It is in flash from the
  moment the image is written until the image is replaced, at an address the symbol table names,
  and nothing in this exercise moves it. That residency is what M2 is about, and it is the one this
  exercise extracts.
- **The short-lived copies are mbedTLS's, not this firmware's.** `sign_report` expands the key into
  a GCM context for every report it signs. mbedTLS 3.6.2 — the version Zephyr v4.1.0 pins — wipes
  that context and the AES key schedule inside it when they are freed (`mbedtls_gcm_free`,
  `mbedtls_aes_free`, both through `mbedtls_platform_zeroize`). That was read in the library's
  source and not measured here. This firmware relies on it rather than doing anything of its own,
  and has not shortened the window: the expanded key exists for the length of every signing.
- **The transport keeps what a served dump sent.** A dump that goes out passes through buffers this
  handler does not own. libcsp v2.1 returns a CSP buffer to its pool as it is and wipes it only when
  it is next handed out (`CSP_BUFFER_ZERO_CLEAR`, which the library's CMakeLists defaults ON), and
  COMM's downlink task frames every report into static buffers that keep a frame's octets until a
  later frame happens to overwrite them. Both read in the source, neither measured.

## What this does NOT solve

Say it plainly, because a mitigation oversold is worse than none:

- **Code execution reads the key anyway.** The allowlist is a check inside one service. Code that is
  already running — EX-F01's attacker, in any build where that overflow is open — does not ask the
  service: it reads the key the way `sign_report` does, because the MPU maps flash readable to all
  code (design 7.4). No allowlist stops that, and nothing in this range does.
- **It bounds one service.** Any other way to read memory on request — another maintenance command,
  a debug interface, a file transfer of a memory region — needs its own bound. The flaw is not "this
  dump had no allowlist"; it is that the key is readable by anything in the image.
- **It covers one node.** COMM holds the same key to verify the link (EX-S01), and so does every
  image that signs or verifies with it. An allowlist on the OBC's dump protects the OBC's copy from
  the OBC's dump.
- **Every other OBC image still serves any address.** The handler is in every OBC this range builds
  and the flag defaults to 0, so `build-obc-f02-hard` is the only image with the allowlist.
  `SECURITY.md` lists that.
- **It says nothing about detection.** The refusal goes to a console, as EX-F01's does. No PUS 5
  event and no counter reach the ground, so somebody walking the dump service across the address
  space looking for a key leaves nothing an operator can see.

The honest description: this closes one way to the key and leaves the key exactly where it was.

## What a flight design would do instead

Keep the key out of the image. Provision it into storage the application cannot read back — a
secure element, or a crypto engine with key slots the CPU cannot export, on a part that has one; the
design records that this part's CRYP is not that. Where a key has to be in memory, load it for the
operation and wipe it afterwards with a primitive the compiler cannot remove. Give each function and
each node its own key, so that one extracted copy opens one door rather than all of them, and rotate
them (EX-S03), so that an extracted key has a bounded life.

Treat a memory dump as the privileged operation it is: authenticated (EX-S02), authorised per
station (EX-G02), restricted to regions declared at build time, and reported to the ground — every
dump, served or refused.

CCSDS SDLS (355.0-B-2) would not have helped here, and could not: the key it depends on is the thing
that was read.
