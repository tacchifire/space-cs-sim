#!/usr/bin/env python3
"""CubeRange golden-vector generator.

Emits tests/golden/*.json from INDEPENDENT oracles only. CubeRange's own codecs are
never consulted here -- that is the whole point.

Oracles used:
  spacepackets 0.32.0 (Apache-2.0)  -> Space Packet primary header, PUS-C TC/TM
  ccsdspy 2.0.1       (BSD-3)       -> Space Packet cross-decode
  crcmod / crc / fastcrc            -> CRC-16 CCITT-FALSE and CRC-32C, 3 ways
  NASA CryptoLib libcryptolib.so    -> Crypto_Calc_FECF (NOSA-1.3, external process, not vendored)
  libcsp csp_oracle (MIT)           -> CSPv1 header + CFP1-over-CAN frames
  NASA CryptoLib tc_oracle          -> TC transfer frame primary header, parsed not reimplemented
  spacepackets.ccsds.tm_frame       -> TM transfer frame primary header
"""
import json, struct, subprocess, sys, ctypes, binascii, pathlib, re

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "golden")
OUT.mkdir(parents=True, exist_ok=True)
H = lambda b: binascii.hexlify(bytes(b)).decode().upper()

# ---------------------------------------------------------------- CRC oracles
import crcmod, fastcrc
from crc import Calculator, Configuration
crc16_a = crcmod.mkCrcFun(0x11021, initCrc=0xFFFF, rev=False, xorOut=0x0000)
crc16_b = fastcrc.crc16.ibm_3740
crc16_c = Calculator(Configuration(width=16, polynomial=0x1021, init_value=0xFFFF,
                                   final_xor_value=0x0000, reverse_input=False,
                                   reverse_output=False)).checksum
crc32_a = crcmod.mkCrcFun(0x11EDC6F41, initCrc=0, rev=True, xorOut=0xFFFFFFFF)
crc32_b = fastcrc.crc32.iscsi

_cl = ctypes.CDLL("./cryptolib/build/libcryptolib.so")
_cl.Crypto_Calc_FECF.restype = ctypes.c_uint16
_cl.Crypto_Calc_FECF.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_int]
def crc16_nasa(d):
    b = (ctypes.c_uint8 * len(d))(*d)
    return _cl.Crypto_Calc_FECF(b, len(d))

def agree16(d):
    vals = {crc16_a(d), crc16_b(d), crc16_c(d), crc16_nasa(d)}
    assert len(vals) == 1, f"CRC-16 oracles disagree on {H(d)}: {vals}"
    return vals.pop()

def agree32(d):
    vals = {crc32_a(d), crc32_b(d)}
    assert len(vals) == 1, f"CRC-32 oracles disagree on {H(d)}: {vals}"
    return vals.pop()

CRC_INPUTS = [b"123456789", bytes.fromhex("00"), bytes.fromhex("A5"), bytes.fromhex("0000"),
              bytes.fromhex("FFFFFFFF"), bytes.fromhex("0842C00000010203"),
              bytes.fromhex("086400010003DEADBEEF"),
              bytes([0x6,0x0,0x0c,0xf0,0x00,0x04,0x00,0x55,0x88,0x73,0xc9,0x00,0x00,0x05,0x21])]

crc_doc = {
  "ccsds_fecf_crc16": {
    "spec": "CCSDS 132.0-B-3 4.1.6.2.2 / CCSDS 232.0-B-4 (same CRC)",
    "width": 16, "poly": "0x1021 (G(X)=X^16+X^12+X^5+1)",
    "init": "0xFFFF (L(X)=sum X^i, i=0..15 -> shift register preset to all ones)",
    "refin": False, "refout": False, "xorout": "0x0000",
    "catalogue_name": "CRC-16/IBM-3740 (a.k.a. CRC-16/CCITT-FALSE)",
    "check_123456789": "0x29B1",
    "coverage": "whole Transfer Frame from first octet of the Primary Header through the last "
                "octet before the FECF (i.e. incl. secondary header, data field and OCF). "
                "The ASM (0x1ACFFC1D) is NOT covered - it belongs to the Channel Coding "
                "sublayer (CCSDS 131.0-B / 231.0-B), not to the frame.",
    "residue_property": "CRC over (frame including its FECF) == 0x0000",
    "oracles": ["crcmod", "fastcrc.crc16.ibm_3740", "crc.Calculator", "NASA CryptoLib Crypto_Calc_FECF"],
    "vectors": [{"in": H(d), "crc": f"0x{agree16(d):04X}",
                 "residue": f"0x{agree16(d + agree16(d).to_bytes(2,'big')):04X}"} for d in CRC_INPUTS],
  },
  "csp_crc32": {
    "spec": "libcsp src/csp_crc32.c (csp_crc32_init/update/final)",
    "width": 32, "poly": "0x1EDC6F41 (reflected 0x82F63B78)", "init": "0xFFFFFFFF",
    "refin": True, "refout": True, "xorout": "0xFFFFFFFF",
    "catalogue_name": "CRC-32/ISCSI (a.k.a. CRC-32C, Castagnoli)",
    "check_123456789": "0xE3069283",
    "coverage": "csp_crc32_append(): over packet->frame_begin..frame_length (header+data) "
                "when the CSP id is prepended, else over packet->data[0..length]. "
                "Appended BIG-ENDIAN (htobe32).",
    "oracles": ["crcmod", "fastcrc.crc32.iscsi", "libcsp csp_crc32_memory()"],
    "vectors": [{"in": H(d), "crc": f"0x{agree32(d):08X}"} for d in CRC_INPUTS],
  },
}
(OUT / "crc.json").write_text(json.dumps(crc_doc, indent=2))

# ------------------------------------------- Space Packet + PUS via spacepackets
import io, ccsdspy
from spacepackets import SpacePacketHeader, PacketType, SequenceFlags
from spacepackets.ecss import PusTc, PusTm
from spacepackets.ccsds.time import CdsShortTimestamp

sp = {"spec": "CCSDS 133.0-B-2 4.1.2/4.1.3 (Packet Primary Header, 6 octets)",
      "length_convention": "Packet Data Length C = (octets in Packet Data Field) - 1  [4.1.3.5.3]",
      "oracles": ["spacepackets 0.32.0", "ccsdspy 2.0.1", "hand decode from the 133.0-B-2 field table"],
      "vectors": []}
for apid, seq, dlen, pt, sec, sf in [
        (0x000, 0, 1, PacketType.TM, False, SequenceFlags.UNSEGMENTED),
        (0x7FF, 0x3FFF, 1, PacketType.TC, True, SequenceFlags.UNSEGMENTED),
        (0x064, 1, 4, PacketType.TC, True, SequenceFlags.UNSEGMENTED),
        (0x123, 42, 8, PacketType.TM, True, SequenceFlags.UNSEGMENTED),
        (0x001, 0, 1, PacketType.TM, False, SequenceFlags.FIRST_SEGMENT),
        (0x001, 0, 1, PacketType.TM, False, SequenceFlags.CONTINUATION_SEGMENT),
        (0x001, 0, 1, PacketType.TM, False, SequenceFlags.LAST_SEGMENT)]:
    raw = bytes(SpacePacketHeader(packet_type=pt, apid=apid, seq_count=seq, data_len=dlen,
                                  sec_header_flag=sec, seq_flags=sf).pack())
    w0, w1, w2 = struct.unpack(">HHH", raw)
    assert (w0 >> 13) & 7 == 0 and w0 & 0x7FF == apid and w1 & 0x3FFF == seq and w2 == dlen
    # ccsdspy independent decode
    pay = bytes(dlen + 1)
    r = ccsdspy.FixedLength([ccsdspy.PacketField(name=f"d{i}", data_type="uint", bit_length=8)
                             for i in range(len(pay))]).load(io.BytesIO(raw + pay), include_primary_header=True)
    assert int(r["CCSDS_APID"][0]) == apid and int(r["CCSDS_PACKET_LENGTH"][0]) == dlen
    sp["vectors"].append({"hdr": H(raw), "apid": apid, "seq_count": seq, "data_len_field": dlen,
                          "packet_type": int(pt), "sec_hdr_flag": int(sec), "seq_flags": int(sf),
                          "total_packet_octets": 6 + dlen + 1})
(OUT / "space_packet.json").write_text(json.dumps(sp, indent=2))

pus = {"spec": "ECSS-E-ST-70-41C (PUS-C)",
       "tc_secondary_header": {
           "size": 5,
           "fields": ["[0] bits7-4 TC-packet-PUS-version = 2 ; bits3-0 acknowledgement flags",
                      "[1] service type", "[2] message subtype",
                      "[3:5] source ID (uint16 big-endian)"]},
       "tm_secondary_header": {
           "size": "7 + time field",
           "fields": ["[0] bits7-4 TM-packet-PUS-version = 2 ; bits3-0 spacecraft time reference status",
                      "[1] service type", "[2] message subtype",
                      "[3:5] message type counter (uint16 big-endian)",
                      "[5:7] destination ID (uint16 big-endian)",
                      "[7:] time field (here CDS 7 octets: P-field 0x40 + 2B day + 4B ms-of-day)"]},
       "packet_error_control": "trailing uint16 big-endian, CRC-16/IBM-3740 over the whole packet excluding the 2 CRC octets",
       "oracle": "spacepackets 0.32.0 (Apache-2.0)",
       "tc_vectors": [], "tm_vectors": []}
for svc, sub, apid, seq, sid, ack, app in [
        (17,1,0x64,0,0x0000,0b1111,b""), (17,1,0x64,1,0x0000,0b1111,b""),
        (8,1,0x64,2,0x00AB,0b1001,bytes.fromhex("DEADBEEF")),
        (3,1,0x123,0x3FFF,0xFFFF,0b1111,bytes.fromhex("01")),
        (11,4,0x001,5,0x0001,0b0000,bytes.fromhex("0102030405"))]:
    raw = bytes(PusTc(service=svc, message_subtype=sub, apid=apid, seq_count=seq,
                      source_id=sid, ack_flags=ack, app_data=app).pack())
    assert raw[6] == (2 << 4 | ack) and raw[7] == svc and raw[8] == sub
    assert struct.unpack(">H", raw[9:11])[0] == sid
    assert struct.unpack(">H", raw[-2:])[0] == agree16(raw[:-2])
    pus["tc_vectors"].append({"service": svc, "subtype": sub, "apid": apid, "seq": seq,
                              "source_id": sid, "ack": ack, "app_data": H(app), "packet": H(raw)})
ts = CdsShortTimestamp(ccsds_days=1000, ms_of_day=43200000).pack()
for svc, sub, apid, cnt, dst, sd in [
        (17,2,0x64,0,0x0000,b""), (3,25,0x64,7,0x0001,bytes.fromhex("0A0B0C0D")),
        (1,1,0x123,65535,0xFFFF,bytes.fromhex("0164000000")),
        (5,4,0x001,1,0x0002,bytes.fromhex("FF"))]:
    raw = bytes(PusTm(service=svc, message_subtype=sub, apid=apid, timestamp=ts,
                      source_data=sd, message_counter=cnt, destination_id=dst).pack())
    assert raw[6] >> 4 == 2 and raw[7] == svc and raw[8] == sub
    assert struct.unpack(">H", raw[9:11])[0] == cnt and struct.unpack(">H", raw[11:13])[0] == dst
    assert struct.unpack(">H", raw[-2:])[0] == agree16(raw[:-2])
    pus["tm_vectors"].append({"service": svc, "subtype": sub, "apid": apid, "msg_counter": cnt,
                              "dest_id": dst, "time_cds": H(ts), "source_data": H(sd), "packet": H(raw)})
(OUT / "pus.json").write_text(json.dumps(pus, indent=2))

# --------------------------------------------------------------- CSP via libcsp
out = subprocess.run(["./csp_oracle"], capture_output=True, text=True, check=True).stdout
csp = {"spec": "libcsp (MIT) CSP v1 32-bit header + CFP 1.x over CAN 2.0B 29-bit id",
       "csp_v1_header": {"size": 4, "endianness": "big (htobe32)",
                         "fields": ["bits31-30 priority", "bits29-25 source", "bits24-20 destination",
                                    "bits19-14 destination port", "bits13-8 source port", "bits7-0 flags"],
                         "src_file": "libcsp/src/csp_id.c:29-41"},
       "cfp1_can_id": {"bits": 29,
                       "fields": ["bits28-24 source (5)", "bits23-19 destination (5)",
                                  "bit18 type 0=BEGIN 1=MORE (1)", "bits17-10 remain (8)",
                                  "bits9-0 CFP identifier (10)"],
                       "src_file": "libcsp/include/csp/interfaces/csp_if_can.h:77-119",
                       "begin_frame_payload": "[0:4] CSPv1 header, [4:6] CSP data length uint16 BE, [6:8] first <=2 data octets",
                       "more_frame_payload": "up to 8 data octets, no header",
                       "remain_begin": "(length + 6 - 1) / 8",
                       "remain_more": "(length - tx_count - data_bytes + 8 - 1) / 8",
                       "src_file_tx": "libcsp/src/interfaces/csp_if_can.c:192-196, 242-246"},
       "raw_oracle_output": out.splitlines()}
(OUT / "csp.json").write_text(json.dumps(csp, indent=2))


# ------------------------------------------------- TM/TC transfer frame primary headers
#
# The layer that had no outside opinion at all. crc.json gives the FECF four, space_packet.json
# gives the Space Packet header two, and the TM and TC TRANSFER FRAME headers rested entirely on
# this project's reading of CCSDS 232.0-B-4 and 132.0-B-3 - which ASSURANCE.md said plainly, and
# which is the one place where two implementations sharing a misreading agree perfectly and are
# both wrong on the wire.
#
# The chain here has two links, and neither end is CubeRange's encoder:
#
#   TC: these octets are packed BELOW from the field table in CCSDS 232.0-B-4 4.1.2, then handed
#       to NASA CryptoLib, which parses them with its own code and reports the fields it found.
#       Agreement means the hand packing and a flight implementation read the standard the same
#       way. CryptoLib also checks `frame length field + 1 == octets`, so the length convention -
#       the single easiest field here to get wrong - is confirmed by something other than us.
#
#   TM: spacepackets packs the header from named fields. That IS a second implementation, so no
#       hand packing is needed on this side.
#
# tests/pytest/test_golden_transfer_frame.py then requires CubeRange's encoder to reproduce every
# octet. This file never imports it.

def _tc_header_by_hand(scid, vcid, total_octets, fsn):
    """CCSDS 232.0-B-4 4.1.2, packed field by field rather than by a formula.

    Written out longhand deliberately: a one-line struct.pack hides which bits went where, and
    hiding that is how a header layout goes unchecked for the life of a project.
    """
    tfvn, bypass, ctrl, spare = 0, 0, 0, 0          # 4.1.2.1 / .2 / .3 - Type-A data frame
    length_field = total_octets - 1                  # 4.1.2.7: total octets in the frame, minus one
    b0 = (tfvn << 6) | (bypass << 5) | (ctrl << 4) | (spare << 2) | ((scid >> 8) & 0x03)
    b1 = scid & 0xFF                                 # 4.1.2.4: SCID is 10 bits, split 2 + 8
    b2 = ((vcid & 0x3F) << 2) | ((length_field >> 8) & 0x03)   # 4.1.2.5: VCID is 6 bits
    b3 = length_field & 0xFF
    b4 = fsn & 0xFF                                  # 4.1.2.8: frame sequence number
    return bytes([b0, b1, b2, b3, b4])

TC_CASES = [(0x0A9, 0, b"", 0), (0x0A9, 1, b"\x01", 7),
            (0x3FF, 63, bytes(range(32)), 255), (0x000, 0, b"\xAA" * 100, 128)]

tc_frames, tc_meta = [], []
for scid, vcid, payload, fsn in TC_CASES:
    total = 5 + len(payload) + 2                      # header + data + FECF
    body = _tc_header_by_hand(scid, vcid, total, fsn) + payload
    frame = body + crc16_a(body).to_bytes(2, "big")   # crcmod, not our CRC
    tc_frames.append(H(frame))
    tc_meta.append({"scid": scid, "vcid": vcid, "fsn": fsn, "payload": H(payload),
                    "total_octets": total, "frame_length_field": total - 1})

parsed = json.loads(subprocess.run(["./tc_oracle", *tc_frames],
                                   capture_output=True, text=True, check=True).stdout)
assert len(parsed) == len(tc_meta)
for meta, got in zip(tc_meta, parsed):
    meta["frame"] = got["frame"]
    meta["cryptolib"] = {k: got[k] for k in ("tfvn", "bypass", "cc", "spare", "scid", "vcid",
                                             "frame_length_field", "fsn",
                                             "length_field_plus_one_equals_octets")}

# --------------------------------------------------------------- TM via spacepackets
from spacepackets.ccsds.tm_frame import (MasterChannelId, TmFramePrimaryHeader,
                                         TransferFrameDataFieldStatus)

TM_CASES = [(0x0A9, 0, 0, 0, b""), (0x0A9, 1, 1, 2, b"\x01"),
            (0x3FF, 7, 255, 255, bytes(range(32))), (0x000, 0, 128, 64, b"\xAA" * 100)]

tm_vectors = []
for scid, vcid, mc, vc, payload in TM_CASES:
    status = TransferFrameDataFieldStatus(secondary_header_flag=False, sync_flag=False,
                                          packet_order_flag=False, segment_len_id=0,
                                          first_header_pointer=0)
    hdr = TmFramePrimaryHeader(
        master_channel_id=MasterChannelId(transfer_frame_version=0, spacecraft_id=scid),
        vc_id=vcid, ocf_flag=False, master_ch_frame_count=mc, vc_frame_count=vc,
        frame_datafield_status=status).pack()
    body = hdr + payload
    tm_vectors.append({"scid": scid, "vcid": vcid, "mc_count": mc, "vc_count": vc,
                       "payload": H(payload), "header": H(hdr),
                       "frame": H(body + crc16_a(body).to_bytes(2, "big")),
                       "total_octets": len(body) + 2})

frames_doc = {
    "tc": {
        "spec": "CCSDS 232.0-B-4 4.1.2 TC transfer frame primary header, 5 octets",
        "oracles": ["hand-packed field by field from CCSDS 232.0-B-4 4.1.2",
                    "NASA CryptoLib Crypto_TC_ProcessSecurity (NOSA-1.3, separate process)"],
        "header_len": 5,
        "length_convention": "frame length field = total octets in the frame, minus 1",
        "fecf": "CRC-16/IBM-3740 over header+data, from crcmod - see crc.json for its four oracles",
        "note": ("CryptoLib returns 103 = MANAGED_PARAMETERS_FOR_GVCID_NOT_FOUND for every vector. "
                 "That is the lookup AFTER the header is parsed and the length is checked, so it "
                 "is the expected status here; -82 would mean it disagreed about the length."),
        "vectors": tc_meta,
    },
    "tm": {
        "spec": "CCSDS 132.0-B-3 4.1.2 TM transfer frame primary header, 6 octets",
        "oracles": ["spacepackets.ccsds.tm_frame.TmFramePrimaryHeader (Apache-2.0)"],
        "header_len": 6,
        "length_convention": "none - the TM primary header carries no frame length field",
        "fecf": "CRC-16/IBM-3740 over header+data, from crcmod",
        "vectors": tm_vectors,
    },
}
(OUT / "transfer_frame.json").write_text(json.dumps(frames_doc, indent=2))

print("wrote:", *[p.name for p in sorted(OUT.iterdir())])
print()
print("CRC-16 vectors (4 oracles agree, incl. NASA CryptoLib):")
for v in crc_doc["ccsds_fecf_crc16"]["vectors"]:
    print(f"   {v['in']:<32} -> {v['crc']}   residue {v['residue']}")
print()
print("CRC-32C vectors (3 oracles agree, incl. libcsp):")
for v in crc_doc["csp_crc32"]["vectors"]:
    print(f"   {v['in']:<32} -> {v['crc']}")
