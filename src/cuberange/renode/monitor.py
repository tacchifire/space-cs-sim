"""Renode Monitor over TCP.

Every hazard handled here was measured, not guessed:

  - One client per Renode process, and there is no reconnect. A second connection is accepted by
    TCP and then never serviced until the first disconnects, so exactly one host component may
    own this channel.
  - Only the FIRST connection after Renode starts sends a banner and a prompt. Every later
    connection sends just the telnet IAC bytes and stays silent until a command arrives, so a
    client that waits for a prompt on connect hangs forever. We send a sync command immediately.
  - A line must be terminated before closing. Sixty disconnects that each left a partial line
    wedged the listener permanently at cycle 54, with Renode alive but no longer accepting.
  - The Monitor blocks for the whole duration of `emulation RunFor`, so this must not be the
    channel a live UI reads from.
  - Scientific notation is silently corrupted: '1e3' becomes 1, '1.5e-2' becomes 1.5, '0b101'
    becomes 0, and '.5' is a syntax error. Numbers are formatted fixed-point on the way out.
  - A batch aborts at the first failing command, silently, and the remaining replies shift. Batch
    callers must count the fields they get back.
  - Any property getter can take the whole process down - `usart3 BaudRate` raises an unhandled
    DivideByZeroException on an unconfigured UART. Keep to commands known to be safe.
"""
from __future__ import annotations

import re
import socket
import time
from typing import List, Optional

PROMPT = re.compile(rb"(?:^|[\r\n])\(([^()\r\n]*)\) $")


class MonitorError(RuntimeError):
    pass


class Monitor:
    def __init__(self, host: str = "127.0.0.1", port: int = 3778, timeout: float = 10.0):
        self._addr = (host, port)
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None

    def connect(self, retries: int = 60) -> "Monitor":
        last: Optional[OSError] = None
        for _ in range(retries):
            try:
                self._sock = socket.create_connection(self._addr, timeout=3)
                self._sock.settimeout(self._timeout)
                break
            except OSError as exc:
                last = exc
                time.sleep(0.5)
        else:
            raise MonitorError(f"Renode Monitor {self._addr} never came up") from last
        # Reconnects are silent, so never wait for an unprompted banner - ask for something.
        self.command("version")
        return self

    def _recv(self, buf: bytes, deadline: float) -> bytes:
        assert self._sock is not None
        if time.time() >= deadline:
            raise MonitorError(f"Monitor timed out; last bytes {buf[-200:]!r}")
        chunk = self._sock.recv(8192)
        if not chunk:
            raise MonitorError("Monitor closed the connection")
        return buf + chunk

    def command(self, line: str) -> str:
        """Send one command and return its output.

        Waiting for "a prompt" is not enough. The Monitor writes the prompt and the echo of the
        command as separate chunks, so the moment the buffer ends with "(EPS) " the prompt regex
        matches and a naive reader returns before the result has arrived - the result then shows up
        at the head of the NEXT command's output. Anchoring on the echo of our own command first,
        and only then looking for the following prompt, removes the race.
        """
        if self._sock is None:
            raise MonitorError("connect() first")
        if "\n" in line:
            raise MonitorError("use batch() for multiple commands")

        echo = line.encode()
        self._sock.sendall(echo + b"\n")
        deadline = time.time() + self._timeout

        buf = b""
        while echo not in buf:
            buf = self._recv(buf, deadline)
        start = buf.index(echo) + len(echo)

        while not PROMPT.search(buf[start:]):
            buf = self._recv(buf, deadline)

        body = PROMPT.sub(b"", buf[start:], count=1)
        return body.decode(errors="replace").strip()

    def batch(self, lines: List[str]) -> str:
        """Send several commands as one ';'-joined line.

        Six to ten times faster than one round trip each - 52 operations across four nodes cost
        437 ms unbatched and 57 ms batched - but a failure part way through aborts the rest
        SILENTLY, so the caller must validate what came back rather than index into it blindly.
        """
        return self.command("; ".join(lines))

    def read_u32(self, address: int) -> int:
        """Read a peripheral register. Only use on registers without read side effects.

        Monitor reads go through the peripheral model, so polling a read-to-clear register (a UART
        status word, a CAN interrupt register, a timer SR) corrupts firmware state. GPIO ODR, LED
        State and GetGPIOs are safe; most other things are not.
        """
        out = self.command(f"sysbus ReadDoubleWord 0x{address:08X}")
        match = re.search(r"0x([0-9A-Fa-f]+)", out)
        if not match:
            raise MonitorError(f"unparseable register read: {out!r}")
        return int(match.group(1), 16)

    def close(self) -> None:
        if self._sock is not None:
            try:
                # Always finish the line first; a partial line can wedge the listener for good.
                self._sock.sendall(b"\n")
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "Monitor":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()
