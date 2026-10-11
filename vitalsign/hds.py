"""Receive heart rate from the Health Data Server (HDS) watch app.

HDS (Apple Watch, also Wear OS) can send each reading straight to a
computer on your network instead of to its cloud: in the watch app, set
the server address to this machine's IP and the port below. It sends
``PUT /`` with ``{"data": "heartRate:72"}`` -- the same thing its own
desktop receiver, hds_desktop, accepts. No account, cloud or Pulsoid.

HDS has no authentication, so anything that can reach the port could
send readings. Three limits keep that to your own devices: only senders
from private/LAN/Tailscale address ranges are accepted (see
DEFAULT_ALLOWED_NETWORKS); requests from web pages are refused (see
_read_request); and the port only ever accepts small heart-rate messages. It never serves your readings back to anyone; the
overlay still gets them only over the localhost WebSocket.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
from collections.abc import Callable, Sequence

from . import merge
from .protocols import HDS_DEFAULT_PORT, Vitals, parse_hds_body

log = logging.getLogger("bridge.hds")

DEFAULT_ALLOWED_NETWORKS = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "fe80::/10",
    "fc00::/7",  # IPv6 unique-local, includes Tailscale's fd7a:115c:a1e0::/48
    "100.64.0.0/10",  # carrier-grade NAT range, which Tailscale uses for its 100.x addresses
)

_MAX_HEADER_BYTES = 8192
_MAX_BODY_BYTES = 4096
_READ_TIMEOUT = 10.0


class HdsSource:
    def __init__(
        self,
        emit: Callable[[str, Vitals], None],
        set_state: Callable[[str], None],
        *,
        host: str | None = None,
        port: int = HDS_DEFAULT_PORT,
        allowed_networks: Sequence[str] = DEFAULT_ALLOWED_NETWORKS,
        fresh_seconds: float = merge.DEFAULT_FRESH_SECONDS,
        label: str = "HDS watch",
    ) -> None:
        self._emit = emit
        self._set_state = set_state
        self.host = host
        self.port = port
        self._allowed = [ipaddress.ip_network(n, strict=False) for n in allowed_networks]
        self._fresh = fresh_seconds
        self.label = label
        self._quiet_timer: asyncio.TimerHandle | None = None
        self.bound_port: int | None = None

    async def run(self) -> None:
        # host=None listens on every interface, IPv4 and IPv6: the watch
        # reaches this machine over Wi-Fi (or Tailscale), never localhost.
        server = await asyncio.start_server(self._handle, self.host, self.port, limit=_MAX_HEADER_BYTES)
        self.bound_port = server.sockets[0].getsockname()[1]
        log.info(f"Listening for the HDS watch app on port {self.bound_port}")
        self._set_state(merge.WAITING)
        try:
            async with server:
                await server.serve_forever()
        finally:
            if self._quiet_timer:
                self._quiet_timer.cancel()

    def _allowed_peer(self, host: str) -> bool:
        try:
            addr = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            return False
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return any(addr in net for net in self._allowed)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        host = peer[0] if peer else ""
        try:
            # Read the (size-capped) request before answering even a
            # refusal: closing a socket with unread input makes the kernel
            # send a reset, and the client would never see the status.
            allowed = self._allowed_peer(host)
            async with asyncio.timeout(_READ_TIMEOUT):
                status, reason, body = await self._read_request(reader, writer if allowed else None)
            if not allowed:
                log.warning(f"HDS: rejected a request from {host} (not a private/LAN/Tailscale address)")
                await _respond(writer, 403, "Forbidden")
                return
            if status != 200:
                await _respond(writer, status, reason)
                return
            vitals = parse_hds_body(body)
            await _respond(writer, 200, "OK")
            if vitals is not None:
                self._received(vitals)
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    @staticmethod
    async def _read_request(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter | None
    ) -> tuple[int, str, bytes]:
        """Read one request. ``writer`` is used only to send 100 Continue."""
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            return 400, "Bad Request", b""
        # PUT only, as HDS sends: a browser has to ask permission (a CORS
        # preflight, which this server never grants) before sending a
        # cross-site PUT, but not before a plain-text POST, so accepting
        # POST would let any web page you visit post fake readings.
        if parts[0] != "PUT":
            return 405, "Method Not Allowed", b""
        headers = {}
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        # Browsers always label cross-origin requests with Origin; the
        # watch app is not a browser and sends none.
        if "origin" in headers:
            return 403, "Forbidden", b""
        if "chunked" in headers.get("transfer-encoding", "").lower():
            return 411, "Length Required", b""
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            return 400, "Bad Request", b""
        if length < 0:
            return 400, "Bad Request", b""
        if length > _MAX_BODY_BYTES:
            return 413, "Payload Too Large", b""
        if writer is not None and headers.get("expect", "").lower() == "100-continue":
            writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
            await writer.drain()
        return 200, "OK", await reader.readexactly(length)

    def _received(self, vitals: Vitals) -> None:
        self._emit(self.label, vitals)
        self._set_state(merge.CONNECTED)
        # Drop back to "waiting" once the watch goes quiet, so the
        # overlay's status reflects reality instead of a stale "connected".
        if self._quiet_timer:
            self._quiet_timer.cancel()
        self._quiet_timer = asyncio.get_running_loop().call_later(self._fresh, self._set_state, merge.WAITING)


async def _respond(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii"))
    await writer.drain()
