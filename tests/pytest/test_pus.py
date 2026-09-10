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
               source_id=0x1234, ack=0b1001, app_data=b"")
    raw = tc.encode()
    assert len(raw) == 5
    assert raw[0] == (PUS_VERSION << 4) | 0b1001
    assert raw[1] == 17
    assert raw[2] == 1
    # 0x1234 is deliberately nobody. This is a codec test and the value is arbitrary, so
    # borrowing a real ground-station id read as a coupling that does not exist. It is also a
    # better value than the one it replaced, whose high octet was zero - a codec that dropped
    # that octet entirely would have passed.
    assert raw[3:5] == b"\x12\x34"


def test_tm_secondary_header_layout():
    tm = PusTm(service=SERVICE_TEST, subtype=SUBTYPE_CONNECTION_TEST_REPORT,
               msg_counter=7, dest_id=0x1234, time=b"\x00\x00\x00\x01", app_data=b"")
    raw = tm.encode()
    assert len(raw) == 7 + 4
    assert raw[0] == (PUS_VERSION << 4)
    assert raw[1] == 17
    assert raw[2] == 2
    assert raw[3:5] == b"\x00\x07"
    assert raw[5:7] == b"\x12\x34"
    assert raw[7:11] == b"\x00\x00\x00\x01"


def test_tc_round_trip_with_application_data():
    original = PusTc(service=8, subtype=1, source_id=1, ack=0b1111, app_data=bytes(range(16)))
    assert PusTc.decode(original.encode()) == original


def test_tm_round_trip_with_application_data():
    original = PusTm(service=3, subtype=25, msg_counter=1, dest_id=2,
                     time=b"\x11\x22\x33\x44", app_data=bytes(range(8)))
    assert PusTm.decode(original.encode(), time_len=4) == original
