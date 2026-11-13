"""Data models for the video OCR scanner pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(slots=True)
class SampledFrame:
    """A decoded video frame selected for potential OCR processing."""

    frame_index: int
    timestamp_sec: float
    image: np.ndarray  # BGR uint8 image


@dataclass(slots=True)
class RawDetection:
    """A single OCR text line detection from a frame."""

    bbox: list[tuple[float, float]]
    x1: float
    y1: float
    x2: float
    y2: float
    text: str
    confidence: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def x_center(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def y_center(self) -> float:
        return (self.y1 + self.y2) / 2.0


@dataclass(slots=True)
class TextBlock:
    """A spatially grouped text block (single handle or multi-line caption)."""

    text: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    lines: list[RawDetection]
    is_handle: bool = False
    handles: list[str] = field(default_factory=list)

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)


@dataclass(slots=True)
class BlockObservation:
    """One occurrence of a TextBlock at a specific sampled frame."""

    frame_index: int
    timestamp_sec: float
    block: TextBlock


@dataclass(slots=True)
class DistinctTextEntry:
    """A deduplicated distinct text item and its best example frame."""

    id: int
    text: str
    is_handle: bool
    handles: list[str]
    confidence: float
    first_seen_sec: float
    last_seen_sec: float
    occurrences: int
    timestamps_sec: list[float]
    best_frame_index: int
    best_timestamp_sec: float
    best_bbox: tuple[int, int, int, int]
    clean_frame_path: str
    boxed_frame_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "is_handle": self.is_handle,
            "handles": self.handles,
            "confidence": round(self.confidence, 4),
            "first_seen_sec": round(self.first_seen_sec, 3),
            "last_seen_sec": round(self.last_seen_sec, 3),
            "occurrences": self.occurrences,
            "timestamps_sec": [round(t, 3) for t in self.timestamps_sec],
            "best_frame_index": self.best_frame_index,
            "best_timestamp_sec": round(self.best_timestamp_sec, 3),
            "best_bbox": list(self.best_bbox),
            "clean_frame_path": self.clean_frame_path,
            "boxed_frame_path": self.boxed_frame_path,
        }


@dataclass(slots=True)
class VideoMetadata:
    """Basic video stream properties."""

    video_path: str
    video_name: str
    duration_sec: float
    fps: float
    width: int
    height: int
    total_frames: int


@dataclass(slots=True)
class VideoScanResult:
    """Full scan result for a single video."""

    metadata: VideoMetadata
    sampled_frames: int
    ocr_frames: int
    skipped_frames: int
    elapsed_sec: float
    has_text: bool
    handles: list[str]
    distinct_texts: list[DistinctTextEntry]
    output_dir: str
    json_path: str
    html_path: str
    scan_mode: str = "full_text"
    stopped_early: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_path": self.metadata.video_path,
            "video_name": self.metadata.video_name,
            "duration_sec": round(self.metadata.duration_sec, 3),
            "fps": round(self.metadata.fps, 3),
            "width": self.metadata.width,
            "height": self.metadata.height,
            "total_frames": self.metadata.total_frames,
            "scan_mode": self.scan_mode,
            "stopped_early": self.stopped_early,
            "sampled_frames": self.sampled_frames,
            "ocr_frames": self.ocr_frames,
            "skipped_frames": self.skipped_frames,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "has_text": self.has_text,
            "handles": self.handles,
            "distinct_count": len(self.distinct_texts),
            "distinct_texts": [entry.to_dict() for entry in self.distinct_texts],
            "output_dir": self.output_dir,
            "json_path": self.json_path,
            "html_path": self.html_path,
        }
