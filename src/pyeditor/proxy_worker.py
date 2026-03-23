from __future__ import annotations

from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any, NamedTuple

from .proxy_service import ensure_proxy


class ProxyEvent(NamedTuple):
    name: str
    args: tuple[Any, ...]


class ProxyController:
    def __init__(self) -> None:
        self._queue: Queue[str] = Queue()
        self._events: Queue[ProxyEvent] = Queue()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._is_running = False
        self._pending_paths: set[str] = set()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    def enqueue(self, source_path: str) -> None:
        with self._lock:
            if source_path in self._pending_paths:
                return
            self._pending_paths.add(source_path)
            self._queue.put(source_path)
            should_start = not self._is_running
            if should_start:
                self._is_running = True

        self._emit("queue_size_changed", self._queue.qsize())
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
                try:
                    source_path = self._queue.get_nowait()
                except Empty:
                    break

                self._emit("queue_size_changed", self._queue.qsize())
                self._emit("proxy_started", source_path)
                try:
                    proxy_path = ensure_proxy(Path(source_path))
                except Exception as exc:  # noqa: BLE001
                    self._emit("proxy_failed", source_path, str(exc))
                else:
                    self._emit("proxy_finished", source_path, str(proxy_path))
                finally:
                    with self._lock:
                        self._pending_paths.discard(source_path)
                    self._queue.task_done()
        finally:
            with self._lock:
                self._is_running = False
            self._emit("idle")


from pathlib import Path