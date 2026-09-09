# Architecture decisions

This records the reasoning behind ReSync Live's design, so future sessions
don't have to re-litigate it. Verified against current docs as of Sept
2026 where noted — re-check anything version-specific before relying on
it long-term.

## Goal

Remote guests join a session over the network, see/hear each other live
(this is a real-time group call, not just a recording pipeline), and each
guest's audio+video is recorded to a separate, synced local file. Network
use is the default/primary case, not a fallback for in-person.

## Core constraint: self-reliance

Explicit decision: **no dependency on any hosted third-party company or
service**, even free/open-source-backed ones (e.g. LiveKit Cloud is out
entirely — not because it's unreliable, but on principle). Within that:

- Things that are unsafe or unreasonable to hand-write (cryptography,
  video/audio codecs) use existing, vetted open-source software that runs
  entirely on hardware we own. Writing your own DTLS/SRTP or your own
  H.264/Opus codec is multi-year specialist engineering with real security
  risk if done imperfectly — not in scope, and not something even large
  companies do in-house.
- Things that ARE reasonably scoped to write ourselves (signaling, the
  room/relay logic) are self-written, not pulled from a library.
- TURN (NAT relay) is a judgment call in between: technically not
  cryptography, but a large, security-sensitive protocol (RFC 5766).
  **Decision: self-host the existing coturn software** rather than
  hand-write a TURN server — same category of risk as hand-rolling
  crypto, just for relay instead of encryption. This is still "no
  company," just existing self-hosted software instead of self-written.

## Rejected: LiveKit (both Cloud and self-hosted)

LiveKit Cloud was the original plan (see git history / earlier docs) but
was ruled out once the self-reliance requirement was made explicit — even
self-hosting LiveKit's open-source server was rejected, because the goal
isn't just "no company," it's building the actual relay/room logic
ourselves rather than depending on someone else's SFU implementation,
open source or not.

## Chosen stack

- **WebRTC media engine: [aiortc](https://github.com/aiortc/aiortc)**
  (Python, asyncio-based, open source). Verified via aiortc's own docs:
  it implements ICE, DTLS key/handshake, SRTP encryption, and audio/video
  codec handling (Opus, H.264, VP8) using existing, tested implementations
  under the hood — this is the "existing crypto/codec software" boundary
  from the self-reliance decision. It is a library we run ourselves, not
  a hosted service.
- **Signaling: self-written**, using only Python's standard library (raw
  sockets implementing the WebSocket handshake/framing per RFC 6455) —
  no signaling framework or library. This is genuinely a scoped, writable
  piece: it's just message-passing (SDP offers/answers, ICE candidates,
  room membership) between browsers and our server.
- **Room/relay ("SFU") logic: self-written**, built on top of aiortc's
  primitives. aiortc provides `MediaRelay` (fans one incoming track out to
  multiple consumers — confirmed via aiortc's docs/changelog, this exists
  specifically for this use case) and `MediaRecorder` (writes a track to a
  file via PyAV, confirmed via aiortc's API docs). We use both directly
  rather than re-implementing frame-by-frame encoding ourselves — that
  logic already exists inside aiortc and re-writing it would just be
  redundant risk, not "more self-reliant."
- **TURN/STUN: self-hosted coturn.**
- **Guest client: plain browser JavaScript**, using the browser's native
  `RTCPeerConnection` and `WebSocket` APIs directly — no CDN library, no
  livekit-client or similar. Browsers implement WebRTC natively; this
  isn't a third-party dependency in the sense we're avoiding, it's a web
  platform standard.

## Known, accepted risk: aiortc's concurrency ceiling

Verified: aiortc is pure Python running on a single asyncio event loop
(subject to the GIL). Sources are consistent that it's well-suited to a
small number of peers but that Python's overhead becomes a real
bottleneck as fan-out increases — and there's a documented history of
frame-rate degradation under multiple simultaneous video clients.

This matters here specifically because an SFU for a 7-person room (6
guests + host) doesn't just receive 6 streams — it forwards on the order
of 30+ outgoing stream copies simultaneously (everyone needs everyone
else's video+audio), all through one Python process.

**Explicit decision (Nick, this conversation): self-reliance takes
priority over guaranteed scale.** We build for 6-7 guests architecturally,
but accept this may hit a real ceiling well before 7 that isn't a bug to
patch, just Python's concurrency model. If/when that happens, the fix is
running multiple relay worker processes to route around the GIL — a
real, separate piece of engineering, not a tweak. Not building that
preemptively; validating incrementally instead (see build order).

## Guest experience

- No install. Guests open a webpage, click join, allow camera/mic.
- Auth/expiry model: TBD (open question, same as before the pivot).

## Video: audio+video for every guest, camera optional per-guest

Every guest records audio+video by default; a guest can disable their
camera (audio-only for that guest) without breaking the room or other
guests' recordings.

## Suggested build order

1. Signaling server that can complete a WebSocket handshake and pass a
   JSON message back and forth — prove the hand-written protocol layer
   works before any WebRTC is involved.
2. Two guests, audio only: guest A and guest B join the same room, each
   gets an aiortc `RTCPeerConnection`, `MediaRelay` forwards A's audio to
   B and vice versa. Confirm they can actually hear each other.
3. Add `MediaRecorder` per guest — confirm two separate, playable audio
   files land in the output folder.
4. Add video to the same 2-guest test.
5. Scale to 4 guests, then the full 7 — this is where the aiortc
   concurrency ceiling either does or doesn't show up. Treat this as a
   real test, not a formality.
6. Self-host coturn, test from a network where direct connection is
   blocked (e.g. mobile hotspot) to confirm TURN relay actually works.
7. Host-side room creation + guest management (mute, remove, connection
   status).
8. Define the exact handoff folder/file naming ReSync will consume.

## Open questions (not yet resolved)

- Room auth model: password? expiring links? waiting room vs. instant join?
- Reconnect behavior: does a guest's mid-session drop need to produce one
  continuous file, or is a second file segment on reconnect acceptable?
- Exact output file naming/folder convention for the ReSync handoff.
- Host app UI approach — thin local web UI vs. small desktop shell.
- Where does the signaling/SFU process actually run — on the host's own
  PC (same machine as recording), reachable via port-forwarding, or on a
  separate self-hosted box? Running it on the host's home network
  reintroduces some of the "my network is the bottleneck" risk that was
  the original reason to avoid self-hosting — worth a real discussion
  before this gets built out further.
