"""The channel plane: the vacuum between the ground station and the spacecraft.

A TCP proxy sits between the ground station and COMM's UART socket. Everything crossing it is
recorded as frames, and anything can be injected in either direction. That is not a convenience -
it is the honest model. An attacker on a radio link sees frames and can transmit frames; it does
not get to call functions inside the ground station or the satellite.

Capture is by frame rather than by byte because a replay attacker needs whole frames. The deframer
is the same one the ground station and the firmware use, so what the attacker captures is exactly
what the satellite would have accepted.

Not modelled yet, and named here so nobody mistakes this for a channel model: propagation delay,
bit errors, pass windows, and Doppler. EX-L01 needs none of them; EX-L02 will.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import List, Optional

from ..proto.frame import Deframer, wrap


class LinkChannel:
    def __init__(self, listen_port: int, sat_host: str = "127.0.0.1", sat_port: int = 3777,
                 listen_host: str = "127.0.0.1"):
        self.listen_addr = (listen_host, listen_port)
        self.sat_addr = (sat_host, sat_port)

        self.uplink_frames: List[bytes] = []      # ground -> satellite
        self.downlink_frames: List[bytes] = []    # satellite -> ground
        self.uplink_bytes = bytearray()
        self.downlink_bytes = bytearray()

        self._up_deframer = Deframer()
        self._down_deframer = Deframer()
        self._sat: Optional[socket.socket] = None
        self._client: Optional[socket.socket] = None
        self._server: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._tx_lock = threading.Lock()

    # ---------------------------------------------------------------- lifecycle
    def start(self, connect_retries: int = 60) -> "LinkChannel":
        last: Optional[OSError] = None
        for _ in range(connect_retries):
            try:
                self._sat = socket.create_connection(self.sat_addr, timeout=3)
                break
            except OSError as exc:
                last = exc
                time.sleep(0.5)
        else:
            raise ConnectionError(f"satellite link {self.sat_addr} never came up") from last
        self._sat.settimeout(0.2)

        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(self.listen_addr)
        self._server.listen(1)
        self._server.settimeout(0.5)

        self._threads = [threading.Thread(target=self._accept_loop, daemon=True),
                         threading.Thread(target=self._downlink_loop, daemon=True)]
        for t in self._threads:
            t.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for sock in (self._client, self._sat, self._server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        for t in self._threads:
            t.join(timeout=2)

    # ---------------------------------------------------------------- plumbing
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except (socket.timeout, OSError):
                continue
            client.settimeout(0.2)
            self._client = client
            self._uplink_loop(client)

    def _uplink_loop(self, client: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                chunk = client.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.uplink_bytes.extend(chunk)
            self.uplink_frames.extend(self._up_deframer.feed(chunk))
            self._to_satellite(chunk)
        self._client = None

    def _downlink_loop(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._sat.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.downlink_bytes.extend(chunk)
            self.downlink_frames.extend(self._down_deframer.feed(chunk))
            client = self._client
            if client is not None:
                try:
                    client.sendall(chunk)
                except OSError:
                    pass

    def _to_satellite(self, raw: bytes) -> None:
        with self._tx_lock:
            if self._sat is not None:
                self._sat.sendall(raw)

    # ---------------------------------------------------------------- attacker
    def replay(self, frame: bytes) -> None:
        """Transmit a captured frame again, exactly as it was.

        No re-encoding: a replay attacker does not need to understand the frame, and re-encoding
        would quietly hide a mitigation that depends on a field the attacker never parsed.
        """
        self._to_satellite(wrap(frame))

    def wait_for_uplink(self, count: int, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.uplink_frames) >= count:
                return True
            time.sleep(0.05)
        return False
