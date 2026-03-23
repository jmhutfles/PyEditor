# PyEditor

PyEditor is a small Python desktop app for preparing action-camera edits and rendering them in the background.

## What it does

- Trim GoPro clips by setting start and end times
- Combine multiple trimmed clips into one final video
- Queue multiple render jobs
- Keep preparing the next edit while the queue renders in the background
- Preview the selected clip and mark trim points from the playhead
- Drag video files directly into the clips pane
- See render progress in the app while ffmpeg is running
- Build cached low-resolution proxy previews automatically for faster scrubbing

## Tech stack

- Python 3.11+
- `tkinter` for the desktop UI
- `tkinterdnd2` for drag-and-drop clip import
- `ffmpeg` and `ffprobe` for video metadata and rendering

## Setup

1. Install Python 3.11 or newer.
2. Install `ffmpeg` and make sure both `ffmpeg` and `ffprobe` are available on your `PATH`, or download a local copy into the workspace `tools` folder.
3. Optional: create a virtual environment.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

To install a local ffmpeg copy without committing it to Git:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\setup_ffmpeg.ps1
```

## Run

```powershell
python -m src.pyeditor.main
```

## Render pipeline

Each queued job is rendered in two stages:

1. Every clip segment is trimmed into a temporary normalized MP4 file.
2. All temporary segments are concatenated into the final output.

This is intentionally simple and dependable for a first version.

## Proxy previews

- When you add a clip, the app queues a low-resolution proxy preview in the background.
- The preview uses the original clip until the proxy finishes.
- Final renders still use the original source media, not the proxy.
- Cached proxy previews are stored under `.pyeditor-cache/proxies` in the workspace.

## Notes

- The first version is trim-and-assemble focused rather than timeline-precision editing.
- Output is encoded as H.264 video with AAC audio.
- Rendering continues in the background while you keep building the next queue entry.
- Live preview uses OpenCV and Pillow inside the app window.
- Preview playback is video-only for now, which keeps trim marking responsive without needing a heavier media framework.
- The app auto-detects bundled `ffmpeg.exe` and `ffprobe.exe` under the workspace `tools` folder before falling back to system `PATH`.
- Rendering now exposes a determinate progress bar based on ffmpeg progress output.
- Proxy previews are generated as low-resolution H.264 files to make clip scrubbing more responsive.

## Git and Large Files

- Do not commit `.pyeditor-cache/`, `.venv/`, or downloaded binaries under `tools/`.
- The repository includes `.gitignore` rules for those local artifacts.
- If those files were already added to Git once, remove them from the Git index with `git rm --cached -r .pyeditor-cache tools` and commit that change.
