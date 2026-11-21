"""The frame transport boundary, proven by running one contract over two different wires.

The point of cuberange.transport is that a caller cannot tell a Renode socket from a serial cable.
That is a claim about behaviour, not about class diagrams, so the conformance test below states the
send/receive expectations once and runs them against both implementations: SpaceLink over a
loopback TCP pair, SerialFrameTransport over an os.openpty() pair. If a third transport ever joins,
it gets the same exercise, unchanged - the day it needs a special case in here, the boundary has
failed at exactly the job it exists for.

The serial side uses a pty rather than a fake because the properties at issue are device properties:
termios raw mode standing between the test and binary frames, and reads that arrive in whatever
chunks the terminal hands out.
"""
import os
import socket
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.gs.link import SpaceLink                       # noqa: E402
from cuberange.proto.frame import wrap                        # noqa: E402
from cuberange.transport import FrameTransport, SerialFrameTransport  # noqa: E402


class TcpFarEnd:
    """The satellite end of a loopback TCP pair: a listening socket SpaceLink connects to."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.conn = None

    def accept(self):
        self.conn, _ = self.sock.accept()
        self.conn.settimeout(5)

    def send(self, data: bytes):
        self.conn.sendall(data)

    def recv_exactly(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.conn.recv(n - len(buf))
            assert chunk, "far end closed early"
            buf.extend(chunk)
        return bytes(buf)

    def close(self):
        for s in (self.conn, self.sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


class PtyFarEnd:
    """The satellite end of a pty pair: the master fd SerialFrameTransport never sees."""

    def __init__(self):
        self.master, self.slave = os.openpty()
        self.device = os.ttyname(self.slave)

    def send(self, data: bytes):
        view = memoryview(data)
        while view:
            view = view[os.write(self.master, view):]

    def recv_exactly(self, n: int) -> bytes:
        buf = bytearray()
        deadline = time.monotonic() + 5
        while len(buf) < n:
            assert time.monotonic() < deadline, "timed out waiting for the far end"
            try:
                chunk = os.read(self.master, n - len(buf))
            except BlockingIOError:
                time.sleep(0.01)
                continue
            assert chunk, "far end closed early"
            buf.extend(chunk)
        return bytes(buf)

    def close(self):
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


def exercise_contract(transport, far):
    """The shared expectations: framing on the wire, frames from poll(), quiet is not dead."""
    frame_out = b"\xde\xad\xbe\xef telecommand"
    frame_in = b"\x01\x02\x03 telemetry reply"

    transport.send_frame(frame_out)
    assert far.recv_exactly(len(wrap(frame_out))) == wrap(frame_out)

    # A frame split across two writes must still come back whole: poll() carries deframer state.
    wire = wrap(frame_in)
    far.send(wire[:5])
    assert transport.poll() == []
    far.send(wire[5:])
    received = []
    deadline = time.monotonic() + 5
    while not received and time.monotonic() < deadline:
        received = transport.poll()
    assert received == [frame_in]

    # A quiet wire is [], not an error and not a hang.
    assert transport.poll() == []


@pytest.fixture(params=["socket", "serial"])
def wired(request):
    if request.param == "socket":
        far = TcpFarEnd()
        transport = SpaceLink(port=far.port)
        transport.connect(retries=10)
        far.accept()
    else:
        far = PtyFarEnd()
        transport = SerialFrameTransport(far.device)
        transport.connect()
    yield transport, far
    transport.close()
    far.close()


def test_both_wires_meet_the_same_contract(wired):
    transport, far = wired
    exercise_contract(transport, far)


def test_spacelink_is_a_frame_transport():
    link = SpaceLink()
    assert isinstance(link, FrameTransport)
    assert link.capabilities == frozenset({"socket"})


def test_serial_is_a_frame_transport():
    transport = SerialFrameTransport("/dev/null")
    assert isinstance(transport, FrameTransport)
    assert transport.capabilities == frozenset({"serial"})


def test_frame_transport_cannot_be_instantiated():
    with pytest.raises(TypeError):
        FrameTransport()


def test_serial_refuses_an_unknown_baud_rate():
    with pytest.raises(ValueError):
        SerialFrameTransport("/dev/null", baudrate=12345)


def test_serial_use_before_connect_is_a_runtime_error():
    transport = SerialFrameTransport("/dev/null")
    with pytest.raises(RuntimeError):
        transport.send_frame(b"x")
    with pytest.raises(RuntimeError):
        transport.poll()
    transport.close()          # closing an unopened transport is not an error
    transport.close()


def test_serial_poll_reports_a_dead_wire_as_connection_error():
    far = PtyFarEnd()
    transport = SerialFrameTransport(far.device)
    transport.connect()
    os.close(far.master)       # the far end vanishes; reads now fail instead of returning data
    try:
        with pytest.raises(ConnectionError):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                transport.poll()
    finally:
        transport.close()
        os.close(far.slave)
