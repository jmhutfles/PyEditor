from __future__ import annotations

from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any, NamedTuple

from .ffmpeg_service import render_job
from .models import RenderJob


class RenderEvent(NamedTuple):
    name: str
    args: tuple[Any, ...]


class RenderController:
    def __init__(self) -> None:
        self._queue: Queue[RenderJob] = Queue()
        self._events: Queue[RenderEvent] = Queue()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._is_running = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    def enqueue(self, job: RenderJob) -> None:
        self._queue.put(job)
        self._emit("queue_size_changed", self._queue.qsize())

    def has_pending_jobs(self) -> bool:
        return not self._queue.empty()

    def start(self) -> None:
        with self._lock:
            if self._is_running:
                return
            self._is_running = True

        self._thread = Thread(target=self._process_queue, name="pyeditor-renderer", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def poll_events(self) -> list[RenderEvent]:
        events: list[RenderEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except Empty:
                return events

    def _emit(self, name: str, *args: Any) -> None:
        self._events.put(RenderEvent(name=name, args=args))

    def _process_queue(self) -> None:
        try:
            while True:
                try:
                    job = self._queue.get_nowait()
                except Empty:
                    break

                self._emit("queue_size_changed", self._queue.qsize())
                job_key = str(job.output_path)
                self._emit("job_started", job_key)
                try:
                    render_job(
                        job,
                        progress_callback=lambda fraction: self._emit("progress_value", job_key, fraction),
                        status_callback=lambda message: self._emit("progress_message", job_key, message),
                    )
                except Exception as exc:  # noqa: BLE001
                    self._emit("job_failed", job_key, str(exc))
                else:
                    self._emit("job_finished", job_key)
                finally:
                    self._queue.task_done()
        finally:
            with self._lock:
                self._is_running = False
            self._emit("queue_empty")
