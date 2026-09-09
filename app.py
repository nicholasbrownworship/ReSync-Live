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
import os
import socket
import tkinter as tk
from tkinter import filedialog, messagebox

from config import Config
from server.engine import ResyncLiveEngine

DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "ReSyncLive Recordings")
PORT = 8765


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
        self.root.geometry("460x480")

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
        else:
            # Push whatever's in the password field into Config right
            # before starting, so the field actually takes effect this
            # session rather than requiring a restart.
            Config.SESSION_PASSWORD = self.password_var.get()
            os.makedirs(self.output_dir, exist_ok=True)
            self.engine.start()
            self.status_label.config(text="Running", fg="green")
            self.toggle_btn.config(text="Stop Session")
            self.password_entry.config(state="disabled")
        self.running = not self.running

    def _rebuild_guest_rows(self, guests: dict[str, dict]):
        for identity in list(self.guest_rows.keys()):
            if identity not in guests:
                self.guest_rows[identity]["frame"].destroy()
                del self.guest_rows[identity]

        for identity, info in guests.items():
            if identity not in self.guest_rows:
                row = tk.Frame(self.guests_frame)
                row.pack(fill="x", pady=2)
                name_label = tk.Label(row, text=info["display_name"], width=14, anchor="w")
                name_label.pack(side="left")

                # Gain slider: 0-200%, affects BOTH the recording and
                # what other guests hear (see sfu/room.py GainAdjustableAudioTrack)
                # - not just a live-only mute.
                var = tk.DoubleVar(value=info["gain"] * 100)
                slider = tk.Scale(
                    row, from_=0, to=200, orient="horizontal", length=140,
                    variable=var, showvalue=True, resolution=5,
                    command=lambda val, i=identity: self.engine.set_gain(i, float(val) / 100.0),
                )
                slider.pack(side="left", padx=4)

                kick_btn = tk.Button(row, text="Kick", width=6, fg="red",
                                      command=lambda i=identity: self.engine.kick(i))
                kick_btn.pack(side="left", padx=4)
                self.guest_rows[identity] = {"frame": row, "slider": slider, "var": var}

    def _poll_status(self):
        if self.running:
            self._rebuild_guest_rows(self.engine.connected_guests())
        self.root.after(1000, self._poll_status)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
