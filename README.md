# CubeRange

*日本語版: [README.ja.md](README.ja.md)*

A hands-on range for **space cybersecurity**: satellite firmware running at instruction level in
[Renode](https://renode.io), a CubeSat's internal CAN bus, an RF link, and a ground station — with
attacks you can actually land, and mitigations proven to stop them.

It is the space analogue of Toyota's [RAMN](https://github.com/ToyotaInfoTech/RAMN) board, which
made automotive security learnable by putting four MCUs and a CAN bus on one PCB.

**Status: five exercises, two spacecraft.** A ground station sends a real ECSS PUS 17,1 and
gets a real 17,2 back through CCSDS framing, an emulated UART link, and CSP over CAN between
emulated STM32H753 nodes. The exercises chain, and each one attacks a limit the previous one's
mitigation admitted to:

- **EX-B01** — an attacker with nothing but bus access silences the satellite. Fixed by
  authenticating power commands, which the write-up notes is replayable.
- **EX-L01** — that fix is defeated by replaying a recording from the space link. No key, no
  parsing, no idea what any field means.
- **EX-A01** — authentication was never the missing control. A torque command that is authentic,
  well-formed and physically impossible spins the spacecraft up, and every subsystem keeps
  reporting nominal while it does.
- **EX-F01** — a telecommand with nothing wrong with it but its length overruns a stack buffer in
  the OBC's PUS 8 handler and returns into a maintenance function no command can reach. Not
  shellcode: SRAM here is execute-never and Renode enforces it, so the payload is an address that
  was already in the image.

- **EX-G01** — no radio, no bus, no operator account. A file written into the ground segment's
  plugin directory becomes a telecommand the operator's own station transmits, while every
  spacecraft-side control from the four exercises above works exactly as designed.

- **EX-G02** — a ground station that may only watch switches the spacecraft's radio off, using a
  telecommand that differs from an authorised one by a single octet: the source id it honestly
  puts in its own header. The spacecraft had been reading that field, printing it, and echoing it
  back down since P0, without ever deciding anything with it.

- **EX-G03** — the cheapest attack here, and the one that needs nothing: a single well-formed
  frame with a sequence number ahead of the operator's, on any virtual channel. EX-L01's replay
  defence keeps one counter for the whole link, so every later command from the real operator is
  rejected — as a *replay*, which sends them hunting an attacker who left thirty seconds ago. The
  same defect locks out a second legitimate ground station just by its existing.

- **EX-G04** — nothing is broken. EX-G02's authority check is on and works, the spacecraft
  refuses the command, and its console says so. The operator does not have the console. From the
  ground, a refused command and a frame that was never received are the same observation, so the
  pass is spent debugging the radio. Four octets of request id and one of reason end that.

- **EX-X01** — two spacecraft, and a crosslink between them, because a constellation whose
  members cannot talk to each other is four satellites. Everything is hardened: the EPS token
  check, the PUS 8 length check, the authority table, the verification reports, all on. A
  compromised peer switches the victim's radio off anyway, by writing the ground station's source
  id into a field it chooses. Its own identity is refused; the ground's is obeyed; the difference
  is two octets. Meanwhile the EPS refuses that same attacker on both builds, because it asks for
  a token rather than a name.

- **EX-S01** — you have cryptography now. The uplink verifies a CCSDS 355.0-B-2 AES-256-GCM MAC
  and an anti-replay counter that lives inside the signed portion, so EX-L01's replay is dead and
  a station can no longer claim another station's identity on the space link. Then EX-X01's
  attack runs again, byte for byte, and switches the radio off. SDLS is a transfer-frame protocol;
  the crosslink carries CSP. The packet did not fail a check — it never met one.

- **EX-S02** — the answer EX-S01 names and does not implement: put the MAC on the telecommand.
  It travels inside the Space Packet, covers the source id and a per-source counter, and verifies
  the same whether the packet arrived on the space link, the crosslink or the internal bus.
  EX-X01's attack — the same solver file, a third time — is refused. Twenty octets on every
  telecommand is what that costs, and authentication still answers who rather than what.

Six attack origins are covered: the internal bus, the space link, the ADCS command envelope, the
OBC's own command parser, the ground segment that decides what to send, and a spacecraft in your
own constellation. EX-G02, EX-G03 and EX-G04 add no origin on purpose — a legitimate origin
exceeding its authority, a legitimate origin exceeding nothing at all, and a control that works
and cannot be heard working.

**Two ground stations, two consoles.** `make ground-stations` runs both as separate nodes
against one spacecraft, and `make gs STATION=backup` is a console to sit at while a scenario runs
elsewhere. They are nodes, not two integers in one process: each has its own identity on the wire,
its own log, and its own view of what it may ask for. A Renode socket terminal serves exactly one
client, so the channel in front of the spacecraft accepts several — every station's uplink reaches
the satellite and every downlink is heard by all of them, which is what a radio does.

The backup station refuses, on the ground, what its own authorisation matrix forbids. `OVERRIDE=1`
sends it anyway, and a vulnerable spacecraft obeys — because the check the operator just stepped
past was never on the spacecraft. That is EX-G02, as two people at two consoles rather than a
flag in a script.

**Where to start:** `make syllabus`. Each exercise declares its prerequisite in its own front
matter, and that command reads them and prints the order — so the sequence cannot drift from the
exercises the way a hand-written list would. `tests/pytest/test_syllabus.py` fails on a
prerequisite that names nothing, on a cycle, and on an exercise no path reaches.

There are two spacecraft. `make constellation` runs eight emulated nodes in one emulation — two
satellites of four, on two CAN hubs, with two space links and their own CSP addresses and
spacecraft IDs — and asserts that the buses are isolated, using a command that is known to work on
the hub it belongs to so that "nothing happened" means the hub and not a typo.

See [the design](docs/superpowers/specs/2026-08-28-cuberange-design.md) for where this is going.

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
compiler is needed because `make check` compiles the shared codec.

```bash
sudo apt install -y bc build-essential python3-dev libicu-dev libssl3
pip install -r requirements.txt
```

**No root?** [docs/host-setup-without-root.md](docs/host-setup-without-root.md) is the path that
was walked on a host with no `sudo`, `gcc`, `pip` or `make`, with the numbers it produced. Nothing
in this repository needs a package manager.

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
| `make probe` | Verifies every Renode capability the design depends on. 40 checks, including five deliberate-failure self-tests |
| `make firmware-p0` | Builds the COMM and OBC images |
| `make firmware-f01` | Every satellite-0 image: COMM, OBC (both), both EPS profiles, both ADCS profiles |
| `make firmware-g01` | The hardened OBC and EPS EX-G01 needs — the exercise is about every spacecraft-side control working |
| `make firmware-sat1` | The same four roles built as spacecraft 1: different CSP addresses, different SCID |
| `tools/ci.sh` | The gate as one command: environment check, then `make check`. What CI runs |
| `make constellation` | Eight nodes, two spacecraft, one emulation — and the bus isolation between them |
| `make demo-p0` | Runs the PUS round trip and asserts it |
| `make demo` | The R0 smoke test: two nodes exchanging CSP pings over CAN |
| `make determinism` | Runs one scenario three times under the CI profile and requires byte-identical guest output |
| `make pair-gate` | Proves every vulnerable/mitigated pair differs by exactly one build flag, and nothing else |
| `make spike` | Injects raw CAN frames from Python with no privileges, and drives virtual time over Renode's External Control API |
| `make verify-all` | Every exercise, both directions: attacks land, mitigations block, features survive |
| `make soak-p0` | Thirty consecutive round trips under a watchdog |
| `make check` | All of the above plus the codec suites |

`make probe` is sensitive to host contention — a clean run is about 6 minutes, and it took 35 with
two other Renode workloads on the machine. Check the load before reading a slow probe as a
regression.

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

Twenty-two Renode and library defects found along the way are recorded, and the ones that can be
asserted are pinned as negative tests in `probe.sh`, so a future release that fixes one makes the
probe fail rather than silently changing behaviour. Two of them cost a day each: a CAN frame that
was structurally perfect and invisible because it was sent as a standard rather than an extended
ID, and a protocol version that libcsp picks at runtime, so "we use CSP v1" was true in the design
and false in the firmware. The twenty-second was found on 2026-09-09 and is the same shape:
`cpu TranslateAddress` caches by address and not by access type, so asking about a read before
asking about an instruction fetch reports that SRAM is executable when it is not.

**About CI, precisely.** For most of this project's life there was none, while several documents
described these gates as CI-enforced. There is now `.github/workflows/check.yml`, and it is worth
being exact about what that does and does not mean.

**Observed passing on 2026-09-10**: run 8, commit `eaf07ce`, on main. Both jobs green, and the
gate step ran `tools/ci.sh` to `CHECK PASSED` in 838 seconds on ubuntu-latest. This paragraph said
"nobody has watched it run" until that day, and not one day less.

The sentence was truer than it meant. GitHub had never registered the workflow at all — a workflow
is registered the first time it runs, `push` matched only main, main had no `.github` directory,
and `workflow_dispatch` only appears for workflows on the default branch. The API reported zero
workflows. Not a button nobody pressed: no button.

Three defects stood between that and green, and none of them can happen on a developer's machine,
where the environment is always already installed. The working branch was not in the push trigger.
The cache restored the Zephyr workspace and SDK but not the pip packages that drive them, while
the installer only runs on a cache miss — so cold runs worked and every warm run died at `west
build`. And fixing `west` alone left Zephyr's own `requirements.txt` behind, which brought the
same failure straight back.

Four consecutive green runs now (8, 10, 11, 12), warm caches included. Still a short record. And the hypothesis that took longest to test was wrong: probe.sh
asserts a 1.5x four-node speed floor, this eight-core machine clears it at 4.3–5.1x, and a
two-vCPU runner looked like the obvious thing to blame. The probe passes there in 110 seconds.

That distinction is the whole discipline. An earlier revision of the design claimed a
TTP-verification tool, an offline mode and an oracle requirement; none of the three existed, and
each read exactly as convincing as a YAML file does.

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
