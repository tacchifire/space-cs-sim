"""The C and Python codecs must produce identical bytes, and refuse identical bytes.

This is the guard the design review asked for: two self-written codecs that share a mistake
round-trip perfectly against themselves. Building the C for the host and diffing its output
against the Python is what makes a divergence loud.

It covered the ENCODERS only. CONTRIBUTING.md said the two were "diffed byte-for-byte", and the
decoders - the functions that parse attacker-controlled bytes, which is the whole reason the
sentence beside it says they are not intended to be vulnerable - were not compared at all, nor
was the VCID accessor added for EX-G03.

Agreement on well-formed input is the easy half. What matters between two parsers is whether they
REFUSE the same things: a C decoder that accepts a frame the Python one rejects means a
spacecraft acting on something the ground station would have discarded, and neither side reports
anything.
"""
import ctypes
import subprocess
from pathlib import Path

import pytest

from cuberange.proto.crc import crc16_ccsds
from cuberange.proto.frame import (Deframer, decode_tc_frame, decode_tm_frame,
                                   encode_tc_frame, encode_tm_frame,
                                   tc_frame_identity, wrap)

REPO = Path(__file__).resolve().parents[2]
COMMON = REPO / "firmware" / "common"

CR_MAX_FRAME_LEN = 1024


class CrDeframer(ctypes.Structure):
    _fields_ = [("buf", ctypes.c_uint8 * (CR_MAX_FRAME_LEN + 8)),
                ("used", ctypes.c_size_t)]


FRAME_CB = ctypes.CFUNCTYPE(None, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                            ctypes.c_void_p)


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
    lib.cr_encode_tc_frame.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                       ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint8]

    lib.cr_encode_tm_frame.restype = ctypes.c_int
    lib.cr_encode_tm_frame.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                       ctypes.c_char_p, ctypes.c_size_t,
                                       ctypes.c_uint8, ctypes.c_uint8]

    lib.cr_decode_tc_frame.restype = ctypes.c_int
    lib.cr_decode_tc_frame.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_uint8),
                                       ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
                                       ctypes.POINTER(ctypes.c_size_t)]

    lib.cr_decode_tm_frame.restype = ctypes.c_int
    lib.cr_decode_tm_frame.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_uint8),
                                       ctypes.POINTER(ctypes.c_uint8),
                                       ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
                                       ctypes.POINTER(ctypes.c_size_t)]

    lib.cr_tc_frame_vcid.restype = ctypes.c_uint8
    lib.cr_tc_frame_vcid.argtypes = [ctypes.c_char_p, ctypes.c_size_t]

    lib.cr_wrap.restype = ctypes.c_size_t
    lib.cr_wrap.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]

    lib.cr_deframer_init.restype = None
    lib.cr_deframer_init.argtypes = [ctypes.POINTER(CrDeframer)]

    lib.cr_deframer_feed.restype = ctypes.c_int
    lib.cr_deframer_feed.argtypes = [ctypes.POINTER(CrDeframer), ctypes.c_char_p,
                                     ctypes.c_size_t, FRAME_CB, ctypes.c_void_p]
    return lib


@pytest.mark.parametrize("n", [0, 1, 2, 7, 8, 63, 255, 512])
def test_crc_agrees(clib, n):
    payload = bytes((i * 11 + 5) & 0xFF for i in range(n))
    assert clib.cr_crc16(payload, len(payload)) == crc16_ccsds(payload)


@pytest.mark.parametrize("n,seq", [(1, 0), (4, 7), (100, 255), (500, 128)])
def test_tc_frame_agrees(clib, n, seq):
    payload = bytes((i * 3) & 0xFF for i in range(n))
    out = ctypes.create_string_buffer(2048)
    written = clib.cr_encode_tc_frame(out, len(out), payload, len(payload), seq)
    assert written > 0, "the C encoder refused the input"
    assert out.raw[:written] == encode_tc_frame(payload, seq)


@pytest.mark.parametrize("n,mc,vc", [(1, 0, 0), (30, 3, 4), (200, 255, 255)])
def test_tm_frame_agrees(clib, n, mc, vc):
    payload = bytes((i * 5) & 0xFF for i in range(n))
    out = ctypes.create_string_buffer(2048)
    written = clib.cr_encode_tm_frame(out, len(out), payload, len(payload), mc, vc)
    assert written > 0, "the C encoder refused the input"
    assert out.raw[:written] == encode_tm_frame(payload, mc, vc)


def test_wrap_agrees(clib):
    frame = encode_tc_frame(b"\x01\x02\x03", 9)
    out = ctypes.create_string_buffer(2048)
    written = clib.cr_wrap(out, len(out), frame, len(frame))
    assert out.raw[:written] == wrap(frame)


@pytest.mark.parametrize("chunk_size", [1, 3, 7, 64, 4096])
def test_deframers_agree_on_the_same_stream(clib, chunk_size):
    """Both deframers must emit the same frames in the same order from the same byte stream,
    including after leading garbage. This is the half most likely to diverge, because the two are
    structurally different: Python re-scans a bytearray, C keeps a fixed ring."""
    frames = [encode_tc_frame(bytes([i]) * (i + 1), seq=i) for i in range(4)]
    stream = b"junk-before-the-first-marker" + b"".join(wrap(f) for f in frames)

    py = Deframer()
    py_out = []
    for i in range(0, len(stream), chunk_size):
        py_out.extend(py.feed(stream[i:i + chunk_size]))

    c_out = []

    def collect(ptr, length, _ctx):
        c_out.append(bytes(ptr[i] for i in range(length)))

    cb = FRAME_CB(collect)
    d = CrDeframer()
    clib.cr_deframer_init(ctypes.byref(d))
    for i in range(0, len(stream), chunk_size):
        piece = stream[i:i + chunk_size]
        clib.cr_deframer_feed(ctypes.byref(d), piece, len(piece), cb, None)

    assert py_out == frames, "the Python deframer lost or mangled a frame"
    assert c_out == py_out, "the C and Python deframers disagree"


# --------------------------------------------------------------------------- the decoders
#
# Hostile input, because agreement on a well-formed frame is the easy half. A C decoder that
# accepts what the Python one rejects means the spacecraft acts on something the ground station
# would have thrown away, and nothing on either side says so.

def _c_decode_tc(clib, frame: bytes):
    seq = ctypes.c_uint8()
    payload = ctypes.POINTER(ctypes.c_uint8)()
    payload_len = ctypes.c_size_t()
    rc = clib.cr_decode_tc_frame(frame, len(frame), ctypes.byref(seq),
                                 ctypes.byref(payload), ctypes.byref(payload_len))
    if rc != 0:
        return None
    return seq.value, bytes(payload[i] for i in range(payload_len.value))


def _c_decode_tm(clib, frame: bytes):
    mc, vc = ctypes.c_uint8(), ctypes.c_uint8()
    payload = ctypes.POINTER(ctypes.c_uint8)()
    payload_len = ctypes.c_size_t()
    rc = clib.cr_decode_tm_frame(frame, len(frame), ctypes.byref(mc), ctypes.byref(vc),
                                 ctypes.byref(payload), ctypes.byref(payload_len))
    if rc != 0:
        return None
    return mc.value, vc.value, bytes(payload[i] for i in range(payload_len.value))


def _py_decode_tc(frame: bytes):
    try:
        return decode_tc_frame(frame)
    except ValueError:
        return None


def _py_decode_tm(frame: bytes):
    try:
        return decode_tm_frame(frame)
    except ValueError:
        return None


def _hostile(frame: bytes) -> list:
    """One valid frame, and the ways a link or an attacker breaks it."""
    out = [frame, b"", frame[:3], frame[:-1], frame + b"\x00"]
    for i in (0, 2, 3, len(frame) - 1):                 # header, length field, FECF
        bad = bytearray(frame)
        bad[i] ^= 0x01
        out.append(bytes(bad))
    bad = bytearray(frame)                              # a length field that lies
    bad[2], bad[3] = 0x03, 0xFF
    out.append(bytes(bad))
    return out


@pytest.mark.parametrize("n,seq", [(0, 0), (1, 7), (32, 255), (100, 128)])
def test_tc_decode_agrees_including_what_it_refuses(clib, n, seq):
    frame = encode_tc_frame(bytes(range(n % 256)) * (n // 256 + 1) if n else b"", seq=seq)
    for candidate in _hostile(frame):
        c, py = _c_decode_tc(clib, candidate), _py_decode_tc(candidate)
        assert (c is None) == (py is None), (
            f"one decoder accepted what the other refused: C={c!r} python={py!r} for "
            f"{candidate.hex().upper()}")
        if c is not None:
            assert c == py, f"decoders disagree on {candidate.hex().upper()}: {c!r} vs {py!r}"


@pytest.mark.parametrize("n,mc,vc", [(0, 0, 0), (1, 1, 2), (32, 255, 255), (100, 7, 3)])
def test_tm_decode_agrees_including_what_it_refuses(clib, n, mc, vc):
    frame = encode_tm_frame(bytes(n), mc_count=mc, vc_count=vc)
    for candidate in _hostile(frame):
        c, py = _c_decode_tm(clib, candidate), _py_decode_tm(candidate)
        assert (c is None) == (py is None), (
            f"one decoder accepted what the other refused for {candidate.hex().upper()}")
        if c is not None:
            assert c == py, f"decoders disagree on {candidate.hex().upper()}: {c!r} vs {py!r}"


@pytest.mark.parametrize("vcid", [0, 1, 2, 31, 32, 63])
def test_the_vcid_accessor_agrees(clib, vcid):
    """Added for EX-G03 and not cross-checked until now. Reading two bits too many puts a ground
    station on somebody else's virtual channel, and the length field shares that octet."""
    frame = encode_tc_frame(b"\x01\x02", seq=3, vcid=vcid)
    assert clib.cr_tc_frame_vcid(frame, len(frame)) == tc_frame_identity(frame)[1] == vcid


def test_a_frame_too_short_for_the_vcid_field_reports_the_sentinel(clib):
    assert clib.cr_tc_frame_vcid(b"\x00\x00", 2) == 0xFF


def test_every_exported_c_function_is_cross_checked():
    """The gap this file was found to have, turned into something that cannot reopen.

    cr_decode_tc_frame, cr_decode_tm_frame and cr_tc_frame_vcid were in the header and not in this
    file, while CONTRIBUTING.md said the two codecs were diffed byte-for-byte.
    """
    import re

    header = (COMMON / "cuberange_proto.h").read_text()
    api = set(re.findall(r"^\w[\w \*]*?\b(cr_\w+)\(", header, re.M))
    assert len(api) >= 8, f"only {len(api)} functions found in the header; the regex has drifted"
    mine = Path(__file__).read_text()
    missing = sorted(f for f in api if f not in mine)
    assert not missing, (
        "these are exported by the C codec and never compared against the Python one: "
        + ", ".join(missing))
