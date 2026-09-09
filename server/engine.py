"""
Wraps the signaling server + Room in a class that runs its own asyncio
event loop on a background thread, so a GUI (Tkinter, running its own
blocking mainloop on the main thread) can start/stop it without the two
event loops fighting each other.

STATUS: first draft, not yet run end-to-end.
"""
import asyncio
import logging
import os
import threading
import time

from signaling.ws_server import serve
from sfu.room import Room

logger = logging.getLogger("resync_live.engine")


class ResyncLiveEngine:
    def __init__(self, output_dir: str, host: str = "0.0.0.0", port: int = 8765):
        self.output_dir = output_dir
        self.host = host
        self.port = port
        self.room: Room | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server_task = None

    def start(self):
        if self._thread is not None:
            return  # already running

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        session_id = time.strftime("%Y-%m-%d_%H%M%S")
        self.room = Room(output_dir=self.output_dir, session_id=session_id)

        async def on_connection(conn):
            identity = None
            try:
                while True:
                    msg = await conn.recv_json()
                    if msg is None:
                        break
                    msg_type = msg.get("type")
                    if msg_type == "offer" and identity is None:
                        identity = msg["identity"]
                        answer = await self.room.handle_join(identity, conn, msg["sdp"])
                        await conn.send_json({"type": "answer", "sdp": answer.sdp})
                    elif msg_type == "answer" and identity is not None:
                        await self.room.handle_renegotiation_answer(identity, msg["sdp"])
                    else:
                        logger.warning("Unexpected message type=%s identity=%s", msg_type, identity)
            finally:
                if identity:
                    await self.room.remove_guest(identity)
                await conn.close()

        try:
            self._loop.run_until_complete(serve(self.host, self.port, on_connection))
        except Exception:
            logger.exception("Engine loop exited")

    def stop(self):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._loop = None

    def connected_guests(self) -> list[str]:
        """
        NOTE: reads Room's dict from a different thread than the one
        mutating it. Safe enough for a status display given Python's GIL
        makes simple dict reads/writes atomic, but not a general-purpose
        thread-safety guarantee - worth revisiting if this grows beyond
        "show a count in the GUI".
        """
        if self.room is None:
            return []
        return list(self.room.guests.keys())
