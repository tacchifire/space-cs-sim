"""A ground station: build a TC, send it, and wait for the answer to THAT request.

The identity parameters are not decoration. Until they existed, `GROUND_SOURCE_ID` and `OBC_APID`
were module constants and `ping()` accepted the first decodable PUS 17,2 it saw - it discarded the
sequence number it had just sent, ignored the packet's APID, and never looked at the transfer
frame's spacecraft ID. With one satellite and one station that is invisible. With two of either it
is a range that reports a healthy spacecraft because a DIFFERENT one answered, which is worse than
a range that reports nothing.

So a station now knows three things: who it is, which spacecraft it is talking to, and which APID
on that spacecraft. `ping()` requires all three to line up before it calls a reply an answer.
"""
from __future__ import annotations

import time

from ..proto.frame import SCID, decode_tm_frame, encode_tc_frame
from ..proto.pus import (PusTc, PusTm, SERVICE_TEST, SUBTYPE_CONNECTION_TEST,
                         SUBTYPE_CONNECTION_TEST_REPORT)
from ..proto.spacepacket import PacketType, SpacePacket
from .link import SpaceLink

# Re-exported, not defined. The number lived here and was copy-pasted into two exercise
# files; cuberange.identity is where identities live and where identity.cmake is compared
# against them.
from ..identity import GROUND_SOURCE_ID  # noqa: F401
OBC_APID = 0x0A9
PUS_TM_TIME_LEN = 4

SERVICE_FUNCTION = 8
SUBTYPE_PERFORM = 1
FUNC_SET_COMM_RAIL = 1


class GroundStation:
    def __init__(self, link: SpaceLink, station_id: int = GROUND_SOURCE_ID, *, vcid: int = 0,
                 target_apid: int = OBC_APID, target_scid: int = SCID):
        self.link = link
        self.station_id = station_id
        # The virtual channel this station transmits on. Every station used VC 0, and a spacecraft
        # keeping one sequence counter for the link then cannot tell two operators apart from one
        # operator replaying itself. CCSDS keeps that counter per VC for exactly this reason.
        self.vcid = vcid
        self.target_apid = target_apid
        self.target_scid = target_scid
        self._tc_seq = 0
        self.undecodable: list = []
        # Replies that decoded but were not ours. Kept rather than dropped: on a range with two
        # stations, "someone else's telemetry arrived here" is the observation the exercise is
        # about, and silently discarding it would hide exactly what a learner should see.
        self.not_for_us: list = []

    def _next_seq(self) -> int:
        seq = self._tc_seq
        self._tc_seq = (self._tc_seq + 1) & 0x3FFF
        return seq

    def _send(self, tc: PusTc) -> int:
        seq = self._next_seq()
        packet = SpacePacket(apid=self.target_apid, ptype=PacketType.TC, sec_hdr=True,
                             seq_count=seq, data=tc.encode())
        self.link.send_frame(encode_tc_frame(packet.encode(), seq & 0xFF,
                                             scid=self.target_scid, vcid=self.vcid))
        return seq

    def send_connection_test(self) -> int:
        return self._send(PusTc(service=SERVICE_TEST, subtype=SUBTYPE_CONNECTION_TEST,
                                source_id=self.station_id))

    def set_comm_rail(self, on: bool) -> int:
        """PUS 8,1: ask the OBC to switch the COMM power rail.

        The OBC holds the EPS power token, so this is the legitimate operator path to a rail that
        an attacker on the internal bus can only reach by forging (EX-B01). The frame this puts on
        the link is the one EX-L01 replays.
        """
        app_data = FUNC_SET_COMM_RAIL.to_bytes(2, "big") + bytes([1 if on else 0])
        return self._send(PusTc(service=SERVICE_FUNCTION, subtype=SUBTYPE_PERFORM,
                                source_id=self.station_id, app_data=app_data))

    def ping(self, timeout: float = 10.0):
        """Send PUS 17,1 and return the report addressed to this station, or None on timeout.

        WHAT THIS DOES AND DOES NOT ESTABLISH, because on a security range the difference matters.

        It establishes that a reply arrived carrying this spacecraft's SCID, this APID, and this
        station's id in the TM's destination field, after this call transmitted. That is enough to
        keep two spacecraft and two ground stations from answering for each other, which is what it
        was built for.

        It does NOT authenticate anything. All three of those fields are unauthenticated and
        attacker-writable: anyone who can transmit on this link can stamp them. A range whose whole
        subject is forged telecommands should not pretend its own health check is proof of
        liveness, and EX-L01 is the exercise about exactly that asymmetry.

        Nor does it correlate the reply with THIS request. The PUS 17,2 the OBC sends echoes the
        requester's source id but not the request's sequence count, so there is no field to match
        on. What it does instead is drop whatever was already queued before transmitting, so a
        report left over from an earlier ping is not read as the answer to this one. Anything that
        needs true request/response correlation needs a counter in the report, which is a firmware
        change and not a host one.
        """
        # Discard the backlog first. Without this, a report that arrived between two pings answers
        # the second one instantly and a dead satellite looks alive for exactly one call.
        self.link.poll()
        self.send_connection_test()
        deadline = time.time() + timeout
        while time.time() < deadline:
            for frame in self.link.poll():
                try:
                    # expect_scid is what stops another spacecraft's telemetry being read as ours.
                    _mc, _vc, payload = decode_tm_frame(frame, expect_scid=self.target_scid)
                    packet = SpacePacket.decode(payload)
                    tm = PusTm.decode(packet.data, time_len=PUS_TM_TIME_LEN)
                except ValueError as exc:
                    # Keep the raw bytes: on a security range a malformed frame is evidence,
                    # not noise. A frame from the wrong spacecraft lands here too, which is the
                    # point - it is recorded rather than acted on.
                    self.undecodable.append((frame, str(exc)))
                    continue
                if (tm.service, tm.subtype) != (SERVICE_TEST, SUBTYPE_CONNECTION_TEST_REPORT):
                    continue
                if packet.apid != self.target_apid or tm.dest_id != self.station_id:
                    # Decodable, from the right spacecraft, and not an answer to us. The OBC
                    # echoes the requester's source id into dest_id, so this is how a second
                    # station's report is told apart from ours.
                    self.not_for_us.append(tm)
                    continue
                return tm
        return None
