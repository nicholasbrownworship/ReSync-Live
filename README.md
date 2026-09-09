# ReSync Live

Remote-guest recording companion to [ReSync](https://github.com/nicholasbrownworship/Resync).

ReSync handles local, in-person multitrack recording. ReSync Live handles
the same job for **remote guests joining over the network** — a live
group call where each guest gets their own isolated, synced audio/video
track recorded straight to a folder on the host's PC.

## Self-reliance

This project deliberately avoids any hosted third-party company or
service — no LiveKit Cloud, no other SaaS, nothing running on
infrastructure we don't own. The WebRTC media engine
([aiortc](https://github.com/aiortc/aiortc)) and TURN relay (coturn) are
existing open-source software we self-host, since hand-writing
cryptography/codecs/NAT-relay protocols ourselves isn't reasonably
scoped or safe to attempt. Signaling and the room/relay ("SFU") logic
*are* self-written — see `docs/ARCHITECTURE.md` for the full reasoning
and the tradeoffs this creates (notably: aiortc's Python concurrency
ceiling is an accepted, unresolved risk at 6-7 simultaneous guests).

## Status

Early scaffolding, **not yet run end-to-end**. The highest-risk unproven
piece is multi-guest renegotiation in `sfu/room.py` (flagged inline) —
this needs to work even for the first 2-guest test. See
`docs/ARCHITECTURE.md` for the full build order.

## Repo layout

- `signaling/` — hand-written WebSocket signaling server (stdlib only).
- `sfu/` — room/relay logic built on aiortc.
- `recorder/` — per-guest recording, built on aiortc's `MediaRecorder`.
- `guest-page/` — plain-JS guest join page (native browser WebRTC APIs).
- `docs/ARCHITECTURE.md` — full decision record and open questions.
