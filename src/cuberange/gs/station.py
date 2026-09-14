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
from dataclasses import dataclass

from ..proto import pus_auth
from ..proto.frame import SCID, decode_tm_frame, encode_tc_frame
from ..proto.pus import (FAILURE_NAMES, PusTc, PusTm, SERVICE_TEST, SERVICE_VERIFICATION,
                         SUBTYPE_ACCEPTANCE_FAILURE, SUBTYPE_CONNECTION_TEST,
                         parse_request_id,
                         SUBTYPE_CONNECTION_TEST_REPORT)
from ..proto.spacepacket import PacketType, SpacePacket
from .link import SpaceLink

# Re-exported, not defined. The number lived here and was copy-pasted into two exercise
# files; cuberange.identity is where identities live and where identity.cmake is compared
# against them.
from ..identity import GROUND_SOURCE_ID  # noqa: F401
OBC_APID = 0x0A9
PUS_TM_TIME_LEN = 4

#: ECSS-E-ST-70-41C service 15 is on-board storage and retrieval. What the spacecraft implements
#: is a subset - "send report N again" - and the firmware header says so at length. EX-L02.
SERVICE_STORAGE = 15
SUBTYPE_RESEND = 1

SERVICE_FUNCTION = 8
SUBTYPE_PERFORM = 1
FUNC_SET_COMM_RAIL = 1



@dataclass(frozen=True)
class Refusal:
    """A PUS 1,2 the spacecraft sent because it would not accept something.

    `apid` and `seq_count` come out of the request id, which is the refused packet's own first
    four octets - so an operator can match this to the command in their own log without the
    spacecraft having had to remember anything.
    """

    apid: int
    seq_count: int
    code: int
    dest_id: int

    @property
    def reason(self) -> str:
        return FAILURE_NAMES.get(self.code, f"code {self.code}")

    def __str__(self) -> str:
        return (f"refused APID 0x{self.apid:03X} seq {self.seq_count}: {self.reason} "
                f"(to station 0x{self.dest_id:04X})")


def decode_refusal(tm: PusTm) -> Refusal:
    if len(tm.app_data) < 5:
        raise ValueError(f"a PUS 1,2 carries a request id and a code; got {len(tm.app_data)}")
    apid, seq = parse_request_id(tm.app_data[:4])
    return Refusal(apid=apid, seq_count=seq, code=tm.app_data[4], dest_id=tm.dest_id)


class GroundStation:
    def __init__(self, link: SpaceLink, station_id: int = GROUND_SOURCE_ID, *, vcid: int = 0,
                 target_apid: int = OBC_APID, target_scid: int = SCID,
                 require_signed_tm: bytes | None = None,
                 uplink_key: bytes | None = None, sdls_spi: int | None = None):
        self.link = link
        self.station_id = station_id
        # The virtual channel this station transmits on. Every station used VC 0, and a spacecraft
        # keeping one sequence counter for the link then cannot tell two operators apart from one
        # operator replaying itself. CCSDS keeps that counter per VC for exactly this reason.
        self.vcid = vcid
        self.target_apid = target_apid
        self.target_scid = target_scid
        self._tc_seq = 0
        #: The key this station will verify reports with, or None to accept unsigned telemetry.
        #:
        #: REQUIRED rather than opportunistic, on purpose. A station that verified a trailer when
        #: one was present and accepted the packet when it was absent would be defeated by an
        #: attacker who simply does not attach one - which is not a subtle attack, and is the
        #: shape most "optional security" ends up having. EX-D01 measures it.
        self.require_signed_tm = require_signed_tm
        #: The uplink half, and it is deliberately symmetric with the line above. A station that
        #: verified telemetry and sent unauthenticated commands would be checking the answers to
        #: questions anybody could ask.
        #:
        #: `uplink_key` puts EX-S02's trailer on every telecommand. `sdls_spi`, when set, also
        #: wraps the frame in EX-S01's SDLS security header, which is what a spacecraft running
        #: both layers requires. Both default to None and the station then transmits exactly the
        #: plain frames it always did - EX-L01 and everything before EX-S01 depend on that.
        self.uplink_key = uplink_key
        self.sdls_spi = sdls_spi
        self._sdls_sn = 1
        #: Reports that arrived without a valid trailer while one was required. Kept rather than
        #: dropped: on a range whose subject is forged telemetry, "something claimed to be from
        #: the spacecraft and was not" is the observation, and discarding it would hide it.
        self.unauthenticated: list = []
        #: The spacecraft's own report counter, and the gaps in it.
        #:
        #: EX-D01 ended by naming this. Authentication proves what the spacecraft SAID; it cannot
        #: prove the spacecraft said everything, and a suppressed refusal is indistinguishable
        #: from a command that was accepted - which is EX-G04's finding, still true after two
        #: rounds of cryptography aimed at forgery.
        #:
        #: The counter is the PUS TM message counter, which the spacecraft increments for every
        #: report it sends. It is sixteen bits and it wraps; `_advance` treats a step of more than
        #: half the range as a wrap rather than a gap, because the alternative is a station that
        #: reports 65000 missing reports once a day.
        #:
        #: THIS ONLY WORKS ON AUTHENTICATED TELEMETRY. Without a trailer an attacker suppresses a
        #: report and forges a replacement carrying the counter value that would have been next,
        #: and the gap closes. EX-D02 measures that, because a detection that a forger can defeat
        #: is worth knowing the shape of rather than trusting.
        #: Every authenticated TM addressed to this station, in arrival order, whatever service
        #: it carries. EX-L03 counts these against a pass schedule; nothing else reads them.
        self.telemetry: list = []
        self.last_report_counter: int | None = None
        self.counter_gaps: list = []
        self.undecodable: list = []
        # Replies that decoded but were not ours. Kept rather than dropped: on a range with two
        # stations, "someone else's telemetry arrived here" is the observation the exercise is
        # about, and silently discarding it would hide exactly what a learner should see.
        self.not_for_us: list = []
        #: PUS 1,2 acceptance failures the spacecraft sent us. A refusal the ground
        #: cannot hear is indistinguishable from a command that never arrived, which
        #: is where EX-G02 and EX-G03 both end.
        self.refusals: list = []

    def _next_seq(self) -> int:
        seq = self._tc_seq
        self._tc_seq = (self._tc_seq + 1) & 0x3FFF
        return seq

    def _send(self, tc: PusTc) -> int:
        seq = self._next_seq()
        raw = SpacePacket(apid=self.target_apid, ptype=PacketType.TC, sec_hdr=True,
                          seq_count=seq, data=tc.encode()).encode()
        if self.uplink_key is not None:
            raw = pus_auth.sign(raw, key=self.uplink_key, seq=seq)
        if self.sdls_spi is not None:
            from ..proto import sdls
            #: A distinct IV per frame. GCM's nonce rule is not negotiable and this counter is the
            #: only thing varying here - see pus_auth.py's note on what reuse costs.
            iv = self._sdls_sn.to_bytes(sdls.IV_LEN, "big")
            frame = sdls.encode_tc(raw, key=self.uplink_key or self.require_signed_tm,
                                   spi=self.sdls_spi, iv=iv, seq_num=self._sdls_sn,
                                   frame_seq=seq & 0xFF, scid=self.target_scid, vcid=self.vcid)
            self._sdls_sn += 1
        else:
            frame = encode_tc_frame(raw, seq & 0xFF, scid=self.target_scid, vcid=self.vcid)
        self.link.send_frame(frame)
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

    def collect(self) -> list:
        """Read whatever has arrived, classify it, and transmit nothing.

        Returns the connection-test reports addressed to this station; refusals land in
        `self.refusals` and anything else in `not_for_us` or `undecodable`. This exists because a
        PUS 1,2 answers a PUS 8, and nothing was polling between those - so the report the
        spacecraft sent so the ground would know was being dropped by the next ping's drain.
        """
        mine = []
        for frame in self.link.poll():
            try:
                _mc, _vc, payload = decode_tm_frame(frame, expect_scid=self.target_scid)
                if self.require_signed_tm is not None:
                    #: Before SpacePacket.decode, because the trailer is inside the packet and its
                    #: length field covers it - parsing first and checking after would mean the
                    #: parser had already read octets nothing vouches for.
                    payload = pus_auth.verify(payload, key=self.require_signed_tm).packet
                packet = SpacePacket.decode(payload)
                tm = PusTm.decode(packet.data, time_len=PUS_TM_TIME_LEN)
            except pus_auth.AuthenticationError as exc:
                self.unauthenticated.append((frame, str(exc)))
                continue
            except ValueError as exc:
                self.undecodable.append((frame, str(exc)))
                continue
            if tm.dest_id == self.station_id:
                self._advance(tm.msg_counter)
            if (tm.service, tm.subtype) == (SERVICE_VERIFICATION, SUBTYPE_ACCEPTANCE_FAILURE):
                if tm.dest_id == self.station_id:
                    self.refusals.append(decode_refusal(tm))
                else:
                    self.not_for_us.append(tm)
                continue
            #: Everything addressed to us is telemetry, whatever service it belongs to. The
            #: list below used to end here for anything that was not a connection-test report,
            #: so a housekeeping beacon arrived, decoded, verified and was dropped without
            #: trace - and EX-L03's whole subject is whether the ground heard the spacecraft.
            #: A station that only counts the replies to its own questions cannot answer that.
            if tm.dest_id == self.station_id:
                self.telemetry.append(tm)
            if (tm.service, tm.subtype) != (SERVICE_TEST, SUBTYPE_CONNECTION_TEST_REPORT):
                continue
            if packet.apid != self.target_apid or tm.dest_id != self.station_id:
                self.not_for_us.append(tm)
                continue
            mine.append(tm)
        return mine

    def _advance(self, counter: int) -> None:
        """Record this report's counter and note anything missing between it and the last.

        Counts the gap rather than merely flagging one, because "three reports are missing" and
        "one report is missing" are different situations for an operator and the difference is
        free to carry.
        """
        if self.last_report_counter is None:
            self.last_report_counter = counter
            return
        step = (counter - self.last_report_counter) & 0xFFFF
        if step == 0:
            return
        #: A backwards step, or a jump of more than half the counter's range, is a wrap or a
        #: reordering rather than 60-thousand lost reports. Reported as neither: this station
        #: claims to detect gaps, not to reconstruct history.
        if step > 1 and step < 0x8000:
            self.counter_gaps.append((self.last_report_counter, counter, step - 1))
        self.last_report_counter = counter

    @property
    def reports_missing(self) -> int:
        """How many reports this station can tell it never received."""
        return sum(missing for _, _, missing in self.counter_gaps)

    def request_resend(self, counter: int) -> int:
        """Ask the spacecraft to send report `counter` again. Returns the TC sequence used.

        WHY THIS EXISTS, and it is not a convenience. A counter gap says the spacecraft said
        something you did not hear. On a clean link that is an attack; on a lossy one it is
        usually the link, and EX-L02 measures an operator who cannot tell the two apart from the
        gap alone. Asking again separates them: what comes back was lost, what never comes back is
        being taken.

        It goes through the same `_send` as every other command, so whatever authentication this
        station is configured for applies. That matters: a resend request a spacecraft would honour
        without checking is a way to make it transmit on demand, which is a thing an attacker would
        enjoy having.
        """
        return self._send(PusTc(service=SERVICE_STORAGE, subtype=SUBTYPE_RESEND,
                                source_id=self.station_id,
                                app_data=counter.to_bytes(2, "big")))

    def await_refusal(self, timeout: float = 10.0):
        """Wait for the spacecraft to say it refused something. None if it never does."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.collect()
            if self.refusals:
                return self.refusals[-1]
            time.sleep(0.1)
        return None

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
        # Clear the backlog first. Without this, a report that arrived between two pings answers
        # the second one instantly and a dead satellite looks alive for exactly one call.
        #
        # Through collect(), not a bare poll(). The first version discarded the frames outright,
        # which threw away any PUS 1,2 that had arrived since the last call - the refusal report
        # the spacecraft sent precisely so the ground would know. A drain that drops evidence is
        # not a drain.
        self.collect()
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
                if (tm.service, tm.subtype) == (SERVICE_VERIFICATION,
                                                SUBTYPE_ACCEPTANCE_FAILURE):
                    if tm.dest_id == self.station_id:
                        self.refusals.append(decode_refusal(tm))
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

    def alive(self, timeout: float = 20.0, per_ping_s: float = 8.0) -> bool:
        """Whether the spacecraft answers, given more than one chance to.

        WHY THIS IS NOT ONE PING. Five exercise verifiers each had their own three-line

            return self.station.ping(timeout=...) is not None

        used as a PRECONDITION - `assert r.alive(), "the satellite was not answering before the
        attack"` - and asserted more than ten times across them. One unanswered ping therefore
        failed the whole test, before the attack under test had been sent. It happened in CI on
        2026-09-12, in EX-G01, and three re-runs of the same test passed: a gate that reports
        link luck as a mitigation failure is worse than no gate, because somebody will start
        re-running it until it is green and stop reading what it says.

        A spacecraft that answers the second ping is alive. A spacecraft that answers none of
        them inside the deadline is not, and this still says so - the deadline is the claim, and
        it is longer than one round trip because the claim is about the spacecraft rather than
        about one frame.

        `timeout` is how long to keep TRYING, which is what the callers already meant by it.

        AND NOTE THE NEGATIVE USE, which is the half that matters more. Several tests assert
        `not alive(timeout=8)` - "the satellite stopped answering after the attack". As one ping
        that was cheap and wrong: a single lost frame proved the attack worked. Retrying makes it
        cost the whole budget and makes it true, because "it did not answer" is a stronger claim
        than "it answered" and needs more patience, not less.
        """
        deadline = time.time() + timeout
        while True:
            if self.ping(timeout=min(per_ping_s, max(0.5, deadline - time.time()))) is not None:
                return True
            if time.time() >= deadline:
                return False
