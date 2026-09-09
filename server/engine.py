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

from config import Config
from signaling.ws_server import serve
from sfu.room import Room

logger = logging.getLogger("resync_live.engine")

GUEST_PAGE_PATH = os.path.join(os.path.dirname(__file__), "..", "guest-page", "index.html")


class ResyncLiveEngine:
    def __init__(self, output_dir: str, host: str = "0.0.0.0", port: int = 8765):
        self.output_dir = output_dir
        self.host = host
        self.port = port
        self.room: Room | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server_task: asyncio.Task | None = None

    def start(self):
        if self._thread is not None:
            return  # already running
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        session_id = time.strftime("%Y-%m-%d_%H%M%S")
        self.room = Room(
            output_dir=self.output_dir,
            session_id=session_id,
            ice_servers=Config.ice_servers(),
        )

        async def on_connection(conn):
            identity = None
            try:
                # Every guest gets the room's connection requirements
                # up front, before they even request camera/mic access -
                # so the join page can show a password prompt if needed
                # and build its RTCPeerConnection with the right ICE
                # servers, instead of guessing.
                await conn.send_json({
                    "type": "config",
                    "ice_servers": Config.ice_servers(),
                    "password_required": bool(Config.SESSION_PASSWORD),
                })

                while True:
                    msg = await conn.recv_json()
                    if msg is None:
                        break
                    msg_type = msg.get("type")

                    if msg_type == "offer" and identity is None:
                        if Config.SESSION_PASSWORD and msg.get("password") != Config.SESSION_PASSWORD:
                            await conn.send_json({"type": "error", "message": "Incorrect password."})
                            break
                        identity = msg["identity"]
                        display_name = msg.get("display_name", identity)
                        answer = await self.room.handle_join(identity, display_name, conn, msg["sdp"])
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
            with open(GUEST_PAGE_PATH, "r", encoding="utf-8") as f:
                guest_page_html = f.read()
        except OSError:
            logger.exception("Could not read guest page at %s", GUEST_PAGE_PATH)
            guest_page_html = ""

        try:
            self._server_task = self._loop.create_task(
                serve(self.host, self.port, on_connection, static_html=guest_page_html)
            )
            self._loop.run_forever()
        except Exception:
            logger.exception("Engine loop exited")

    def stop(self):
        if self._loop is not None:
            if self._server_task is not None:
                # Wait for the task to actually finish cancelling before
                # stopping the loop, so it doesn't get torn down mid-flight
                # (which asyncio logs as a "Task was destroyed" warning -
                # harmless, but noisy, and easy to avoid properly).
                self._server_task.add_done_callback(lambda _: self._loop.stop())
                self._loop.call_soon_threadsafe(self._server_task.cancel)
            else:
                self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._loop = None
        self._server_task = None

    def connected_guests(self) -> dict[str, dict]:
        """Returns {identity: {display_name, gain}} - see the
        thread-safety note above; unchanged reasoning applies here."""
        if self.room is None:
            return {}
        return {
            identity: {
                "display_name": g.display_name,
                "gain": g.audio_gain_track.gain if g.audio_gain_track else 1.0,
            }
            for identity, g in self.room.guests.items()
        }

    def set_gain(self, identity: str, gain: float):
        if self.room is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.room.set_gain(identity, gain), self._loop)

    def kick(self, identity: str):
        if self.room is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.room.remove_guest(identity), self._loop)
