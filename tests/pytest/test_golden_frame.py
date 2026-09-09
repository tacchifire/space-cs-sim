"""The transfer-frame layer, checked against committed vectors rather than a live library.

Two problems this file exists for.

FIRST, the frame layer had no independent oracle at all. `test_frame.py` encodes with our encoder,
decodes with our decoder and checks the CRC with our CRC - which design section 9.3 opens by saying
is not evidence, because two implementations that share a misreading agree perfectly.

SECOND, the vectors that DO come from outside were consumed by nothing. `tests/golden/crc.json`
carries FECF values four independent implementations agree on, including NASA CryptoLib, and no
test read the file: `test_crc.py` calls `pytest.importorskip("crcmod")` and recomputes. That makes
the conformance layer conditional on a pip install, which is how it silently vanished from a run
before (design section 16, W26). Committed vectors cannot vanish.

WHAT THIS DOES AND DOES NOT ESTABLISH, stated because the distinction is the point:

  - The FECF is independently verified. Four implementations and the CCSDS 132.0-B-3 text agree on
    the parameters, one vector reproduces a value Yamcs publishes, and the residue property is a
    statement from the standard rather than from us.
  - The HEADER FIELD PACKING is not. No second implementation of CubeRange's TC and TM primary
    headers exists here, so their layout still rests on this project's reading of 232.0-B-4 and
    132.0-B-3. ASSURANCE.md says so, and this file does not pretend otherwise.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.proto.crc import crc16_ccsds                          # noqa: E402
from cuberange.proto.frame import (FECF_LEN, SCID, TC_HEADER_LEN,    # noqa: E402
                                   TM_HEADER_LEN, decode_tc_frame, decode_tm_frame,
                                   encode_tc_frame, encode_tm_frame, tc_frame_identity,
                                   tm_frame_identity)

GOLDEN = REPO / "tests" / "golden" / "crc.json"


@pytest.fixture(scope="module")
def fecf() -> dict:
    if not GOLDEN.is_file():
        pytest.fail(
            f"{GOLDEN} is missing. Rebuild the oracles with tools/oracles/build.sh and regenerate "
            f"with tools/gen_golden.py. Skipping here would remove the only outside opinion this "
            f"layer has.")
    return json.loads(GOLDEN.read_text())["ccsds_fecf_crc16"]


def test_the_vectors_name_four_independent_oracles(fecf):
    """If the file stopped being independent, everything below is checking us against us."""
    oracles = fecf["oracles"]
    assert len(oracles) >= 4, f"only {len(oracles)} FECF oracles: {oracles}"
    assert any("CryptoLib" in o for o in oracles), (
        "NASA CryptoLib is the one oracle here that is a spacecraft implementation rather than a "
        "CRC library; losing it would leave only general-purpose catalogues")
    assert fecf["catalogue_name"].startswith("CRC-16/IBM-3740")


def test_our_fecf_matches_every_committed_vector(fecf):
    """The parameters, checked against agreement rather than against a name.

    "CRC-16-CCITT" names at least ten different CRCs, and two implementations that both pick the
    wrong one round-trip perfectly while being wrong on the wire. That is why the design refuses
    the label and pins the parameters.
    """
    for v in fecf["vectors"]:
        data = bytes.fromhex(v["in"])
        assert f"0x{crc16_ccsds(data):04X}" == v["crc"], (
            f"FECF over {v['in']}: ours 0x{crc16_ccsds(data):04X}, four oracles say {v['crc']}")


def test_the_residue_property_holds_for_every_vector(fecf):
    """CCSDS 132.0-B-3: the CRC over a frame INCLUDING its FECF is zero.

    A receiver checks a frame this way rather than by recomputing and comparing, so this property
    is what `decode_*_frame` actually relies on.
    """
    assert fecf["residue_property"].endswith("0x0000")
    for v in fecf["vectors"]:
        data = bytes.fromhex(v["in"])
        with_fecf = data + crc16_ccsds(data).to_bytes(2, "big")
        assert crc16_ccsds(with_fecf) == 0x0000, f"residue is not zero for {v['in']}"
        assert f"0x{crc16_ccsds(with_fecf):04X}" == v["residue"]


def test_the_yamcs_anchored_vector_is_present(fecf):
    """One vector reproduces a value Yamcs publishes for a real TM frame.

    It is the only thing in this file connecting the CRC to a working ground system rather than to
    a catalogue, and the design's section 6.2 records it. Losing it would not fail anything else.
    """
    known = {v["in"]: v["crc"] for v in fecf["vectors"]}
    assert known.get("06000CF0000400558873C900000521") == "0x75FB", (
        "the Yamcs-anchored FECF vector is gone from crc.json")


def test_a_frame_this_project_builds_satisfies_the_standard_s_residue():
    """Ours, end to end: encode, then check the property the standard states about the result."""
    for payload in (b"", b"\x01", bytes(range(32)), b"\xFF" * 100):
        for frame in (encode_tc_frame(payload, seq=7),
                      encode_tm_frame(payload, mc_count=1, vc_count=2)):
            assert crc16_ccsds(frame) == 0x0000, (
                "a frame this project built does not satisfy the residue property, so a real "
                "receiver would reject it")


def test_the_fecf_covers_the_header_and_the_data_and_not_the_asm():
    """Coverage is a decision, and getting it wrong produces frames that verify on both ends.

    Two implementations that both include or both exclude the same bytes agree perfectly and are
    both wrong on the wire. The committed coverage note is the outside statement; this asserts our
    encoder matches it.
    """
    payload = b"\xAA\xBB\xCC"
    frame = encode_tc_frame(payload, seq=3)
    body, fecf = frame[:-FECF_LEN], frame[-FECF_LEN:]
    assert len(body) == TC_HEADER_LEN + len(payload)
    assert int.from_bytes(fecf, "big") == crc16_ccsds(body), (
        "the FECF does not cover exactly the primary header plus the data field")

    tm = encode_tm_frame(payload, mc_count=0, vc_count=0)
    assert int.from_bytes(tm[-FECF_LEN:], "big") == crc16_ccsds(tm[:-FECF_LEN])
    assert len(tm) == TM_HEADER_LEN + len(payload) + FECF_LEN


def test_a_corrupted_frame_is_refused_at_every_octet():
    """The residue check has to catch a flip anywhere, including in the FECF itself."""
    frame = bytearray(encode_tc_frame(b"\x10\x20\x30", seq=5))
    for i in range(len(frame)):
        bad = bytearray(frame)
        bad[i] ^= 0x01
        with pytest.raises(ValueError):
            decode_tc_frame(bytes(bad))


def test_the_spacecraft_id_survives_the_round_trip():
    """Identity is in the header the FECF covers, so a change to one must not silently pass."""
    for scid in (0x000, SCID, 0x0AA, 0x3FF):
        tc = encode_tc_frame(b"\x01", seq=1, scid=scid)
        assert tc_frame_identity(tc)[0] == scid
        decode_tc_frame(tc, expect_scid=scid)
        tm = encode_tm_frame(b"\x01", mc_count=0, vc_count=0, scid=scid)
        assert tm_frame_identity(tm)[0] == scid
        decode_tm_frame(tm, expect_scid=scid)
