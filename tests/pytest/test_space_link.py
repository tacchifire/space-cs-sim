"""The ground station's end of the space link, over real loopback sockets.

Twelve files use SpaceLink and none tested it. It is fifty lines, which is why: a fifty-line
client looks like it cannot be wrong. The two things it does that can be - carrying deframer state
across polls, and telling a closed link apart from a quiet one - are both invisible when they
break. A ground station that silently returns no frames looks exactly like a spacecraft that did
not answer, and every exercise here distinguishes those two for a living.
"""
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.link import SpaceLink            # noqa: E402
from cuberange.proto.frame import wrap             # noqa: E402


class FarEnd:
    """One connection, recording what arrives and sending what the test asks for."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received = bytearray()
        self.conn = None
        self._ready = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        try:
            self.conn, _ = self.sock.accept()
        except OSError:
            return
        self.conn.settimeout(0.2)
        self._ready.set()
        while True:
            try:
                chunk = self.conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return
            self.received.extend(chunk)

    def wait(self, timeout=5.0):
        assert self._ready.wait(timeout), "nothing connected"

    def send(self, data: bytes):
        self.wait()
        self.conn.sendall(data)

    def hang_up(self):
        self.wait()
        self.conn.close()

    def close(self):
        for s in (self.conn, self.sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


@pytest.fixture
def linked():
    far = FarEnd()
    link = SpaceLink(port=far.port)
    link.connect(retries=10)
    far.wait()
    try:
        yield far, link
    finally:
        link.close()
        far.close()


def drain(link, want, timeout=3.0):
    got = []
    deadline = time.time() + timeout
    while len(got) < want and time.time() < deadline:
        got.extend(link.poll())
    return got


def test_a_sent_frame_arrives_wrapped(linked):
    far, link = linked
    frame = b"\x01\x02\x03"
    link.send_frame(frame)
    deadline = time.time() + 3
    while len(far.received) < len(wrap(frame)) and time.time() < deadline:
        time.sleep(0.02)
    assert bytes(far.received) == wrap(frame)


def test_a_frame_split_across_polls_is_still_returned_whole(linked):
    """The deframer's state has to survive between calls.

    Fed per-poll without carrying state, a frame arriving in two packets is returned as nothing.
    The ground station then reports no answer, which is what a dead spacecraft looks like.
    """
    far, link = linked
    frame = bytes(range(40))
    raw = wrap(frame)
    far.send(raw[:5])
    assert link.poll() == [], "a partial frame was returned as if complete"
    time.sleep(0.05)
    far.send(raw[5:])
    assert drain(link, 1) == [frame]


def test_two_frames_in_one_packet_are_both_returned(linked):
    far, link = linked
    a, b = b"\xAA" * 8, b"\xBB" * 12
    far.send(wrap(a) + wrap(b))
    assert drain(link, 2) == [a, b]


def test_a_quiet_link_returns_nothing_and_does_not_raise(linked):
    """The distinction the whole class exists to make: quiet is not closed."""
    far, link = linked
    assert link.poll() == []
    assert link.poll() == []


def test_a_closed_far_end_raises_rather_than_reading_as_quiet(linked):
    far, link = linked
    far.hang_up()
    with pytest.raises(ConnectionError):
        for _ in range(50):
            link.poll()
            time.sleep(0.02)


def test_the_raw_bytes_are_kept_even_when_they_are_not_frames(linked):
    """The docstring calls this forensics: malformed traffic is evidence, not noise.

    An attacker's malformed frame is the interesting one, and a client that only kept what it
    could parse would throw away exactly the traffic worth looking at.
    """
    far, link = linked
    junk = b"\xDE\xAD\xBE\xEF" * 4
    far.send(junk)
    deadline = time.time() + 3
    while len(link.raw_rx) < len(junk) and time.time() < deadline:
        link.poll()
    assert bytes(link.raw_rx).startswith(junk) or junk in bytes(link.raw_rx), (
        "unparseable octets were discarded, so the evidence is gone")


def test_using_the_link_before_connecting_is_an_error():
    link = SpaceLink(port=1)
    with pytest.raises(RuntimeError):
        link.send_frame(b"\x00")
    with pytest.raises(RuntimeError):
        link.poll()


def test_close_is_idempotent_and_makes_later_use_an_error(linked):
    far, link = linked
    link.close()
    link.close()
    with pytest.raises(RuntimeError):
        link.poll()


def test_connect_gives_up_with_the_address_it_could_not_reach():
    """A ConnectionError naming nothing sends the reader to the wrong port."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()                      # nothing is listening there now
    link = SpaceLink(port=port)
    with pytest.raises(ConnectionError) as exc:
        link.connect(retries=1)
    assert str(port) in str(exc.value)
