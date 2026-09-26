"""collect_until waits on the event, not on the clock - the primitive EX-D01's flake needed.

A caller that does `time.sleep(N); collect()` has guessed how long the spacecraft, the link and the
host socket will take and then looked exactly once. On a loaded runner the guess is short and the
single look finds a frame still in flight - which is not a mitigation failure, it is latency, and a
test that reads it as one reddens CI on good code. That is what happened to EX-D01's verifier on
runs 58 and 60. collect_until looks repeatedly until the event the caller named arrives and returns
the instant it does, so a fast host is not slowed and a slow one is not failed.

The link is faked and delivers late on a wall clock; nothing here needs Renode. These tests are
written to be timing-ROBUST - they assert on the event, never on a tight elapsed-time window -
because a flaky test of a flake fix would be the joke that tells itself.
"""
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.identity import GROUND_STATIONS                       # noqa: E402
from cuberange.gs.station import GroundStation, OBC_APID, PUS_TM_TIME_LEN  # noqa: E402
from cuberange.proto.frame import SCID, encode_tm_frame             # noqa: E402
from cuberange.proto.pus import (FAILURE_NOT_AUTHORISED, PusTm, request_id,   # noqa: E402
                                 SERVICE_VERIFICATION, SUBTYPE_ACCEPTANCE_FAILURE)
from cuberange.proto.spacepacket import PacketType, SpacePacket      # noqa: E402

STATION = GROUND_STATIONS["primary"]


def refusal_frame() -> bytes:
    """A well-formed PUS 1,2 refusal addressed to the station, in a TM transfer frame."""
    app = request_id(OBC_APID, 0) + bytes([FAILURE_NOT_AUTHORISED])
    tm = PusTm(service=SERVICE_VERIFICATION, subtype=SUBTYPE_ACCEPTANCE_FAILURE,
               msg_counter=0, dest_id=STATION, time=bytes(PUS_TM_TIME_LEN), app_data=app)
    pkt = SpacePacket(apid=OBC_APID, ptype=PacketType.TM, sec_hdr=True, seq_count=0,
                      data=tm.encode())
    return encode_tm_frame(pkt.encode(), mc_count=0, vc_count=0, scid=SCID)


class DelayedLink:
    """Delivers its frame once, only after `arrive_in` seconds on the wall clock have passed.

    A frame=None link never delivers anything, which is how a negative case (nothing ever arrives)
    is expressed. Arrival is on the wall clock, so a slow CPU does not change WHEN the frame lands,
    only how many times collect_until looks before it does.
    """

    def __init__(self, frame: bytes | None, arrive_in: float):
        self.frame = frame
        self.arrive_at = time.time() + arrive_in
        self.delivered = False

    def poll(self):
        if self.frame is not None and not self.delivered and time.time() >= self.arrive_at:
            self.delivered = True
            return [self.frame]
        return []


def station_for(link) -> GroundStation:
    return GroundStation(link, station_id=STATION, target_scid=SCID)


def test_a_single_collect_misses_a_frame_still_in_flight():
    """The bug, in one assertion: look once too early and the refusal is not there yet."""
    st = station_for(DelayedLink(refusal_frame(), arrive_in=0.5))
    st.collect()
    assert not st.refusals, "the frame arrived implausibly fast; raise arrive_in"


def test_collect_until_waits_for_a_frame_a_single_look_would_miss():
    """The fix: keep looking, and the same frame is caught."""
    st = station_for(DelayedLink(refusal_frame(), arrive_in=0.5))
    got = st.collect_until(lambda: st.refusals, timeout=10.0)
    assert got and st.refusals, "collect_until gave up before the frame arrived"


def test_collect_until_returns_before_the_timeout_when_the_event_arrives():
    """A fast host is not made to sit out the deadline: a 30s timeout returns in well under it."""
    st = station_for(DelayedLink(refusal_frame(), arrive_in=0.3))
    t0 = time.time()
    st.collect_until(lambda: st.refusals, timeout=30.0)
    assert st.refusals and time.time() - t0 < 15.0, "collect_until waited far past the event"


def test_collect_until_gives_up_and_returns_falsy_when_nothing_arrives():
    """The negative case EX-D01's requiring-station test depends on: no event, no hang, no truthy.

    It must return a falsy value so the caller can then assert the thing it feared did NOT happen -
    a collect_until that hung, or returned truthy here, would make that assertion impossible.
    """
    st = station_for(DelayedLink(None, arrive_in=0.0))
    result = st.collect_until(lambda: st.refusals, timeout=0.5)
    assert not result and not st.refusals


def test_await_refusal_keeps_its_contract_on_top_of_collect_until():
    """EX-G04 and EX-X01 call await_refusal for a refusal-or-None; the refactor must not change it."""
    st = station_for(DelayedLink(refusal_frame(), arrive_in=0.3))
    r = st.await_refusal(timeout=10.0)
    assert r is not None and r.dest_id == STATION
    none = station_for(DelayedLink(None, arrive_in=0.0))
    assert none.await_refusal(timeout=0.5) is None
