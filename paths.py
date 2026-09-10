"""
Path resolution that works correctly both running from source AND
packaged into a PyInstaller onefile .exe - these need DIFFERENT logic,
and getting this wrong was a real, confirmed bug (guest-page/index.html
was never being found inside the packaged .exe).

Two different needs, two different answers:
- Bundled read-only resources (guest-page/index.html): PyInstaller
  onefile extracts these to a TEMPORARY directory at runtime
  (sys._MEIPASS), deleted after the process exits.
- Persistent data we write ourselves (the TLS cert/key): must NOT go in
  that temp directory, since it's wiped every run - defeats the whole
  point of persisting the cert so guests don't see a new browser
  warning every session. Goes next to the actual .exe instead.
"""
import os
import sys


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def bundled_resource_path(relative_path: str) -> str:
    """Path to a read-only file bundled INTO the app (e.g. the guest
    join page). Must be listed in the PyInstaller build's --add-data
    for this to exist when frozen - see .github/workflows/build-release.yml."""
    if is_frozen():
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


def persistent_data_dir() -> str:
    """Directory for data WE write that must survive across runs (the
    TLS cert). Next to the actual .exe when frozen - NOT the temporary
    extraction directory, which is deleted when the process exits."""
    if is_frozen():
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return base
