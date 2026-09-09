# ReSync Live

Remote-guest recording companion to [ReSync](https://github.com/nicholasbrownworship/Resync).

ReSync handles local, in-person multitrack recording. ReSync Live handles the
same job for **remote guests joining over the network** — each guest gets
their own isolated, synced audio/video track, recorded straight to a folder
on the host's PC. ReSync Live's job ends there; ReSync (or any editor) picks
up the resulting files exactly like a local recording.

## Why this exists

The previous approach (VDO.Ninja bringing remote feeds into OBS) was fragile
because it relied on the host's home network/upload bandwidth as a single
point of failure, and isolating tracks reliably was inconsistent. ReSync Live
is built on [LiveKit](https://livekit.io) (open-source WebRTC infrastructure)
so the actual networking, NAT traversal, and relay is solved infrastructure,
not something hand-rolled.

## How recording works (the important architectural decision)

There are two ways to get recordings out of a LiveKit room: **Egress**
(LiveKit's server-side recording feature, which writes to cloud storage) and
a **custom recording participant** that joins the room like any other guest,
subscribes to every track, and writes files locally.

**ReSync Live uses the second approach.** The host app connects to the
LiveKit room as a hidden, silent participant. LiveKit's Python `rtc` SDK
hands it decoded audio/video frames (with per-frame timestamps) for every
guest's track. The app encodes those frames straight to disk via PyAV/FFmpeg.

This means the LiveKit Cloud project is used **purely as a WebRTC relay** —
no cloud storage, no Egress, ever. Recordings only ever exist on the host's
own machine. See `docs/ARCHITECTURE.md` for the full reasoning and the
alternatives that were ruled out.

## Status

Early scaffolding. Not yet functional end-to-end. Build order (see
`docs/ARCHITECTURE.md` for detail):

1. Bare-bones two-person room using LiveKit's own quickstart — validate the
   platform before building anything custom.
2. Minimal guest join webpage (`guest-page/`).
3. Recording service that subscribes to tracks and writes files
   (`recorder/`) — validate with 2 guests, then 4, then the full group of 7.
4. Host-side room creation + guest management UI.
5. Define the exact handoff folder/file format ReSync will consume.

## Repo layout

- `recorder/` — Python service that joins a room and records each
  participant's tracks to local files.
- `guest-page/` — static webpage guests open to join a room (no install).
- `docs/` — architecture decisions and open questions.
