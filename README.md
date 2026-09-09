# ReSync Live

Remote-guest recording companion to [ReSync](https://github.com/nicholasbrownworship/Resync).

ReSync handles local, in-person multitrack recording. ReSync Live handles
the same job for **remote guests joining over the network** — a live
group call where each guest gets their own isolated, synced audio/video
track recorded straight to a folder on the host's PC.

## Self-reliance

This project deliberately avoids any hosted third-party company or
service — no LiveKit Cloud, no other SaaS, nothing running on
infrastructure we don't own, self-hosted entirely on Nick's own network.
The WebRTC media engine ([aiortc](https://github.com/aiortc/aiortc)) and
TURN relay (coturn) are existing open-source software self-hosted,
since hand-writing cryptography/codecs/NAT-relay protocols ourselves
isn't reasonably scoped or safe to attempt. Signaling and the room/relay
("SFU") logic *are* self-written. See `docs/ARCHITECTURE.md` for the
full reasoning and open tradeoffs.

## What Nick actually runs

`app.py` — a Tkinter desktop app (Start/Stop, connected guest list,
recordings folder picker), meant to be packaged into a standalone `.exe`
via PyInstaller (see `docs/BUILD.md`) so it's download-and-run, not a
terminal command.

## Status

Early scaffolding, **not yet run end-to-end**. See `docs/ARCHITECTURE.md`
for the full build order and known unproven pieces (multi-guest
renegotiation, PyInstaller packaging of PyAV's native dependencies).

## Repo layout

- `app.py` — the desktop app entry point (Tkinter GUI).
- `server/engine.py` — runs the signaling+aiortc engine on a background thread.
- `signaling/` — hand-written WebSocket signaling server (stdlib only).
- `sfu/` — room/relay logic built on aiortc.
- `recorder/` — per-guest recording, built on aiortc's `MediaRecorder`.
- `guest-page/` — plain-JS guest join page (native browser WebRTC APIs, no install needed for guests).
- `docs/ARCHITECTURE.md` — full decision record and open questions.
- `docs/BUILD.md` — packaging the desktop app into a `.exe`.
