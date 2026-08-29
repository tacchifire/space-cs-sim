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
from cuberange.proto.frame import Deframer, encode_tc_frame, encode_tm_frame, wrap

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
