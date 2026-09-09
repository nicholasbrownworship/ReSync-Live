"""
Configuration for the ReSync Live recording service.

Values are read from environment variables so credentials never live in
source control. Copy `.env.example` to `.env` and fill these in, or set
them in your shell before running.
"""
import os


class Config:
    # LiveKit Cloud project connection details.
    # Find these in the LiveKit Cloud dashboard for your project.
    LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "")  # e.g. wss://your-project.livekit.cloud
    LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
    LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")

    # Identity the recorder uses when it joins a room as a silent participant.
    RECORDER_IDENTITY = os.environ.get("RECORDER_IDENTITY", "resync-live-recorder")

    # Where finished recordings land on the host machine.
    OUTPUT_DIR = os.environ.get("RESYNC_LIVE_OUTPUT_DIR", "./recordings")

    @classmethod
    def validate(cls):
        missing = [
            name
            for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
            if not getattr(cls, name)
        ]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "See .env.example."
            )
