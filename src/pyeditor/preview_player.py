from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import cv2
from PIL import Image

try:
    import vlc
except ImportError:
    vlc = None


def _vlc_runtime_dirs() -> list[Path]:
    return [
        Path("C:/Program Files/VideoLAN/VLC"),
        Path("C:/Program Files (x86)/VideoLAN/VLC"),
    ]


def _find_vlc_runtime_dir() -> Path | None:
    for runtime_dir in _vlc_runtime_dirs():
        if runtime_dir.exists():
            return runtime_dir
    return None


class PreviewPlayer:
    def __init__(self, max_width: int = 480, max_height: int = 270) -> None:
        self.max_width = max_width
        self.max_height = max_height
        self._capture: cv2.VideoCapture | None = None
        self._video_widget_id: int | None = None
        self._vlc_instance = self._create_vlc_instance()
        self._vlc_player = self._vlc_instance.media_player_new() if self._vlc_instance is not None else None
        self._duration_seconds = 0.0
        self._fps = 30.0
        self._playhead_seconds = 0.0
        self._is_playing = False
        self._last_tick = time.perf_counter()
        self._current_path: Path | None = None
        self._active_backend = "opencv"
        self._backend_message = "Preview backend: OpenCV"
        if self._vlc_player is not None:
            self._vlc_player.audio_set_mute(True)
            self._backend_message = "Preview backend: VLC"

    @property
    def duration_seconds(self) -> float:
        return self._duration_seconds

    @property
    def playhead_seconds(self) -> float:
        return self._playhead_seconds

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    @property
    def uses_embedded_video(self) -> bool:
        return self._active_backend == "vlc" and self._current_path is not None

    @property
    def backend_message(self) -> str:
        return self._backend_message

    def attach_video_widget(self, widget_id: int) -> None:
        self._video_widget_id = widget_id
        self._bind_vlc_output_window()

    def load_clip(self, clip_path: Path, duration_seconds: float) -> Image.Image | None:
        if self.uses_embedded_video and self._current_path == clip_path and self._vlc_player is not None:
            self._duration_seconds = max(0.0, duration_seconds)
            return self.seek(self._playhead_seconds)

        if self._current_path == clip_path and self._capture is not None:
            self._duration_seconds = max(0.0, duration_seconds)
            return self.seek(self._playhead_seconds)

        if self._vlc_player is not None and self._video_widget_id is not None:
            return self._load_vlc_clip(clip_path, duration_seconds)

        self._active_backend = "opencv"
        self._backend_message = "Preview backend: OpenCV"
        return self._load_opencv_clip(clip_path, duration_seconds)

    def _load_opencv_clip(self, clip_path: Path, duration_seconds: float) -> Image.Image | None:
        self.release()
        self._active_backend = "opencv"
        self._backend_message = "Preview backend: OpenCV"

        capture = cv2.VideoCapture(str(clip_path))
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"Could not open preview for {clip_path}")

        self._capture = capture
        self._current_path = clip_path
        fps = capture.get(cv2.CAP_PROP_FPS)
        if fps and fps > 1:
            self._fps = fps
        else:
            self._fps = 30.0

        self._duration_seconds = max(0.0, duration_seconds)
        self._playhead_seconds = 0.0
        self._is_playing = False
        self._last_tick = time.perf_counter()
        return self.seek(0.0)

    def _load_vlc_clip(self, clip_path: Path, duration_seconds: float) -> Image.Image | None:
        self.release()
        self._active_backend = "vlc"
        self._backend_message = "Preview backend: VLC"
        self._current_path = clip_path
        self._duration_seconds = max(0.0, duration_seconds)
        self._playhead_seconds = 0.0
        self._is_playing = False
        self._last_tick = time.perf_counter()

        assert self._vlc_instance is not None
        assert self._vlc_player is not None

        media = self._vlc_instance.media_new_path(str(clip_path))
        self._vlc_player.set_media(media)
        self._vlc_player.audio_set_mute(True)
        self._bind_vlc_output_window()
        self._vlc_player.play()
        self._wait_for_vlc_ready()
        self._vlc_player.set_pause(1)
        self.seek(0.0)
        return None

    def release(self) -> None:
        self._is_playing = False
        if self._vlc_player is not None:
            self._vlc_player.stop()
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._current_path = None
        self._duration_seconds = 0.0
        self._playhead_seconds = 0.0

    def play(self) -> None:
        if self.uses_embedded_video:
            if self._vlc_player is None:
                return
            self._vlc_player.play()
            self._is_playing = True
            self._last_tick = time.perf_counter()
            return

        if self._capture is None:
            return
        self._is_playing = True
        self._last_tick = time.perf_counter()

    def pause(self) -> None:
        if self.uses_embedded_video and self._vlc_player is not None:
            self._vlc_player.set_pause(1)
        self._is_playing = False

    def toggle(self) -> None:
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def seek(self, seconds: float) -> Image.Image | None:
        if self.uses_embedded_video:
            if self._vlc_player is None:
                return None
            bounded_seconds = max(0.0, min(seconds, self._duration_seconds or seconds))
            self._playhead_seconds = bounded_seconds
            self._vlc_player.set_time(int(round(bounded_seconds * 1000.0)))
            return None

        if self._capture is None:
            return None

        bounded_seconds = max(0.0, min(seconds, self._duration_seconds))
        self._playhead_seconds = bounded_seconds
        self._capture.set(cv2.CAP_PROP_POS_MSEC, bounded_seconds * 1000.0)
        return self._read_current_frame()

    def tick(self) -> Image.Image | None:
        if self.uses_embedded_video:
            if self._vlc_player is None:
                return None

            current_time_ms = self._vlc_player.get_time()
            media_length_ms = self._vlc_player.get_length()
            if media_length_ms and media_length_ms > 0 and media_length_ms / 1000.0 > self._duration_seconds:
                self._duration_seconds = media_length_ms / 1000.0
            if current_time_ms is not None and current_time_ms >= 0:
                bounded_seconds = current_time_ms / 1000.0
                if self._duration_seconds > 0:
                    bounded_seconds = min(bounded_seconds, self._duration_seconds)
                self._playhead_seconds = max(0.0, bounded_seconds)

            self._is_playing = bool(self._vlc_player.is_playing())
            if not self._is_playing and self._duration_seconds > 0 and self._playhead_seconds >= self._duration_seconds - 0.05:
                self._playhead_seconds = self._duration_seconds
            return None

        if self._capture is None or not self._is_playing:
            return None

        now = time.perf_counter()
        elapsed = now - self._last_tick
        self._last_tick = now

        next_position = self._playhead_seconds + elapsed
        if next_position >= self._duration_seconds:
            self._playhead_seconds = self._duration_seconds
            self._is_playing = False
            return self.seek(self._duration_seconds)
        return self.seek(next_position)

    def _read_current_frame(self) -> Image.Image | None:
        if self._capture is None:
            return None

        ok, frame = self._capture.read()
        if not ok:
            return None

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb_frame)
        image.thumbnail((self.max_width, self.max_height))
        return image

    def _create_vlc_instance(self):
        if vlc is None:
            return None

        runtime_dir = _find_vlc_runtime_dir()
        if hasattr(os, "add_dll_directory"):
            for candidate_dir in _vlc_runtime_dirs():
                if candidate_dir.exists():
                    os.add_dll_directory(str(candidate_dir))

        if runtime_dir is not None:
            plugin_dir = runtime_dir / "plugins"
            if plugin_dir.exists():
                os.environ.setdefault("VLC_PLUGIN_PATH", str(plugin_dir))

        try:
            return vlc.Instance(
                "--no-video-title-show",
                "--quiet",
                "--verbose=-1",
                "--no-plugins-cache",
            )
        except Exception:
            self._backend_message = "Preview backend: OpenCV"
            return None

    def _bind_vlc_output_window(self) -> None:
        if self._vlc_player is None or self._video_widget_id is None:
            return

        if sys.platform.startswith("win"):
            self._vlc_player.set_hwnd(self._video_widget_id)
        elif sys.platform.startswith("linux"):
            self._vlc_player.set_xwindow(self._video_widget_id)
        elif sys.platform == "darwin":
            self._vlc_player.set_nsobject(self._video_widget_id)

    def _wait_for_vlc_ready(self) -> None:
        if self._vlc_player is None or vlc is None:
            return

        deadline = time.perf_counter() + 1.0
        warm_states = {vlc.State.NothingSpecial, vlc.State.Opening, vlc.State.Buffering}
        while time.perf_counter() < deadline:
            if self._vlc_player.get_state() not in warm_states:
                break
            time.sleep(0.02)