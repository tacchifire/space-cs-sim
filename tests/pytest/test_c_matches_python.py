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

from cuberange.identity import GROUND_STATIONS as _GS  # noqa: E402
from cuberange.proto import pus_auth, sdls  # noqa: E402
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


class CrPusAuthParts(ctypes.Structure):
    """`struct cr_pus_auth_parts`, field for field."""
    _fields_ = [("apid", ctypes.c_uint16), ("party_id", ctypes.c_uint16),
                ("direction", ctypes.c_uint8),
                ("seq", ctypes.c_uint32),
                ("mac", ctypes.c_void_p),
                ("aad", ctypes.c_void_p), ("aad_len", ctypes.c_size_t),
                ("inner_len", ctypes.c_size_t)]


class CrSdlsParts(ctypes.Structure):
    """`struct cr_sdls_parts`, field for field.

    Declared here rather than parsed out of the header, and the pointer fields are c_void_p with
    the offsets recovered by subtracting the frame's own address - which is what lets this compare
    WHERE the C code decided each field starts, rather than only what it copied.
    """
    _fields_ = [("spi", ctypes.c_uint16),
                ("iv", ctypes.c_void_p), ("iv_len", ctypes.c_size_t),
                ("seq_num", ctypes.c_uint32),
                ("sn", ctypes.c_void_p), ("sn_len", ctypes.c_size_t),
                ("payload", ctypes.c_void_p), ("payload_len", ctypes.c_size_t),
                ("mac", ctypes.c_void_p), ("mac_len", ctypes.c_size_t),
                ("aad", ctypes.c_void_p), ("aad_len", ctypes.c_size_t)]


@pytest.fixture(scope="module")
def clib(tmp_path_factory):
    so = tmp_path_factory.mktemp("clib") / "libcrproto.so"
    subprocess.run(
        ["cc", "-std=c11", "-O1", "-fPIC", "-shared",
         "-I", str(COMMON), str(COMMON / "cuberange_proto.c"),
         str(COMMON / "cuberange_sdls.c"), str(COMMON / "cuberange_pus_auth.c"),
         "-o", str(so)],
        check=True)
    lib = ctypes.CDLL(str(so))

    lib.cr_crc16.restype = ctypes.c_uint16
    lib.cr_crc16.argtypes = [ctypes.c_char_p, ctypes.c_size_t]

    for fn in ("cr_sdls_spi_at", "cr_sdls_iv_at"):
        getattr(lib, fn).restype = ctypes.c_size_t
        getattr(lib, fn).argtypes = []
    lib.cr_sdls_sn_at.restype = ctypes.c_size_t
    lib.cr_sdls_sn_at.argtypes = [ctypes.c_size_t]
    lib.cr_sdls_pdu_at.restype = ctypes.c_size_t
    lib.cr_sdls_pdu_at.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
    lib.cr_sdls_split.restype = ctypes.c_int
    lib.cr_sdls_split.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t,
                                  ctypes.c_size_t, ctypes.c_size_t,
                                  ctypes.POINTER(CrSdlsParts)]

    lib.cr_pus_auth_split.restype = ctypes.c_int
    lib.cr_pus_auth_split.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                      ctypes.POINTER(CrPusAuthParts)]
    lib.cr_pus_auth_nonce.restype = None
    lib.cr_pus_auth_nonce.argtypes = [ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32,
                                      ctypes.c_uint8, ctypes.c_char_p]
    lib.cr_pus_auth_direction.restype = ctypes.c_uint8
    lib.cr_pus_auth_direction.argtypes = [ctypes.c_char_p]
    lib.cr_pus_auth_prepare.restype = ctypes.c_size_t
    lib.cr_pus_auth_prepare.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint32,
                                        ctypes.POINTER(CrPusAuthParts)]
    lib.cr_pus_auth_strip.restype = None
    lib.cr_pus_auth_strip.argtypes = [ctypes.c_char_p, ctypes.c_size_t]

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

    headers = sorted(COMMON.glob("cuberange_*.h"))
    assert len(headers) >= 2, f"only {len(headers)} shared headers found; the glob has drifted"
    api = set()
    for h in headers:
        api |= set(re.findall(r"^\w[\w \*]*?\b(cr_\w+)\(", h.read_text(), re.M))
    assert len(api) >= 13, f"only {len(api)} functions found across {len(headers)} headers"
    mine = Path(__file__).read_text()
    missing = sorted(f for f in api if f not in mine)
    assert not missing, (
        "these are exported by the C codec and never compared against the Python one: "
        + ", ".join(missing))


# --------------------------------------------------------------------------------------------
# SDLS: the security header's layout, in two implementations
#
# The MAC is not compared here, and that is on purpose: the C side computes none. Crypto lives in
# whatever library the platform has - mbedTLS on the spacecraft, OpenSSL on the host - and mixing
# it into the shared codec would end this comparison. What CAN drift silently is WHERE each field
# starts, and that is what these check.

SDLS_LENGTHS = (sdls.IV_LEN, sdls.SN_LEN, sdls.MAC_LEN)


def _split(clib, frame: bytes, iv_len=None, sn_len=None, mac_len=None):
    """cr_sdls_split, with the pointer fields turned back into offsets."""
    iv_len = sdls.IV_LEN if iv_len is None else iv_len
    sn_len = sdls.SN_LEN if sn_len is None else sn_len
    mac_len = sdls.MAC_LEN if mac_len is None else mac_len
    buf = ctypes.create_string_buffer(frame, len(frame))
    parts = CrSdlsParts()
    rc = clib.cr_sdls_split(buf, len(frame), iv_len, sn_len, mac_len, ctypes.byref(parts))
    if rc != 0:
        return rc, None
    base = ctypes.cast(buf, ctypes.c_void_p).value
    return 0, {
        "spi": parts.spi,
        "iv_at": parts.iv - base, "iv_len": parts.iv_len,
        "seq_num": parts.seq_num,
        "sn_at": parts.sn - base, "sn_len": parts.sn_len,
        "pdu_at": parts.payload - base, "payload_len": parts.payload_len,
        "mac_at": parts.mac - base, "mac_len": parts.mac_len,
        "aad_at": parts.aad - base, "aad_len": parts.aad_len,
    }


def test_the_c_offset_helpers_agree_with_the_python_constants(clib):
    assert clib.cr_sdls_spi_at() == sdls.SPI_AT
    assert clib.cr_sdls_iv_at() == sdls.IV_AT
    assert clib.cr_sdls_sn_at(sdls.IV_LEN) == sdls.SN_AT
    assert clib.cr_sdls_pdu_at(sdls.IV_LEN, sdls.SN_LEN) == sdls.PDU_AT


@pytest.mark.parametrize("payload", [b"", b"\x01", bytes.fromhex("08a9c00000000a"),
                                     bytes(range(64)), b"\xFF" * 200])
def test_both_split_an_authenticated_frame_the_same_way(clib, payload):
    key, iv = bytes(range(32)), bytes(range(0xA0, 0xAC))
    frame = sdls.encode_tc(payload, key=key, spi=9, iv=iv, seq_num=0x01020304,
                           frame_seq=7, scid=0x0A9, vcid=0)
    rc, got = _split(clib, frame)
    assert rc == 0, f"C refused a frame Python produced: rc={rc}"
    py = sdls.decode_tc(frame, key=key)

    assert got["spi"] == py.spi
    assert got["iv_at"] == sdls.IV_AT and got["iv_len"] == sdls.IV_LEN
    assert frame[got["iv_at"]:got["iv_at"] + got["iv_len"]] == py.iv
    assert got["seq_num"] == py.seq_num
    assert got["pdu_at"] == sdls.PDU_AT
    assert frame[got["pdu_at"]:got["pdu_at"] + got["payload_len"]] == py.payload
    assert got["mac_len"] == sdls.MAC_LEN
    assert got["mac_at"] == len(frame) - 2 - sdls.MAC_LEN
    #: The authenticated portion has to be the SAME octets on both sides, or the two compute MACs
    #: over different things and the spacecraft rejects what the ground signed.
    assert got["aad_at"] == 0
    assert got["aad_len"] == got["mac_at"]


@pytest.mark.parametrize("payload", [b"", bytes(range(32))])
def test_both_refuse_the_same_hostile_frames(clib, payload):
    """What they REFUSE, which is the half that was missing for the plain codec until 2026-09-12."""
    key, iv = bytes(range(32)), bytes(range(0xA0, 0xAC))
    good = sdls.encode_tc(payload, key=key, spi=9, iv=iv, seq_num=1, frame_seq=0)

    for i, bad in enumerate(_hostile(good)):
        label = f"case {i} ({len(bad)} octets)"
        rc, _ = _split(clib, bad)
        try:
            sdls.decode_tc(bad, key=key)
            py_ok = True
        except sdls.AuthenticationError:
            py_ok = False
        #: C does no crypto, so it can ACCEPT a frame whose MAC is wrong where Python refuses.
        #: The other direction must never happen: if C refuses the shape, Python must too, or the
        #: spacecraft is dropping frames the ground believes are fine.
        if rc != 0:
            assert not py_ok, (
                f"{label}: C refused it (rc={rc}) and Python accepted it - the spacecraft would "
                f"drop a frame the ground station thinks is valid")


def test_the_c_side_refuses_lengths_it_does_not_support(clib):
    """A sequence number wider than the field that holds it is refused, not truncated.

    seq_num is a uint32. Accepting sn_len 8 would produce an anti-replay counter that wraps four
    octets earlier than the sender's, and the symptom would be a spacecraft that rejects valid
    commands after a while.
    """
    key, iv = bytes(range(32)), bytes(range(0xA0, 0xAC))
    frame = sdls.encode_tc(b"\x01", key=key, spi=9, iv=iv, seq_num=1, frame_seq=0)
    for iv_len, sn_len, mac_len in ((0, 4, 16), (17, 4, 16), (12, 8, 16), (12, 4, 0), (12, 4, 17)):
        rc, _ = _split(clib, frame, iv_len, sn_len, mac_len)
        assert rc == -4, f"iv_len={iv_len} sn_len={sn_len} mac_len={mac_len} gave rc={rc}, not -4"


# --------------------------------------------------------------------------------------------
# PUS-layer authentication: the trailer's layout, in two implementations
#
# Same split as the SDLS one and for the same reason - the C side computes no MAC, because crypto
# lives in whatever library the platform has. What can drift silently is where the sequence number
# and the MAC start, and what the authenticated region covers; if those disagree the ground signs
# one thing and the spacecraft checks another, and every telecommand is refused with no way to see
# which end is wrong.

def _auth_packet(payload=b"\x00\x01\x00", source_id=None, seq=7, apid=0x0A9):
    from cuberange.identity import GROUND_STATIONS
    from cuberange.proto.pus import PusTc
    from cuberange.proto.spacepacket import PacketType, SpacePacket

    if source_id is None:
        source_id = GROUND_STATIONS["primary"]
    inner = SpacePacket(apid=apid, ptype=PacketType.TC, sec_hdr=True, seq_count=0,
                        data=PusTc(service=8, subtype=1, source_id=source_id,
                                   app_data=payload).encode()).encode()
    return pus_auth.sign(inner, key=bytes(range(32)), seq=seq), inner


def _auth_split(clib, packet: bytes):
    buf = ctypes.create_string_buffer(packet, len(packet))
    parts = CrPusAuthParts()
    rc = clib.cr_pus_auth_split(buf, len(packet), ctypes.byref(parts))
    if rc != 0:
        return rc, None
    base = ctypes.cast(buf, ctypes.c_void_p).value
    return 0, {"apid": parts.apid, "source_id": parts.party_id,
               "direction": parts.direction, "seq": parts.seq,
               "mac_at": parts.mac - base, "aad_at": parts.aad - base,
               "aad_len": parts.aad_len, "inner_len": parts.inner_len}


#: Station ids come from cuberange.identity - test_identity.py forbids writing them by hand, and
#: caught this file doing it. The extremes (0, 0xFFFF) are not stations and are literals on purpose.
@pytest.mark.parametrize("payload,source_id,seq,apid", [
    (b"", _GS["primary"], 0, 0x0A9),
    (b"\x00\x01\x00", _GS["backup"], 7, 0x0A9),
    (bytes(range(64)), 0xFFFF, 0xFFFFFFFF, 0x7FF),
    (b"\xAA" * 200, 0, 1, 0x000),
])
def test_both_split_an_authenticated_packet_the_same_way(clib, payload, source_id, seq, apid):
    signed, inner = _auth_packet(payload, source_id, seq, apid)
    rc, got = _auth_split(clib, signed)
    assert rc == 0, f"C refused a packet Python produced: rc={rc}"
    py = pus_auth.verify(signed, key=bytes(range(32)))

    assert got["apid"] == apid
    assert got["source_id"] == py.source_id == source_id
    assert got["seq"] == py.seq == seq
    assert got["mac_at"] == len(signed) - pus_auth.MAC_LEN
    assert got["aad_at"] == 0
    assert got["aad_len"] == got["mac_at"], "the authenticated region must end where the MAC begins"
    #: inner_len is what the packet becomes once the trailer is gone - the thing the OBC parses.
    assert got["inner_len"] == len(inner), (
        f"C says the inner packet is {got['inner_len']} octets, Python produced {len(inner)}")


@pytest.mark.parametrize("apid,source_id,seq", [
    (0x0A9, _GS["primary"], 0), (0x7FF, 0xFFFF, 0xFFFFFFFF), (0x000, 0x0001, 0x01020304),
])
def test_both_derive_the_same_nonce(clib, apid, source_id, seq):
    """A nonce that differs by one octet is a MAC that never verifies and a message nobody can debug."""
    for direction in (pus_auth.DIRECTION_TC, pus_auth.DIRECTION_TM):
        buf = ctypes.create_string_buffer(pus_auth.NONCE_LEN)
        clib.cr_pus_auth_nonce(apid, source_id, seq, direction, buf)
        assert buf.raw[:pus_auth.NONCE_LEN] == pus_auth.nonce(apid, source_id, seq, direction)
    #: And the two directions must not collide, which is the whole reason the octet is there.
    assert (pus_auth.nonce(apid, source_id, seq, pus_auth.DIRECTION_TC)
            != pus_auth.nonce(apid, source_id, seq, pus_auth.DIRECTION_TM))


def test_both_put_the_length_field_back_the_same_way(clib):
    """After the trailer is removed, the packet must be exactly what was signed."""
    signed, inner = _auth_packet()
    buf = ctypes.create_string_buffer(signed, len(signed))
    clib.cr_pus_auth_strip(buf, len(signed))
    stripped = buf.raw[:len(signed) - pus_auth.TRAILER_LEN]
    assert stripped == inner, (
        f"C's stripped packet differs from the one that was signed\n"
        f"  C:      {stripped.hex()}\n  Python: {inner.hex()}")
    assert stripped == pus_auth.verify(signed, key=bytes(range(32))).packet


def test_both_refuse_the_same_malformed_packets(clib):
    signed, _ = _auth_packet()
    key = bytes(range(32))
    for i, bad in enumerate(_hostile(signed)):
        rc, _ = _auth_split(clib, bad)
        try:
            pus_auth.verify(bad, key=key)
            py_ok = True
        except pus_auth.AuthenticationError:
            py_ok = False
        #: C does no crypto, so it may accept a packet whose MAC is wrong. The other direction is
        #: the one that must never happen: C refusing a shape Python accepts means the spacecraft
        #: drops telecommands the ground believes are valid.
        if rc != 0:
            assert not py_ok, f"case {i}: C refused it (rc={rc}) and Python accepted it"


def _tm_packet(dest_id=None, seq=5, apid=0x0A9):
    """A PUS 1,2 acceptance-failure report, which is what the spacecraft signs."""
    from cuberange.gs.station import PUS_TM_TIME_LEN
    from cuberange.identity import GROUND_STATIONS
    from cuberange.proto.pus import PusTm
    from cuberange.proto.spacepacket import PacketType, SpacePacket

    if dest_id is None:
        dest_id = GROUND_STATIONS["primary"]
    return SpacePacket(apid=apid, ptype=PacketType.TM, sec_hdr=True, seq_count=0,
                       data=PusTm(service=1, subtype=2, dest_id=dest_id,
                                  time=bytes(PUS_TM_TIME_LEN),
                                  app_data=bytes(5)).encode()).encode()


@pytest.mark.parametrize("build", ["tc", "tm"])
def test_both_read_the_counterparty_from_the_right_place(clib, build):
    """A TC's source id and a TM's destination id are at different offsets, and the nonce needs
    whichever the packet's type bit says. Getting it wrong produces a MAC that never verifies and
    a message nobody can debug."""
    packet = _auth_packet()[0] if build == "tc" else pus_auth.sign(
        _tm_packet(), key=bytes(range(32)), seq=5)
    rc, got = _auth_split(clib, packet)
    assert rc == 0
    assert clib.cr_pus_auth_direction(packet) == pus_auth.direction_of(packet)
    assert got["direction"] == pus_auth.direction_of(packet)
    assert got["source_id"] == pus_auth.party_of(packet)
    assert pus_auth.verify(packet, key=bytes(range(32))).source_id == got["source_id"]


def test_the_c_signer_and_the_python_signer_produce_the_same_packet(clib):
    """cr_pus_auth_prepare fills in everything but the MAC; the MAC is the caller's.

    So: let C prepare the packet, compute the tag over the region C says to, write it where C says
    to, and require the result to equal what pus_auth.sign produced. That compares the length-field
    rewrite, the sequence placement, the nonce and the authenticated region in one assertion - and
    those are exactly the four things that make a spacecraft and a ground station disagree about a
    report that is actually fine.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = bytes(range(32))
    for inner in (_tm_packet(), _auth_packet()[1]):
        buf = ctypes.create_string_buffer(inner + bytes(pus_auth.TRAILER_LEN),
                                          len(inner) + pus_auth.TRAILER_LEN)
        parts = CrPusAuthParts()
        total = clib.cr_pus_auth_prepare(buf, len(inner), 5, ctypes.byref(parts))
        assert total == len(inner) + pus_auth.TRAILER_LEN

        aad = buf.raw[:parts.aad_len]
        iv = pus_auth.nonce(parts.apid, parts.party_id, parts.seq, parts.direction)
        tag = AESGCM(key).encrypt(iv, b"", aad)
        built = aad + tag

        assert built == pus_auth.sign(inner, key=key, seq=5), (
            f"C-prepared and Python-signed differ\n  C:      {built.hex()}\n"
            f"  Python: {pus_auth.sign(inner, key=key, seq=5).hex()}")
