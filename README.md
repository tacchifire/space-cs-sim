# CubeRange

A hands-on range for **space cybersecurity**: satellite firmware running at instruction level in
[Renode](https://renode.io), a CubeSat's internal CAN bus, an RF link, and a ground station — with
attacks you can actually land, and mitigations proven to stop them.

It is the space analogue of Toyota's [RAMN](https://github.com/ToyotaInfoTech/RAMN) board, which
made automotive security learnable by putting four MCUs and a CAN bus on one PCB.

**Status: P0 done, first exercise landed.** A ground station sends a real ECSS PUS 17,1 and gets a
real 17,2 back through CCSDS framing, an emulated UART link, and CSP over CAN between emulated
STM32H753 nodes. Two exercises are playable and they chain: **EX-B01**, where an attacker with nothing but bus
access silences the satellite, and **EX-L01**, where its mitigation is defeated by replaying a
recording — no key, no parsing, from the space link. See
[the design](docs/superpowers/specs/2026-08-28-cuberange-design.md) for where this is going.

```
$ make demo-p0
COMM: uplink frame seq=0 carrying 11 octets -> OBC
COMM: forwarded 11 octets to OBC on port 10
OBC:  accepted a connection from 5 on port 10
OBC:  APID 0x0a9 PUS 17,1 from source 66
OBC:  PUS 17,2 report sent to COMM (counter 0)
COMM: downlink 17 octets from node 1
```

## Why not just use NOS3 or a CTF

NASA's [NOS3](https://github.com/nasa/nos3) simulates at the **operations** level: cFS runs
natively on Linux and the buses are middleware. Hack-A-Sat's tooling emulated firmware but was a
one-off, unmaintained since 2020. CubeRange sits at the **firmware and silicon** level and stays
there: real Zephyr images on a real MCU model, a real CAN bus, and a real link — which is what
makes memory-corruption, key extraction, bus injection and fault-injection exercises honest rather
than scripted.

## Getting started

Needs Linux (WSL2 is fine), Python ≥ 3.10, ~8 GB of disk. No root, no Docker.

On a minimal image, install these first. `bc` is not optional — the probe's arithmetic is six
calls to it. `libicu` and OpenSSL 3 are dlopened by Renode's bundled .NET at startup, and a C
compiler is needed because `make check` compiles the shared codec and because `crcmod` ships no
wheels for any architecture.

```bash
sudo apt install -y bc build-essential python3-dev libicu-dev libssl3
pip install -r requirements.txt
```

GTK is *not* needed: every invocation here passes `--disable-xwt`, so Renode never builds a UI.

```bash
# 1. Renode 1.16.1 portable, extracted to ~/tools/renode_1.16.1-dotnet_portable
#    Both architectures unpack to that same directory name.
case "$(uname -m)" in
  x86_64)  RENODE_ASSET=renode-1.16.1.linux-portable-dotnet.tar.gz ;;
  aarch64) RENODE_ASSET=renode-1.16.1.linux-arm64-portable-dotnet.tar.gz ;;
esac
curl -L -o /tmp/renode.tar.gz \
  "https://github.com/renode/renode/releases/download/v1.16.1/$RENODE_ASSET"
mkdir -p ~/tools && tar xzf /tmp/renode.tar.gz -C ~/tools

# 2. Zephyr v4.1.0 + SDK (see tools/setup-toolchain.sh for why these exact versions)
./tools/setup-toolchain.sh
git clone --depth 1 --branch v2.1 https://github.com/libcsp/libcsp ~/libcsp

# 3. Check the ground under your feet, then run the thing
make probe
make demo-p0
```

| Target | What it does |
| --- | --- |
| `make probe` | Verifies every Renode capability the design depends on. 34 checks, including five deliberate-failure self-tests |
| `make firmware-p0` | Builds the COMM and OBC images |
| `make demo-p0` | Runs the PUS round trip and asserts it |
| `make demo` | The R0 smoke test: two nodes exchanging CSP pings over CAN |
| `make spike` | Injects raw CAN frames from Python with no privileges, and drives virtual time over Renode's External Control API |
| `make verify-all` | Every exercise, both directions: attacks land, mitigations block, features survive |
| `make soak-p0` | Thirty consecutive round trips under a watchdog |
| `make check` | All of the above plus the codec suites |

## How it fits together

```
ground station (Python)  --TCP-->  COMM.usart2  --CSP/CAN-->  OBC
   CCSDS + PUS codec                 gateway                 PUS dispatch
```

`usart3` is Zephyr's console on every node; the space link is `usart2`, because a link sharing the
console delivers 9239 bytes of boot banner before the first application byte. The attacker injects
raw CAN frames through a runtime-compiled C# peripheral on a bare Renode machine — no privileges,
no SocketCAN, no attacker firmware.

## The evidence rule

Nothing in the design is asserted without a command that reproduces it. `tools/renode-probe/probe.sh`
is the standing gate, and it is required to be able to fail: it carries self-tests that feed it
known-bad input, because an earlier version reported PASS for every check when its output directory
was missing.

That rule has earned its keep. The design's first two revisions claimed capabilities that did not
survive contact with Renode — including the MCU choice — and its central performance premise turned
out to be wrong by a factor of eight, an artifact of two Renode defaults rather than the workload.
Section 16 of the design document is the record of every such correction.

Twenty-one Renode and library defects found along the way are recorded, and the ones that can be
asserted are pinned as negative tests, so a future release that fixes one makes CI fail rather than
silently changing behaviour. Two of them cost a day each: a CAN frame that was structurally perfect
and invisible because it was sent as a standard rather than an extended ID, and a protocol version
that libcsp picks at runtime, so "we use CSP v1" was true in the design and false in the firmware.

## Before you use it

This repository contains working attacks against spacecraft protocols. The target is synthetic and
must stay that way.

| Document | What it settles |
| --- | --- |
| [SAFE_USE.md](SAFE_USE.md) | Everything attacked here is emulated. Do not point it at anything real, and do not add real identifiers |
| [SECURITY.md](SECURITY.md) | Which weaknesses are deliberate, which are worth reporting, and how |
| [ASSURANCE.md](ASSURANCE.md) | What a result here does and does not support as a claim — read this before citing one |
| [CONTRIBUTING.md](CONTRIBUTING.md) | The evidence rule, and what an exercise must prove |
| [docs/legal/export-control.md](docs/legal/export-control.md) | What is known, what is not, and that nothing is claimed |

## Licence

Apache-2.0, see [LICENSE](LICENSE). Dependencies are kept permissive on purpose: Renode (MIT),
Zephyr (Apache-2.0), libcsp (MIT). NASA CryptoLib and NOS3 (NOSA 1.3), Yamcs (AGPL-3.0) and OpenC3
are used — where used at all — as external oracles or optional adapters, never vendored.
