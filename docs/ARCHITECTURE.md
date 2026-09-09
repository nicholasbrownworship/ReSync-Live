# Architecture decisions

This records the reasoning behind ReSync Live's design, so future sessions
don't have to re-litigate it. Decisions here were made and verified against
LiveKit's current docs as of Sept 2026 — re-check anything version-specific
before relying on it long-term.

## Goal

Remote guests join a room over the network. Each guest's audio+video is
recorded to a separate, synced local file on the host's PC. Output feeds
into ReSync the same way a local in-person recording does. Network use is
the default/primary case (not a fallback for in-person).

## Why LiveKit

Raw WebRTC requires building/maintaining signaling servers and TURN relay
infrastructure — most of the real difficulty in VDO.Ninja/Riverside-style
tools. LiveKit (open-source WebRTC infra, SFU + client SDKs) solves this,
turning a multi-month networking project into an app-layer project.

## Where the LiveKit server lives: LiveKit Cloud (hosted)

Guests connect to LiveKit's infrastructure, not the host's home network.
Avoids NAT/port-forwarding/upload-bandwidth-as-single-point-of-failure
problems — the exact fragility that made the old VDO.Ninja setup unreliable.
Self-hosting the LiveKit server was considered and rejected for the same
reason: it reintroduces "my network is the bottleneck."

## Recording: custom recording participant, NOT Egress

Two paths exist for getting files out of a LiveKit room:

**Egress (rejected for this project).** LiveKit's built-in server-side
recording feature. Verified via LiveKit docs: on LiveKit Cloud, Egress
writes only to cloud storage (S3-compatible, Azure, GCP, or Ali OSS) or
streams audio via WebSocket — there is no "write to a folder on the host's
PC" option, because Egress workers run inside LiveKit's cloud
infrastructure, not on the host's machine. Using Egress would mean
recordings live in a cloud bucket (even if only transiently, then
downloaded), which conflicts with the requirement that recordings never
leave the host's own machine.

**Custom recording participant (chosen).** The host app connects to the
room as a normal participant that publishes nothing and only subscribes.
Verified via LiveKit's Python `rtc` SDK docs: subscribing to a remote
track yields a stream of already-decoded frames — `VideoFrameEvent`
(carries `frame` + `timestamp_us`) for video, and equivalent timestamped
frames for audio. The app takes these raw frames and encodes/muxes them
to files itself (via PyAV, which wraps FFmpeg) — effectively doing the
same job Egress would have done, just locally and under our control.

Tradeoff acknowledged: this is meaningfully more engineering than Egress
(Egress does encoding/muxing for you; here we build that ourselves), but
it's the only path that keeps recordings 100% local with LiveKit Cloud
used purely as a relay.

One thing this SDK does NOT give you: `AVSynchronizer` in the Python SDK
is for the *outgoing/publishing* direction (e.g. syncing generated audio
with generated video when your app is the one speaking into a room) — it
is not a tool for syncing *incoming* subscribed tracks. For recording,
sync comes from using each frame's own timestamp directly.

## Guest experience

- No install. Guests join via a plain webpage (LiveKit JS SDK) — click a
  link, allow camera/mic, done.
- Auth/expiry model: TBD (open question).

## Scaling target: 6-7 simultaneous guests, GPU-encoded (RTX 3080 host)

Verified: NVIDIA's consumer GeForce NVENC concurrent-session cap has been
raised repeatedly via driver updates (2 → 3 → 5 → 8 sessions as of recent
drivers), which comfortably covers 6-7 simultaneous encode sessions on a
current driver. Two things are NOT verified and need real-world testing,
not assumption:

1. **Encode throughput**, not just session count — the RTX 3080 (Ampere)
   has a single physical NVENC engine, unlike dual-encoder RTX 40-series
   cards higher up the stack. Session-count headroom doesn't guarantee
   real-time throughput at 7-way parallel 1080p encode.
2. **Decode load** — incoming guest video is decoded by LiveKit's
   underlying WebRTC engine before Python ever sees a frame. Whether that
   decode step is hardware-accelerated automatically wasn't confirmed from
   docs.

**Decision:** build the pipeline architecturally ready for 7 guests (not
hardcoded to fewer), but validate scaling empirically in the build order
below rather than assuming it works — same incremental approach the
original VDO.Ninja replacement plan called for, just validating a
different bottleneck (local encode/decode capacity instead of network).

## Video: audio+video for every guest, camera optional per-guest

Every guest records audio+video by default; a guest can disable their
camera (audio-only for that guest) without breaking the room or other
guests' recordings.

## Suggested build order

1. Bare-bones two-person room using LiveKit's own sample/quickstart, no
   custom code — validates LiveKit Cloud itself before building anything.
2. Minimal guest join webpage.
3. Recording service: subscribe to tracks, write synced files per
   participant. Test with 2 people first.
4. Scale test to 4 guests (Josh, Corey, Joshua), then the full group of 7.
5. Host-side room creation + guest management (mute, remove, connection
   status).
6. Define the exact handoff folder/file naming ReSync will consume.

## Open questions (not yet resolved)

- LiveKit Cloud pricing/limits for this usage pattern — needs a current
  check before relying on the free tier long-term.
- Room auth model: password? expiring links? waiting room vs. instant join?
- Reconnect behavior: does a guest's mid-session drop need to produce one
  continuous file, or is a second file segment on reconnect acceptable?
- Exact output file naming/folder convention for the ReSync handoff.
- Host app UI approach — thin local web UI vs. small desktop shell around
  the Python recording service.
