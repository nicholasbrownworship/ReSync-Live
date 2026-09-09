"""
ReSync Live server entrypoint: runs the self-written signaling server
and wires incoming offers into the Room/relay logic.

STATUS: first draft, not yet run end-to-end.

Usage:
    python server_main.py
"""
import asyncio
import logging
import os
import time

from signaling.ws_server import serve
from sfu.room import Room

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("resync_live.server")

OUTPUT_DIR = os.environ.get("RESYNC_LIVE_OUTPUT_DIR", "./recordings")
HOST = os.environ.get("RESYNC_LIVE_HOST", "0.0.0.0")
PORT = int(os.environ.get("RESYNC_LIVE_PORT", "8765"))

session_id = time.strftime("%Y-%m-%d_%H%M%S")
room = Room(output_dir=OUTPUT_DIR, session_id=session_id)


async def on_connection(conn):
    logger.info("New signaling connection")
    try:
        while True:
            msg = await conn.recv_json()
            if msg is None:
                break

            if msg.get("type") == "offer":
                identity = msg["identity"]
                answer = await room.handle_offer(identity, msg["sdp"], "offer")
                await conn.send_json({"type": "answer", "sdp": answer.sdp})
            else:
                logger.warning("Unhandled message type: %s", msg.get("type"))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(serve(HOST, PORT, on_connection))
