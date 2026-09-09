"""
ReSync Live server entrypoint: runs the self-written signaling server
and wires messages into the Room/relay logic. Each guest's WebSocket
connection carries messages in both directions - not just responses to
what the guest sent, since the server needs to push renegotiation offers
to already-connected guests when someone new starts publishing.

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
    identity = None
    logger.info("New signaling connection")
    try:
        while True:
            msg = await conn.recv_json()
            if msg is None:
                break

            msg_type = msg.get("type")

            if msg_type == "offer" and identity is None:
                # This is a NEW guest's initial join offer.
                identity = msg["identity"]
                answer = await room.handle_join(identity, conn, msg["sdp"])
                await conn.send_json({"type": "answer", "sdp": answer.sdp})

            elif msg_type == "answer" and identity is not None:
                # This is an existing guest replying to a renegotiation
                # offer the SERVER pushed (see Room._renegotiate).
                await room.handle_renegotiation_answer(identity, msg["sdp"])

            else:
                logger.warning(
                    "Unexpected message type=%s while identity=%s", msg_type, identity
                )
    finally:
        if identity:
            await room.remove_guest(identity)
        await conn.close()


if __name__ == "__main__":
    asyncio.run(serve(HOST, PORT, on_connection))
