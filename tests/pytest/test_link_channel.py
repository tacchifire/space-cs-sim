"""The channel plane, driven over real loopback sockets and no emulator.

LinkChannel is what an EX-L01 student attaches to: a TCP proxy between the ground station and
COMM's UART socket that records every frame and can transmit any of them again. Its docstring
makes a strong claim - "what the attacker captures is exactly what the satellite would have
accepted" - and nothing tested it. A capture that loses a frame, or a replay that re-encodes one,
would not fail an exercise loudly; it would make the mitigation look stronger than it is, which
is the direction of error this repository cares about most.

Real sockets rather than fakes, because the properties at issue are socket properties: frames
arriving split across recv boundaries, and two threads writing the same connection.
"""
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.channel.link_channel import LinkChannel   # noqa: E402
from cuberange.proto.frame import wrap                   # noqa: E402


class FakeSatellite:
    """A socket that accepts one connection, records everything, and can send on demand."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(10)
        self.port = self.sock.getsockname()[1]
        self.received = bytearray()
        self.conn = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            self.conn, _ = self.sock.accept()
        except OSError:
            return
        self.conn.settimeout(0.2)
        while not self._stop.is_set():
            try:
                chunk = self.conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.received.extend(chunk)

    def send(self, data: bytes):
        deadline = time.time() + 5
        while self.conn is None and time.time() < deadline:
            time.sleep(0.02)
        assert self.conn is not None, "the channel never connected to the satellite"
        self.conn.sendall(data)

    def wait_for(self, n: int, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.received) >= n:
                return True
            time.sleep(0.02)
        return len(self.received) >= n

    def close(self):
        self._stop.set()
        for s in (self.conn, self.sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self._thread.join(timeout=3)


@pytest.fixture
def wired():
    """A satellite, a channel in front of it, and a client attached to the channel."""
    sat = FakeSatellite()
    chan = LinkChannel(listen_port=0, sat_port=sat.port)
    chan.start(connect_retries=20)
    port = chan._server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    client.settimeout(5)
    try:
        yield sat, chan, client
    finally:
        client.close()
        chan.stop()
        sat.close()


def test_an_uplink_frame_reaches_the_satellite_byte_for_byte(wired):
    sat, chan, client = wired
    frame = b"\x01\x02\x03\x04\x05"
    client.sendall(wrap(frame))
    assert sat.wait_for(len(wrap(frame))), "the channel did not forward the uplink"
    assert bytes(sat.received) == wrap(frame)
    assert chan.wait_for_uplink(1, timeout=5)
    assert chan.uplink_frames == [frame], (
        "the captured frame is not the frame that was sent, so a replay would not be a replay")


def test_a_frame_split_across_recv_boundaries_is_captured_whole(wired):
    """The property the whole capture rests on, and the easiest to lose.

    A student's ground station writes whenever it likes; TCP delivers whenever it likes. If the
    deframer were fed per-recv without carrying state, a frame split over two packets would be
    captured as nothing or as two halves, and the exercise would report that the attacker saw no
    traffic - which is not a firmware fact.
    """
    sat, chan, client = wired
    frame = bytes(range(64))
    raw = wrap(frame)
    for i in range(0, len(raw), 3):          # three octets at a time, with gaps
        client.sendall(raw[i:i + 3])
        time.sleep(0.01)
    assert chan.wait_for_uplink(1, timeout=5), (
        f"nothing was captured from a frame delivered in pieces; got {chan.uplink_frames}")
    assert chan.uplink_frames == [frame]
    assert bytes(sat.received) == raw, "the pieces did not reach the satellite unchanged"


def test_two_frames_in_one_write_are_captured_as_two(wired):
    sat, chan, client = wired
    a, b = b"\xAA" * 10, b"\xBB" * 20
    client.sendall(wrap(a) + wrap(b))
    assert chan.wait_for_uplink(2, timeout=5), chan.uplink_frames
    assert chan.uplink_frames == [a, b]


def test_a_downlink_reaches_the_client_and_is_recorded(wired):
    sat, chan, client = wired
    frame = b"\x10\x20\x30"
    sat.send(wrap(frame))
    got = bytearray()
    deadline = time.time() + 5
    while len(got) < len(wrap(frame)) and time.time() < deadline:
        try:
            got.extend(client.recv(4096))
        except socket.timeout:
            break
    assert bytes(got) == wrap(frame), "the downlink did not reach the ground station"
    deadline = time.time() + 5
    while not chan.downlink_frames and time.time() < deadline:
        time.sleep(0.02)
    assert chan.downlink_frames == [frame]


def test_replay_transmits_the_captured_octets_and_not_a_re_encoding(wired):
    """The docstring's claim: no re-encoding.

    Re-encoding would quietly repair a frame the attacker never parsed, and a mitigation that
    depends on a field the attacker mangled would then look stronger than it is.
    """
    sat, chan, client = wired
    frame = bytes([0x00, 0xA9, 0x00, 0x07, 0x07, 0x01, 0x80, 0x2E])
    client.sendall(wrap(frame))
    assert chan.wait_for_uplink(1, timeout=5)
    before = len(sat.received)

    chan.replay(chan.uplink_frames[0])
    assert sat.wait_for(before + len(wrap(frame))), "the replay never reached the satellite"
    assert bytes(sat.received[before:]) == wrap(frame), (
        "the replayed octets differ from the captured ones")


def test_the_transmit_lock_keeps_two_writers_from_interleaving():
    """Two threads write the satellite socket: the proxy's own forward, and the attacker's replay.

    Driven against the lock directly rather than over a socket. Forcing a real sendall to split
    means overflowing the kernel send buffer, which depends on the host's SO_SNDBUF and on the
    receiver not draining - a test that passes because the buffer was large enough is a test that
    measured the buffer. The recorded intervals below cannot be misread.

    What this does NOT claim: that a whole uplink FRAME is atomic against a replay. The proxy
    forwards the uplink in per-recv chunks, so a replay can land between two of them, and that is
    the right behaviour - two transmitters on one radio channel do collide, and pretending
    otherwise would make the channel plane less honest than the vacuum it models. The lock's job
    is narrower: no single write is torn by another.
    """
    import cuberange.channel.link_channel as lc

    class RecordingSocket:
        def __init__(self, log, gap):
            self.log, self.gap = log, gap

        def sendall(self, data):
            tag = chr(data[0])
            self.log.append(f"start-{tag}")
            time.sleep(self.gap)
            self.log.append(f"end-{tag}")

    def run(with_lock: bool) -> list:
        log = []
        chan = lc.LinkChannel(listen_port=0, sat_port=1)
        chan._sat = RecordingSocket(log, gap=0.05)
        if not with_lock:
            class _NoLock:
                def __enter__(self_inner): return None
                def __exit__(self_inner, *a): return False
            chan._tx_lock = _NoLock()
        threads = [threading.Thread(target=chan._to_satellite, args=(b"A" * 4,)),
                   threading.Thread(target=chan._to_satellite, args=(b"B" * 4,))]
        for t in threads:
            t.start()
            time.sleep(0.01)          # the second writer arrives while the first is mid-write
        for t in threads:
            t.join(timeout=5)
        return log

    def nested(log) -> bool:
        depth, seen = 0, False
        for entry in log:
            depth += 1 if entry.startswith("start") else -1
            seen = seen or depth > 1
        return seen

    assert not nested(run(with_lock=True)), (
        "two writes overlapped despite the transmit lock")

    # The control. Without it the test above would pass against a deleted lock, which is the
    # failure this repository keeps finding in its own concurrency work.
    assert nested(run(with_lock=False)), (
        "the two writers did not overlap even without the lock, so the test above proves nothing "
        "- the timing no longer creates a window")


def test_stop_leaves_no_threads_running():
    """An exercise that starts a channel per attempt would otherwise accumulate them."""
    sat = FakeSatellite()
    # Compared by identity, not by count. The first version counted threads, and the fake
    # satellite's own server thread exits when the channel disconnects - so the total went DOWN
    # and the assertion reported "-1 threads survived".
    before = set(threading.enumerate())
    chan = LinkChannel(listen_port=0, sat_port=sat.port)
    chan.start(connect_retries=20)
    started = set(threading.enumerate()) - before
    assert started, "start() created no threads"

    chan.stop()
    deadline = time.time() + 5
    while any(t.is_alive() for t in started) and time.time() < deadline:
        time.sleep(0.05)
    alive = [t.name for t in started if t.is_alive()]
    assert not alive, f"channel thread(s) survived stop(): {alive}"
    sat.close()


# --------------------------------------------------------------------------- more than one station

def test_two_ground_stations_attach_at_once(wired):
    """Renode's socket terminal serves exactly one client, which is why this proxy has to serve
    more. Without it the range can have two ground station identities and never two nodes."""
    sat, chan, first = wired
    port = chan._server.getsockname()[1]
    second = socket.create_connection(("127.0.0.1", port), timeout=5)
    second.settimeout(5)
    try:
        deadline = time.time() + 5
        while chan.attached < 2 and time.time() < deadline:
            time.sleep(0.02)
        assert chan.attached == 2, (
            f"only {chan.attached} station(s) attached; the second is sitting in the accept "
            f"backlog, which is one station taking turns rather than two stations")
    finally:
        second.close()


def test_both_stations_reach_the_satellite(wired):
    sat, chan, first = wired
    port = chan._server.getsockname()[1]
    second = socket.create_connection(("127.0.0.1", port), timeout=5)
    second.settimeout(5)
    try:
        a, b = b"\x11" * 6, b"\x22" * 6
        first.sendall(wrap(a))
        assert chan.wait_for_uplink(1, timeout=5)
        second.sendall(wrap(b))
        assert chan.wait_for_uplink(2, timeout=5), chan.uplink_frames
        assert set(chan.uplink_frames) == {a, b}
        assert sat.wait_for(len(wrap(a)) + len(wrap(b)))
    finally:
        second.close()


def test_a_downlink_is_heard_by_every_attached_station(wired):
    """A downlink is a transmission, not a reply to whoever spoke last.

    A second site taking telemetry from a pass it is not commanding is ordinary operations, and a
    channel that unicast to the last talker would make that impossible to teach.
    """
    sat, chan, first = wired
    port = chan._server.getsockname()[1]
    second = socket.create_connection(("127.0.0.1", port), timeout=5)
    second.settimeout(5)
    try:
        deadline = time.time() + 5
        while chan.attached < 2 and time.time() < deadline:
            time.sleep(0.02)
        frame = b"\x77\x88"
        sat.send(wrap(frame))
        for name, sock in (("first", first), ("second", second)):
            got = bytearray()
            end = time.time() + 5
            while len(got) < len(wrap(frame)) and time.time() < end:
                try:
                    got.extend(sock.recv(4096))
                except socket.timeout:
                    break
            assert bytes(got) == wrap(frame), f"the {name} station did not hear the downlink"
    finally:
        second.close()


def test_one_station_leaving_does_not_take_the_other_with_it(wired):
    sat, chan, first = wired
    port = chan._server.getsockname()[1]
    second = socket.create_connection(("127.0.0.1", port), timeout=5)
    second.settimeout(5)
    deadline = time.time() + 5
    while chan.attached < 2 and time.time() < deadline:
        time.sleep(0.02)
    second.close()

    deadline = time.time() + 5
    while chan.attached > 1 and time.time() < deadline:
        time.sleep(0.05)
    assert chan.attached == 1, f"{chan.attached} attached after one left"

    frame = b"\x33" * 4
    first.sendall(wrap(frame))
    assert chan.wait_for_uplink(1, timeout=5), (
        "the surviving station's uplink stopped when the other disconnected")
