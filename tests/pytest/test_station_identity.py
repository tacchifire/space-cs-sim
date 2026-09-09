"""A ground station must not accept another spacecraft's answer, or another station's.

This is the failure the identity parameters exist to stop, and it is worth a unit test rather than
only an end-to-end one: with a single satellite on the range it cannot happen, so it would sit
undetected until the day a second one is added — and then present as intermittent flakiness rather
than as a wrong answer.

The link is faked. Nothing here needs Renode; the question is entirely about what the station is
willing to call a reply.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.station import GroundStation, OBC_APID          # noqa: E402
from cuberange.proto.frame import (SCID, decode_tm_frame, encode_tm_frame,  # noqa: E402
                                   tc_frame_identity, tm_frame_identity)
from cuberange.proto.pus import (PusTm, SERVICE_TEST,             # noqa: E402
                                 SUBTYPE_CONNECTION_TEST_REPORT)
from cuberange.proto.spacepacket import PacketType, SpacePacket   # noqa: E402

SAT_A_SCID, SAT_B_SCID = SCID, 0x0AA
STATION_1, STATION_2 = 0x0042, 0x0043


class FakeLink:
    """Records what was sent, and answers only after something has been sent.

    The reply is withheld until the first send on purpose. `ping()` drains the link before
    transmitting - so that a report left over from an earlier ping is not read as the answer to
    this one - and a fake that queued its reply up front would have that drain throw the reply
    away. Delivering only after a send is also what a satellite does.
    """

    def __init__(self, replies=()):
        self.sent = []
        self._queued = list(replies)
        self._ready: list = []

    def send_frame(self, frame: bytes) -> None:
        self.sent.append(frame)
        self._ready.extend(self._queued)
        self._queued = []

    def poll(self):
        out, self._ready = self._ready, []
        return out


def report(scid: int, apid: int, dest_id: int) -> bytes:
    """A well-formed PUS 17,2 in a TM transfer frame, from whichever spacecraft you name."""
    tm = PusTm(service=SERVICE_TEST, subtype=SUBTYPE_CONNECTION_TEST_REPORT,
               msg_counter=0, dest_id=dest_id, time=b"\x00\x00\x00\x01")
    packet = SpacePacket(apid=apid, ptype=PacketType.TM, sec_hdr=True, seq_count=1,
                         data=tm.encode())
    return encode_tm_frame(packet.encode(), mc_count=1, vc_count=1, scid=scid)


def test_a_reply_from_our_own_spacecraft_is_accepted():
    link = FakeLink([report(SAT_A_SCID, OBC_APID, STATION_1)])
    station = GroundStation(link, station_id=STATION_1, target_scid=SAT_A_SCID)
    assert station.ping(timeout=1.0) is not None


def test_a_reply_from_the_other_spacecraft_is_refused():
    """The quietest failure: satellite B answers and station 1 calls satellite A healthy."""
    link = FakeLink([report(SAT_B_SCID, OBC_APID, STATION_1)])
    station = GroundStation(link, station_id=STATION_1, target_scid=SAT_A_SCID)
    assert station.ping(timeout=1.0) is None
    assert station.undecodable, "the wrong-spacecraft frame was dropped without being recorded"
    assert "spacecraft" in station.undecodable[0][1]


def test_a_reply_addressed_to_the_other_station_is_refused():
    """Two stations, one satellite. Station 2's report must not satisfy station 1."""
    link = FakeLink([report(SAT_A_SCID, OBC_APID, STATION_2)])
    station = GroundStation(link, station_id=STATION_1, target_scid=SAT_A_SCID)
    assert station.ping(timeout=1.0) is None
    assert station.not_for_us, "somebody else's report was discarded instead of recorded"
    assert station.not_for_us[0].dest_id == STATION_2


def test_a_reply_from_the_wrong_apid_is_refused():
    link = FakeLink([report(SAT_A_SCID, OBC_APID ^ 1, STATION_1)])
    station = GroundStation(link, station_id=STATION_1, target_scid=SAT_A_SCID)
    assert station.ping(timeout=1.0) is None


def test_the_station_stamps_its_own_target_on_what_it_transmits():
    """A station pointed at satellite B must not put satellite A's SCID on the wire."""
    link = FakeLink()
    GroundStation(link, station_id=STATION_2, target_scid=SAT_B_SCID).send_connection_test()
    assert len(link.sent) == 1
    scid, _vcid = tc_frame_identity(link.sent[0])
    assert scid == SAT_B_SCID


def test_two_stations_do_not_share_a_transmitted_identity():
    a, b = FakeLink(), FakeLink()
    GroundStation(a, station_id=STATION_1, target_scid=SAT_A_SCID).send_connection_test()
    GroundStation(b, station_id=STATION_2, target_scid=SAT_B_SCID).send_connection_test()
    assert tc_frame_identity(a.sent[0])[0] != tc_frame_identity(b.sent[0])[0]


@pytest.mark.parametrize("scid", [0x000, 0x0A9, 0x0AA, 0x3FF])
def test_tm_identity_survives_a_round_trip(scid):
    """The accessor and the encoder must agree, or every check above is checking noise."""
    frame = report(scid, OBC_APID, STATION_1)
    assert tm_frame_identity(frame)[0] == scid
    decode_tm_frame(frame, expect_scid=scid)          # must not raise
    with pytest.raises(ValueError, match="spacecraft"):
        decode_tm_frame(frame, expect_scid=scid ^ 1)
