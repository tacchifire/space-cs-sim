"""Minimal ground station: build a TC, send it, wait for the matching TM."""
from __future__ import annotations

import time

from ..proto.frame import decode_tm_frame, encode_tc_frame
from ..proto.pus import (PusTc, PusTm, SERVICE_TEST, SUBTYPE_CONNECTION_TEST,
                         SUBTYPE_CONNECTION_TEST_REPORT)
from ..proto.spacepacket import PacketType, SpacePacket
from .link import SpaceLink

GROUND_SOURCE_ID = 0x0042
OBC_APID = 0x0A9
PUS_TM_TIME_LEN = 4


class GroundStation:
    def __init__(self, link: SpaceLink):
        self.link = link
        self._tc_seq = 0
        self.undecodable: list = []

    def _next_seq(self) -> int:
        seq = self._tc_seq
        self._tc_seq = (self._tc_seq + 1) & 0x3FFF
        return seq

    def send_connection_test(self) -> int:
        tc = PusTc(service=SERVICE_TEST, subtype=SUBTYPE_CONNECTION_TEST,
                   source_id=GROUND_SOURCE_ID)
        seq = self._next_seq()
        packet = SpacePacket(apid=OBC_APID, ptype=PacketType.TC, sec_hdr=True,
                             seq_count=seq, data=tc.encode())
        self.link.send_frame(encode_tc_frame(packet.encode(), seq & 0xFF))
        return seq

    def ping(self, timeout: float = 10.0):
        """Send PUS 17,1 and return the PusTm report, or None on timeout."""
        self.send_connection_test()
        deadline = time.time() + timeout
        while time.time() < deadline:
            for frame in self.link.poll():
                try:
                    _mc, _vc, payload = decode_tm_frame(frame)
                    packet = SpacePacket.decode(payload)
                    tm = PusTm.decode(packet.data, time_len=PUS_TM_TIME_LEN)
                except ValueError as exc:
                    # Keep the raw bytes: on a security range a malformed frame is evidence,
                    # not noise.
                    self.undecodable.append((frame, str(exc)))
                    continue
                if (tm.service, tm.subtype) == (SERVICE_TEST, SUBTYPE_CONNECTION_TEST_REPORT):
                    return tm
        return None
