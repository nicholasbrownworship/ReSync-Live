"""
Room/relay logic: the self-written "SFU" piece. Manages one aiortc
RTCPeerConnection per connected guest (a star topology - every guest
connects only to the server, never directly to each other), uses
aiortc's MediaRelay to fan each guest's track out to every other guest,
and handles renegotiation when a guest's track needs to reach someone
who already connected earlier.

STATUS: first draft, not yet run end-to-end. This is the highest-risk,
least-proven part of the self-built stack.

Known, deliberate simplification (not yet a solved problem): renegotiation
is only serialized per-target-guest via a lock, to stop two overlapping
offers landing on the same peer connection when a new guest's audio and
video tracks both arrive within milliseconds of each other (which is the
normal case - a guest publishes both at once). It does NOT implement full
"perfect negotiation" (polite/impolite peer roles, offer rollback on
glare). That's a real WebRTC concept for handling races where both sides
try to renegotiate at once - not needed for this star topology today
because only the server ever initiates renegotiation, never the guest,
but worth knowing about if this expands later (e.g. guests muting/
unmuting causing rapid track add/remove).
"""
import asyncio
import logging

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay

from recorder.session_recorder import SessionRecorder

logger = logging.getLogger("resync_live.sfu")


class GuestState:
    def __init__(self, identity: str, pc: RTCPeerConnection, conn):
        self.identity = identity
        self.pc = pc
        self.conn = conn  # WebSocketConnection - needed to PUSH renegotiation offers
        self.published_tracks: dict[str, object] = {}  # "audio"/"video" -> track
        self.answer_ready = asyncio.Event()  # signaled when a pushed offer gets its answer


class Room:
    def __init__(self, output_dir: str, session_id: str):
        self.relay = MediaRelay()
        self.guests: dict[str, GuestState] = {}
        self.recorder = SessionRecorder(output_dir=output_dir, session_id=session_id)
        self._renegotiation_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, identity: str) -> asyncio.Lock:
        if identity not in self._renegotiation_locks:
            self._renegotiation_locks[identity] = asyncio.Lock()
        return self._renegotiation_locks[identity]

    async def handle_join(self, identity: str, conn, sdp: str) -> RTCSessionDescription:
        """A new guest's initial offer. Returns the answer to send back
        on THIS SAME connection (ordinary request/response, no push
        needed for this part)."""
        pc = RTCPeerConnection()
        guest = GuestState(identity, pc, conn)
        self.guests[identity] = guest

        @pc.on("track")
        async def on_track(track):
            logger.info("Received %s track from %s", track.kind, identity)
            guest.published_tracks[track.kind] = track
            await self.recorder.add_track(identity, track, self.relay)
            await self._forward_to_others(identity, track)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info("%s connection state: %s", identity, pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                await self.remove_guest(identity)

        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))

        # Give the new guest every track already published by existing
        # guests, up front, as part of THIS initial answer - no
        # renegotiation needed for what the new guest receives on join,
        # only for what existing guests receive once the new guest starts
        # publishing (handled by on_track -> _forward_to_others above).
        for other_identity, other in self.guests.items():
            if other_identity == identity:
                continue
            for track in other.published_tracks.values():
                pc.addTrack(self.relay.subscribe(track))

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return pc.localDescription

    async def _forward_to_others(self, source_identity: str, track):
        """A guest just started publishing a track. Every other already-
        connected guest's PC needs it added, then renegotiated."""
        for other_identity, other in self.guests.items():
            if other_identity == source_identity:
                continue
            other.pc.addTrack(self.relay.subscribe(track))
            await self._renegotiate(other)

    async def _renegotiate(self, guest: GuestState):
        """Pushes a fresh offer to an already-connected guest (over their
        existing signaling connection) and waits for their answer, which
        arrives asynchronously via handle_renegotiation_answer below.
        Serialized per-guest so two tracks arriving close together don't
        create overlapping offers to the same peer connection."""
        async with self._lock_for(guest.identity):
            offer = await guest.pc.createOffer()
            await guest.pc.setLocalDescription(offer)
            await guest.conn.send_json({
                "type": "offer",
                "sdp": guest.pc.localDescription.sdp,
            })
            await guest.answer_ready.wait()
            guest.answer_ready.clear()

    async def handle_renegotiation_answer(self, identity: str, sdp: str):
        """Called from the connection's receive loop when a guest replies
        to a server-pushed renegotiation offer."""
        guest = self.guests.get(identity)
        if guest is None:
            logger.warning("Renegotiation answer from unknown guest %s", identity)
            return
        await guest.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
        guest.answer_ready.set()

    async def remove_guest(self, identity: str):
        guest = self.guests.pop(identity, None)
        if guest:
            await guest.pc.close()
            await self.recorder.stop_guest(identity)
            self._renegotiation_locks.pop(identity, None)
            logger.info("Removed guest %s", identity)
