"""
Per-track recording: takes a subscribed LiveKit track and writes it to a
local file using PyAV (FFmpeg bindings).

STATUS: first-draft skeleton, not yet run end-to-end. The frame-to-file
encoding loop below follows the documented shape of LiveKit's Python `rtc`
SDK (VideoStream/AudioStream yield timestamped frames) and PyAV's encoding
API, but has not been validated against a live room yet. Treat file
naming, container/codec choices, and the audio/video frame formats as
things to confirm on first real test, not settled facts.

Build-order note (see docs/ARCHITECTURE.md): validate this with ONE guest,
audio only, before adding video or additional guests.
"""
import asyncio
import logging
import os
import time

import av
from livekit import rtc

logger = logging.getLogger("resync_live.recorder")


class ParticipantRecorder:
    """
    Records one remote participant's audio and/or video tracks to separate
    local files. One instance per guest.
    """

    def __init__(self, participant_identity: str, output_dir: str, session_id: str):
        self.participant_identity = participant_identity
        self.output_dir = output_dir
        self.session_id = session_id
        self._audio_task: asyncio.Task | None = None
        self._video_task: asyncio.Task | None = None
        self._start_time: float | None = None

    def _safe_name(self) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in self.participant_identity)

    def _output_path(self, kind: str, ext: str) -> str:
        session_dir = os.path.join(self.output_dir, self.session_id)
        os.makedirs(session_dir, exist_ok=True)
        return os.path.join(session_dir, f"{self._safe_name()}_{kind}.{ext}")

    def start_audio(self, track: rtc.Track):
        self._audio_task = asyncio.create_task(self._record_audio(track))

    def start_video(self, track: rtc.Track):
        self._video_task = asyncio.create_task(self._record_video(track))

    async def _record_audio(self, track: rtc.Track):
        path = self._output_path("audio", "wav")
        logger.info("Recording audio for %s -> %s", self.participant_identity, path)

        audio_stream = rtc.AudioStream(track)
        container = None
        stream = None

        try:
            async for event in audio_stream:
                frame = event.frame
                if container is None:
                    self._start_time = self._start_time or time.time()
                    container = av.open(path, mode="w")
                    stream = container.add_stream(
                        "pcm_s16le",
                        rate=frame.sample_rate,
                    )
                    stream.channels = frame.num_channels

                audio_frame = av.AudioFrame(
                    format="s16",
                    layout="mono" if frame.num_channels == 1 else "stereo",
                    samples=frame.samples_per_channel,
                )
                audio_frame.planes[0].update(bytes(frame.data))
                audio_frame.sample_rate = frame.sample_rate

                for packet in stream.encode(audio_frame):
                    container.mux(packet)
        finally:
            if container is not None and stream is not None:
                for packet in stream.encode(None):
                    container.mux(packet)
                container.close()
            logger.info("Finished audio recording for %s", self.participant_identity)

    async def _record_video(self, track: rtc.Track):
        path = self._output_path("video", "mp4")
        logger.info("Recording video for %s -> %s", self.participant_identity, path)

        video_stream = rtc.VideoStream(track)
        container = None
        stream = None

        try:
            async for event in video_stream:
                frame = event.frame
                if container is None:
                    self._start_time = self._start_time or time.time()
                    container = av.open(path, mode="w")
                    # NOTE: hardware-encoder codec name (e.g. "h264_nvenc")
                    # should be selected here once GPU-encode is wired up
                    # and validated. Starting with software encode
                    # ("libx264") is the safer first step to prove
                    # correctness before adding NVENC.
                    stream = container.add_stream("libx264", rate=30)
                    stream.width = frame.width
                    stream.height = frame.height
                    stream.pix_fmt = "yuv420p"

                video_frame = frame.convert(rtc.VideoBufferType.I420)
                av_frame = av.VideoFrame.from_ndarray(
                    video_frame.data, format="yuv420p"
                )
                for packet in stream.encode(av_frame):
                    container.mux(packet)
        finally:
            if container is not None and stream is not None:
                for packet in stream.encode(None):
                    container.mux(packet)
                container.close()
            logger.info("Finished video recording for %s", self.participant_identity)

    async def stop(self):
        for task in (self._audio_task, self._video_task):
            if task is not None:
                task.cancel()
