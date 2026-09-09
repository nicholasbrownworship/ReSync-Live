"""
Central settings, read from environment variables so nothing sensitive
(the session password, TURN credentials) lives in source control.

STATUS: first draft. TURN_HOST etc. correspond to whatever coturn is
configured with on the host machine - see docs/BUILD.md /
docs/ARCHITECTURE.md, coturn setup itself is still an open item there.
"""
import os


class Config:
    # Self-hosted coturn - serves both STUN and TURN, so guests never
    # depend on a third-party STUN service (e.g. Google's public one).
    TURN_HOST = os.environ.get("RESYNC_LIVE_TURN_HOST", "")
    TURN_PORT = os.environ.get("RESYNC_LIVE_TURN_PORT", "3478")
    TURN_USERNAME = os.environ.get("RESYNC_LIVE_TURN_USERNAME", "")
    TURN_CREDENTIAL = os.environ.get("RESYNC_LIVE_TURN_CREDENTIAL", "")

    # Simple shared password for the session, since this is reachable
    # from the open internet once port-forwarded. Empty = no password
    # (fine for a same-network test, not fine once forwarded).
    SESSION_PASSWORD = os.environ.get("RESYNC_LIVE_SESSION_PASSWORD", "")

    @classmethod
    def ice_servers(cls) -> list[dict]:
        """Returned as plain dicts (not aiortc.RTCIceServer objects) so
        this same method can feed both the Python side (server/engine.py
        converts them) and the JSON message sent to the browser guest
        page, which needs the same shape either way."""
        servers = []
        if cls.TURN_HOST:
            servers.append({"urls": f"stun:{cls.TURN_HOST}:{cls.TURN_PORT}"})
            if cls.TURN_USERNAME and cls.TURN_CREDENTIAL:
                servers.append({
                    "urls": f"turn:{cls.TURN_HOST}:{cls.TURN_PORT}",
                    "username": cls.TURN_USERNAME,
                    "credential": cls.TURN_CREDENTIAL,
                })
        return servers
