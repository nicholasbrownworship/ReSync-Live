"""
ReSync Live desktop app - the thing Nick actually double-clicks. Tkinter
GUI wrapping the signaling/aiortc engine, which runs on its own
background thread.

STATUS: first draft, not yet run. Packaging via PyInstaller into a
standalone .exe is the next step after this runs correctly from source
- see docs/BUILD.md.

Run from source (before packaging):
    python app.py
"""
import logging
import math
import os
import socket
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

# CRITICAL FIX: without this, Python's logging module shows NOTHING
# below WARNING level - every logger.info() call throughout this
# entire codebase (frame-arrival diagnostics, format detection,
# recording status, everything) was silently producing zero output.
# This was discovered only after several rounds of asking for console
# output that was never actually capable of appearing. Logs go to a
# file next to the exe AND to the console (when one exists), so
# diagnostic info survives even when double-clicking hides the console.
LOG_PATH = os.path.join(
    os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)),
    "resync-live.log",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH, encoding="utf-8")],
)

from config import Config
from server.engine import ResyncLiveEngine

DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "ReSyncLive Recordings")
PORT = 8765


def db_to_linear(db: float) -> float:
    return 10 ** (db / 20)


def linear_to_db(gain: float) -> float:
    # A confirmed real limitation this replaces: the old 0-200% (0x-2x)
    # linear range was nowhere near enough to rescue genuinely quiet
    # microphone input - real audio gain staging routinely needs 10x or
    # more. dB is also the standard way audio gain is actually expressed.
    if gain <= 0:
        return -60.0  # effectively silent; avoids log(0)
    return max(-60.0, 20 * math.log10(gain))


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "unknown"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ReSync Live")
        self.root.geometry("560x480")

        self.output_dir = DEFAULT_OUTPUT_DIR
        self.engine = ResyncLiveEngine(output_dir=self.output_dir, port=PORT)
        self.running = False
        self.guest_rows: dict[str, dict] = {}  # identity -> {frame, mute_btn}

        tk.Label(root, text="ReSync Live", font=("Segoe UI", 16, "bold")).pack(pady=(16, 4))
        self.status_label = tk.Label(root, text="Stopped", fg="gray")
        self.status_label.pack()

        self.ip_label = tk.Label(
            root,
            text=(
                f"Local IP: {get_local_ip()}:{PORT}\n"
                "(share your PUBLIC IP with guests, not this)\n"
                "Tell guests to visit https:// (not http://) and click through\n"
                "the one-time browser security warning - see docs/ARCHITECTURE.md."
            ),
            justify="center", fg="#555",
        )
        self.ip_label.pack(pady=(8, 8))

        # Session password - set before starting, shown here so it's
        # easy to read off and share alongside the IP.
        pw_frame = tk.Frame(root)
        pw_frame.pack(pady=(0, 8))
        tk.Label(pw_frame, text="Session password (optional):").pack(side="left")
        self.password_var = tk.StringVar(value=Config.SESSION_PASSWORD)
        self.password_entry = tk.Entry(pw_frame, textvariable=self.password_var, width=16)
        self.password_entry.pack(side="left", padx=6)

        self.toggle_btn = tk.Button(root, text="Start Session", width=20, command=self.toggle)
        self.toggle_btn.pack(pady=8)

        tk.Label(root, text="Connected guests:", font=("Segoe UI", 10, "bold")).pack(pady=(16, 4))
        self.guests_frame = tk.Frame(root)
        self.guests_frame.pack(fill="x", padx=16)

        folder_frame = tk.Frame(root)
        folder_frame.pack(pady=(24, 0))
        tk.Label(folder_frame, text="Recordings folder:").pack(side="left")
        tk.Button(folder_frame, text="Choose...", command=self.choose_folder).pack(side="left", padx=6)

        self.folder_label = tk.Label(root, text=self.output_dir, fg="#555", wraplength=420)
        self.folder_label.pack(pady=(4, 0))

        self._poll_status()

    def choose_folder(self):
        if self.running:
            messagebox.showinfo("ReSync Live", "Stop the session before changing the recordings folder.")
            return
        chosen = filedialog.askdirectory(initialdir=self.output_dir)
        if chosen:
            self.output_dir = chosen
            self.folder_label.config(text=self.output_dir)
            self.engine = ResyncLiveEngine(output_dir=self.output_dir, port=PORT)

    def toggle(self):
        if self.running:
            self.engine.stop()
            self.status_label.config(text="Stopped", fg="gray")
            self.toggle_btn.config(text="Start Session")
            self.password_entry.config(state="normal")
            self.running = False
        else:
            Config.SESSION_PASSWORD = self.password_var.get()
            os.makedirs(self.output_dir, exist_ok=True)
            self.engine.start()
            self.status_label.config(text="Starting...", fg="orange")
            self.toggle_btn.config(state="disabled")
            self.password_entry.config(state="disabled")
            # start() only spawns a background thread and returns
            # immediately - it does NOT mean the server actually came
            # up successfully. Check shortly after, instead of assuming
            # success (a real bug found the hard way: a startup failure
            # was previously silent, with the GUI claiming "Running"
            # regardless of whether the server ever actually bound the
            # port).
            self.root.after(1500, self._check_startup_result)

    def _check_startup_result(self):
        self.toggle_btn.config(state="normal")
        if self.engine.startup_error:
            messagebox.showerror(
                "ReSync Live - Failed to start",
                f"The session could not be started:\n\n{self.engine.startup_error}",
            )
            self.status_label.config(text="Stopped (failed to start)", fg="red")
            self.engine.stop()
            self.running = False
        else:
            self.status_label.config(text="Running", fg="green")
            self.toggle_btn.config(text="Stop Session")
            self.running = True

    def _rebuild_guest_rows(self, guests: dict[str, dict]):
        for identity in list(self.guest_rows.keys()):
            if identity not in guests:
                self.guest_rows[identity]["frame"].destroy()
                del self.guest_rows[identity]

        for identity, info in guests.items():
            if identity not in self.guest_rows:
                row = tk.Frame(self.guests_frame)
                row.pack(fill="x", pady=2)
                name_label = tk.Label(row, text=info["display_name"], width=12, anchor="w")
                name_label.pack(side="left")

                # Real-time level meter - added because there was
                # previously no way to see how loud a guest actually
                # was before recording them, so gain got set blind.
                meter = tk.Canvas(row, width=80, height=16, bg="black",
                                   highlightthickness=1, highlightbackground="#444")
                meter.pack(side="left", padx=4)
                meter_bar = meter.create_rectangle(0, 0, 0, 16, fill="green", width=0)

                # Gain slider in dB (-20dB to +30dB ~= 0.1x to ~32x
                # linear) - replaces the old 0-200% range, which
                # confirmed in real testing could not boost quiet mic
                # input to an audible level. Affects BOTH the recording
                # and what other guests hear (see sfu/room.py
                # GainAdjustableAudioTrack) - not just a live-only mute.
                var = tk.DoubleVar(value=linear_to_db(info["gain"]))
                slider = tk.Scale(
                    row, from_=-20, to=30, orient="horizontal", length=140,
                    variable=var, showvalue=True, resolution=1, label="dB",
                    command=lambda val, i=identity: self.engine.set_gain(i, db_to_linear(float(val))),
                )
                slider.pack(side="left", padx=4)

                kick_btn = tk.Button(row, text="Kick", width=6, fg="red",
                                      command=lambda i=identity: self.engine.kick(i))
                kick_btn.pack(side="left", padx=4)
                self.guest_rows[identity] = {
                    "frame": row, "slider": slider, "var": var,
                    "meter": meter, "meter_bar": meter_bar,
                }

            # Update the meter every cycle, for every row (new or existing).
            # Uses a dB scale, not linear - a confirmed real bug: normal
            # speech peaks around 10-30% of full digital scale, so a
            # LINEAR meter makes healthy audio look like near-silence.
            # Real audio meters are logarithmic for exactly this reason.
            level = info.get("level", 0.0)
            level_db = 20 * math.log10(level) if level > 0 else -60.0
            # Map -40dB (quiet) to 0dB (full scale) onto the 0-80px bar.
            fraction = max(0.0, min(1.0, (level_db + 40) / 40))
            width = int(fraction * 80)
            color = "red" if level_db > -3 else ("orange" if level_db > -12 else "green")
            row_widgets = self.guest_rows[identity]
            row_widgets["meter"].coords(row_widgets["meter_bar"], 0, 0, width, 16)
            row_widgets["meter"].itemconfig(row_widgets["meter_bar"], fill=color)

    def _poll_status(self):
        if self.running:
            self._rebuild_guest_rows(self.engine.connected_guests())
        # Faster than the original 1000ms so the level meter actually
        # feels live rather than visibly stepping once a second.
        self.root.after(200, self._poll_status)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
