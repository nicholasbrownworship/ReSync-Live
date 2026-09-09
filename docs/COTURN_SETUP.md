# Setting up coturn (TURN/STUN relay)

STATUS: written from documentation research, NOT independently tested -
I have no Windows machine or Docker available in my environment to
verify this end-to-end. Treat this as a strong starting point, not a
guarantee. If something doesn't match what you see, the actual error
message is more trustworthy than this doc.

## Why Docker, not a native Windows build

coturn is developed Linux-first. A native Windows build requires Cygwin
(a Linux compatibility layer) and has documented crash reports doing it
that way. The reliable, well-supported path is coturn's official Docker
image, which needs Docker Desktop for Windows installed first.

**Worth flagging explicitly:** Docker itself is software you'd install
and run locally, not a hosted service - same category as aiortc or
FFmpeg in the self-reliance sense - but it's a new judgment call I
haven't gotten explicit sign-off on. If you'd rather avoid Docker
entirely, the alternative is building coturn from source via Cygwin,
which is more fragile based on what turned up in research.

## Setup steps

1. Install Docker Desktop for Windows (docker.com), if not already
   installed.

2. Edit `coturn/turnserver.conf` in this repo - replace
   `CHANGE_THIS_PASSWORD` with a real password. This is the credential
   the app uses to authenticate to your own TURN server - not something
   guests see or need.

3. Run coturn (from the repo root, in PowerShell or a terminal with
   Docker running):

       docker run -d --name resync-turn --network=host `
         -v ${PWD}/coturn/turnserver.conf:/etc/coturn/turnserver.conf `
         coturn/coturn

   NOTE: `--network=host` may not behave the same on Windows as on
   Linux (Docker Desktop for Windows runs containers inside a Linux VM
   via WSL2, and host networking support has historically been
   inconsistent there). If ports aren't reachable with `--network=host`,
   the fallback is explicit port mapping instead:

       docker run -d --name resync-turn `
         -p 3478:3478 -p 3478:3478/udp `
         -p 49152-49352:49152-49352/udp `
         -v ${PWD}/coturn/turnserver.conf:/etc/coturn/turnserver.conf `
         coturn/coturn

4. Set these before running `app.py` (matching what you put in
   turnserver.conf):

       RESYNC_LIVE_TURN_HOST=<your public IP - same one you share with guests>
       RESYNC_LIVE_TURN_PORT=3478
       RESYNC_LIVE_TURN_USERNAME=resync
       RESYNC_LIVE_TURN_CREDENTIAL=<the password you set in turnserver.conf>

5. Router port forwarding - in addition to the app's own port (8765),
   forward:
   - 3478 TCP + UDP (coturn's TURN/STUN listening port)
   - 49152-49352 UDP (the relay port range set in turnserver.conf)

## Testing coturn actually works, before trusting it in a real session

Trickle ICE (a standard, widely-used browser-based STUN/TURN test page)
lets you plug in your TURN server's address/username/password and see
whether it successfully gathers a "relay" candidate. This is a good
sanity check to run BEFORE your first real multi-guest test - if it
can't get a relay candidate, nothing downstream will work either,
and it's a much smaller thing to debug in isolation.
