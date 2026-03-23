from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass(slots=True)
class ClipSegment:
    source_path: Path
    start_seconds: float = 0.0
    end_seconds: float | None = None
    duration_seconds: float | None = None
    proxy_path: Path | None = None
    proxy_status: str = "pending"
    proxy_error: str | None = None

    @property
    def display_name(self) -> str:
        return self.source_path.name

    @property
    def effective_end(self) -> float | None:
        if self.end_seconds is not None:
            return self.end_seconds
        return self.duration_seconds

    @property
    def trim_duration(self) -> float | None:
        effective_end = self.effective_end
        if effective_end is None:
            return None
        return max(0.0, effective_end - self.start_seconds)

    @property
    def preview_path(self) -> Path:
        if self.proxy_path is not None:
            return self.proxy_path
        return self.source_path


@dataclass(slots=True)
class RenderJob:
    output_path: Path
    clips: List[ClipSegment] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.output_path.name
