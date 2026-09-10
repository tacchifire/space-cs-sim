"""The Monitor lock must be a mechanism, not a comment.

`PowerDomain.read_rail` is two dependent commands - `mach set "EPS"` then a register read - and
with two spacecraft there are two PowerDomains sharing the one Monitor session Renode permits. If
the other one's `mach set` lands in between, the read returns the WRONG machine's register and
nothing anywhere reports it.

The lock was added for that. The problem with adding a lock is that everything passes afterwards
whether or not it works, which is this project's recorded failure mode: a gate that cannot fail.
So these tests interleave two callers on purpose and require the interleaving to be visible when
the lock is absent. The first test asserts corruption WITHOUT the lock - if that assertion ever
stops holding, the tests below have stopped proving anything and should be rewritten rather than
deleted.

A fake socket stands in for Renode. Nothing here needs an emulator: the question is entirely about
whether one caller's commands can be split by another's.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.renode.monitor import Monitor  # noqa: E402


class FakeRenode:
    """Records the order commands arrive in, and answers them the way the Monitor expects.

    The pause inside recv is what makes an unlocked interleaving reproducible rather than
    occasional: it holds each caller between sending its command and receiving its reply, which is
    exactly the window a second `mach set` would slip into.
    """

    def __init__(self, delay: float = 0.01):
        self.log: list[str] = []
        self._pending: list[bytes] = []
        self._delay = delay
        self._guard = threading.Lock()

    def sendall(self, data: bytes) -> None:
        line = data.decode().strip()
        if not line:
            return
        with self._guard:
            self.log.append(line)
            # Echo, then the prompt the Monitor anchors on.
            self._pending.append(data.rstrip(b"\n") + b"\r\nok\r\n(machine) ")

    def recv(self, _n: int) -> bytes:
        time.sleep(self._delay)
        with self._guard:
            return self._pending.pop(0) if self._pending else b"(machine) "

    def settimeout(self, _t) -> None:
        pass

    def close(self) -> None:
        pass


def _monitor(fake: FakeRenode) -> Monitor:
    m = Monitor(port=0)
    m._sock = fake
    return m


# A caller does real work between selecting a machine and reading from it - PowerDomain.read_rail
# calls command() and then read_u32(), which formats a second command string. That gap is the
# window a second caller's `mach set` slips into, so the model has to have one. Both paths below
# take it; only the locked one holds the session across it, which is the whole difference.
GAP = 0.004


def _select_and_read(mon: Monitor, machine: str, rounds: int, use_lock: bool) -> None:
    for _ in range(rounds):
        if use_lock:
            with mon.exclusive():
                mon.command(f'mach set "{machine}"')
                time.sleep(GAP)
                mon.command("sysbus ReadDoubleWord 0x58020C14")
        else:
            mon.command(f'mach set "{machine}"')
            time.sleep(GAP)
            mon.command("sysbus ReadDoubleWord 0x58020C14")


def _interleavings(log: list[str]) -> int:
    """How many reads followed a `mach set` for a DIFFERENT machine than their own caller's.

    Reconstructed from the arrival order: a read is attributed to whichever `mach set` most
    recently preceded it, so a pair that got split shows up as two reads after one select.
    """
    selected = None
    reads_since_select = 0
    split = 0
    for line in log:
        if line.startswith("mach set"):
            selected = line
            reads_since_select = 0
        elif line.startswith("sysbus ReadDoubleWord"):
            reads_since_select += 1
            if selected is None or reads_since_select > 1:
                split += 1
    return split


def _run_pair(use_lock: bool, rounds: int = 25) -> int:
    fake = FakeRenode()
    mon = _monitor(fake)
    threads = [threading.Thread(target=_select_and_read, args=(mon, name, rounds, use_lock))
               for name in ("SAT0_EPS", "SAT1_EPS")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a caller never finished - possible deadlock"
    return _interleavings(fake.log)


def test_without_the_lock_two_callers_do_interleave():
    """The control. If this ever stops failing, the test below proves nothing.

    Bypassing `exclusive()` is the closest this can get to deleting the lock without editing the
    module, and it reproduces exactly the corruption the lock exists to prevent.
    """
    split = _run_pair(use_lock=False)
    assert split > 0, (
        "two unsynchronised callers did not interleave even once, so this test can no longer tell "
        "a working lock from a missing one. Increase the rounds or the FakeRenode delay rather "
        "than deleting the assertion.")


def test_with_exclusive_the_pairs_stay_together():
    assert _run_pair(use_lock=True) == 0, (
        "a read was split from its `mach set` despite exclusive(); with two spacecraft that "
        "returns the wrong machine's register and reports nothing")


def test_exclusive_is_reentrant():
    """A caller holding the session must still be able to use the single-command helpers."""
    mon = _monitor(FakeRenode(delay=0))
    with mon.exclusive():
        with mon.exclusive():
            mon.command("version")


def test_close_is_idempotent_and_does_not_need_a_live_socket():
    mon = _monitor(FakeRenode(delay=0))
    mon.close()
    mon.close()


def test_a_second_session_to_one_endpoint_is_refused():
    """Renode services one client and never the second; hanging is a worse answer than raising."""
    from cuberange.renode.monitor import MonitorError, _LIVE

    addr = ("127.0.0.1", 59998)
    holder = Monitor(port=addr[1])
    _LIVE[addr] = holder
    try:
        with pytest.raises(MonitorError, match="already open"):
            Monitor(port=addr[1]).connect(retries=1)
    finally:
        _LIVE.pop(addr, None)


def test_a_failed_handshake_does_not_leave_the_endpoint_claimed():
    """The registry is claimed before the handshake, so the handshake must clean up after itself.

    A Renode that accepts the TCP connection and then wedges - the ~13% launch failure this project
    already records - would otherwise mark the address taken for the life of the process, and every
    later connect() would refuse with a message naming a cause that is not the real one.
    """
    import socket as _socket
    import threading as _threading

    from cuberange.renode.monitor import _LIVE, Monitor, MonitorError

    # A server that accepts and then says nothing, which is what a wedged Renode looks like.
    srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    held = []
    stop = _threading.Event()

    def accept_and_stall():
        try:
            conn, _ = srv.accept()
            held.append(conn)          # keep it open; never reply
            stop.wait(10)
        except OSError:
            pass

    t = _threading.Thread(target=accept_and_stall, daemon=True)
    t.start()
    try:
        with pytest.raises(Exception):
            Monitor(port=port, timeout=1.0).connect(retries=1)

        assert ("127.0.0.1", port) not in _LIVE, (
            "the endpoint is still marked as taken after a failed handshake; a second Monitor to "
            "it would be refused for a reason that is not true and cannot be cleared")

        # And the proof that it matters: a second attempt must fail on the handshake again, not on
        # the registry. Same exception type either way, so compare the message.
        with pytest.raises(Exception) as exc:
            Monitor(port=port, timeout=1.0).connect(retries=1)
        assert "already open in this process" not in str(exc.value), (
            f"the second attempt was refused by the registry rather than by the wedged peer: "
            f"{exc.value}")
    finally:
        stop.set()
        for c in held:
            c.close()
        srv.close()
        t.join(timeout=5)
