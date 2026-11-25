"""End-to-end single-video OCR scanning pipeline."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import numpy as np

from .dedup import build_distinct_entries, deduplicate_observations
from .grouping import HANDLE_TOKEN_RE, group_detections
from .models import BlockObservation, TextBlock, VideoScanResult
from .ocr_engine import OcrEngine
from .report import save_example_frames, write_html_report, write_json_report
from .sampler import iter_sampled_frames, read_video_metadata

VALID_SCAN_MODES = frozenset({"full_text", "handle_only"})


def _has_complete_handle(blocks: list[TextBlock]) -> bool:
    """Return True when at least one non-truncated @handle is present."""
    for block in blocks:
        if not block.handles:
            continue
        for match in HANDLE_TOKEN_RE.finditer(block.text):
            raw_token = match.group(0)
            if len(raw_token.rstrip(".")) < 3:
                continue
            trailing = block.text[match.end() :]
            if raw_token.endswith("..") or trailing.startswith(("..", "…")):
                continue
            return True
    return False


def scan_video(
    video_path: str | Path,
    output_root: str | Path = "outputs",
    sample_fps: float = 2.0,
    min_confidence: float = 0.55,
    latin_only: bool = True,
    engine: OcrEngine | None = None,
    scan_id: str | None = None,
    scan_mode: str = "full_text",
    write_html: bool = True,
    cancel_check: Callable[[], bool] | None = None,
) -> VideoScanResult:
    """Scan one video for distinct on-screen text and save frames, JSON, and HTML report."""
    if scan_mode not in VALID_SCAN_MODES:
        raise ValueError(f"Unsupported scan_mode '{scan_mode}'. Expected 'full_text' or 'handle_only'.")

    t0 = time.perf_counter()
    path = Path(video_path)
    metadata = read_video_metadata(path)
    ocr = engine or OcrEngine(min_confidence=min_confidence, latin_only=latin_only)

    observations: list[BlockObservation] = []
    frame_cache: dict[int, tuple[float, np.ndarray]] = {}
    last_seen_overrides: dict[int, float] = {}

    sampled_count = 0
    ocr_count = 0
    skipped_count = 0
    last_ocr_frame_idx: int | None = None
    stopped_early = False

    for step in iter_sampled_frames(path, sample_fps=sample_fps):
        if cancel_check and cancel_check():
            stopped_early = True
            break

        sampled_count += 1
        if not step.should_ocr or step.frame is None:
            skipped_count += 1
            if last_ocr_frame_idx is not None:
                last_seen_overrides[last_ocr_frame_idx] = step.timestamp_sec
            continue

        ocr_count += 1
        last_ocr_frame_idx = step.frame_index
        detections = ocr.detect(step.frame.image)
        blocks = group_detections(detections)
        if blocks:
            frame_cache[step.frame_index] = (step.timestamp_sec, step.frame.image)
            for block in blocks:
                observations.append(
                    BlockObservation(
                        frame_index=step.frame_index,
                        timestamp_sec=step.timestamp_sec,
                        block=block,
                    )
                )
            if scan_mode == "handle_only" and _has_complete_handle(blocks):
                stopped_early = True
                break

    clustered = deduplicate_observations(
        observations,
        frame_w=metadata.width,
        frame_h=metadata.height,
    )

    out_dir = Path(output_root) / (scan_id or path.stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    clean_paths, boxed_paths = save_example_frames(out_dir, frame_cache, clustered)
    distinct_entries = build_distinct_entries(
        clustered,
        clean_frame_paths=clean_paths,
        boxed_frame_paths=boxed_paths,
        last_seen_overrides=last_seen_overrides,
    )

    # Collect unique handles preserving order of first appearance
    seen_handles: set[str] = set()
    all_handles: list[str] = []
    for entry in distinct_entries:
        for h in entry.handles:
            key = h.lower()
            if key not in seen_handles:
                seen_handles.add(key)
                all_handles.append(h)

    json_path = out_dir / "result.json"
    html_path = out_dir / "report.html"
    elapsed = time.perf_counter() - t0

    result = VideoScanResult(
        metadata=metadata,
        sampled_frames=sampled_count,
        ocr_frames=ocr_count,
        skipped_frames=skipped_count,
        elapsed_sec=elapsed,
        has_text=bool(distinct_entries),
        handles=all_handles,
        distinct_texts=distinct_entries,
        output_dir=str(out_dir.resolve()),
        json_path=str(json_path.resolve()),
        html_path=str(html_path.resolve()) if write_html else "",
        scan_mode=scan_mode,
        stopped_early=stopped_early,
    )

    write_json_report(result, json_path)
    if write_html:
        write_html_report(result, html_path)
    return result
