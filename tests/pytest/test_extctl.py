"""The External Control client, against a server that speaks the documented protocol.

extctl.py is 236 lines of wire format reversed from Renode's own C client, and its docstring
writes that format out byte by byte. Nothing compared the two. A prose specification beside an
implementation is the same arrangement as identity.cmake beside identity.py, and it drifts the
same way - except here the drift is silent in a worse direction: a misparsed reply does not throw,
it returns a plausible number. `sysbus_read` handing back the wrong word looks exactly like a
firmware that wrote the wrong word.

The fake server below implements the protocol as the docstring states it. That makes these tests a
comparison between the two statements of it, not a comparison of the code with itself.
"""
import socket
import struct
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.renode import extctl                       # noqa: E402
from cuberange.renode.extctl import (API_VERSIONS, MAGIC, RC_ASYNC_EVENT,  # noqa: E402
                                     RC_COMMAND_FAILED, RC_FATAL_ERROR,
                                     RC_INVALID_COMMAND, RC_SUCCESS_HANDSHAKE,
                                     RC_SUCCESS_WITH_DATA, Renode, RenodeError)

HANDSHAKE_LEN = 2 + 2 * len(API_VERSIONS)


class FakeRenode:
    """Accepts one client, records the handshake, and replies with a scripted script."""

    def __init__(self, script=b"", prefix=b"", ack=True):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.handshake = b""
        self.requests = bytearray()
        self._script, self._prefix, self._ack = script, prefix, ack
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        self.conn = conn
        conn.settimeout(5)
        try:
            while len(self.handshake) < HANDSHAKE_LEN:
                chunk = conn.recv(HANDSHAKE_LEN - len(self.handshake))
                if not chunk:
                    return
                self.handshake += chunk
            conn.sendall(self._prefix)
            if self._ack:
                conn.sendall(bytes([RC_SUCCESS_HANDSHAKE]))
            else:
                conn.sendall(bytes([RC_INVALID_COMMAND]))
                return
            conn.sendall(self._script)
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                self.requests.extend(chunk)
        except OSError:
            return

    def close(self):
        for s in (getattr(self, "conn", None), self.sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def gpio_event(ed=7, ts=123, state=True) -> bytes:
    data = struct.pack("<Q?", ts, state)
    return bytes([RC_ASYNC_EVENT, extctl.CMD_GPIO]) + struct.pack("<II", ed, len(data)) + data


def documented_handshake() -> bytes:
    """The handshake literal written in extctl.py's own docstring, parsed back to bytes.

    "06 00 | 01 00 | 02 00 | 03 00 | 04 00 | 05 01 | 06 00"
    """
    for line in (extctl.__doc__ or "").splitlines():
        if line.strip().startswith("06 00 |"):
            return bytes(int(b, 16) for b in line.replace("|", " ").split())
    pytest.fail("extctl.py's docstring no longer states the handshake bytes")


# --------------------------------------------------------------------------- the handshake

def test_the_client_sends_exactly_the_handshake_its_docstring_documents():
    """Prose against implementation, the identity.cmake arrangement.

    Either can be edited alone. Renode rejects a wrong handshake loudly, so the failure is not
    subtle - but the docstring going stale is, and it is the only specification of this protocol
    anyone here has.
    """
    far = FakeRenode()
    try:
        Renode(far.port).close()
        assert far.handshake == documented_handshake(), (
            f"sent {far.handshake.hex(' ')}, documented {documented_handshake().hex(' ')}")
    finally:
        far.close()


def test_a_rejected_handshake_raises_with_the_code():
    far = FakeRenode(ack=False)
    try:
        with pytest.raises(RenodeError) as exc:
            Renode(far.port)
        assert str(RC_INVALID_COMMAND) in str(exc.value)
    finally:
        far.close()


def test_orphan_events_before_the_ack_are_skipped_not_mistaken_for_a_mismatch():
    """A documented Renode behaviour with a documented consequence.

    A control client that died mid-run_for leaves its GPIO registrations alive, and the server
    pushes those events onto the NEXT socket before the handshake ack. Renode's own C library
    reports that as "API command version mismatch" - a message about the wrong thing entirely.
    """
    far = FakeRenode(prefix=gpio_event(ed=3) + gpio_event(ed=4))
    try:
        client = Renode(far.port)
        assert [e.ed for e in client.orphan_events] == [3, 4], (
            "the orphan events were not collected, so a reconnect after a crash reports a "
            "version mismatch that is not happening")
        client.close()
    finally:
        far.close()


# --------------------------------------------------------------------------- framing

def test_a_request_is_framed_as_the_docstring_states():
    """'R' 'E' | u8 command | u32 payload_len | payload, little-endian."""
    reply = bytes([RC_SUCCESS_WITH_DATA, extctl.CMD_RUN_FOR]) + struct.pack("<I", 0)
    far = FakeRenode(script=reply)
    try:
        client = Renode(far.port)
        client.run_for(5, extctl.MS)
        deadline = threading.Event()
        for _ in range(100):
            if len(far.requests) >= 2 + 1 + 4:
                break
            deadline.wait(0.02)
        req = bytes(far.requests)
        assert req[:2] == MAGIC == b"RE"
        assert req[2] == extctl.CMD_RUN_FOR
        (length,) = struct.unpack("<I", req[3:7])
        assert length == 8, f"run_for's payload should be one u64, got {length}"
        (value,) = struct.unpack("<Q", req[7:7 + length])
        assert value == 5 * extctl.MS, "the time value is not little-endian microseconds"
        client.close()
    finally:
        far.close()


# --------------------------------------------------------------------------- error paths

@pytest.mark.parametrize("rc,needle", [
    (RC_FATAL_ERROR, "FATAL_ERROR"),
    (RC_COMMAND_FAILED, "COMMAND_FAILED"),
    (RC_INVALID_COMMAND, "INVALID_COMMAND"),
])
def test_every_error_code_raises_rather_than_returning_a_value(rc, needle):
    """A return code read as data is the failure mode that does not announce itself."""
    if rc == RC_FATAL_ERROR:
        msg = b"something went wrong"
        script = bytes([rc]) + struct.pack("<I", len(msg)) + msg
    elif rc == RC_COMMAND_FAILED:
        msg = b"no such machine"
        script = bytes([rc, extctl.CMD_GET_MACHINE]) + struct.pack("<I", len(msg)) + msg
    else:
        script = bytes([rc, extctl.CMD_GET_MACHINE])

    far = FakeRenode(script=script)
    try:
        client = Renode(far.port)
        with pytest.raises(RenodeError) as exc:
            client.get_machine("nosuch")
        assert needle in str(exc.value)
        client.close()
    finally:
        far.close()


def test_a_truncated_reply_raises_instead_of_returning_short_data():
    """recv returns what it has. A client that trusted one read would return a partial word."""
    far = FakeRenode(script=bytes([RC_SUCCESS_WITH_DATA, extctl.CMD_GET_TIME])
                     + struct.pack("<I", 8) + b"\x01\x02")     # promises 8, sends 2
    try:
        client = Renode(far.port, timeout=1.0)
        with pytest.raises((RenodeError, OSError)):
            client.get_current_time_us()
        client.close()
    finally:
        far.close()
