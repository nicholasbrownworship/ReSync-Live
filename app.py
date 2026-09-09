"""
ReSync Live desktop app - the thing Nick actually double-clicks. Tkinter
GUI (ships in Python's standard library, no extra dependency) wrapping
the signaling/aiortc engine, which runs on its own background thread.

STATUS: first draft, not yet run. Packaging via PyInstaller into a
standalone .exe is the next step after this runs correctly from source
- see docs/ARCHITECTURE.md for the known PyAV/PyInstaller bundling risk
to test for early.

Run from source (before packaging):
    python app.py
"""
import os
import socket
import tkinter as tk
from tkinter import filedialog, messagebox

from server.engine import ResyncLiveEngine

DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "ReSyncLive Recordings")
PORT = 8765


def get_local_ip() -> str:
    """Best-effort local network IP, shown to help Nick find his public
    IP to share with guests (this is the LAN IP, not the public one -
    see the note in the GUI itself)."""
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
        self.root.geometry("420x320")

        self.output_dir = DEFAULT_OUTPUT_DIR
        self.engine = ResyncLiveEngine(output_dir=self.output_dir, port=PORT)
        self.running = False

        tk.Label(root, text="ReSync Live", font=("Segoe UI", 16, "bold")).pack(pady=(16, 4))

        self.status_label = tk.Label(root, text="Stopped", fg="gray")
        self.status_label.pack()

        self.ip_label = tk.Label(
            root,
            text=f"Local IP: {get_local_ip()}:{PORT}\n(share your PUBLIC IP with guests, not this)",
            justify="center",
            fg="#555",
        )
        self.ip_label.pack(pady=(8, 8))

        self.toggle_btn = tk.Button(root, text="Start Session", width=20, command=self.toggle)
        self.toggle_btn.pack(pady=8)

        tk.Label(root, text="Connected guests:").pack(pady=(16, 0))
        self.guests_label = tk.Label(root, text="(none)", fg="#333")
        self.guests_label.pack()

        folder_frame = tk.Frame(root)
        folder_frame.pack(pady=(20, 0))
        tk.Label(folder_frame, text="Recordings folder:").pack(side="left")
        tk.Button(folder_frame, text="Choose...", command=self.choose_folder).pack(side="left", padx=6)

        self.folder_label = tk.Label(root, text=self.output_dir, fg="#555", wraplength=380)
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
        else:
            os.makedirs(self.output_dir, exist_ok=True)
            self.engine.start()
            self.status_label.config(text="Running", fg="green")
            self.toggle_btn.config(text="Stop Session")
        self.running = not self.running

    def _poll_status(self):
        if self.running:
            guests = self.engine.connected_guests()
            self.guests_label.config(text=", ".join(guests) if guests else "(none)")
        self.root.after(1000, self._poll_status)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
