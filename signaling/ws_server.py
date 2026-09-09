"""
Minimal WebSocket server implementing RFC 6455, using only Python's
standard library — no `websockets` package, no web framework. This is
the self-written signaling transport: it does nothing but complete the
handshake and pass text frames (JSON messages) back and forth. All the
actual SDP/ICE/room logic lives in sfu/room.py, not here.

STATUS: first draft, not yet run against a real browser. The handshake
and basic text-frame send/receive follow RFC 6455 directly. NOT yet
handling: fragmented frames, ping/pong keepalive, or frames larger than
a single read() — all worth hardening once basic connectivity is proven,
per the build order in docs/ARCHITECTURE.md (step 1: prove this layer
works before any WebRTC is involved).
"""
import asyncio
import base64
import hashlib
import json
import logging

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

logger = logging.getLogger("resync_live.signaling")


class WebSocketConnection:
    """Wraps one accepted TCP connection after the WS handshake completes."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.closed = False

    async def send_json(self, data: dict):
        payload = json.dumps(data).encode("utf-8")
        await self._send_frame(payload)

    async def _send_frame(self, payload: bytes, opcode: int = 0x1):
        # Server-to-client frames are NOT masked (RFC 6455 5.1).
        length = len(payload)
        header = bytearray()
        header.append(0x80 | opcode)  # FIN=1, opcode
        if length <= 125:
            header.append(length)
        elif length <= 0xFFFF:
            header.append(126)
            header += length.to_bytes(2, "big")
        else:
            header.append(127)
            header += length.to_bytes(8, "big")
        self.writer.write(bytes(header) + payload)
        await self.writer.drain()

    async def recv_json(self) -> dict | None:
        """Returns the next parsed JSON text message, or None on close."""
        frame = await self._recv_frame()
        if frame is None:
            return None
        try:
            return json.loads(frame.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Received non-JSON WS frame, ignoring")
            return {}

    async def _recv_frame(self) -> bytes | None:
        header = await self.reader.readexactly(2)
        if not header:
            return None
        fin_opcode, mask_len = header
        opcode = fin_opcode & 0x0F
        masked = bool(mask_len & 0x80)
        length = mask_len & 0x7F

        if opcode == 0x8:  # close frame
            self.closed = True
            return None

        if length == 126:
            length = int.from_bytes(await self.reader.readexactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await self.reader.readexactly(8), "big")

        mask_key = await self.reader.readexactly(4) if masked else b""
        payload = await self.reader.readexactly(length)

        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        return payload

    async def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass


async def _handshake(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, static_html: str = ""
) -> bool:
    """
    Reads the HTTP request line/headers. If it's a WebSocket upgrade,
    completes the 101 handshake and returns True (signaling proceeds).
    If it's a plain GET (a guest's browser just navigating to the
    address), serves `static_html` directly on the SAME port instead -
    this is how a guest actually reaches the join page: one address,
    one port, no separate file to be sent.
    """
    request_line = await reader.readline()
    if not request_line:
        return False

    headers = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b""):
            break
        key, _, value = line.decode("latin-1").partition(":")
        headers[key.strip().lower()] = value.strip()

    is_upgrade = headers.get("upgrade", "").lower() == "websocket"
    ws_key = headers.get("sec-websocket-key")

    if not is_upgrade or not ws_key:
        # Plain HTTP request - a browser loading the page, not our own
        # signaling JS opening a WebSocket. Serve the guest page.
        if static_html:
            body = static_html.encode("utf-8")
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("utf-8") + body
            writer.write(response)
        else:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        return False

    accept = base64.b64encode(
        hashlib.sha1((ws_key + WS_MAGIC).encode("utf-8")).digest()
    ).decode("utf-8")

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    )
    writer.write(response.encode("utf-8"))
    await writer.drain()
    return True


async def serve(host: str, port: int, on_connection, static_html: str = ""):
    """
    Starts the server. `on_connection` is an async callable invoked with
    a WebSocketConnection for each client that completes the WS
    handshake. `static_html`, if given, is served as-is to any plain
    (non-WebSocket) HTTP request on this same port - this is how guests
    load the join page: they visit http://<host>:<port>/ directly.
    """

    async def handle(reader, writer):
        try:
            if await _handshake(reader, writer, static_html):
                conn = WebSocketConnection(reader, writer)
                await on_connection(conn)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, host, port)
    logger.info("Signaling server listening on %s:%d", host, port)
    async with server:
        await server.serve_forever()
