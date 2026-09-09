"""TCP client for the space link.

The far end is a Renode socket terminal attached to the COMM node's usart2. It must have been
created with telnetMode=false; with the default true, Renode prepends 11 telnet IAC bytes and the
first frame is unparseable.
"""
from __future__ import annotations

import socket
import time
from typing import Optional

from ..proto.frame import Deframer, wrap
from .. import ports


class SpaceLink:
    def __init__(self, host: str = "127.0.0.1", port: int = ports.link(0),
                 timeout: float = 0.2):
        self._addr = (host, port)
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._deframer = Deframer()
        self.raw_rx = bytearray()      # kept for forensics: malformed traffic is evidence

    def connect(self, retries: int = 60) -> None:
        last: Optional[OSError] = None
        for _ in range(retries):
            try:
                self._sock = socket.create_connection(self._addr, timeout=3)
                self._sock.settimeout(self._timeout)
                return
            except OSError as exc:
                last = exc
                time.sleep(0.5)
        raise ConnectionError(f"space link {self._addr} never came up") from last

    def send_frame(self, frame: bytes) -> None:
        if self._sock is None:
            raise RuntimeError("connect() first")
        self._sock.sendall(wrap(frame))

    def poll(self) -> list:
        """Return any complete frames received since the last call. Never blocks for long."""
        if self._sock is None:
            raise RuntimeError("connect() first")
        try:
            chunk = self._sock.recv(4096)
        except socket.timeout:
            return []
        if not chunk:
            raise ConnectionError("space link closed by the far end")
        self.raw_rx.extend(chunk)
        return self._deframer.feed(chunk)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
