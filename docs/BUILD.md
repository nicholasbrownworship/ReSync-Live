# Building the desktop app

Not yet verified - this is the planned approach, flagged in
docs/ARCHITECTURE.md as having known PyInstaller/PyAV bundling risk to
test for before relying on it.

## Run from source first

    pip install -r requirements.txt
    python app.py

Confirm this actually works (signaling, aiortc, recording, GUI) before
attempting to package it - packaging failures are much easier to debug
against known-working source than to debug blind.

## Package into a standalone .exe (Windows)

    pyinstaller --onefile --windowed --name "ReSync Live" app.py

If this fails with missing-module errors related to `av` (PyAV) - a
known, documented issue with PyInstaller and PyAV's native libraries -
the next step is adding explicit `--hidden-import` flags or a PyInstaller
hook for `av`, not immediately assuming the whole approach is broken.
Search PyAV's GitHub issues for "pyinstaller" for current workarounds,
since these have changed across PyInstaller/PyAV versions.

The resulting `.exe` (in `dist/`) is what actually gets double-clicked -
this is the deliverable, not `python app.py`.
