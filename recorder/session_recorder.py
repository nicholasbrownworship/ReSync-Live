"""
Per-session recording, built directly on aiortc's own MediaRecorder
(which already handles muxing frames to files via PyAV) rather than
re-implementing that encoding loop ourselves — see docs/ARCHITECTURE.md
for why re-writing that would be redundant risk, not more self-reliant.

STATUS: recordings have come back EMPTY in real testing (files exist,
zero actual content) - root cause not yet found. Added frame-count
logging below specifically to answer one question: are frames even
reaching this code at all, or is nothing arriving in the first place?
That distinguishes a media-connectivity problem from a muxing/encoding
problem, which need completely different fixes.
"""
import logging
import os

from aiortc.contrib.media import MediaRecorder
from aiortc.mediastreams import MediaStreamTrack

logger = logging.getLogger("resync_live.recorder")


class _FrameCountingTrack(MediaStreamTrack):
    """Transparent pass-through that logs frame arrival - diagnostic
    only, added specifically to answer whether real media frames are
    reaching the recorder at all."""

    def __init__(self, source_track: MediaStreamTrack, label: str):
        super().__init__()
        self.kind = source_track.kind
        self.source_track = source_track
        self.label = label
        self.count = 0

    async def recv(self):
        frame = await self.source_track.recv()
        self.count += 1
        if self.count == 1:
            # For video specifically: this is the actual resolution the
            # SERVER receives, which may differ from what the guest's
            # camera captured - browsers apply their own automatic
            # downscaling to outgoing WebRTC video based on bandwidth
            # estimation, independent of capture resolution. This is the
            # fact that actually matters for "is the recording low-res."
            if self.kind == "video":
                logger.info(
                    "FIRST %s frame received for %s (%dx%d)",
                    self.kind, self.label, frame.width, frame.height,
                )
            else:
                logger.info("FIRST %s frame received for %s", self.kind, self.label)
        elif self.count % 150 == 0:
            logger.info("%d %s frames received so far for %s", self.count, self.kind, self.label)
        return frame


class SessionRecorder:
    def __init__(self, output_dir: str, session_id: str):
        self.session_dir = os.path.join(output_dir, session_id)
        os.makedirs(self.session_dir, exist_ok=True)
        self._recorders: dict[str, MediaRecorder] = {}

    def _safe_name(self, identity: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in identity)

    async def add_track(self, identity: str, track, relay):
        """
        Starts (or adds to) a MediaRecorder for this guest. Audio and
        video tracks for the same guest are recorded to separate files
        via separate MediaRecorder instances, since each guest's audio
        and video tracks arrive as separate `track` events, not paired.
        """
        key = f"{identity}_{track.kind}"
        if key in self._recorders:
            return

        ext = "wav" if track.kind == "audio" else "mp4"
        path = os.path.join(self.session_dir, f"{self._safe_name(identity)}_{track.kind}.{ext}")
        logger.info("Recording %s track for %s -> %s", track.kind, identity, path)

        relayed = relay.subscribe(track)
        counted = _FrameCountingTrack(relayed, label=f"{identity}_{track.kind}")

        recorder = MediaRecorder(path)
        recorder.addTrack(counted)

        if track.kind == "video":
            # Confirmed real bug: aiortc's MediaRecorder creates its
            # libx264 stream with NO explicit bitrate/quality settings
            # (verified by reading its source directly), so it falls
            # back to FFmpeg's conservative general-purpose defaults -
            # not tuned for quality. There's no public option to
            # configure this, so this reaches into MediaRecorder's
            # internal track mapping (a private, name-mangled attribute)
            # to set it directly on the underlying PyAV stream before
            # any frames are encoded. Verified this actually changes
            # output size substantially (not a silent no-op) via a
            # direct before/after comparison on identical test content.
            # STATUS: relies on aiortc's internal implementation detail
            # (MediaRecorder._MediaRecorder__tracks) that could break on
            # a future aiortc version - if recordings mysteriously fail
            # to start after an aiortc upgrade, check this first.
            try:
                stream = recorder._MediaRecorder__tracks[counted].stream
                # No bit_rate cap here on purpose - CRF mode picks
                # however many bits the content actually needs for the
                # target quality, and a competing bitrate ceiling can
                # override that and force it back down, undoing the
                # quality target for detailed content like real 1080p
                # video. Confirmed as the actual cause after CRF alone
                # was insufficient with a 4 Mbps cap still in place.
                stream.codec_context.options = {"crf": "18", "preset": "medium"}
            except Exception:
                logger.exception("Could not set high-quality video encoding options - falling back to aiortc's defaults")

        self._recorders[key] = recorder
        await recorder.start()

    async def stop_guest(self, identity: str):
        for kind in ("audio", "video"):
            key = f"{identity}_{kind}"
            recorder = self._recorders.pop(key, None)
            if recorder:
                await recorder.stop()
                logger.info("Stopped recording %s for %s", kind, identity)
