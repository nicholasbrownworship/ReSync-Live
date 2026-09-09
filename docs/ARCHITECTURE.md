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

## Hosting location: Nick's own PC/network (resolved)

Explicit decision: no rented server of any kind, including a VPS —
self-hosted on Nick's own home network, on the same fiber connection
used for everything else.

**Bandwidth check (resolved, not a concern):** a 7-person room with
video needs roughly 12 Mbps down / 60+ Mbps up through the relay (every
guest's stream gets re-forwarded to every other guest). Nick's
connection is symmetric fiber at 500+ Mbps, which comfortably clears
this — the "home network is the bottleneck" failure mode that broke the
old VDO.Ninja setup does NOT apply here on this connection.

**Reachability (resolved):**
- Router port-forwarding: available (Nick has admin access). Only a
  small, fixed set of ports need forwarding — the signaling WebSocket
  port and coturn's TURN port/relay range — not a wide unpredictable
  range, because guest media is routed through the self-hosted coturn
  relay rather than relying on raw direct NAT traversal.
- No static IP. **Decision: manual IP sharing for now** — before a
  session, share the current public IP directly with the small, stable
  guest roster (Josh, Corey, Joshua, Spencer). Zero third parties
  involved. **Future direction (not now):** Nick owns a domain and
  intends to eventually point it at the home IP instead of sharing it
  manually — this still depends on a domain registrar (an inherent part
  of how domain names work at all), so it's a smaller, later compromise
  on the "zero third party" principle, made deliberately rather than
  by accident. Not being built now.

## Renegotiation (first real implementation attempt, still unverified)

Star topology: every guest connects only to the server, never directly
to another guest. When guest A is already connected and guest B starts
publishing, the server must push a NEW offer to A's existing connection
and get a fresh answer — this is "renegotiation," and it's the part that
makes a self-built multi-party SFU harder than a simple 1:1 call.

Implemented in `sfu/room.py`: a per-guest `asyncio.Lock` serializes
renegotiation attempts to the same guest, since a guest normally
publishes an audio track AND a video track within milliseconds of each
other — without the lock, two overlapping `createOffer()` calls to the
same peer connection would race.

**What this does NOT yet handle (known gaps, not silently ignored):**
- Full "perfect negotiation" (polite/impolite peer roles, offer
  rollback) for glare where BOTH sides try to renegotiate at once. Not
  needed today because only the server ever initiates renegotiation —
  but relevant if guests muting/unmuting starts causing rapid track
  add/remove later.
- Mapping which incoming browser `track` event belongs to which guest.
  Right now the guest page just displays tracks in arrival order — fine
  for a 2-guest test, not fine once there are 3+ others to tell apart.
  Needs an identity tag carried alongside each track (e.g. via a small
  data-channel message, or embedding identity in the track's stream ID)
  before real multi-guest testing.

## Host app: packaged desktop app, not a script (resolved)

Explicit correction (Nick, this conversation): the host side must be
something Nick downloads and double-clicks — not a terminal command.
**Decision: Tkinter** for the GUI (ships inside Python's standard
library, so it adds zero extra dependency — fits the self-reliance bar
better than pulling in Qt or a separate GUI framework), wrapping the
same signaling/aiortc/recorder engine already built, packaged into a
standalone `.exe` via PyInstaller (`app.py` is the entry point;
`server/engine.py` runs the asyncio signaling+aiortc loop on a
background thread so it doesn't block Tkinter's own mainloop).

**Known, unverified risk:** PyAV (which aiortc depends on for codec
handling) has a documented history of PyInstaller bundling issues —
missing modules, native library discovery failures — reported against
multiple PyInstaller/PyAV versions over the years. Modern PyInstaller
has improved hook support for this kind of thing, but I'm not claiming
it'll package cleanly on the first attempt. Treat "does this actually
produce a working .exe" as its own early validation step (see
docs/BUILD.md), not an assumption baked into the plan.

- Room auth model: password? expiring links? waiting room vs. instant join?
- Reconnect behavior: does a guest's mid-session drop need to produce one
  continuous file, or is a second file segment on reconnect acceptable?
- Exact output file naming/folder convention for the ReSync handoff.
- Host app UI approach — thin local web UI vs. small desktop shell.
- coturn install/config specifics on the host machine (which OS, exact
  port/range choices) — not yet worked out.
