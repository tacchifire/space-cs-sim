# What CubeRange does and does not support as a claim

*日本語版: [ASSURANCE.ja.md](ASSURANCE.ja.md)*

A simulator that is useful for training is routinely mistaken for evidence about flight hardware.
This document exists so that mistake has to be made deliberately.

## What a result here does support

- **That a code pattern is exploitable.** The firmware is real Zephyr, cross-compiled by the real
  toolchain, running as instructions on a Cortex-M7 model. When an exploit works, the bug is in the
  code, not in a script pretending the bug exists.
- **That a protocol behaviour is what the standard says**, to the extent stated in the design
  document — and no further, and not uniformly across layers. Be precise about which:

  | Layer | Independent oracle | Where |
  | --- | --- | --- |
  | Space Packet primary header | spacepackets, ccsdspy, and a hand decode from 133.0-B-2 | `test_golden_spacepacket.py` |
  | FECF CRC-16 | crcmod, fastcrc, crc, NASA CryptoLib, and the CCSDS 132.0-B-3 text | `test_golden_frame.py` |
  | CSP v1 header and CFP-over-CAN | libcsp itself, run to produce the vectors | `tests/golden/csp.json` |
  | PUS-C secondary headers | spacepackets | `test_oracle_pus.py` |
  | **TM/TC transfer frame HEADERS** | **none** | — |

  Read the last row precisely, because the row above it is easy to over-read. The FECF that covers
  a transfer frame is independently verified four ways, and one vector reproduces a value Yamcs
  publishes for a real TM frame. The FIELD PACKING of the TC and TM primary headers is not: no
  second implementation of them exists here, so their layout still rests on this project's reading
  of CCSDS 232.0-B-4 and 132.0-B-3. A transposed field inside a header would satisfy every test in
  the tree.

  All committed vectors are produced by `tools/gen_golden.py` from oracles that
  `tools/oracles/build.sh` builds — and until 2026-09-09 neither of those oracles was in the
  repository, so the vectors could not be regenerated and their independence was an assertion.
  `test_golden_coverage.py` now requires every committed vector to be read by a test, because a
  vector nobody checks against looks like coverage and is not.
- **That a mitigation blocks a specific attack, and does not break the feature it protects.** Every
  exercise asserts both, plus the case where the legitimate operation still succeeds.
- **That a control is worth building.** Which is the point of a range.

## What it does not support

Do not cite a CubeRange result as evidence of any of these:

| Claim | Why not |
| --- | --- |
| Flight readiness or qualification | Nothing here has been near a qualification process |
| Real-time behaviour | Renode is a functional emulator. Instruction timing, cache, bus contention and interrupt latency are not modelled |
| RF or link-layer conformance | The outer framing is "CubeRange lab framing", not a CCSDS CLTU. There is no modulation, coding, Doppler or noise |
| CCSDS or ECSS conformance | Subsets are implemented, with documented deviations. Conformance is a testing regime this project has not undergone |
| Cryptographic assurance | SDLS is not implemented yet. When it is, it will be a lab implementation, not a validated one |
| Hardware security | No side channels, no glitching, no fault injection, no silicon errata. Renode models registers, not physics |
| Radiation or fault tolerance | Not modelled at all |
| That a real satellite is or is not vulnerable | This is a synthetic target. It resembles real systems because it implements the same standards, which is not the same as being one |

## Specific fidelity limits that have been measured

These came out of testing the emulator itself, and each one rules out a class of exercise:

- **Renode raises no fault for access to unmapped address space.** An exercise triggered by a wild
  pointer would silently do nothing, so there is none.
- **`MPU_CTRL.PRIVDEFENA` is ignored for privileged accesses.** Only explicitly programmed MPU
  regions are enforced, so "no region means denied" does not hold here as it would on silicon.
- **The CAN hub does not model bus acknowledgement.** Error frames and bus-off diagnostics cannot
  be taught on this platform.
- **Renode does enforce MPU execute-never.** `probe.sh` section G is the reproduction: an
  instruction fetch at 0x24003000 is refused while the same query in flash succeeds. This is why
  EX-F01 is code reuse rather than injected shellcode — shellcode fails here exactly as it fails on
  real silicon, and an exercise that needed the MPU disabled would be teaching something untrue.

  **How you ask matters.** `cpu TranslateAddress` in 1.16.1 caches by address and not by access
  type, so a Read query followed by an InstructionFetch query on the same address returns success
  for both. Checking execute-never in that order — the natural order — reports that SRAM is
  executable. Ask about the fetch first, or in a fresh process. Pinned as a negative assertion (D22)
  so a future Renode that fixes the cache makes the probe fail rather than quietly changing what
  this paragraph means.

The full register of emulator and library defects is section 3.2 of
`docs/superpowers/specs/2026-08-28-cuberange-design.md`.

## Where the claims come from

Every capability the design relies on is reproduced by `tools/renode-probe/probe.sh`, which runs as
the first job of `make check`. If a claim cannot be reproduced there, it does not belong in the
design — and the harness carries deliberate-failure self-tests, because a gate that cannot fail is
not a gate.
