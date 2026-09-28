"""The ground's own ledger of SA directives, and the one question it answers.

EX-S07's detector: an entry in the spacecraft's SA-management log that names one of YOUR SAs as
the retirer, which you never sent. Attribution names the key, never the hand holding it - so a copy
of your key reads as you everywhere except in your own record of what you did.

The link is faked; nothing here needs Renode. The Renode half is
exercises/EX-S07-*/verify_ex_s07.py.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.station import GroundStation, OBC_APID, PUS_TM_TIME_LEN   # noqa: E402
from cuberange.identity import GROUND_STATIONS                      # noqa: E402
from cuberange.keys import (SA_OWNER_OPERATOR, SA_OWNERS, SDLS_KEY_ROTATED,  # noqa: E402
                            SDLS_SPI, SDLS_SPI_PARTNER, SDLS_SPI_ROTATED)
from cuberange.proto import sdls                                    # noqa: E402
from cuberange.proto.frame import SCID, encode_tm_frame             # noqa: E402
from cuberange.proto.pus import PusTm, SERVICE_HOUSEKEEPING, SUBTYPE_HK_REPORT   # noqa: E402
from cuberange.proto.spacepacket import PacketType, SpacePacket     # noqa: E402

STATION = GROUND_STATIONS["primary"]
MINE = frozenset(spi for spi, owner in SA_OWNERS.items() if owner == SA_OWNER_OPERATOR)
BY_ME = (SDLS_SPI, SDLS_SPI_ROTATED)          # SPI 9 retired under SPI 10 - one of mine
BY_PARTNER = (SDLS_SPI, SDLS_SPI_PARTNER)     # SPI 9 retired under SPI 11 - not mine


class FakeLink:
    def __init__(self, frames=()):
        self.sent = []
        self._frames = list(frames)

    def send_frame(self, frame: bytes) -> None:
        self.sent.append(frame)

    def poll(self):
        out, self._frames = self._frames, []
        return out


def station(owned=MINE, link=None) -> GroundStation:
    return GroundStation(link or FakeLink(), station_id=STATION, target_scid=SCID,
                         sdls_key=SDLS_KEY_ROTATED, sdls_spi=SDLS_SPI_ROTATED, owned_spis=owned)


def hk_beacon(*log: tuple[int, int]) -> bytes:
    """A housekeeping report carrying counts and an SA log, as the OBC builds it: 14 + 4k octets."""
    app = bytes(14) + b"".join(t.to_bytes(2, "big") + b.to_bytes(2, "big") for t, b in log)
    tm = PusTm(service=SERVICE_HOUSEKEEPING, subtype=SUBTYPE_HK_REPORT, msg_counter=0,
               dest_id=STATION, time=bytes(PUS_TM_TIME_LEN), app_data=app)
    pkt = SpacePacket(apid=OBC_APID, ptype=PacketType.TM, sec_hdr=True, seq_count=0,
                      data=tm.encode())
    return encode_tm_frame(pkt.encode(), mc_count=0, vc_count=0, scid=SCID)


def test_an_entry_in_your_name_that_you_never_sent_is_unexplained():
    st = station()
    st.sa_retire_log = [BY_ME]
    assert st.unexplained_sa_retirements == [BY_ME]


def test_an_entry_you_did_send_is_explained():
    st = station()
    st.send_sa_stop(SDLS_SPI)
    st.sa_retire_log = [BY_ME]
    assert st.unexplained_sa_retirements == []


def test_one_directive_explains_one_entry_not_every_identical_one():
    """A multiset: send it once, see it twice, and one of the two is not yours."""
    st = station()
    st.send_sa_stop(SDLS_SPI)
    st.sa_retire_log = [BY_ME, BY_ME]
    assert st.unexplained_sa_retirements == [BY_ME]


def test_a_partners_retirement_is_not_in_your_name():
    """A retirement under someone else's SA is EX-S05/EX-S06's attribution, not this question."""
    st = station()
    st.sa_retire_log = [BY_PARTNER]
    assert st.unexplained_sa_retirements == []


def test_a_directive_not_yet_logged_is_not_an_alarm():
    """Sent and not in the log is latency, a refusal or the bounded ring - never "an attack"."""
    st = station()
    st.send_sa_stop(SDLS_SPI)
    assert st.unexplained_sa_retirements == []


def test_a_station_that_does_not_know_its_own_sas_cannot_tell():
    """None, not []: "I cannot judge" and "nothing is wrong" are different answers."""
    st = station(owned=None)
    st.sa_retire_log = [BY_ME]
    assert st.unexplained_sa_retirements is None


def test_the_ledger_is_the_one_difference():
    """Same link, same log, same ledger; only `owned_spis` differs, and only the verdict changes.

    This is how a host-side pair proves it differs by one thing (EX-G01 does the same with two
    policy objects): nothing the stations hold differs except whether they know which SAs are
    theirs.
    """
    trusting, reconciling = station(owned=None), station(owned=MINE)
    for st in (trusting, reconciling):
        st.sa_retire_log = [BY_ME]
    assert trusting.sa_directives_sent == reconciling.sa_directives_sent == []
    assert trusting.sa_retire_log == reconciling.sa_retire_log
    assert trusting.unexplained_sa_retirements is None
    assert reconciling.unexplained_sa_retirements == [BY_ME]


def test_a_directive_is_recorded_in_the_ledger_and_not_as_a_telecommand():
    """The directive is not a telecommand - send_sa_stop says so, and the spacecraft agrees, since
    the OBC never counts it. Counting it in commands_sent made the operator's own rotation read as
    unexplained_commands = -1. A non-STOP directive (a negative test's malformed type) retires
    nothing, so it is not a retirement this station ordered."""
    link = FakeLink()
    st = station(link=link)
    st.send_sa_stop(SDLS_SPI)
    st.send_sa_stop(SDLS_SPI, directive=0x7F)
    assert len(link.sent) == 2
    assert st.commands_sent == 0
    assert st.sa_directives_sent == [BY_ME]
    assert sdls.DIR_STOP_SA != 0x7F


def test_the_log_on_a_real_beacon_reaches_the_detector():
    """The whole ground-side path, from a beacon frame through collect() to the verdict."""
    st = station(link=FakeLink([hk_beacon(BY_PARTNER, BY_ME)]))
    st.collect()
    assert st.sa_retire_log == [BY_PARTNER, BY_ME]
    assert st.unexplained_sa_retirements == [BY_ME]
