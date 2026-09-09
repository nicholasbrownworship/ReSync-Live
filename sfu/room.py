"""
Room/relay logic: the self-written "SFU" piece. Manages one aiortc
RTCPeerConnection per connected guest, uses aiortc's MediaRelay to fan
each guest's track out to every other guest (and to the recorder), and
handles renegotiation when a new guest joins an in-progress room.

STATUS: first draft, not yet run end-to-end. This is the highest-risk,
least-proven part of the self-built stack — validate with 2 guests,
audio only, before anything else (see docs/ARCHITECTURE.md build order).
"""
import logging

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay

from recorder.session_recorder import SessionRecorder

logger = logging.getLogger("resync_live.sfu")


class Guest:
    def __init__(self, identity: str, pc: RTCPeerConnection):
        self.identity = identity
        self.pc = pc


class Room:
    def __init__(self, output_dir: str, session_id: str):
        self.relay = MediaRelay()
        self.guests: dict[str, Guest] = {}
        self.recorder = SessionRecorder(output_dir=output_dir, session_id=session_id)

    async def handle_offer(self, identity: str, sdp: str, sdp_type: str) -> RTCSessionDescription:
        """
        A new guest is joining (or renegotiating). Creates their
        RTCPeerConnection if needed, wires up relaying of their tracks
        to every existing guest (and vice versa), and returns the answer.
        """
        pc = self.guests[identity].pc if identity in self.guests else RTCPeerConnection()

        if identity not in self.guests:
            guest = Guest(identity, pc)
            self.guests[identity] = guest

            @pc.on("track")
            async def on_track(track):
                logger.info("Received %s track from %s", track.kind, identity)
                # Record this guest's own track.
                await self.recorder.add_track(identity, track, self.relay)
                # Forward it to every OTHER already-connected guest.
                for other_identity, other_guest in self.guests.items():
                    if other_identity == identity:
                        continue
                    other_guest.pc.addTrack(self.relay.subscribe(track))
                    # NOTE: adding a track to an already-connected peer's
                    # PC after its initial offer/answer requires a fresh
                    # renegotiation (new offer pushed to that client over
                    # the signaling socket). Not yet implemented here -
                    # this is the piece flagged as highest-risk/least-
                    # proven in docs/ARCHITECTURE.md. Needed even for the
                    # first 2-guest test.

            @pc.on("connectionstatechange")
            async def on_connectionstatechange():
                logger.info("%s connection state: %s", identity, pc.connectionState)
                if pc.connectionState in ("failed", "closed"):
                    await self.remove_guest(identity)

        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))

        # Also give this new guest every EXISTING guest's tracks.
        for other_identity, other_guest in self.guests.items():
            if other_identity == identity:
                continue
            # NOTE: needs access to other_guest's already-received tracks
            # here, not just future ones - relay.subscribe needs the
            # actual track object. Wiring this correctly (tracking each
            # guest's published tracks in a lookup) is part of the same
            # renegotiation work flagged above.

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return pc.localDescription

    async def remove_guest(self, identity: str):
        guest = self.guests.pop(identity, None)
        if guest:
            await guest.pc.close()
            await self.recorder.stop_guest(identity)
            logger.info("Removed guest %s", identity)
