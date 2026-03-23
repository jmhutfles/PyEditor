from __future__ import annotations

import time
from pathlib import Path

import cv2
from PIL import Image


class PreviewPlayer:
    def __init__(self, max_width: int = 480, max_height: int = 270) -> None:
        self.max_width = max_width
        self.max_height = max_height
        self._capture: cv2.VideoCapture | None = None
        self._duration_seconds = 0.0
        self._fps = 30.0
        self._playhead_seconds = 0.0
        self._is_playing = False
        self._last_tick = time.perf_counter()
        self._current_path: Path | None = None

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

    def load_clip(self, clip_path: Path, duration_seconds: float) -> Image.Image | None:
        if self._current_path == clip_path and self._capture is not None:
            self._duration_seconds = max(0.0, duration_seconds)
            return self.seek(self._playhead_seconds)

        self.release()
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

    def release(self) -> None:
        self._is_playing = False
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._current_path = None
        self._duration_seconds = 0.0
        self._playhead_seconds = 0.0

    def play(self) -> None:
        if self._capture is None:
            return
        self._is_playing = True
        self._last_tick = time.perf_counter()

    def pause(self) -> None:
        self._is_playing = False

    def toggle(self) -> None:
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def seek(self, seconds: float) -> Image.Image | None:
        if self._capture is None:
            return None

        bounded_seconds = max(0.0, min(seconds, self._duration_seconds))
        self._playhead_seconds = bounded_seconds
        self._capture.set(cv2.CAP_PROP_POS_MSEC, bounded_seconds * 1000.0)
        return self._read_current_frame()

    def tick(self) -> Image.Image | None:
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