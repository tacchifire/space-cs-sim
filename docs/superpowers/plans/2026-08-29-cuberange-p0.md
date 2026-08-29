# CubeRange P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python ground station sends a real ECSS PUS 17,1 connection test inside a CCSDS TC transfer frame; the COMM node receives it on its space-link UART, forwards it to the OBC over CSP/CAN, the OBC answers with PUS 17,2, and the reply returns to the ground station by the same path.

**Architecture:** Four planes with process boundaries. A host-side Python ground station owns the CCSDS/PUS codec and a TCP client. Renode exposes the COMM node's `usart2` as a TCP socket terminal, which is the space link. COMM is a gateway: it deframes the link, hands the Space Packet to the OBC over libcsp, and reframes the OBC's answer back onto the link. The OBC owns PUS service dispatch. The same wire format is implemented twice — once in Python for the ground and once in C for the firmware — and the two are cross-checked against each other *and* against an independent third implementation, because two self-written codecs that share a mistake round-trip perfectly.

**Tech Stack:** Renode 1.16.1 (MIT) · Zephyr v4.1.0 + SDK v1.0.1 · libcsp v2.1 (MIT, Zephyr module) · Python 3.10 · `spacepackets` (Apache-2.0, independent oracle only) · pytest · GNU Make

---

## Global Constraints

Every task inherits these. They are measured values from the design document, not preferences.

- **Renode is pinned to 1.16.1**, build `d66b0c2a-202602160923`. Features present only on `master` are out of scope.
- **Zephyr is pinned to v4.1.0.** `main` requires Python ≥ 3.12 and will not configure on this host's 3.10.
- **Board is `nucleo_h753zi`.** CSP addresses: OBC = 1, EPS = 2, ADCS = 4, COMM = 5.
- **UART assignment is fixed:** `usart3` is Zephyr's console (`zephyr,console`), `usart2` is the space link (alias `spacelink`, enabled by `firmware/apps/*/boards/nucleo_h753zi.overlay`). Nothing may print to `usart2` except the link protocol.
- **`emulation CreateServerSocketTerminal <port> "<name>" false`** — the third argument is load-bearing. `telnetMode` defaults to **true** and prepends 11 telnet IAC bytes (`ff fd 00 ff fb 01 ff fb 03 ff fc 22`), which corrupts a binary link.
- **Every `.resc` sets** `emulation SetGlobalQuantum "0.002"`. 2 ms is the largest quantum that keeps firmware output byte-identical to a 100 µs reference; above it timing drifts silently and only in longer runs.
- **Interactive runs add** `emulation SetGlobalAdvanceImmediately true` (2.34× real time). **CI runs must not** — CI keeps 1.0× and adds `emulation SetGlobalSerialExecution true` and `emulation SetSeed 12345`, without which 14/14 consecutive runs diverge internally.
- **Every Renode invocation in a script or test uses `< /dev/null` and a wall-clock timeout.** A failing command drops Renode into the interactive Monitor, which hangs forever on a TTY.
- **FECF CRC is CRC-16/IBM-3740**: width 16, poly `0x1021`, init `0xFFFF`, no input or output reflection, xorout `0x0000`. Never write "CRC-16-CCITT" — ten different CRC-16s go by that name and two implementations that both pick the wrong one round-trip perfectly.
- **The outer delimiter is "CubeRange lab framing", not CCSDS CLTU.** CLTU is CCSDS 231.0-B-4 and starts `EB90`; we do not implement it. Documentation must never call this CCSDS channel conformance.
- **Licence:** all new code Apache-2.0. `spacepackets` may be imported by tests only, never by shipped ground-station code paths, so the runtime dependency list stays minimal.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/cuberange/proto/crc.py` | FECF CRC-16/IBM-3740, nothing else |
| `src/cuberange/proto/spacepacket.py` | CCSDS 133.0-B-2 primary header encode/decode |
| `src/cuberange/proto/pus.py` | ECSS-E-ST-70-41C TC and TM secondary headers |
| `src/cuberange/proto/frame.py` | TC/TM transfer frames + the ASM lab framing |
| `src/cuberange/gs/link.py` | TCP client for the Renode socket terminal |
| `src/cuberange/gs/station.py` | Ties codec + link together; `ping()` |
| `firmware/common/cuberange_proto.h` | Shared C wire structs and prototypes |
| `firmware/common/cuberange_proto.c` | The C codec — same wire format, independently written |
| `firmware/apps/comm/` | Gateway: link ⇄ CSP |
| `firmware/apps/obc/` | PUS dispatch, service 17 |
| `firmware/common/boards/nucleo_h753zi.overlay` | Shared `spacelink` overlay for both apps |
| `scripts/multi-node/p0.resc` | Two nodes, CAN hub, socket terminal |
| `tests/pytest/` | Codec unit tests, oracle cross-checks |
| `tests/native/` | Builds the C codec for the host and checks it against the Python vectors |
| `tests/e2e/test_p0_roundtrip.py` | Starts Renode, runs the full exchange, asserts |

`firmware/apps/csp_ping/` from R0 stays as-is. It is the smoke test that CAN and libcsp still work, and `make demo` keeps running it.

---

### Task 1: FECF CRC

**Files:**
- Create: `src/cuberange/proto/crc.py`
- Test: `tests/pytest/test_crc.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `crc16_ccsds(data: bytes) -> int`

- [ ] **Step 1: Write the failing test**

`tests/pytest/test_crc.py`:

```python
"""FECF CRC, checked against the CCSDS text and an independent implementation.

CCSDS 132.0-B-3 section 4.1.6.2.2 gives G(X) = X^16 + X^12 + X^5 + 1 and notes that the
X^(n-16).L(X) term "has the effect of presetting the shift register to all '1' state". That is
poly 0x1021, init 0xFFFF, no reflection, xorout 0 - i.e. CRC-16/IBM-3740, NOT the XMODEM or
KERMIT variants that also get called "CCITT".
"""
import pytest

from cuberange.proto.crc import crc16_ccsds

# Check vector from the CRC catalogue for IBM-3740: "123456789" -> 0xE5CC
CHECK_VECTOR = (b"123456789", 0xE5CC)


def test_catalogue_check_vector():
    data, expected = CHECK_VECTOR
    assert crc16_ccsds(data) == expected


def test_empty_input_is_the_init_value():
    assert crc16_ccsds(b"") == 0xFFFF


def test_residue_is_zero_when_the_crc_is_included():
    """Appending the CRC big-endian and re-running must yield 0. This is the property a receiver
    uses, so if it does not hold the codec is wrong even if the forward direction looks right."""
    frame = bytes(range(32))
    fecf = crc16_ccsds(frame)
    assert crc16_ccsds(frame + fecf.to_bytes(2, "big")) == 0x0000


def test_differs_from_the_variants_it_is_often_confused_with():
    """Guards against silently switching to XMODEM (init 0) or KERMIT (reflected)."""
    assert crc16_ccsds(b"123456789") != 0x31C3   # XMODEM
    assert crc16_ccsds(b"123456789") != 0x2189   # KERMIT
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd /home/ubuntu/workspace/space-cs-sim && PYTHONPATH=src pytest tests/pytest/test_crc.py -v`
Expected: `ModuleNotFoundError: No module named 'cuberange.proto.crc'`

- [ ] **Step 3: Write the implementation**

Create `src/cuberange/__init__.py`, `src/cuberange/proto/__init__.py` (both empty), and
`src/cuberange/proto/crc.py`:

```python
"""CCSDS Frame Error Control Field CRC.

CRC-16/IBM-3740: width 16, poly 0x1021, init 0xFFFF, refin/refout false, xorout 0x0000.
Source: CCSDS 132.0-B-3 s4.1.6.2.2. Do not call this "CRC-16-CCITT" - that name is ambiguous
across at least ten parameter sets and picking the wrong one still round-trips.
"""

POLY = 0x1021
INIT = 0xFFFF


def crc16_ccsds(data: bytes) -> int:
    """Return the 16-bit FECF over `data`.

    The FECF covers the frame from the first octet of the primary header through the last octet
    before the FECF itself. The Attached Sync Marker is NOT covered.
    """
    reg = INIT
    for byte in data:
        reg ^= byte << 8
        for _ in range(8):
            if reg & 0x8000:
                reg = ((reg << 1) ^ POLY) & 0xFFFF
            else:
                reg = (reg << 1) & 0xFFFF
    return reg
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `PYTHONPATH=src pytest tests/pytest/test_crc.py -v`
Expected: 4 passed

- [ ] **Step 5: Cross-check against an independent implementation**

Add to `tests/pytest/test_crc.py`:

```python
def test_agrees_with_an_independent_implementation():
    """`crcmod` is a third-party implementation with its own parameter tables. If our hand-rolled
    loop and crcmod agree on random inputs, a transcription error in either is unlikely."""
    crcmod = pytest.importorskip("crcmod")
    reference = crcmod.mkCrcFun(0x11021, initCrc=0xFFFF, rev=False, xorOut=0x0000)
    for n in (0, 1, 2, 7, 8, 63, 255):
        payload = bytes((i * 7 + 3) & 0xFF for i in range(n))
        assert crc16_ccsds(payload) == reference(payload), f"mismatch at length {n}"
```

Run: `python3 -m pip install crcmod && PYTHONPATH=src pytest tests/pytest/test_crc.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/cuberange/__init__.py src/cuberange/proto/__init__.py \
        src/cuberange/proto/crc.py tests/pytest/test_crc.py
git commit -m "feat(proto): FECF CRC-16/IBM-3740 with an independent cross-check"
```

---

### Task 2: CCSDS Space Packet primary header

**Files:**
- Create: `src/cuberange/proto/spacepacket.py`
- Test: `tests/pytest/test_spacepacket.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class PacketType(IntEnum): TM = 0; TC = 1`
  - `class SpacePacket` — fields `apid: int`, `ptype: PacketType`, `sec_hdr: bool`, `seq_count: int`, `data: bytes`
  - `SpacePacket.encode() -> bytes`
  - `SpacePacket.decode(raw: bytes) -> SpacePacket`

- [ ] **Step 1: Write the failing test**

`tests/pytest/test_spacepacket.py`:

```python
"""CCSDS 133.0-B-2 primary header.

Layout, 6 octets:
  octet 0-1 : version(3) | type(1) | sec-hdr-flag(1) | APID(11)
  octet 2-3 : sequence-flags(2) | sequence-count(14)
  octet 4-5 : packet data length

s4.1.3.5.3: "C = (Total Number of Octets in the Packet Data Field) - 1". The off-by-one here is
the single most common CCSDS implementation bug, so it gets its own test.
"""
import pytest

from cuberange.proto.spacepacket import PacketType, SpacePacket


def test_encodes_the_documented_bit_layout():
    pkt = SpacePacket(apid=0x123, ptype=PacketType.TC, sec_hdr=True,
                      seq_count=5, data=b"\xAA\xBB\xCC")
    raw = pkt.encode()
    #  version 0, type 1 (TC), sec-hdr 1, APID 0x123 -> 0b000_1_1_00100100011 = 0x1923
    #  seq flags 0b11 (unsegmented), count 5        -> 0xC005
    #  data length = 3 - 1 = 2                      -> 0x0002
    assert raw[:6] == bytes.fromhex("1923 C005 0002".replace(" ", ""))
    assert raw[6:] == b"\xAA\xBB\xCC"


def test_data_length_field_is_octets_minus_one():
    for n in (1, 2, 17, 255):
        raw = SpacePacket(apid=1, ptype=PacketType.TM, sec_hdr=False,
                          seq_count=0, data=bytes(n)).encode()
        assert int.from_bytes(raw[4:6], "big") == n - 1


def test_rejects_an_empty_data_field():
    """A zero-length data field cannot be represented: C would have to be -1."""
    with pytest.raises(ValueError):
        SpacePacket(apid=1, ptype=PacketType.TM, sec_hdr=False, seq_count=0, data=b"").encode()


def test_round_trip():
    original = SpacePacket(apid=0x7FF, ptype=PacketType.TC, sec_hdr=True,
                           seq_count=0x3FFF, data=bytes(range(40)))
    assert SpacePacket.decode(original.encode()) == original


def test_decode_rejects_a_truncated_packet():
    good = SpacePacket(apid=1, ptype=PacketType.TC, sec_hdr=True,
                       seq_count=0, data=b"\x01\x02\x03").encode()
    with pytest.raises(ValueError):
        SpacePacket.decode(good[:-1])
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=src pytest tests/pytest/test_spacepacket.py -v`
Expected: `ModuleNotFoundError: No module named 'cuberange.proto.spacepacket'`

- [ ] **Step 3: Write the implementation**

`src/cuberange/proto/spacepacket.py`:

```python
"""CCSDS Space Packet Protocol, 133.0-B-2. Primary header only; the secondary header is PUS
and lives in cuberange.proto.pus."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

PRIMARY_HEADER_LEN = 6
SEQ_FLAGS_UNSEGMENTED = 0b11


class PacketType(IntEnum):
    TM = 0
    TC = 1


@dataclass(frozen=True)
class SpacePacket:
    apid: int
    ptype: PacketType
    sec_hdr: bool
    seq_count: int
    data: bytes

    def encode(self) -> bytes:
        if not 0 <= self.apid <= 0x7FF:
            raise ValueError(f"APID {self.apid} does not fit in 11 bits")
        if not 0 <= self.seq_count <= 0x3FFF:
            raise ValueError(f"sequence count {self.seq_count} does not fit in 14 bits")
        if len(self.data) == 0:
            raise ValueError("packet data field must contain at least one octet")

        word0 = (int(self.ptype) << 12) | (int(self.sec_hdr) << 11) | self.apid
        word1 = (SEQ_FLAGS_UNSEGMENTED << 14) | self.seq_count
        word2 = len(self.data) - 1
        return (word0.to_bytes(2, "big") + word1.to_bytes(2, "big")
                + word2.to_bytes(2, "big") + self.data)

    @staticmethod
    def decode(raw: bytes) -> "SpacePacket":
        if len(raw) < PRIMARY_HEADER_LEN:
            raise ValueError(f"space packet shorter than a primary header: {len(raw)} octets")
        word0 = int.from_bytes(raw[0:2], "big")
        word1 = int.from_bytes(raw[2:4], "big")
        data_len = int.from_bytes(raw[4:6], "big") + 1
        body = raw[PRIMARY_HEADER_LEN:]
        if len(body) != data_len:
            raise ValueError(
                f"declared data length {data_len} but {len(body)} octets follow the header")
        return SpacePacket(
            apid=word0 & 0x7FF,
            ptype=PacketType((word0 >> 12) & 0x1),
            sec_hdr=bool((word0 >> 11) & 0x1),
            seq_count=word1 & 0x3FFF,
            data=body,
        )
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `PYTHONPATH=src pytest tests/pytest/test_spacepacket.py -v`
Expected: 5 passed

- [ ] **Step 5: Cross-check against `spacepackets`**

Create `tests/pytest/test_oracle_spacepacket.py`:

```python
"""Independent oracle. Our codec and spacepackets are separate implementations of the same book;
if they agree byte-for-byte, a shared misreading is unlikely. spacepackets is Apache-2.0 and is a
TEST dependency only - it must never be imported by shipped ground-station code."""
import pytest

from cuberange.proto.spacepacket import PacketType, SpacePacket

sp = pytest.importorskip("spacepackets")
from spacepackets.ccsds.spacepacket import SpacePacketHeader, PacketType as OraclePacketType  # noqa: E402


@pytest.mark.parametrize("apid,seq,payload_len", [(0x001, 0, 1), (0x123, 5, 3), (0x7FF, 0x3FFF, 64)])
def test_primary_header_matches_the_oracle(apid, seq, payload_len):
    payload = bytes((i * 3) & 0xFF for i in range(payload_len))
    ours = SpacePacket(apid=apid, ptype=PacketType.TC, sec_hdr=True,
                       seq_count=seq, data=payload).encode()
    theirs = SpacePacketHeader(
        packet_type=OraclePacketType.TC, apid=apid, seq_count=seq,
        data_len=payload_len - 1, sec_header_flag=True,
    ).pack()
    assert ours[:6] == theirs
```

Run: `python3 -m pip install spacepackets && PYTHONPATH=src pytest tests/pytest/test_oracle_spacepacket.py -v`
Expected: 3 passed

> If the oracle's constructor signature differs in the installed version, adapt the call — but do
> not adapt *our* encoder to match the oracle without first checking the CCSDS text. The oracle is
> a second opinion, not an authority.

- [ ] **Step 6: Commit**

```bash
git add src/cuberange/proto/spacepacket.py tests/pytest/test_spacepacket.py \
        tests/pytest/test_oracle_spacepacket.py
git commit -m "feat(proto): CCSDS space packet primary header, cross-checked against spacepackets"
```

---

### Task 3: PUS TC and TM secondary headers

**Files:**
- Create: `src/cuberange/proto/pus.py`
- Test: `tests/pytest/test_pus.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PUS_VERSION = 2`
  - `class PusTc` — fields `service: int`, `subtype: int`, `source_id: int`, `ack: int`, `app_data: bytes`; methods `encode() -> bytes`, `decode(raw) -> PusTc`
  - `class PusTm` — fields `service: int`, `subtype: int`, `msg_counter: int`, `dest_id: int`, `time: bytes`, `app_data: bytes`; methods `encode() -> bytes`, `decode(raw, time_len) -> PusTm`
  - `SERVICE_TEST = 17`, `SUBTYPE_CONNECTION_TEST = 1`, `SUBTYPE_CONNECTION_TEST_REPORT = 2`

- [ ] **Step 1: Write the failing test**

`tests/pytest/test_pus.py`:

```python
"""ECSS-E-ST-70-41C secondary headers.

TC, 5 octets: [0] pus_version<<4 | ack_flags, [1] service, [2] subtype, [3:5] source ID u16 BE
TM, 7 octets: [0] pus_version<<4 | sc_time_ref, [1] service, [2] subtype,
              [3:5] message type counter u16 BE, [5:7] destination ID u16 BE, then the time field

The standard itself is behind ECSS registration and could not be fetched, so these layouts rest on
two independent implementations agreeing (spacepackets and FSFW). That limitation is recorded in
the design document and must not be quietly upgraded to "verified against the standard".
"""
from cuberange.proto.pus import (PUS_VERSION, PusTc, PusTm, SERVICE_TEST,
                                 SUBTYPE_CONNECTION_TEST, SUBTYPE_CONNECTION_TEST_REPORT)


def test_tc_secondary_header_layout():
    tc = PusTc(service=SERVICE_TEST, subtype=SUBTYPE_CONNECTION_TEST,
               source_id=0x0042, ack=0b1001, app_data=b"")
    raw = tc.encode()
    assert len(raw) == 5
    assert raw[0] == (PUS_VERSION << 4) | 0b1001
    assert raw[1] == 17
    assert raw[2] == 1
    assert raw[3:5] == b"\x00\x42"


def test_tm_secondary_header_layout():
    tm = PusTm(service=SERVICE_TEST, subtype=SUBTYPE_CONNECTION_TEST_REPORT,
               msg_counter=7, dest_id=0x0042, time=b"\x00\x00\x00\x01", app_data=b"")
    raw = tm.encode()
    assert len(raw) == 7 + 4
    assert raw[0] == (PUS_VERSION << 4)
    assert raw[1] == 17
    assert raw[2] == 2
    assert raw[3:5] == b"\x00\x07"
    assert raw[5:7] == b"\x00\x42"
    assert raw[7:11] == b"\x00\x00\x00\x01"


def test_tc_round_trip_with_application_data():
    original = PusTc(service=8, subtype=1, source_id=1, ack=0b1111, app_data=bytes(range(16)))
    assert PusTc.decode(original.encode()) == original


def test_tm_round_trip_with_application_data():
    original = PusTm(service=3, subtype=25, msg_counter=1, dest_id=2,
                     time=b"\x11\x22\x33\x44", app_data=bytes(range(8)))
    assert PusTm.decode(original.encode(), time_len=4) == original
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=src pytest tests/pytest/test_pus.py -v`
Expected: `ModuleNotFoundError: No module named 'cuberange.proto.pus'`

- [ ] **Step 3: Write the implementation**

`src/cuberange/proto/pus.py`:

```python
"""ECSS-E-ST-70-41C PUS-C secondary headers, TC and TM.

PUS is an application-layer convention carried inside a CCSDS Space Packet. It does not define
RF modulation, transfer framing, COP-1 or SDLS; keep those concerns out of this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PUS_VERSION = 2          # PUS-C
TC_SEC_HDR_LEN = 5
TM_SEC_HDR_LEN = 7

SERVICE_TEST = 17
SUBTYPE_CONNECTION_TEST = 1
SUBTYPE_CONNECTION_TEST_REPORT = 2


@dataclass(frozen=True)
class PusTc:
    service: int
    subtype: int
    source_id: int = 0
    ack: int = 0b1111
    app_data: bytes = b""

    def encode(self) -> bytes:
        if not 0 <= self.ack <= 0xF:
            raise ValueError("ack flags must fit in 4 bits")
        return (bytes([(PUS_VERSION << 4) | self.ack, self.service, self.subtype])
                + self.source_id.to_bytes(2, "big") + self.app_data)

    @staticmethod
    def decode(raw: bytes) -> "PusTc":
        if len(raw) < TC_SEC_HDR_LEN:
            raise ValueError(f"PUS TC shorter than a secondary header: {len(raw)} octets")
        version = raw[0] >> 4
        if version != PUS_VERSION:
            raise ValueError(f"unsupported PUS version {version}")
        return PusTc(service=raw[1], subtype=raw[2],
                     source_id=int.from_bytes(raw[3:5], "big"),
                     ack=raw[0] & 0xF, app_data=raw[TC_SEC_HDR_LEN:])


@dataclass(frozen=True)
class PusTm:
    service: int
    subtype: int
    msg_counter: int = 0
    dest_id: int = 0
    time: bytes = b""
    app_data: bytes = field(default=b"")

    def encode(self) -> bytes:
        return (bytes([PUS_VERSION << 4, self.service, self.subtype])
                + self.msg_counter.to_bytes(2, "big")
                + self.dest_id.to_bytes(2, "big")
                + self.time + self.app_data)

    @staticmethod
    def decode(raw: bytes, time_len: int) -> "PusTm":
        need = TM_SEC_HDR_LEN + time_len
        if len(raw) < need:
            raise ValueError(f"PUS TM shorter than header+time: {len(raw)} < {need}")
        version = raw[0] >> 4
        if version != PUS_VERSION:
            raise ValueError(f"unsupported PUS version {version}")
        return PusTm(service=raw[1], subtype=raw[2],
                     msg_counter=int.from_bytes(raw[3:5], "big"),
                     dest_id=int.from_bytes(raw[5:7], "big"),
                     time=raw[TM_SEC_HDR_LEN:need], app_data=raw[need:])
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `PYTHONPATH=src pytest tests/pytest/test_pus.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/cuberange/proto/pus.py tests/pytest/test_pus.py
git commit -m "feat(proto): ECSS PUS-C TC and TM secondary headers"
```

---

### Task 4: Transfer frames and the lab framing

**Files:**
- Create: `src/cuberange/proto/frame.py`
- Test: `tests/pytest/test_frame.py`

**Interfaces:**
- Consumes: `crc16_ccsds` (Task 1).
- Produces:
  - `ASM = b"\x1a\xcf\xfc\x1d"`
  - `SCID = 0x0A9`, `VCID = 0`
  - `encode_tc_frame(payload: bytes, seq: int) -> bytes`
  - `decode_tc_frame(frame: bytes) -> tuple[int, bytes]` returning `(seq, payload)`
  - `encode_tm_frame(payload: bytes, mc_count: int, vc_count: int) -> bytes`
  - `decode_tm_frame(frame: bytes) -> tuple[int, int, bytes]` returning `(mc_count, vc_count, payload)`
  - `wrap(frame: bytes) -> bytes` — prepends ASM + u16 BE length
  - `class Deframer` with `feed(chunk: bytes) -> list[bytes]`

- [ ] **Step 1: Write the failing test**

`tests/pytest/test_frame.py`:

```python
"""TC/TM transfer frames (CCSDS 232.0-B-4 / 132.0-B-3, minimal profiles) plus the CubeRange lab
framing that delimits them on a byte-stream UART.

The lab framing is ASM + u16 big-endian length + frame. It is NOT a CCSDS CLTU: CLTU is defined in
231.0-B-4 and starts EB90. Calling this "CCSDS channel conformance" would be false.
"""
import pytest

from cuberange.proto.crc import crc16_ccsds
from cuberange.proto.frame import (ASM, Deframer, decode_tc_frame, decode_tm_frame,
                                   encode_tc_frame, encode_tm_frame, wrap)


def test_tc_frame_carries_its_payload_and_a_valid_fecf():
    payload = b"\x01\x02\x03\x04"
    frame = encode_tc_frame(payload, seq=7)
    assert crc16_ccsds(frame) == 0x0000, "FECF residue must be zero over the whole frame"
    seq, got = decode_tc_frame(frame)
    assert (seq, got) == (7, payload)


def test_tc_frame_length_field_is_total_octets_minus_one():
    payload = bytes(20)
    frame = encode_tc_frame(payload, seq=0)
    declared = ((frame[2] & 0x03) << 8) | frame[3]
    assert declared == len(frame) - 1


def test_tm_frame_round_trip():
    payload = bytes(range(30))
    frame = encode_tm_frame(payload, mc_count=3, vc_count=4)
    assert crc16_ccsds(frame) == 0x0000
    assert decode_tm_frame(frame) == (3, 4, payload)


def test_decode_rejects_a_corrupted_frame():
    frame = bytearray(encode_tc_frame(b"\xAA\xBB", seq=1))
    frame[6] ^= 0xFF
    with pytest.raises(ValueError, match="FECF"):
        decode_tc_frame(bytes(frame))


def test_deframer_reassembles_across_arbitrary_chunk_boundaries():
    frames = [encode_tc_frame(bytes([i]) * (i + 1), seq=i) for i in range(4)]
    stream = b"".join(wrap(f) for f in frames)

    for chunk_size in (1, 3, 7, len(stream)):
        d = Deframer()
        out = []
        for i in range(0, len(stream), chunk_size):
            out.extend(d.feed(stream[i:i + chunk_size]))
        assert out == frames, f"failed at chunk size {chunk_size}"


def test_deframer_resynchronises_after_leading_garbage():
    """A ground station may attach mid-stream, or the link may have dropped bytes. The deframer
    must find the next ASM rather than give up."""
    good = wrap(encode_tc_frame(b"\x99", seq=2))
    d = Deframer()
    assert d.feed(b"garbage-before-the-marker" + good) == [encode_tc_frame(b"\x99", seq=2)]


def test_deframer_survives_a_byte_that_looks_like_the_start_of_an_asm():
    d = Deframer()
    good = wrap(encode_tc_frame(b"\x55", seq=3))
    assert d.feed(ASM[:3] + good) == [encode_tc_frame(b"\x55", seq=3)]
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=src pytest tests/pytest/test_frame.py -v`
Expected: `ModuleNotFoundError: No module named 'cuberange.proto.frame'`

- [ ] **Step 3: Write the implementation**

`src/cuberange/proto/frame.py`:

```python
"""TC and TM transfer frames, minimal profiles, plus the CubeRange lab framing.

TC primary header, 5 octets (CCSDS 232.0-B-4):
  [0] tf-version(2) | bypass(1) | ctrl-cmd(1) | reserved(2) | scid-high(2)
  [1] scid-low(8)
  [2] vcid(6) | frame-length-high(2)
  [3] frame-length-low(8)          frame length = total octets - 1
  [4] frame sequence number(8)

TM primary header, 6 octets (CCSDS 132.0-B-3):
  [0:2] tf-version(2) | scid(10) | vcid(3) | ocf-flag(1)
  [2]   master channel frame count
  [3]   virtual channel frame count
  [4:6] data field status: sec-hdr(1) | sync(1) | packet-order(1) | seg-len-id(2) | fhp(11)

Both are followed by the data field and a 2-octet FECF. No OCF, no secondary header, no COP-1.
"""
from __future__ import annotations

from .crc import crc16_ccsds

ASM = b"\x1a\xcf\xfc\x1d"
SCID = 0x0A9
VCID = 0

TC_HEADER_LEN = 5
TM_HEADER_LEN = 6
FECF_LEN = 2
FIRST_HEADER_POINTER = 0          # a packet starts at the first octet of the data field
MAX_FRAME_LEN = 1024


def encode_tc_frame(payload: bytes, seq: int) -> bytes:
    total = TC_HEADER_LEN + len(payload) + FECF_LEN
    if total > MAX_FRAME_LEN:
        raise ValueError(f"TC frame of {total} octets exceeds the {MAX_FRAME_LEN} limit")
    length_field = total - 1
    header = bytes([
        (SCID >> 8) & 0x03,
        SCID & 0xFF,
        ((VCID & 0x3F) << 2) | ((length_field >> 8) & 0x03),
        length_field & 0xFF,
        seq & 0xFF,
    ])
    body = header + payload
    return body + crc16_ccsds(body).to_bytes(2, "big")


def decode_tc_frame(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < TC_HEADER_LEN + FECF_LEN:
        raise ValueError(f"TC frame too short: {len(frame)} octets")
    if crc16_ccsds(frame) != 0x0000:
        raise ValueError("FECF check failed")
    declared = (((frame[2] & 0x03) << 8) | frame[3]) + 1
    if declared != len(frame):
        raise ValueError(f"TC frame declares {declared} octets but is {len(frame)}")
    return frame[4], frame[TC_HEADER_LEN:-FECF_LEN]


def encode_tm_frame(payload: bytes, mc_count: int, vc_count: int) -> bytes:
    total = TM_HEADER_LEN + len(payload) + FECF_LEN
    if total > MAX_FRAME_LEN:
        raise ValueError(f"TM frame of {total} octets exceeds the {MAX_FRAME_LEN} limit")
    word0 = ((SCID & 0x3FF) << 4) | ((VCID & 0x7) << 1)      # ocf flag = 0
    header = (word0.to_bytes(2, "big")
              + bytes([mc_count & 0xFF, vc_count & 0xFF])
              + FIRST_HEADER_POINTER.to_bytes(2, "big"))
    body = header + payload
    return body + crc16_ccsds(body).to_bytes(2, "big")


def decode_tm_frame(frame: bytes) -> tuple[int, int, bytes]:
    if len(frame) < TM_HEADER_LEN + FECF_LEN:
        raise ValueError(f"TM frame too short: {len(frame)} octets")
    if crc16_ccsds(frame) != 0x0000:
        raise ValueError("FECF check failed")
    return frame[2], frame[3], frame[TM_HEADER_LEN:-FECF_LEN]


def wrap(frame: bytes) -> bytes:
    """CubeRange lab framing: ASM + u16 big-endian length + frame."""
    return ASM + len(frame).to_bytes(2, "big") + frame


class Deframer:
    """Byte-stream to frame reassembler.

    A UART hands us arbitrary chunks, so the deframer holds partial state between feeds and hunts
    for the next ASM after any corruption. It never raises: a link that has seen garbage should
    keep working once the next good frame arrives.
    """

    def __init__(self, max_frame_len: int = MAX_FRAME_LEN):
        self._buf = bytearray()
        self._max = max_frame_len

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buf.extend(chunk)
        out: list[bytes] = []
        while True:
            start = self._buf.find(ASM)
            if start < 0:
                # Keep the last few bytes: they may be a partial ASM split across chunks.
                if len(self._buf) > len(ASM) - 1:
                    del self._buf[:len(self._buf) - (len(ASM) - 1)]
                return out
            del self._buf[:start]
            if len(self._buf) < len(ASM) + 2:
                return out
            length = int.from_bytes(self._buf[len(ASM):len(ASM) + 2], "big")
            if length == 0 or length > self._max:
                # Bogus length: skip this ASM and look for the next one.
                del self._buf[:len(ASM)]
                continue
            end = len(ASM) + 2 + length
            if len(self._buf) < end:
                return out
            out.append(bytes(self._buf[len(ASM) + 2:end]))
            del self._buf[:end]
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `PYTHONPATH=src pytest tests/pytest/test_frame.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/cuberange/proto/frame.py tests/pytest/test_frame.py
git commit -m "feat(proto): TC/TM transfer frames and the CubeRange lab framing"
```

---

### Task 5: The C codec, cross-checked against the Python one

**Files:**
- Create: `firmware/common/cuberange_proto.h`
- Create: `firmware/common/cuberange_proto.c`
- Create: `tests/native/test_proto.c`
- Create: `tests/native/Makefile`
- Create: `tests/pytest/test_c_matches_python.py`

**Interfaces:**
- Consumes: the wire formats defined in Tasks 1-4.
- Produces (C):
  - `uint16_t cr_crc16(const uint8_t *data, size_t len);`
  - `int cr_encode_tc_frame(uint8_t *out, size_t out_cap, const uint8_t *payload, size_t len, uint8_t seq);`
  - `int cr_decode_tc_frame(const uint8_t *frame, size_t len, uint8_t *seq, const uint8_t **payload, size_t *payload_len);`
  - `int cr_encode_tm_frame(uint8_t *out, size_t out_cap, const uint8_t *payload, size_t len, uint8_t mc, uint8_t vc);`
  - `size_t cr_wrap(uint8_t *out, size_t out_cap, const uint8_t *frame, size_t len);`
  - `void cr_deframer_init(cr_deframer_t *d);`
  - `int cr_deframer_feed(cr_deframer_t *d, const uint8_t *chunk, size_t len, cr_frame_cb cb, void *ctx);`

> Why this task exists: the design's own review found that two self-written codecs which share a
> mistake round-trip perfectly. The C is written from the same specification but independently,
> and this task's job is to make them disagree loudly if they ever diverge.

- [ ] **Step 1: Write the failing native test**

`tests/native/test_proto.c`:

```c
/* Native (host) unit test for the firmware codec. Built with plain gcc - no Zephyr, no board -
 * so it runs in CI in milliseconds and can be fuzzed later. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cuberange_proto.h"

static int failures;

#define CHECK(cond, msg) do { \
	if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, msg); failures++; } \
} while (0)

static void test_crc_check_vector(void)
{
	CHECK(cr_crc16((const uint8_t *)"123456789", 9) == 0xE5CC, "CRC-16/IBM-3740 check vector");
}

static void test_tc_frame_round_trip(void)
{
	const uint8_t payload[] = {0x01, 0x02, 0x03, 0x04};
	uint8_t frame[64];
	int n = cr_encode_tc_frame(frame, sizeof(frame), payload, sizeof(payload), 7);
	CHECK(n > 0, "encode returned a length");
	CHECK(cr_crc16(frame, (size_t)n) == 0x0000, "FECF residue is zero");

	uint8_t seq;
	const uint8_t *out;
	size_t out_len;
	CHECK(cr_decode_tc_frame(frame, (size_t)n, &seq, &out, &out_len) == 0, "decode succeeded");
	CHECK(seq == 7, "sequence number survived");
	CHECK(out_len == sizeof(payload), "payload length survived");
	CHECK(memcmp(out, payload, out_len) == 0, "payload bytes survived");
}

static void test_decode_rejects_corruption(void)
{
	const uint8_t payload[] = {0xAA, 0xBB};
	uint8_t frame[64];
	int n = cr_encode_tc_frame(frame, sizeof(frame), payload, sizeof(payload), 1);
	frame[6] ^= 0xFF;
	uint8_t seq; const uint8_t *out; size_t out_len;
	CHECK(cr_decode_tc_frame(frame, (size_t)n, &seq, &out, &out_len) != 0,
	      "corrupted frame is rejected");
}

struct collect {
	int count;
	uint8_t last[256];
	size_t last_len;
};

static void on_frame(const uint8_t *frame, size_t len, void *ctx)
{
	struct collect *c = ctx;
	c->count++;
	c->last_len = len < sizeof(c->last) ? len : sizeof(c->last);
	memcpy(c->last, frame, c->last_len);
}

static void test_deframer_across_chunk_boundaries(void)
{
	const uint8_t payload[] = {0x55, 0x66, 0x77};
	uint8_t frame[64], stream[128];
	int n = cr_encode_tc_frame(frame, sizeof(frame), payload, sizeof(payload), 2);
	size_t total = cr_wrap(stream, sizeof(stream), frame, (size_t)n);

	for (size_t chunk = 1; chunk <= total; chunk++) {
		cr_deframer_t d;
		struct collect c = {0};
		cr_deframer_init(&d);
		for (size_t i = 0; i < total; i += chunk) {
			size_t take = (i + chunk <= total) ? chunk : total - i;
			cr_deframer_feed(&d, stream + i, take, on_frame, &c);
		}
		CHECK(c.count == 1, "exactly one frame emerged");
		CHECK(c.last_len == (size_t)n, "frame length matched");
		CHECK(memcmp(c.last, frame, c.last_len) == 0, "frame bytes matched");
	}
}

int main(void)
{
	test_crc_check_vector();
	test_tc_frame_round_trip();
	test_decode_rejects_corruption();
	test_deframer_across_chunk_boundaries();
	if (failures) { printf("%d FAILURES\n", failures); return 1; }
	printf("all native codec tests passed\n");
	return 0;
}
```

`tests/native/Makefile`:

```make
# Host build of the firmware codec. No Zephyr, no board - the codec is deliberately free of
# both so it can be unit tested and later fuzzed on the host.
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O1 -g -I../../firmware/common
SRC := ../../firmware/common/cuberange_proto.c test_proto.c

.PHONY: test clean
test: test_proto
	./test_proto

test_proto: $(SRC) ../../firmware/common/cuberange_proto.h
	$(CC) $(CFLAGS) -o $@ $(SRC)

clean:
	rm -f test_proto
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd tests/native && make test`
Expected: `fatal error: cuberange_proto.h: No such file or directory`

- [ ] **Step 3: Write the header**

`firmware/common/cuberange_proto.h`:

```c
/*
 * CubeRange wire codec, shared by every node.
 *
 * Deliberately free of Zephyr and board dependencies so it builds on the host for unit tests and
 * fuzzing. Everything is caller-allocated; nothing here allocates or blocks.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef CUBERANGE_PROTO_H
#define CUBERANGE_PROTO_H

#include <stddef.h>
#include <stdint.h>

#define CR_ASM_0 0x1a
#define CR_ASM_1 0xcf
#define CR_ASM_2 0xfc
#define CR_ASM_3 0x1d

#define CR_SCID           0x0A9
#define CR_VCID           0
#define CR_TC_HEADER_LEN  5
#define CR_TM_HEADER_LEN  6
#define CR_FECF_LEN       2
#define CR_MAX_FRAME_LEN  1024

/* CRC-16/IBM-3740: poly 0x1021, init 0xFFFF, no reflection, xorout 0. CCSDS 132.0-B-3 s4.1.6.2.2.
 * This is NOT the XMODEM or KERMIT variant, both of which are also called "CCITT". */
uint16_t cr_crc16(const uint8_t *data, size_t len);

/* Return the frame length written to `out`, or -1 if it does not fit. */
int cr_encode_tc_frame(uint8_t *out, size_t out_cap,
		       const uint8_t *payload, size_t len, uint8_t seq);
int cr_encode_tm_frame(uint8_t *out, size_t out_cap,
		       const uint8_t *payload, size_t len, uint8_t mc, uint8_t vc);

/* Return 0 on success. `payload` points into `frame`; it is not copied. */
int cr_decode_tc_frame(const uint8_t *frame, size_t len, uint8_t *seq,
		       const uint8_t **payload, size_t *payload_len);
int cr_decode_tm_frame(const uint8_t *frame, size_t len, uint8_t *mc, uint8_t *vc,
		       const uint8_t **payload, size_t *payload_len);

/* ASM + u16 big-endian length + frame. Returns the total written, or 0 if it does not fit. */
size_t cr_wrap(uint8_t *out, size_t out_cap, const uint8_t *frame, size_t len);

typedef void (*cr_frame_cb)(const uint8_t *frame, size_t len, void *ctx);

typedef struct {
	uint8_t buf[CR_MAX_FRAME_LEN + 8];
	size_t used;
} cr_deframer_t;

void cr_deframer_init(cr_deframer_t *d);

/* Feed a chunk; `cb` fires once per complete frame. Never fails on bad input - it resynchronises
 * on the next ASM, because a link that saw garbage must keep working. Returns the frame count. */
int cr_deframer_feed(cr_deframer_t *d, const uint8_t *chunk, size_t len,
		     cr_frame_cb cb, void *ctx);

#endif /* CUBERANGE_PROTO_H */
```

- [ ] **Step 4: Write the implementation**

`firmware/common/cuberange_proto.c`:

```c
/* SPDX-License-Identifier: Apache-2.0 */
#include <string.h>

#include "cuberange_proto.h"

static const uint8_t CR_ASM[4] = {CR_ASM_0, CR_ASM_1, CR_ASM_2, CR_ASM_3};

uint16_t cr_crc16(const uint8_t *data, size_t len)
{
	uint16_t reg = 0xFFFF;

	for (size_t i = 0; i < len; i++) {
		reg ^= (uint16_t)data[i] << 8;
		for (int bit = 0; bit < 8; bit++) {
			reg = (reg & 0x8000) ? (uint16_t)((reg << 1) ^ 0x1021)
					     : (uint16_t)(reg << 1);
		}
	}
	return reg;
}

static void append_fecf(uint8_t *frame, size_t body_len)
{
	uint16_t fecf = cr_crc16(frame, body_len);
	frame[body_len] = (uint8_t)(fecf >> 8);
	frame[body_len + 1] = (uint8_t)(fecf & 0xFF);
}

int cr_encode_tc_frame(uint8_t *out, size_t out_cap,
		       const uint8_t *payload, size_t len, uint8_t seq)
{
	size_t total = CR_TC_HEADER_LEN + len + CR_FECF_LEN;

	if (total > out_cap || total > CR_MAX_FRAME_LEN) {
		return -1;
	}
	uint16_t length_field = (uint16_t)(total - 1);
	out[0] = (uint8_t)((CR_SCID >> 8) & 0x03);
	out[1] = (uint8_t)(CR_SCID & 0xFF);
	out[2] = (uint8_t)(((CR_VCID & 0x3F) << 2) | ((length_field >> 8) & 0x03));
	out[3] = (uint8_t)(length_field & 0xFF);
	out[4] = seq;
	memcpy(out + CR_TC_HEADER_LEN, payload, len);
	append_fecf(out, CR_TC_HEADER_LEN + len);
	return (int)total;
}

int cr_encode_tm_frame(uint8_t *out, size_t out_cap,
		       const uint8_t *payload, size_t len, uint8_t mc, uint8_t vc)
{
	size_t total = CR_TM_HEADER_LEN + len + CR_FECF_LEN;

	if (total > out_cap || total > CR_MAX_FRAME_LEN) {
		return -1;
	}
	uint16_t word0 = (uint16_t)(((CR_SCID & 0x3FF) << 4) | ((CR_VCID & 0x7) << 1));
	out[0] = (uint8_t)(word0 >> 8);
	out[1] = (uint8_t)(word0 & 0xFF);
	out[2] = mc;
	out[3] = vc;
	out[4] = 0;                     /* data field status: first header pointer = 0 */
	out[5] = 0;
	memcpy(out + CR_TM_HEADER_LEN, payload, len);
	append_fecf(out, CR_TM_HEADER_LEN + len);
	return (int)total;
}

int cr_decode_tc_frame(const uint8_t *frame, size_t len, uint8_t *seq,
		       const uint8_t **payload, size_t *payload_len)
{
	if (len < CR_TC_HEADER_LEN + CR_FECF_LEN || cr_crc16(frame, len) != 0x0000) {
		return -1;
	}
	size_t declared = (size_t)(((frame[2] & 0x03) << 8) | frame[3]) + 1;

	if (declared != len) {
		return -1;
	}
	*seq = frame[4];
	*payload = frame + CR_TC_HEADER_LEN;
	*payload_len = len - CR_TC_HEADER_LEN - CR_FECF_LEN;
	return 0;
}

int cr_decode_tm_frame(const uint8_t *frame, size_t len, uint8_t *mc, uint8_t *vc,
		       const uint8_t **payload, size_t *payload_len)
{
	if (len < CR_TM_HEADER_LEN + CR_FECF_LEN || cr_crc16(frame, len) != 0x0000) {
		return -1;
	}
	*mc = frame[2];
	*vc = frame[3];
	*payload = frame + CR_TM_HEADER_LEN;
	*payload_len = len - CR_TM_HEADER_LEN - CR_FECF_LEN;
	return 0;
}

size_t cr_wrap(uint8_t *out, size_t out_cap, const uint8_t *frame, size_t len)
{
	size_t total = sizeof(CR_ASM) + 2 + len;

	if (total > out_cap) {
		return 0;
	}
	memcpy(out, CR_ASM, sizeof(CR_ASM));
	out[4] = (uint8_t)(len >> 8);
	out[5] = (uint8_t)(len & 0xFF);
	memcpy(out + 6, frame, len);
	return total;
}

void cr_deframer_init(cr_deframer_t *d)
{
	d->used = 0;
}

int cr_deframer_feed(cr_deframer_t *d, const uint8_t *chunk, size_t len,
		     cr_frame_cb cb, void *ctx)
{
	int emitted = 0;

	for (size_t i = 0; i < len; i++) {
		if (d->used < sizeof(d->buf)) {
			d->buf[d->used++] = chunk[i];
		} else {
			/* Overflow can only mean we are tracking garbage. Drop the oldest octet so
			 * the next real ASM can still be found. */
			memmove(d->buf, d->buf + 1, sizeof(d->buf) - 1);
			d->buf[sizeof(d->buf) - 1] = chunk[i];
		}

		for (;;) {
			size_t start = 0;
			int found = 0;

			while (start + sizeof(CR_ASM) <= d->used) {
				if (memcmp(d->buf + start, CR_ASM, sizeof(CR_ASM)) == 0) {
					found = 1;
					break;
				}
				start++;
			}
			if (!found) {
				/* Retain only what could still be a partial ASM. */
				size_t keep = (d->used < sizeof(CR_ASM) - 1)
						? d->used : sizeof(CR_ASM) - 1;
				memmove(d->buf, d->buf + d->used - keep, keep);
				d->used = keep;
				break;
			}
			if (start > 0) {
				memmove(d->buf, d->buf + start, d->used - start);
				d->used -= start;
			}
			if (d->used < sizeof(CR_ASM) + 2) {
				break;
			}
			size_t flen = ((size_t)d->buf[4] << 8) | d->buf[5];

			if (flen == 0 || flen > CR_MAX_FRAME_LEN) {
				memmove(d->buf, d->buf + sizeof(CR_ASM),
					d->used - sizeof(CR_ASM));
				d->used -= sizeof(CR_ASM);
				continue;
			}
			size_t end = sizeof(CR_ASM) + 2 + flen;

			if (d->used < end) {
				break;
			}
			cb(d->buf + sizeof(CR_ASM) + 2, flen, ctx);
			emitted++;
			memmove(d->buf, d->buf + end, d->used - end);
			d->used -= end;
		}
	}
	return emitted;
}
```

- [ ] **Step 5: Run the native tests and confirm they pass**

Run: `cd tests/native && make test`
Expected: `all native codec tests passed`

- [ ] **Step 6: Make the two implementations check each other**

`tests/pytest/test_c_matches_python.py`:

```python
"""The C and Python codecs must produce identical bytes.

This is the guard the design review asked for: two self-written codecs that share a mistake
round-trip perfectly against themselves. Building the C for the host and diffing its output
against the Python is what makes a divergence loud.
"""
import ctypes
import subprocess
from pathlib import Path

import pytest

from cuberange.proto.crc import crc16_ccsds
from cuberange.proto.frame import encode_tc_frame, encode_tm_frame, wrap

REPO = Path(__file__).resolve().parents[2]
COMMON = REPO / "firmware" / "common"


@pytest.fixture(scope="module")
def clib(tmp_path_factory):
    so = tmp_path_factory.mktemp("clib") / "libcrproto.so"
    subprocess.run(
        ["cc", "-std=c11", "-O1", "-fPIC", "-shared",
         "-I", str(COMMON), str(COMMON / "cuberange_proto.c"), "-o", str(so)],
        check=True)
    lib = ctypes.CDLL(str(so))
    lib.cr_crc16.restype = ctypes.c_uint16
    lib.cr_crc16.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    lib.cr_encode_tc_frame.restype = ctypes.c_int
    lib.cr_encode_tm_frame.restype = ctypes.c_int
    lib.cr_wrap.restype = ctypes.c_size_t
    return lib


def _c_encode(lib, fn, payload, *args):
    out = ctypes.create_string_buffer(2048)
    n = fn(out, ctypes.c_size_t(len(out)), payload, ctypes.c_size_t(len(payload)), *args)
    assert n > 0, "C encoder refused the input"
    return out.raw[:n]


@pytest.mark.parametrize("n", [0, 1, 2, 7, 8, 63, 255, 512])
def test_crc_agrees(clib, n):
    payload = bytes((i * 11 + 5) & 0xFF for i in range(n))
    assert clib.cr_crc16(payload, len(payload)) == crc16_ccsds(payload)


@pytest.mark.parametrize("n,seq", [(1, 0), (4, 7), (100, 255)])
def test_tc_frame_agrees(clib, n, seq):
    payload = bytes((i * 3) & 0xFF for i in range(n))
    assert _c_encode(clib, clib.cr_encode_tc_frame, payload,
                     ctypes.c_uint8(seq)) == encode_tc_frame(payload, seq)


@pytest.mark.parametrize("n,mc,vc", [(1, 0, 0), (30, 3, 4), (200, 255, 255)])
def test_tm_frame_agrees(clib, n, mc, vc):
    payload = bytes((i * 5) & 0xFF for i in range(n))
    assert _c_encode(clib, clib.cr_encode_tm_frame, payload,
                     ctypes.c_uint8(mc), ctypes.c_uint8(vc)) == encode_tm_frame(payload, mc, vc)


def test_wrap_agrees(clib):
    frame = encode_tc_frame(b"\x01\x02\x03", 9)
    out = ctypes.create_string_buffer(2048)
    n = clib.cr_wrap(out, ctypes.c_size_t(len(out)), frame, ctypes.c_size_t(len(frame)))
    assert out.raw[:n] == wrap(frame)
```

- [ ] **Step 7: Run the cross-check and confirm it passes**

Run: `PYTHONPATH=src pytest tests/pytest/test_c_matches_python.py -v`
Expected: 15 passed

- [ ] **Step 8: Commit**

```bash
git add firmware/common/cuberange_proto.h firmware/common/cuberange_proto.c \
        tests/native/ tests/pytest/test_c_matches_python.py
git commit -m "feat(proto): C codec for the firmware, byte-checked against the Python one"
```

---

### Task 6: COMM node — link to CSP gateway

**Files:**
- Create: `firmware/apps/comm/CMakeLists.txt`
- Create: `firmware/apps/comm/prj.conf`
- Create: `firmware/apps/comm/src/main.c`
- Create: `firmware/apps/comm/boards/nucleo_h753zi.overlay`
- Modify: `Makefile` — add a `firmware-p0` target

**Interfaces:**
- Consumes: `cuberange_proto.h` (Task 5), libcsp.
- Produces: a node at CSP address 5 that forwards deframed TC payloads to CSP address 1 port 17, and reframes anything arriving on that port back onto the link.

- [ ] **Step 1: Write the overlay and build files**

`firmware/apps/comm/boards/nucleo_h753zi.overlay` — identical to the csp_ping one:

```dts
/*
 * The space link is usart2. usart3 stays Zephyr's console, because a link that shares the console
 * delivers 9239 bytes of boot banner to the ground station before the first application byte.
 */
&usart2 {
	pinctrl-0 = <&usart2_tx_pa2 &usart2_rx_pa3>;
	pinctrl-names = "default";
	current-speed = <115200>;
	status = "okay";
};

/ {
	aliases {
		spacelink = &usart2;
	};
};
```

`firmware/apps/comm/CMakeLists.txt`:

```cmake
# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(cuberange_comm)

target_sources(app PRIVATE src/main.c ../../common/cuberange_proto.c)
target_include_directories(app PRIVATE ../../common)
target_link_libraries(app PRIVATE csp)
```

`firmware/apps/comm/prj.conf`:

```
CONFIG_LIBCSP=y
CONFIG_CSP_USE_RTABLE=n
CONFIG_CSP_HAVE_CAN=y

CONFIG_PICOLIBC=y
CONFIG_CAN=y
CONFIG_EVENTS=y
CONFIG_HEAP_MEM_POOL_SIZE=4096

CONFIG_SERIAL=y
CONFIG_UART_INTERRUPT_DRIVEN=y

# Nothing but the link protocol may reach usart2, and the console must stay on usart3.
CONFIG_LOG=n
CONFIG_BOOT_BANNER=y
```

- [ ] **Step 2: Write the COMM application**

`firmware/apps/comm/src/main.c`:

```c
/*
 * CubeRange COMM node: the gateway between the space link and the internal bus.
 *
 * uplink:   usart2 bytes -> deframe -> TC frame -> Space Packet -> CSP to the OBC
 * downlink: CSP from the OBC -> TM frame -> lab framing -> usart2 bytes
 *
 * COMM deliberately does not parse PUS. It is a link-layer device; service dispatch belongs to
 * the OBC. That boundary is what makes an attacker who owns COMM different from one who owns the
 * OBC, which several exercises depend on.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>

#include <csp/csp.h>
#include <csp/drivers/can_zephyr.h>

#include "cuberange_proto.h"

#define COMM_ADDR     5
#define OBC_ADDR      1
#define CSP_PORT_PUS  17
#define CAN_BITRATE   1000000

#define RX_RING_SIZE  512
#define ROUTER_STACK  1024
#define LINK_STACK    2048
#define DOWN_STACK    2048

static const struct device *link_dev;
static csp_iface_t *can_iface;

K_MSGQ_DEFINE(link_rx_q, 1, RX_RING_SIZE, 1);

static void link_isr(const struct device *dev, void *user_data)
{
	ARG_UNUSED(user_data);

	if (!uart_irq_update(dev)) {
		return;
	}
	while (uart_irq_rx_ready(dev)) {
		uint8_t byte;

		if (uart_fifo_read(dev, &byte, 1) != 1) {
			break;
		}
		/* Dropping under overrun is correct here: the ground station will retransmit, and
		 * blocking in an ISR would stall the whole node. */
		(void)k_msgq_put(&link_rx_q, &byte, K_NO_WAIT);
	}
}

static void link_write(const uint8_t *buf, size_t len)
{
	for (size_t i = 0; i < len; i++) {
		uart_poll_out(link_dev, buf[i]);
	}
}

/* One deframed TC frame: strip the frame header and hand the Space Packet to the OBC. */
static void on_tc_frame(const uint8_t *frame, size_t len, void *ctx)
{
	ARG_UNUSED(ctx);

	uint8_t seq;
	const uint8_t *packet;
	size_t packet_len;

	if (cr_decode_tc_frame(frame, len, &seq, &packet, &packet_len) != 0) {
		printk("COMM: dropping a TC frame that failed its FECF or length check\n");
		return;
	}
	printk("COMM: uplink frame seq=%u carrying %u octets -> OBC\n",
	       seq, (unsigned int)packet_len);

	csp_packet_t *out = csp_buffer_get(packet_len);

	if (out == NULL) {
		printk("COMM: no CSP buffer for a %u octet packet\n", (unsigned int)packet_len);
		return;
	}
	memcpy(out->data, packet, packet_len);
	out->length = (uint16_t)packet_len;
	csp_sendto(CSP_PRIO_NORM, OBC_ADDR, CSP_PORT_PUS, CSP_PORT_PUS, CSP_O_NONE, out, 1000);
}

static void router_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	while (1) {
		csp_route_work();
	}
}

static void link_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	cr_deframer_t deframer;
	cr_deframer_init(&deframer);

	while (1) {
		uint8_t byte;

		if (k_msgq_get(&link_rx_q, &byte, K_FOREVER) == 0) {
			cr_deframer_feed(&deframer, &byte, 1, on_tc_frame, NULL);
		}
	}
}

/* Downlink: anything the OBC sends to CSP port 17 becomes a TM frame on the link. */
static void down_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	csp_socket_t sock = {0};
	csp_bind(&sock, CSP_PORT_PUS);
	csp_listen(&sock, 4);

	uint8_t frame[CR_MAX_FRAME_LEN];
	uint8_t wire[CR_MAX_FRAME_LEN + 8];
	uint8_t mc = 0, vc = 0;

	while (1) {
		csp_conn_t *conn = csp_accept(&sock, 1000);

		if (conn == NULL) {
			continue;
		}
		csp_packet_t *packet;

		while ((packet = csp_read(conn, 100)) != NULL) {
			int n = cr_encode_tm_frame(frame, sizeof(frame),
						   packet->data, packet->length, mc++, vc++);
			if (n > 0) {
				size_t total = cr_wrap(wire, sizeof(wire), frame, (size_t)n);

				link_write(wire, total);
				printk("COMM: downlink %u octets from node %d\n",
				       (unsigned int)packet->length, csp_conn_src(conn));
			} else {
				printk("COMM: TM payload of %u octets does not fit a frame\n",
				       (unsigned int)packet->length);
			}
			csp_buffer_free(packet);
		}
		csp_close(conn);
	}
}

K_THREAD_DEFINE(router_id, ROUTER_STACK, router_task, NULL, NULL, NULL, 0, 0, K_TICKS_FOREVER);
K_THREAD_DEFINE(link_id, LINK_STACK, link_task, NULL, NULL, NULL, 1, 0, K_TICKS_FOREVER);
K_THREAD_DEFINE(down_id, DOWN_STACK, down_task, NULL, NULL, NULL, 1, 0, K_TICKS_FOREVER);

int main(void)
{
	printk("CUBERANGE: COMM (addr %d) booting\n", COMM_ADDR);

	csp_init();
	k_thread_start(router_id);

	const struct device *can_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_canbus));

	if (!device_is_ready(can_dev)) {
		printk("COMM: FATAL can device not ready\n");
		return -1;
	}
	int err = csp_can_open_and_add_interface(can_dev, "CAN", COMM_ADDR, CAN_BITRATE,
						 COMM_ADDR, 0x3FFF, &can_iface);
	if (err != CSP_ERR_NONE) {
		printk("COMM: FATAL csp_can_open_and_add_interface -> %d\n", err);
		return -1;
	}
	can_iface->is_default = 1;

	link_dev = DEVICE_DT_GET(DT_ALIAS(spacelink));
	if (!device_is_ready(link_dev)) {
		printk("COMM: FATAL space link device not ready\n");
		return -1;
	}
	uart_irq_callback_user_data_set(link_dev, link_isr, NULL);
	uart_irq_rx_enable(link_dev);

	k_thread_start(link_id);
	k_thread_start(down_id);

	printk("CUBERANGE: COMM ready, link on %s\n", link_dev->name);
	return 0;
}
```

- [ ] **Step 3: Add the build target**

Append to `Makefile`:

```make
# P0 node images. One source tree per role; the shared codec is compiled into each.
firmware-p0:
	@test -f $(ENV) || { echo "missing $(ENV) - run 'make toolchain' first"; exit 1; }
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-comm firmware/apps/comm
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc firmware/apps/obc
	@ls -l $(OUT)/build-comm/zephyr/zephyr.elf $(OUT)/build-obc/zephyr/zephyr.elf
```

- [ ] **Step 4: Confirm COMM builds**

Run: `make firmware-p0 2>&1 | tail -20`
Expected: the COMM build reaches `Linking C executable zephyr/zephyr.elf`, then fails on the OBC
because Task 7 has not created it yet. That is the expected state at this step.

- [ ] **Step 5: Commit**

```bash
git add firmware/apps/comm Makefile
git commit -m "feat(comm): space link to CSP gateway"
```

---

### Task 7: OBC node — PUS service 17

**Files:**
- Create: `firmware/apps/obc/CMakeLists.txt`
- Create: `firmware/apps/obc/prj.conf`
- Create: `firmware/apps/obc/src/main.c`

**Interfaces:**
- Consumes: `cuberange_proto.h` (Task 5), libcsp, the Space Packet and PUS layouts from Tasks 2-3.
- Produces: a node at CSP address 1 that answers PUS 17,1 with PUS 17,2 addressed back to COMM.

- [ ] **Step 1: Write the build files**

`firmware/apps/obc/CMakeLists.txt`:

```cmake
# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(cuberange_obc)

target_sources(app PRIVATE src/main.c ../../common/cuberange_proto.c)
target_include_directories(app PRIVATE ../../common)
target_link_libraries(app PRIVATE csp)
```

`firmware/apps/obc/prj.conf`:

```
CONFIG_LIBCSP=y
CONFIG_CSP_USE_RTABLE=n
CONFIG_CSP_HAVE_CAN=y

CONFIG_PICOLIBC=y
CONFIG_CAN=y
CONFIG_EVENTS=y
CONFIG_HEAP_MEM_POOL_SIZE=4096

CONFIG_LOG=n
CONFIG_BOOT_BANNER=y
```

> The OBC has no `boards/` overlay: it never touches the space link. Only COMM does.

- [ ] **Step 2: Write the OBC application**

`firmware/apps/obc/src/main.c`:

```c
/*
 * CubeRange OBC node: command and data handling.
 *
 * Receives CCSDS Space Packets from COMM over CSP and dispatches by PUS service. P0 implements
 * service 17 (test) only; every other service is answered with nothing and logged, so an
 * unimplemented service is visible rather than silent.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/sys/printk.h>
#include <string.h>

#include <csp/csp.h>
#include <csp/drivers/can_zephyr.h>

#include "cuberange_proto.h"

#define OBC_ADDR      1
#define COMM_ADDR     5
#define CSP_PORT_PUS  17
#define CAN_BITRATE   1000000

#define SP_HEADER_LEN     6
#define PUS_TC_SEC_LEN    5
#define PUS_TM_SEC_LEN    7
#define PUS_VERSION       2
#define SERVICE_TEST      17
#define SUBTYPE_TEST      1
#define SUBTYPE_TEST_REP  2
#define OBC_APID          0x0A9
#define TIME_LEN          4

#define ROUTER_STACK 1024
#define APP_STACK    2048

static csp_iface_t *can_iface;
static uint16_t tm_seq_count;
static uint16_t tm_msg_counter;

/* Build a TM Space Packet carrying a PUS 17,2 report and send it to COMM. */
static void send_test_report(uint16_t source_id)
{
	uint8_t body[SP_HEADER_LEN + PUS_TM_SEC_LEN + TIME_LEN];
	uint32_t now = (uint32_t)k_uptime_get();
	size_t data_len = PUS_TM_SEC_LEN + TIME_LEN;

	uint16_t word0 = (0 << 12) | (1 << 11) | OBC_APID;      /* TM, secondary header present */
	uint16_t word1 = (0x3u << 14) | (tm_seq_count++ & 0x3FFF);
	uint16_t word2 = (uint16_t)(data_len - 1);

	body[0] = (uint8_t)(word0 >> 8);  body[1] = (uint8_t)(word0 & 0xFF);
	body[2] = (uint8_t)(word1 >> 8);  body[3] = (uint8_t)(word1 & 0xFF);
	body[4] = (uint8_t)(word2 >> 8);  body[5] = (uint8_t)(word2 & 0xFF);

	uint8_t *sec = body + SP_HEADER_LEN;
	uint16_t counter = tm_msg_counter++;

	sec[0] = PUS_VERSION << 4;
	sec[1] = SERVICE_TEST;
	sec[2] = SUBTYPE_TEST_REP;
	sec[3] = (uint8_t)(counter >> 8);  sec[4] = (uint8_t)(counter & 0xFF);
	sec[5] = (uint8_t)(source_id >> 8); sec[6] = (uint8_t)(source_id & 0xFF);
	sec[7] = (uint8_t)(now >> 24); sec[8] = (uint8_t)(now >> 16);
	sec[9] = (uint8_t)(now >> 8);  sec[10] = (uint8_t)(now & 0xFF);

	csp_packet_t *packet = csp_buffer_get(sizeof(body));

	if (packet == NULL) {
		printk("OBC: no CSP buffer for a test report\n");
		return;
	}
	memcpy(packet->data, body, sizeof(body));
	packet->length = (uint16_t)sizeof(body);
	csp_sendto(CSP_PRIO_NORM, COMM_ADDR, CSP_PORT_PUS, CSP_PORT_PUS, CSP_O_NONE, packet, 1000);
	printk("OBC: PUS 17,2 report sent to COMM (counter %u)\n", counter);
}

static void handle_space_packet(const uint8_t *raw, size_t len)
{
	if (len < SP_HEADER_LEN + PUS_TC_SEC_LEN) {
		printk("OBC: space packet too short: %u octets\n", (unsigned int)len);
		return;
	}
	uint16_t word0 = (uint16_t)((raw[0] << 8) | raw[1]);
	uint16_t apid = word0 & 0x7FF;
	size_t declared = (size_t)((raw[4] << 8) | raw[5]) + 1;

	if (declared != len - SP_HEADER_LEN) {
		printk("OBC: declared data length %u but %u octets follow\n",
		       (unsigned int)declared, (unsigned int)(len - SP_HEADER_LEN));
		return;
	}
	const uint8_t *sec = raw + SP_HEADER_LEN;
	uint8_t service = sec[1];
	uint8_t subtype = sec[2];
	uint16_t source_id = (uint16_t)((sec[3] << 8) | sec[4]);

	printk("OBC: APID 0x%03x PUS %u,%u from source %u\n", apid, service, subtype, source_id);

	if (service == SERVICE_TEST && subtype == SUBTYPE_TEST) {
		send_test_report(source_id);
	} else {
		printk("OBC: service %u,%u is not implemented in P0\n", service, subtype);
	}
}

static void router_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	while (1) {
		csp_route_work();
	}
}

static void app_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	csp_socket_t sock = {0};
	csp_bind(&sock, CSP_PORT_PUS);
	csp_listen(&sock, 4);
	printk("CUBERANGE: OBC listening on CSP port %d\n", CSP_PORT_PUS);

	while (1) {
		csp_conn_t *conn = csp_accept(&sock, 1000);

		if (conn == NULL) {
			continue;
		}
		csp_packet_t *packet;

		while ((packet = csp_read(conn, 100)) != NULL) {
			handle_space_packet(packet->data, packet->length);
			csp_buffer_free(packet);
		}
		csp_close(conn);
	}
}

K_THREAD_DEFINE(router_id, ROUTER_STACK, router_task, NULL, NULL, NULL, 0, 0, K_TICKS_FOREVER);
K_THREAD_DEFINE(app_id, APP_STACK, app_task, NULL, NULL, NULL, 1, 0, K_TICKS_FOREVER);

int main(void)
{
	printk("CUBERANGE: OBC (addr %d) booting\n", OBC_ADDR);

	csp_init();
	k_thread_start(router_id);

	const struct device *can_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_canbus));

	if (!device_is_ready(can_dev)) {
		printk("OBC: FATAL can device not ready\n");
		return -1;
	}
	int err = csp_can_open_and_add_interface(can_dev, "CAN", OBC_ADDR, CAN_BITRATE,
						 OBC_ADDR, 0x3FFF, &can_iface);
	if (err != CSP_ERR_NONE) {
		printk("OBC: FATAL csp_can_open_and_add_interface -> %d\n", err);
		return -1;
	}
	can_iface->is_default = 1;

	k_thread_start(app_id);
	printk("CUBERANGE: OBC ready\n");
	return 0;
}
```

- [ ] **Step 3: Build both images**

Run: `make firmware-p0`
Expected: two `zephyr.elf` files listed, one per node.

- [ ] **Step 4: Commit**

```bash
git add firmware/apps/obc
git commit -m "feat(obc): PUS service 17 dispatch over CSP"
```

---

### Task 8: Ground station and the end-to-end test

**Files:**
- Create: `src/cuberange/gs/__init__.py`
- Create: `src/cuberange/gs/link.py`
- Create: `src/cuberange/gs/station.py`
- Create: `scripts/multi-node/p0.resc`
- Create: `tests/e2e/test_p0_roundtrip.py`
- Modify: `Makefile` — add `demo-p0`

**Interfaces:**
- Consumes: Tasks 1-4 (codec), Tasks 6-7 (firmware images).
- Produces:
  - `class SpaceLink` — `connect()`, `send_frame(frame: bytes)`, `poll() -> list[bytes]`, `close()`
  - `class GroundStation` — `ping(timeout: float = 10.0) -> bool`

- [ ] **Step 1: Write the link and station**

`src/cuberange/gs/__init__.py` (empty), then `src/cuberange/gs/link.py`:

```python
"""TCP client for the space link.

The far end is a Renode socket terminal attached to the COMM node's usart2. It must have been
created with telnetMode=false; with the default true, Renode prepends 11 telnet IAC bytes and the
first frame is unparseable.
"""
from __future__ import annotations

import socket

from ..proto.frame import Deframer, wrap


class SpaceLink:
    def __init__(self, host: str = "127.0.0.1", port: int = 3777, timeout: float = 0.2):
        self._addr = (host, port)
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._deframer = Deframer()

    def connect(self, retries: int = 60) -> None:
        last: OSError | None = None
        for _ in range(retries):
            try:
                self._sock = socket.create_connection(self._addr, timeout=3)
                self._sock.settimeout(self._timeout)
                return
            except OSError as exc:
                last = exc
                import time
                time.sleep(0.5)
        raise ConnectionError(f"space link {self._addr} never came up") from last

    def send_frame(self, frame: bytes) -> None:
        assert self._sock is not None, "connect() first"
        self._sock.sendall(wrap(frame))

    def poll(self) -> list[bytes]:
        """Return any complete frames received since the last call. Never blocks for long."""
        assert self._sock is not None, "connect() first"
        try:
            chunk = self._sock.recv(4096)
        except socket.timeout:
            return []
        if not chunk:
            raise ConnectionError("space link closed by the far end")
        return self._deframer.feed(chunk)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
```

`src/cuberange/gs/station.py`:

```python
"""Minimal ground station: build a TC, send it, wait for the matching TM."""
from __future__ import annotations

import time

from ..proto.frame import decode_tm_frame, encode_tc_frame
from ..proto.pus import (PusTc, PusTm, SERVICE_TEST, SUBTYPE_CONNECTION_TEST,
                         SUBTYPE_CONNECTION_TEST_REPORT)
from ..proto.spacepacket import PacketType, SpacePacket
from .link import SpaceLink

GROUND_SOURCE_ID = 0x0042
OBC_APID = 0x0A9
PUS_TM_TIME_LEN = 4


class GroundStation:
    def __init__(self, link: SpaceLink):
        self.link = link
        self._tc_seq = 0

    def _next_seq(self) -> int:
        seq = self._tc_seq
        self._tc_seq = (self._tc_seq + 1) & 0x3FFF
        return seq

    def send_connection_test(self) -> int:
        tc = PusTc(service=SERVICE_TEST, subtype=SUBTYPE_CONNECTION_TEST,
                   source_id=GROUND_SOURCE_ID)
        seq = self._next_seq()
        packet = SpacePacket(apid=OBC_APID, ptype=PacketType.TC, sec_hdr=True,
                             seq_count=seq, data=tc.encode())
        self.link.send_frame(encode_tc_frame(packet.encode(), seq & 0xFF))
        return seq

    def ping(self, timeout: float = 10.0) -> bool:
        """Send PUS 17,1 and return True once a matching 17,2 report comes back."""
        self.send_connection_test()
        deadline = time.time() + timeout
        while time.time() < deadline:
            for frame in self.link.poll():
                try:
                    _mc, _vc, payload = decode_tm_frame(frame)
                    packet = SpacePacket.decode(payload)
                    tm = PusTm.decode(packet.data, time_len=PUS_TM_TIME_LEN)
                except ValueError:
                    # Keep the raw bytes: on a security range a malformed frame is evidence,
                    # not noise.
                    print(f"ground: undecodable downlink frame {frame.hex()}")
                    continue
                if (tm.service, tm.subtype) == (SERVICE_TEST, SUBTYPE_CONNECTION_TEST_REPORT):
                    return True
        return False
```

- [ ] **Step 2: Write the scenario script**

`scripts/multi-node/p0.resc`:

```
:name: CubeRange P0
:description: Ground station -> space link -> COMM -> CSP/CAN -> OBC -> PUS 17,2 -> back

$comm?=@/tmp/cuberange/build-comm/zephyr/zephyr.elf
$obc?=@/tmp/cuberange/build-obc/zephyr/zephyr.elf
$commuart?=@/tmp/cuberange/comm.uart
$obcuart?=@/tmp/cuberange/obc.uart
$linkport?=3777

emulation CreateCANHub "canHub"

mach create "COMM"
machine LoadPlatformDescription @platforms/boards/nucleo_h753zi.repl
connector Connect sysbus.fdcan1 canHub
sysbus LoadELF $comm
sysbus.usart3 CreateFileBackend $commuart true
# The third argument disables telnet mode. Leave it out and Renode prepends 11 IAC bytes, which
# makes the first frame unparseable.
emulation CreateServerSocketTerminal $linkport "spacelink" false
connector Connect sysbus.usart2 spacelink
logLevel 3 sysbus
logLevel 3 rcc
logLevel 3 fdcan1

mach create "OBC"
machine LoadPlatformDescription @platforms/boards/nucleo_h753zi.repl
connector Connect sysbus.fdcan1 canHub
sysbus LoadELF $obc
sysbus.usart3 CreateFileBackend $obcuart true
logLevel 3 sysbus
logLevel 3 rcc
logLevel 3 fdcan1

emulation SetGlobalQuantum "0.002"
emulation SetGlobalAdvanceImmediately true
```

- [ ] **Step 3: Write the failing end-to-end test**

`tests/e2e/test_p0_roundtrip.py`:

```python
"""P0 acceptance: a real PUS 17,1 goes out and a real PUS 17,2 comes back.

Renode runs free (`start`) here rather than being stepped, because the ground station is an
independent process talking over TCP and the exchange is millisecond-scale. Exercises that need
determinism use the CI profile instead - see the design's execution-profile section.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.link import SpaceLink          # noqa: E402
from cuberange.gs.station import GroundStation   # noqa: E402

RENODE_DIR = Path(os.environ.get(
    "RENODE_DIR", Path.home() / "tools" / "renode_1.16.1-dotnet_portable"))
OUT = Path(os.environ.get("OUT", "/tmp/cuberange"))
LINK_PORT = 3777


@pytest.fixture
def renode():
    for name in ("build-comm", "build-obc"):
        elf = OUT / name / "zephyr" / "zephyr.elf"
        if not elf.exists():
            pytest.skip(f"{elf} missing - run 'make firmware-p0' first")

    proc = subprocess.Popen(
        ["./renode", "--disable-xwt", "--plain", "--hide-analyzers", "--hide-log",
         "--port", "3778",
         "-e", f"include @{REPO}/scripts/multi-node/p0.resc",
         "-e", "start"],
        cwd=RENODE_DIR, stdout=open(OUT / "p0-renode.log", "w"),
        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    yield proc
    os.killpg(os.getpgid(proc.pid), 15)
    proc.wait(timeout=10)


def test_pus17_round_trip(renode):
    link = SpaceLink(port=LINK_PORT)
    link.connect()
    try:
        # The nodes need their CAN interfaces up before the first uplink.
        time.sleep(3)
        station = GroundStation(link)
        assert station.ping(timeout=20), "no PUS 17,2 report came back"
    finally:
        link.close()


def test_link_carries_no_console_output(renode):
    """The space link must be free of Zephyr's console. If this fails, someone pointed the link at
    usart3 or enabled logging on usart2."""
    link = SpaceLink(port=LINK_PORT)
    link.connect()
    try:
        time.sleep(3)
        raw = b""
        try:
            raw = link._sock.recv(4096)     # noqa: SLF001 - deliberately looking at raw bytes
        except OSError:
            pass
        assert b"Booting Zephyr" not in raw, f"console output leaked onto the link: {raw[:80]!r}"
    finally:
        link.close()
```

- [ ] **Step 4: Run it and confirm it fails**

Run: `PYTHONPATH=src pytest tests/e2e/test_p0_roundtrip.py -v`
Expected: FAIL on `no PUS 17,2 report came back` (or a skip if the images are missing — build them first).

- [ ] **Step 5: Make it pass**

Build and run:

```bash
make firmware-p0
PYTHONPATH=src pytest tests/e2e/test_p0_roundtrip.py -v
```

If the round trip fails, read `/tmp/cuberange/comm.uart` and `/tmp/cuberange/obc.uart` before
changing any code. Those consoles say exactly how far the exchange got:

| Last line seen | Meaning |
| --- | --- |
| no `COMM: uplink frame` | the deframer never completed a frame — check `telnetMode=false` and the ASM |
| `COMM: dropping a TC frame` | FECF or length mismatch — the Python and C codecs disagree; Task 5's cross-check should have caught it |
| `OBC: APID ... PUS 17,1` but no report | the OBC's reply path or CSP addressing is wrong |
| `OBC: PUS 17,2 report sent` but no `COMM: downlink` | COMM is not bound to CSP port 17, or the OBC addressed the wrong node |
| `COMM: downlink` but the test still fails | the ground station's TM decode is wrong — check the time field length |

- [ ] **Step 6: Add the demo target**

Append to `Makefile`:

```make
demo-p0: firmware-p0
	PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest tests/e2e/test_p0_roundtrip.py -v
```

- [ ] **Step 7: Run the whole suite**

Run: `PYTHONPATH=src pytest tests/pytest tests/e2e -v && (cd tests/native && make test) && make probe`
Expected: every test passes and the probe reports 34 pass, 0 fail.

- [ ] **Step 8: Commit**

```bash
git add src/cuberange/gs tests/e2e scripts/multi-node/p0.resc Makefile
git commit -m "feat(gs): ground station and the P0 end-to-end round trip"
```

---

## Self-Review

**Spec coverage.** P0's definition in the design — "Python sends a real PUS-17 in a minimal TC
frame, COMM receives it over the UART link and forwards it over libcsp v1/CFP, OBC responds, TM
returns through the same path, request and response IDs are asserted, no privilege or GUI
required" — is covered by Tasks 1-8. The UART-separation constraint has its own test in Task 8.
The conformance-oracle requirement is Task 2 step 5 and Task 5 step 6. Deliberately **not** in P0
and correctly deferred by the design: SDLS, the physics plane, the channel model, exercises, the
GUI, and the attacker node.

**Deferred to the next plan, and named here so they are not forgotten:** the design's acceptance
bar for P0 includes "thirty repeated runs complete without loss or hang", which needs the
watchdog-and-RSS-ceiling wrapper that risk R22 (13% of Renode runs hang and leak to 17 GB)
demands. That wrapper is a P0-exit task, not a codec task, and belongs in the same plan as the CI
profile work.

**Placeholders.** None. Every step carries the code it refers to.

**Type consistency.** `crc16_ccsds` / `cr_crc16`, `encode_tc_frame` / `cr_encode_tc_frame`,
`Deframer.feed` / `cr_deframer_feed`, `SpacePacket`, `PusTc`, `PusTm`, `SpaceLink`,
`GroundStation` are spelled the same in every task that mentions them. The CSP port is 17
everywhere. `PUS_TM_TIME_LEN = 4` on the ground matches `TIME_LEN 4` in the OBC.
