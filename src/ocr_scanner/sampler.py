"""Adaptive video frame sampling with block-max pixel-diff change detection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from .models import SampledFrame, VideoMetadata


@dataclass(slots=True)
class FrameStep:
    """Yielded step for each sampled timestamp in a video."""

    frame_index: int
    timestamp_sec: float
    should_ocr: bool
    frame: SampledFrame | None


def read_video_metadata(video_path: str | Path) -> VideoMetadata:
    """Inspect video dimensions, FPS, duration, and frame count."""
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {path}")

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0.0:
            fps = 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration_sec = total_frames / fps if total_frames > 0 else 0.0
        return VideoMetadata(
            video_path=str(path),
            video_name=path.name,
            duration_sec=duration_sec,
            fps=fps,
            width=width,
            height=height,
            total_frames=total_frames,
        )
    finally:
        cap.release()


def _compute_thumbnail(image: np.ndarray, thumb_width: int = 144) -> np.ndarray:
    """Create a blurred grayscale thumbnail for robust frame-diff comparison."""
    h, w = image.shape[:2]
    scale = thumb_width / max(1, w)
    thumb_h = max(16, int(round(h * scale)))
    small = cv2.resize(image, (thumb_width, thumb_h), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (3, 3), 0)


def _block_max_mad(prev_thumb: np.ndarray, curr_thumb: np.ndarray, grid_rows: int = 12, grid_cols: int = 8) -> tuple[float, float]:
    """Return (global_mad, max_tile_mad) so small corner changes still trigger OCR."""
    diff = cv2.absdiff(prev_thumb, curr_thumb).astype(np.float32)
    global_mad = float(np.mean(diff))

    h, w = diff.shape
    row_step = max(1, h // grid_rows)
    col_step = max(1, w // grid_cols)
    max_tile = 0.0
    for r in range(0, h, row_step):
        r_end = min(h, r + row_step)
        for c in range(0, w, col_step):
            c_end = min(w, c + col_step)
            tile_mean = float(np.mean(diff[r:r_end, c:c_end]))
            if tile_mean > max_tile:
                max_tile = tile_mean
    return global_mad, max_tile


def iter_sampled_frames(
    video_path: str | Path,
    sample_fps: float = 2.0,
    global_diff_thresh: float = 1.8,
    tile_diff_thresh: float = 3.8,
) -> Iterator[FrameStep]:
    """Iterate sampled frames sequentially at sample_fps, always including first & last frame.

    Frames whose global and max-tile visual difference from the previous OCR'd frame
    fall below thresholds are marked should_ocr=False so downstream can skip redundant OCR
    while still updating active track timestamps.
    """
    path = Path(video_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {path}")

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0.0:
            fps = 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        interval_frames = max(1, int(round(fps / max(0.1, sample_fps))))

        last_ocr_thumb: np.ndarray | None = None
        last_valid_idx = -1
        last_valid_img: np.ndarray | None = None
        last_yielded_idx = -1

        idx = 0
        while True:
            ok = cap.grab()
            if not ok:
                break

            is_target = idx == 0 or (idx % interval_frames == 0) or (total_frames > 0 and idx == total_frames - 1)
            ret, frame = cap.retrieve()
            if not ret or frame is None:
                idx += 1
                continue

            last_valid_idx = idx
            last_valid_img = frame

            if is_target:
                ts = idx / fps
                thumb = _compute_thumbnail(frame)
                if last_ocr_thumb is None:
                    should_ocr = True
                else:
                    global_mad, max_tile = _block_max_mad(last_ocr_thumb, thumb)
                    should_ocr = global_mad >= global_diff_thresh or max_tile >= tile_diff_thresh

                if should_ocr:
                    last_ocr_thumb = thumb
                    yield FrameStep(
                        frame_index=idx,
                        timestamp_sec=ts,
                        should_ocr=True,
                        frame=SampledFrame(frame_index=idx, timestamp_sec=ts, image=frame),
                    )
                else:
                    yield FrameStep(
                        frame_index=idx,
                        timestamp_sec=ts,
                        should_ocr=False,
                        frame=None,
                    )
                last_yielded_idx = idx

            idx += 1

        # Guarantee the final decoded frame is evaluated if total_frames metadata was slightly off
        if last_valid_img is not None and last_valid_idx > last_yielded_idx:
            ts = last_valid_idx / fps
            thumb = _compute_thumbnail(last_valid_img)
            should_ocr = True
            if last_ocr_thumb is not None:
                global_mad, max_tile = _block_max_mad(last_ocr_thumb, thumb)
                should_ocr = global_mad >= global_diff_thresh or max_tile >= tile_diff_thresh
            yield FrameStep(
                frame_index=last_valid_idx,
                timestamp_sec=ts,
                should_ocr=should_ocr,
                frame=SampledFrame(frame_index=last_valid_idx, timestamp_sec=ts, image=last_valid_img) if should_ocr else None,
            )
    finally:
        cap.release()
