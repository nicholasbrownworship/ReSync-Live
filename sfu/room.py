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
video tracks both arrive within milliseconds of each other. It does NOT
implement full "perfect negotiation" (polite/impolite peer roles, offer
rollback on glare) - not needed today because only the server ever
initiates renegotiation, never the guest.
"""
import asyncio
import logging

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamTrack

from recorder.session_recorder import SessionRecorder

logger = logging.getLogger("resync_live.sfu")


class MutableAudioTrack(MediaStreamTrack):
    """
    Wraps a relayed audio track so the HOST can mute a guest in the live
    mix (what other guests hear) without touching their recording and
    without needing to renegotiate the connection to add/remove a track.
    Muted frames are replaced with silence of the same shape, rather
    than the original audio - the receiving end still gets a continuous
    stream, just silent.

    STATUS: first draft, not yet run. Constructing a correctly-shaped
    silent AudioFrame (matching format/layout/sample count) from
    scratch, per-frame, is the part most likely to need adjustment once
    this runs against real audio.
    """
    kind = "audio"

    def __init__(self, source_track: MediaStreamTrack):
        super().__init__()
        self.source_track = source_track
        self.muted = False

    async def recv(self):
        frame = await self.source_track.recv()
        if not self.muted:
            return frame
        for plane in frame.planes:
            plane.update(bytes(len(plane)))
        return frame


class GuestState:
    def __init__(self, identity: str, display_name: str, pc: RTCPeerConnection, conn):
        self.identity = identity
        self.display_name = display_name
        self.pc = pc
        self.conn = conn  # WebSocketConnection - needed to PUSH renegotiation offers
        self.published_tracks: dict[str, object] = {}  # "audio"/"video" -> track
        self.mute_wrappers: list[MutableAudioTrack] = []  # audio tracks THIS guest sends to others
        self.answer_ready = asyncio.Event()
        self.muted = False


class Room:
    def __init__(self, output_dir: str, session_id: str, ice_servers: list[dict] | None = None):
        self.relay = MediaRelay()
        self.guests: dict[str, GuestState] = {}
        self.recorder = SessionRecorder(output_dir=output_dir, session_id=session_id)
        self._renegotiation_locks: dict[str, asyncio.Lock] = {}
        self._rtc_configuration = self._build_rtc_configuration(ice_servers or [])

    def _build_rtc_configuration(self, ice_servers: list[dict]) -> RTCConfiguration:
        servers = [
            RTCIceServer(
                urls=s["urls"],
                username=s.get("username"),
                credential=s.get("credential"),
            )
            for s in ice_servers
        ]
        return RTCConfiguration(iceServers=servers)

    def _lock_for(self, identity: str) -> asyncio.Lock:
        if identity not in self._renegotiation_locks:
            self._renegotiation_locks[identity] = asyncio.Lock()
        return self._renegotiation_locks[identity]

    async def handle_join(
        self, identity: str, display_name: str, conn, sdp: str
    ) -> RTCSessionDescription:
        """A new guest's initial offer. Returns the answer to send back
        on THIS SAME connection (ordinary request/response)."""
        pc = RTCPeerConnection(configuration=self._rtc_configuration)
        guest = GuestState(identity, display_name, pc, conn)
        self.guests[identity] = guest

        @pc.on("track")
        async def on_track(track):
            logger.info("Received %s track from %s (%s)", track.kind, identity, display_name)
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
        # guests, up front, as part of THIS initial answer.
        for other_identity, other in self.guests.items():
            if other_identity == identity:
                continue
            for kind, track in other.published_tracks.items():
                relayed = self.relay.subscribe(track)
                if kind == "audio":
                    wrapper = MutableAudioTrack(relayed)
                    wrapper.muted = other.muted
                    other.mute_wrappers.append(wrapper)
                    pc.addTrack(wrapper)
                else:
                    pc.addTrack(relayed)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return pc.localDescription

    async def _forward_to_others(self, source_identity: str, track):
        """A guest just started publishing a track. Every other already-
        connected guest's PC needs it added, then renegotiated. Audio
        tracks go through a MutableAudioTrack so the host can mute this
        guest later without renegotiating again."""
        source = self.guests[source_identity]
        for other_identity, other in self.guests.items():
            if other_identity == source_identity:
                continue
            relayed = self.relay.subscribe(track)
            if track.kind == "audio":
                wrapper = MutableAudioTrack(relayed)
                wrapper.muted = source.muted
                source.mute_wrappers.append(wrapper)
                other.pc.addTrack(wrapper)
            else:
                other.pc.addTrack(relayed)
            await self._renegotiate(other)

    async def _renegotiate(self, guest: GuestState):
        """Pushes a fresh offer to an already-connected guest and waits
        for their answer (arrives via handle_renegotiation_answer)."""
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
        guest = self.guests.get(identity)
        if guest is None:
            logger.warning("Renegotiation answer from unknown guest %s", identity)
            return
        await guest.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
        guest.answer_ready.set()

    async def set_muted(self, identity: str, muted: bool):
        """Host-initiated mute: affects what OTHER guests hear, not the
        recording, which keeps capturing this guest's real audio."""
        guest = self.guests.get(identity)
        if guest is None:
            return
        guest.muted = muted
        for wrapper in guest.mute_wrappers:
            wrapper.muted = muted
        logger.info("%s %s", identity, "muted" if muted else "unmuted")

    async def remove_guest(self, identity: str):
        guest = self.guests.pop(identity, None)
        if guest:
            await guest.pc.close()
            await self.recorder.stop_guest(identity)
            self._renegotiation_locks.pop(identity, None)
            logger.info("Removed guest %s", identity)
