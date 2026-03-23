# Local Tools

This folder is for machine-local helper binaries that should not be committed to Git.

## ffmpeg setup

Run the PowerShell helper below to download a local ffmpeg build into this folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\setup_ffmpeg.ps1
```

The app automatically detects `ffmpeg.exe` and `ffprobe.exe` under `tools/`.