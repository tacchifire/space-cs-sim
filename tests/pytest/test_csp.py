"""CSP v1 and CFP-over-CAN, checked against golden vectors produced by libcsp itself.

tests/golden/csp.json is the verbatim output of a driver that registered a capturing CAN tx_func
and pushed real packets through libcsp's own csp_can1_tx path. Our encoder is written
independently from the documented layout; if it drifts from what the satellite's own stack emits,
these tests fail rather than the satellite silently ignoring a forged frame.
"""
import json
import re
from pathlib import Path

import pytest

from cuberange.proto.csp import (BEGIN, MORE, decode_cfp_id, decode_header, encode_header,
                                 encode_packet)

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "golden" / "csp.json").read_text())
RAW = GOLDEN["raw_oracle_output"]

HDR_RE = re.compile(
    r"CSPv1 hdr pri=(\d+) src=(\d+) dst=(\d+) dport=(\d+) sport=(\d+) flags=0x([0-9A-F]+) "
    r"-> ([0-9A-F]{8})")
CASE_RE = re.compile(r"^B\d+ .*?: CSP payload len=(\d+) src=(\d+) dst=(\d+) dport=(\d+) sport=(\d+)")
FRAME_RE = re.compile(r"^\s+FRAME (\d+): can_id=0x([0-9A-F]+) \(29b\) dlc=(\d+) data=([0-9A-F]*)")


def header_cases():
    return [(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6], 16), m[7])
            for m in (HDR_RE.match(line) for line in RAW) if m]


def fragmentation_cases():
    """Yield (length, src, dst, dport, sport, [(can_id, data_hex), ...]) per B-case."""
    cases, current = [], None
    for line in RAW:
        case = CASE_RE.match(line)
        if case:
            if current:
                cases.append(current)
            current = {"length": int(case[1]), "src": int(case[2]), "dst": int(case[3]),
                       "dport": int(case[4]), "sport": int(case[5]), "frames": []}
            continue
        frame = FRAME_RE.match(line)
        if frame and current is not None:
            current["frames"].append((int(frame[2], 16), int(frame[3]), frame[4]))
    if current:
        cases.append(current)
    return cases


def test_the_golden_file_actually_contains_vectors():
    """Guard against a silently empty oracle file turning every test below into a no-op."""
    assert len(header_cases()) >= 9, f"only {len(header_cases())} header vectors"
    cases = fragmentation_cases()
    assert len(cases) >= 7, f"only {len(cases)} fragmentation cases"
    assert sum(len(c["frames"]) for c in cases) >= 21


@pytest.mark.parametrize("case", header_cases(), ids=lambda c: f"pri{c[0]}_src{c[1]}_dst{c[2]}")
def test_header_matches_libcsp(case):
    pri, src, dst, dport, sport, flags, expected = case
    assert encode_header(pri, src, dst, dport, sport, flags).hex().upper() == expected


@pytest.mark.parametrize("case", header_cases(), ids=lambda c: f"rt_pri{c[0]}_src{c[1]}")
def test_header_round_trip(case):
    pri, src, dst, dport, sport, flags, _ = case
    got = decode_header(encode_header(pri, src, dst, dport, sport, flags))
    assert got == {"priority": pri, "src": src, "dst": dst,
                   "dport": dport, "sport": sport, "flags": flags}


@pytest.mark.parametrize("case", fragmentation_cases(),
                         ids=lambda c: f"len{c['length']}_{c['src']}to{c['dst']}")
def test_fragmentation_matches_libcsp(case):
    # The oracle used an ascending payload starting at 0xA0; reproduce it so the bytes line up.
    payload = bytes((0xA0 + i) & 0xFF for i in range(case["length"]))
    # libcsp's transfer id auto-increments across the oracle run; take it from the first frame.
    transfer_id = case["frames"][0][0] & 0x3FF

    frames = encode_packet(case["src"], case["dst"], case["dport"], case["sport"],
                           payload, priority=2, transfer_id=transfer_id)

    assert len(frames) == len(case["frames"]), (
        f"produced {len(frames)} frames, libcsp produced {len(case['frames'])}")
    for i, (frame, (want_id, want_dlc, want_data)) in enumerate(zip(frames, case["frames"])):
        assert frame.can_id == want_id, (
            f"frame {i} id {frame.can_id:08X} != {want_id:08X}")
        assert len(frame.data) == want_dlc, f"frame {i} dlc {len(frame.data)} != {want_dlc}"
        assert frame.data.hex().upper() == want_data, f"frame {i} data mismatch"


def test_begin_and_more_types_are_what_the_layout_says():
    payload = bytes(20)
    frames = encode_packet(1, 2, 10, 20, payload, transfer_id=7)
    assert decode_cfp_id(frames[0].can_id)[2] == BEGIN
    for frame in frames[1:]:
        assert decode_cfp_id(frame.can_id)[2] == MORE


def test_remaining_count_reaches_zero_on_the_last_frame():
    """A receiver uses `remain` to know when the packet is complete; an off-by-one here strands
    every multi-frame transfer."""
    for length in (0, 1, 2, 3, 10, 11, 18, 64, 200):
        frames = encode_packet(1, 2, 10, 20, bytes(length), transfer_id=1)
        assert decode_cfp_id(frames[-1].can_id)[3] == 0, f"length {length} never reaches remain=0"


def test_rejects_addresses_that_do_not_fit_csp_v1():
    with pytest.raises(ValueError):
        encode_header(0, 32, 0, 0, 0)      # source is 5 bits
    with pytest.raises(ValueError):
        encode_header(0, 0, 0, 64, 0)      # port is 6 bits
