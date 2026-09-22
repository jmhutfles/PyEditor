from __future__ import annotations

from collections import deque
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any, NamedTuple

from .proxy_service import ensure_proxy


class ProxyEvent(NamedTuple):
    name: str
    args: tuple[Any, ...]


class ProxyTask(NamedTuple):
    source_path: str
    duration_seconds: float | None


class ProxyController:
    def __init__(self) -> None:
        self._events: Queue[ProxyEvent] = Queue()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._is_running = False
        self._tasks: deque[ProxyTask] = deque()
        self._pending_paths: set[str] = set()
        self._active_path: str | None = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    @property
    def queue_size(self) -> int:
        with self._lock:
            return len(self._tasks)

    def enqueue(self, source_path: str, duration_seconds: float | None = None, prioritize: bool = False) -> None:
        should_start = False
        with self._lock:
            if source_path == self._active_path:
                return

            existing_index = next((index for index, task in enumerate(self._tasks) if task.source_path == source_path), None)
            if existing_index is not None:
                existing_task = self._tasks[existing_index]
                updated_task = existing_task
                if duration_seconds is not None and existing_task.duration_seconds is None:
                    updated_task = ProxyTask(source_path=source_path, duration_seconds=duration_seconds)
                    self._tasks[existing_index] = updated_task
                if prioritize and existing_index != 0:
                    del self._tasks[existing_index]
                    self._tasks.appendleft(updated_task)
            elif source_path not in self._pending_paths:
                task = ProxyTask(source_path=source_path, duration_seconds=duration_seconds)
                if prioritize:
                    self._tasks.appendleft(task)
                else:
                    self._tasks.append(task)
                self._pending_paths.add(source_path)
                should_start = not self._is_running
                if should_start:
                    self._is_running = True

        self._emit("queue_size_changed", self.queue_size)
        if should_start:
            self._thread = Thread(target=self._process_queue, name="pyeditor-proxy-builder", daemon=True)
            self._thread.start()

    def poll_events(self) -> list[ProxyEvent]:
        events: list[ProxyEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except Empty:
                return events

    def shutdown(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _emit(self, name: str, *args: Any) -> None:
        self._events.put(ProxyEvent(name=name, args=args))

    def _process_queue(self) -> None:
        try:
            while True:
                with self._lock:
                    if not self._tasks:
                        self._active_path = None
                        self._is_running = False
                        break
                    task = self._tasks.popleft()
                    self._active_path = task.source_path

                self._emit("queue_size_changed", self.queue_size)
                self._emit("proxy_started", task.source_path)
                try:
                    proxy_path = ensure_proxy(
                        Path(task.source_path),
                        duration_seconds=task.duration_seconds,
                        progress_callback=lambda fraction, source_path=task.source_path: self._emit(
                            "proxy_progress", source_path, fraction
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    self._emit("proxy_failed", task.source_path, str(exc))
                else:
                    self._emit("proxy_finished", task.source_path, str(proxy_path))
                finally:
                    with self._lock:
                        self._pending_paths.discard(task.source_path)
                        self._active_path = None
        finally:
            with self._lock:
                self._active_path = None
            self._emit("idle")