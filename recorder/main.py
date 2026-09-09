"""
ReSync Live recording service entrypoint.

Connects to a LiveKit room as a silent, non-publishing participant, and
starts a ParticipantRecorder for every remote guest's audio/video tracks.

STATUS: first-draft skeleton, not yet run against a live room. See
docs/ARCHITECTURE.md for the build order this should be validated against
(one guest, audio only, before anything else).

Usage:
    python -m recorder.main <room_name>
"""
import asyncio
import logging
import sys
import time

from livekit import rtc

from recorder.config import Config
from recorder.track_recorder import ParticipantRecorder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("resync_live.main")


async def run(room_name: str):
    Config.validate()

    room = rtc.Room()
    session_id = time.strftime("%Y-%m-%d_%H%M%S")
    recorders: dict[str, ParticipantRecorder] = {}

    def get_recorder(participant: rtc.RemoteParticipant) -> ParticipantRecorder:
        if participant.identity not in recorders:
            recorders[participant.identity] = ParticipantRecorder(
                participant_identity=participant.identity,
                output_dir=Config.OUTPUT_DIR,
                session_id=session_id,
            )
        return recorders[participant.identity]

    @room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        logger.info("Subscribed to %s track from %s", track.kind, participant.identity)
        recorder = get_recorder(participant)
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            recorder.start_audio(track)
        elif track.kind == rtc.TrackKind.KIND_VIDEO:
            recorder.start_video(track)

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        logger.info("Participant disconnected: %s", participant.identity)
        # NOTE: open question (see docs/ARCHITECTURE.md) - does a
        # disconnect/reconnect need to produce one continuous file, or is
        # a new file segment acceptable? Currently: just logs, recorder
        # keeps running and will pick up a re-subscribed track if the
        # guest rejoins with the same identity.

    # TODO: generate an access token for this recorder identity rather
    # than assuming one is already available - see livekit-api's
    # AccessToken helper for this.
    token = ""  # placeholder - wire up token generation before first test

    await room.connect(Config.LIVEKIT_URL, token)
    logger.info("Recorder connected to room '%s' as '%s'", room_name, Config.RECORDER_IDENTITY)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for recorder in recorders.values():
            await recorder.stop()
        await room.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m recorder.main <room_name>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1]))
