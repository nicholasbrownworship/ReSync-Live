"""
Per-session recording, built directly on aiortc's own MediaRecorder
(which already handles muxing frames to files via PyAV) rather than
re-implementing that encoding loop ourselves — see docs/ARCHITECTURE.md
for why re-writing that would be redundant risk, not more self-reliant.

STATUS: first draft, not yet run against real tracks.
"""
import logging
import os

from aiortc.contrib.media import MediaRecorder

logger = logging.getLogger("resync_live.recorder")


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

        recorder = MediaRecorder(path)
        recorder.addTrack(relay.subscribe(track))
        self._recorders[key] = recorder
        await recorder.start()

    async def stop_guest(self, identity: str):
        for kind in ("audio", "video"):
            key = f"{identity}_{kind}"
            recorder = self._recorders.pop(key, None)
            if recorder:
                await recorder.stop()
                logger.info("Stopped recording %s for %s", kind, identity)
