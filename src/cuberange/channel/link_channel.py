"""The channel plane: the vacuum between the ground station and the spacecraft.

A TCP proxy sits between the ground station and COMM's UART socket. Everything crossing it is
recorded as frames, and anything can be injected in either direction. That is not a convenience -
it is the honest model. An attacker on a radio link sees frames and can transmit frames; it does
not get to call functions inside the ground station or the satellite.

Capture is by frame rather than by byte because a replay attacker needs whole frames. The deframer
is the same one the ground station and the firmware use, so what the attacker captures is exactly
what the satellite would have accepted.

MORE THAN ONE GROUND STATION can attach. A spacecraft is talked to by several sites, and a
socket terminal in Renode accepts exactly one client - so without this the range could have two
ground station identities and never two ground station NODES. Every attached station's uplink is
forwarded to the satellite; every downlink is broadcast to all of them, which is what a radio
does. Two stations transmitting at once interleave, and that is not a defect: two transmitters on
one channel collide. The transmit lock keeps a single write from being torn, and nothing pretends
to more than that.

Not modelled yet, and named here so nobody mistakes this for a channel model: propagation delay,
bit errors, pass windows, and Doppler. EX-L01 needs none of them; EX-L02 will.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import List, Optional

from ..proto.frame import Deframer, wrap
from .. import ports


class LinkChannel:
    def __init__(self, listen_port: int, sat_host: str = "127.0.0.1", sat_port: int = ports.link(0),
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
        self._clients: List[socket.socket] = []
        self._clients_lock = threading.Lock()
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
        self._server.listen(8)
        self._server.settimeout(0.5)

        self._threads = [threading.Thread(target=self._accept_loop, daemon=True),
                         threading.Thread(target=self._downlink_loop, daemon=True)]
        for t in self._threads:
            t.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._clients_lock:
            closing = list(self._clients)
            self._clients.clear()
        for sock in closing + [self._sat, self._server]:
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
            with self._clients_lock:
                self._clients.append(client)
            # Its own thread: serving one client inline meant the second ground station sat in
            # the accept backlog until the first disconnected, which is not two stations.
            t = threading.Thread(target=self._uplink_loop, args=(client,), daemon=True)
            t.start()
            self._threads.append(t)

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
        with self._clients_lock:
            if client in self._clients:
                self._clients.remove(client)
        try:
            client.close()
        except OSError:
            pass

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
            # Broadcast. A downlink is a transmission, not a reply to whoever spoke last: every
            # attached station hears it, which is how a second site takes telemetry from a pass it
            # is not commanding.
            with self._clients_lock:
                attached = list(self._clients)
            for client in attached:
                try:
                    client.sendall(chunk)
                except OSError:
                    pass

    def _to_satellite(self, raw: bytes) -> None:
        with self._tx_lock:
            if self._sat is not None:
                self._sat.sendall(raw)

    @property
    def attached(self) -> int:
        """How many ground stations are on the channel right now."""
        with self._clients_lock:
            return len(self._clients)

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
