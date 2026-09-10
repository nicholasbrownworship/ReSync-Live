"""
Room/relay logic: the self-written "SFU" piece. Manages one aiortc
RTCPeerConnection per connected guest (a star topology - every guest
connects only to the server, never directly to each other), uses
aiortc's MediaRelay to fan each guest's track out to every other guest,
and handles renegotiation when a guest's track needs to reach someone
who already connected earlier.

STATUS: core join/renegotiation/recording flow verified via simulated
guests (see docs/ARCHITECTURE.md). Gain control and track-identity
mapping (added this session) are NOT yet verified against a real
browser - only the server-side logic and mid-consistency were checked
against a second aiortc-based simulated client, not a real one.

Known, deliberate simplification (not yet a solved problem): renegotiation
is only serialized per-target-guest via a lock, to stop two overlapping
offers landing on the same peer connection when a new guest's audio and
video tracks both arrive within milliseconds of each other. It does NOT
implement full "perfect negotiation" - not needed today because only
the server ever initiates renegotiation, never the guest.
"""
import asyncio
import logging

from aiortc import RTCConfiguration, RTCIceCandidate, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamTrack
from aiortc.sdp import candidate_from_sdp

from recorder.session_recorder import SessionRecorder

logger = logging.getLogger("resync_live.sfu")


def parse_browser_candidate(candidate_dict: dict) -> RTCIceCandidate | None:
    """
    Converts a browser's RTCIceCandidate JSON (from pc.onicecandidate,
    relayed over our signaling channel) into an aiortc RTCIceCandidate
    that pc.addIceCandidate() can accept. aiortc does not take the raw
    candidate string directly - candidate_from_sdp parses the "candidate:
    ..." line into its component fields (foundation, priority, ip,
    port, type, etc.), and sdpMid/sdpMLineIndex have to be set
    separately since they're not part of that string, they're sibling
    fields on the browser's candidate object.

    This is the fix for a confirmed, real bug: without relaying trickle
    candidates like this, real browsers (which discover candidates
    asynchronously by default) had no way to tell the server about any
    candidate found after the initial offer - meaning the media
    connection could fail to ever establish even though signaling
    completed, exactly matching what was observed in testing (frames
    never arrived, even on a single machine with no network in the way).
    """
    candidate_str = candidate_dict.get("candidate", "")
    if not candidate_str:
        return None  # empty string/None candidate = end-of-candidates marker
    value = candidate_str.split("candidate:", 1)[-1]
    candidate = candidate_from_sdp(value)
    candidate.sdpMid = candidate_dict.get("sdpMid")
    candidate.sdpMLineIndex = candidate_dict.get("sdpMLineIndex")
    return candidate


class GainAdjustableAudioTrack(MediaStreamTrack):
    """
    Wraps a guest's RAW incoming audio track with an adjustable gain
    factor, applied ONCE at the source - both the recorder and every
    relay to other guests read from this SAME wrapped track, so a gain
    change affects what's recorded AND what everyone else hears,
    identically. This is deliberately different from a live-only mute:
    the point is to prevent clipping/peaking from ever being captured
    in the first place, not just to control the live monitor mix.

    STATUS: first draft. Multiplying raw PCM samples in-place via numpy
    on each frame's plane data is the part most likely to need
    adjustment once run against real audio (sample format assumptions:
    16-bit signed integer PCM, which is what aiortc/WebRTC audio uses,
    but not independently re-verified here).

    Also tracks a live peak level (0.0-1.0, with decay) so the host app
    can show a real level meter - added because there was previously no
    way to see how loud a guest actually was before recording them,
    leading to guessing at gain values blind.
    """
    kind = "audio"

    def __init__(self, source_track: MediaStreamTrack):
        super().__init__()
        self.source_track = source_track
        self.gain = 1.0  # 1.0 = unchanged, 0.0 = silent, >1.0 = boosted
        self.level = 0.0  # peak level with decay, 0.0-1.0, for VU-meter display

    async def recv(self):
        frame = await self.source_track.recv()

        import numpy as np

        for plane in frame.planes:
            samples = np.frombuffer(bytes(plane), dtype=np.int16)
            if len(samples) > 0:
                peak = float(np.abs(samples).max()) / 32768.0
                # Decay rather than snap to the new peak each frame -
                # otherwise the meter would flicker unreadably fast at
                # normal frame rates.
                self.level = max(peak, self.level * 0.7)
            if self.gain != 1.0:
                adjusted = np.clip(samples.astype(np.float32) * self.gain, -32768, 32767).astype(np.int16)
                plane.update(adjusted.tobytes())
        return frame


class GuestState:
    def __init__(self, identity: str, display_name: str, pc: RTCPeerConnection, conn):
        self.identity = identity
        self.display_name = display_name
        self.pc = pc
        self.conn = conn  # WebSocketConnection - needed to PUSH renegotiation offers
        self.published_tracks: dict[str, object] = {}  # "audio"/"video" -> (possibly wrapped) track
        self.audio_gain_track: GainAdjustableAudioTrack | None = None
        self.answer_ready = asyncio.Event()
        # Per-consumer bookkeeping (on the OTHER guests' peer connections
        # this guest's tracks were added to): maps the specific relayed
        # track object -> (identity, display_name, kind) so we can look
        # up, after renegotiation, which guest a given transceiver/mid
        # actually carries. Populated in _add_relayed_track.
        self.track_owners: dict[object, tuple[str, str, str]] = {}


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

    def _add_relayed_track(self, target: GuestState, source: GuestState, kind: str):
        """
        Adds `source`'s track (audio goes through source's shared gain
        wrapper first) to `target`'s peer connection, and records which
        source guest it came from so we can later tell `target`'s
        browser which of its incoming tracks is whose.
        """
        published = source.published_tracks[kind]
        relayed = self.relay.subscribe(published)
        target.pc.addTrack(relayed)
        target.track_owners[relayed] = (source.identity, source.display_name, kind)
        return relayed

    async def _send_track_info(self, guest: GuestState):
        """
        Sends the guest's browser a mapping of {mid: {identity,
        display_name, kind}} for every track currently on their peer
        connection - the browser reads a track's mid from
        RTCTrackEvent.transceiver.mid to know whose audio/video it just
        received. Safe to call repeatedly (e.g. after every
        renegotiation); the client just overwrites its lookup table.
        """
        info = {}
        for t in guest.pc.getTransceivers():
            if t.sender and t.sender.track in guest.track_owners:
                identity, display_name, kind = guest.track_owners[t.sender.track]
                info[t.mid] = {"identity": identity, "display_name": display_name, "kind": kind}
        if info:
            await guest.conn.send_json({"type": "track_info", "tracks": info})

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
            if track.kind == "audio":
                gain_track = GainAdjustableAudioTrack(track)
                guest.audio_gain_track = gain_track
                guest.published_tracks["audio"] = gain_track
                await self.recorder.add_track(identity, gain_track, self.relay)
            else:
                guest.published_tracks["video"] = track
                await self.recorder.add_track(identity, track, self.relay)
            await self._forward_to_others(identity)

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
            for kind in other.published_tracks:
                self._add_relayed_track(target=guest, source=other, kind=kind)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await self._send_track_info(guest)
        return pc.localDescription

    async def _forward_to_others(self, source_identity: str):
        """A guest just started publishing a track (or its second track
        - audio/video arrive as separate events). Every other already-
        connected guest's PC needs whichever of source's tracks it
        doesn't have yet, then renegotiation."""
        source = self.guests[source_identity]
        for other_identity, other in self.guests.items():
            if other_identity == source_identity:
                continue
            added_any = False
            for kind in source.published_tracks:
                track_obj = source.published_tracks[kind]
                already_has = any(
                    owner == (source_identity, source.display_name, kind)
                    for owner in other.track_owners.values()
                )
                if not already_has:
                    self._add_relayed_track(target=other, source=source, kind=kind)
                    added_any = True
            if added_any:
                await self._renegotiate(other)
                await self._send_track_info(other)

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

    async def handle_ice_candidate(self, identity: str, candidate_dict: dict):
        """A trickle ICE candidate discovered by the guest's browser
        after their initial offer was already sent - see
        parse_browser_candidate for why this exists at all."""
        guest = self.guests.get(identity)
        if guest is None:
            logger.warning("ICE candidate from unknown guest %s", identity)
            return
        try:
            candidate = parse_browser_candidate(candidate_dict)
            if candidate is not None:
                await guest.pc.addIceCandidate(candidate)
        except Exception:
            logger.exception("Failed to add ICE candidate from %s: %r", identity, candidate_dict)

    async def set_gain(self, identity: str, gain: float):
        """
        Host-initiated gain adjustment for one guest's audio - affects
        BOTH the recording and what every other guest hears, since both
        read from the same GainAdjustableAudioTrack. gain=0.0 is
        equivalent to the old mute; gain=1.0 is unchanged.
        """
        guest = self.guests.get(identity)
        if guest is None or guest.audio_gain_track is None:
            return
        guest.audio_gain_track.gain = max(0.0, gain)
        logger.info("%s gain set to %.2f", identity, gain)

    async def remove_guest(self, identity: str):
        guest = self.guests.pop(identity, None)
        if guest:
            await guest.pc.close()
            await self.recorder.stop_guest(identity)
            self._renegotiation_locks.pop(identity, None)
            logger.info("Removed guest %s", identity)

    async def stop_all(self):
        """
        Cleanly finalizes every still-connected guest's recording.
        Confirmed real bug this fixes: MP4 files need their 'moov atom'
        (duration/seek metadata) written at the END, once total length
        is known - if the app stops (or the session is ended) while a
        guest is still connected, that guest's MediaRecorder.stop() was
        never being called, leaving the file without that atom. Windows'
        built-in players are strict about this and would refuse to open
        such a file even though the raw video data inside is intact -
        verified by inspecting a properly-stopped recording's moov atom
        directly, confirming this is what "stopped properly" looks like
        and what was missing otherwise.
        """
        for identity in list(self.guests.keys()):
            await self.remove_guest(identity)
