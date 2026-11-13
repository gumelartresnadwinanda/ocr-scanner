"""Command-line interface for scanning a video for on-screen text and @handles."""

from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import scan_video


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-scanner",
        description="Extract distinct on-screen text and @handles from a video with example frames.",
    )
    parser.add_argument("video", type=Path, help="Path to the video file to scan.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("outputs"),
        help="Root output directory for reports and example frames (default: outputs).",
    )
    parser.add_argument(
        "--sample-fps",
        type=float,
        default=2.0,
        help="Sampling rate in frames per second (default: 2.0).",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.55,
        help="Minimum OCR confidence score in [0, 1] (default: 0.55).",
    )
    parser.add_argument(
        "--allow-non-latin",
        action="store_true",
        help="Allow non-Latin scripts instead of filtering to English/Latin text.",
    )
    parser.add_argument(
        "--mode",
        choices=["full_text", "handle_only"],
        default="full_text",
        help="full_text scans all frames; handle_only stops as soon as an @handle is found.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = scan_video(
        video_path=args.video,
        output_root=args.out_dir,
        sample_fps=args.sample_fps,
        min_confidence=args.min_confidence,
        latin_only=not args.allow_non_latin,
        scan_mode=args.mode,
    )

    print(f"Video: {result.metadata.video_name} ({result.metadata.width}x{result.metadata.height}, {result.metadata.duration_sec:.2f}s)")
    mode_note = f"{result.scan_mode} (stopped early at @handle)" if result.stopped_early else result.scan_mode
    print(f"Mode: {mode_note} | Frames: {result.sampled_frames} sampled | {result.ocr_frames} OCR'd | {result.skipped_frames} skipped in {result.elapsed_sec:.2f}s")
    print(f"Has text: {result.has_text} | Distinct items: {len(result.distinct_texts)}")
    print(f"@Handles: {', '.join(result.handles) if result.handles else 'None'}")
    print("-" * 60)
    for entry in result.distinct_texts:
        kind = "@HANDLE" if entry.is_handle else "TEXT"
        preview = entry.text.replace("\n", " / ")
        print(
            f"[{entry.id}] ({kind}, conf={entry.confidence:.3f}, "
            f"{entry.first_seen_sec:.2f}s-{entry.last_seen_sec:.2f}s, {entry.occurrences}x): "
            f"{preview!r} -> {entry.boxed_frame_path}"
        )
    print("-" * 60)
    print(f"HTML report: {result.html_path}")
    print(f"JSON result: {result.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
