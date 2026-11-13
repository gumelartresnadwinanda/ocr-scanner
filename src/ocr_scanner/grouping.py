"""Spatial line merging and region-based text block grouping."""

from __future__ import annotations

import re
from pathlib import Path

from .models import RawDetection, TextBlock

HANDLE_TOKEN_RE = re.compile(r"@[A-Za-z0-9_.]{2,}")
STANDALONE_HANDLE_RE = re.compile(r"^@[A-Za-z0-9_.]{2,}$")
PLATFORM_LABELS = frozenset({"tiktok", "instagram", "reels", "youtube", "shorts", "capcut"})
BARE_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.]{3,}$")

_BANG_FILENAME_RE = re.compile(r"^@?([A-Za-z0-9_.]{2,50}?)!!(.+)$")
_DUNDER_FILENAME_RE = re.compile(r"^@?([A-Za-z0-9_.]{2,50})(?:__(?!_))(.+)$")
_TRAIL_ID_FILENAME_RE = re.compile(r"^@?([A-Za-z0-9_.]{3,50}?)_\d{5,}(?:\s*\(\d+\))?$")
_AT_START_FILENAME_RE = re.compile(r"^@([A-Za-z0-9_.]{2,50})(?:$|[\s-])")
_HEX_OR_UUID_RE = re.compile(
    r"^(?:[0-9a-f]{16,}|[0-9a-f]{8}-[0-9a-f-]{18,})(?:\s*\(\d+\))?$",
    re.IGNORECASE,
)
_META_CDN_RE = re.compile(r"^AQ[A-Za-z0-9_-]{18,}$")
_GENERIC_FILENAME_PREFIXES = frozenset(
    {
        "instagram",
        "tiktok",
        "douyin",
        "reels",
        "shorts",
        "snaptik",
        "ssstik",
        "snapinsta",
        "video",
        "vid",
        "clip",
        "download",
        "downloaded",
        "untitled",
        "img",
        "image",
        "screen",
        "recording",
        "sample",
        "test",
        "demo",
        "tmp",
        "temp",
        "output",
        "unknown",
        "none",
        "null",
    }
)


def suggest_handle_from_filename(video_name: str) -> str | None:
    """Infer a likely creator handle/folder name from downloader filename conventions."""
    stem = Path(video_name).stem.strip()
    if (
        not stem
        or stem.startswith("#")
        or _HEX_OR_UUID_RE.match(stem)
        or _META_CDN_RE.match(stem)
    ):
        return None

    candidate: str | None = None
    for pattern in (
        _BANG_FILENAME_RE,
        _DUNDER_FILENAME_RE,
        _TRAIL_ID_FILENAME_RE,
        _AT_START_FILENAME_RE,
    ):
        match = pattern.match(stem)
        if match:
            candidate = match.group(1)
            break

    if not candidate:
        return None

    cleaned = candidate.lstrip("@").strip().rstrip(". ")
    if len(cleaned) < 2 or cleaned.isdigit() or not any(ch.isalpha() for ch in cleaned):
        return None
    if (
        cleaned.lower() in _GENERIC_FILENAME_PREFIXES
        or _HEX_OR_UUID_RE.match(cleaned)
        or _META_CDN_RE.match(cleaned)
    ):
        return None
    return cleaned


def extract_handles(text: str) -> list[str]:
    """Extract unique @handles found inside text in order of appearance."""
    seen: set[str] = set()
    handles: list[str] = []
    for match in HANDLE_TOKEN_RE.finditer(text):
        token = match.group(0).rstrip(".")
        if len(token) < 3:
            continue
        key = token.lower()
        if key not in seen:
            seen.add(key)
            handles.append(token)
    return handles


def _merge_horizontal_line_parts(detections: list[RawDetection]) -> list[RawDetection]:
    """Merge boxes that lie on the same horizontal baseline (e.g. '@' + 'username')."""
    if not detections:
        return []

    ordered = sorted(detections, key=lambda d: (d.y_center, d.x1))
    lines: list[list[RawDetection]] = []

    for det in ordered:
        placed = False
        for line in lines:
            ref = line[-1]
            min_h = max(1.0, min(ref.height, det.height))
            max_h = max(ref.height, det.height)
            y_overlap = min(ref.y2, det.y2) - max(ref.y1, det.y1)
            if y_overlap >= 0.45 * min_h and (max_h / min_h) <= 1.7:
                line.append(det)
                placed = True
                break
        if not placed:
            lines.append([det])

    merged: list[RawDetection] = []
    for line in lines:
        line.sort(key=lambda d: d.x1)
        current = line[0]
        for nxt in line[1:]:
            min_h = max(1.0, min(current.height, nxt.height))
            x_gap = nxt.x1 - current.x2
            max_gap = 0.65 * min_h if current.text.strip() == "@" else 0.45 * min_h
            if -0.5 * min_h <= x_gap <= max_gap:
                joiner = "" if (current.text.strip() == "@" or x_gap <= 0.08 * min_h) else " "
                combined_text = f"{current.text}{joiner}{nxt.text}".strip()
                combined_conf = min(current.confidence, nxt.confidence)
                x1 = min(current.x1, nxt.x1)
                y1 = min(current.y1, nxt.y1)
                x2 = max(current.x2, nxt.x2)
                y2 = max(current.y2, nxt.y2)
                current = RawDetection(
                    bbox=[(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    text=combined_text,
                    confidence=combined_conf,
                )
            else:
                merged.append(current)
                current = nxt
        merged.append(current)
    return merged


def _drop_single_char_icon_noise(detections: list[RawDetection]) -> list[RawDetection]:
    """Remove standalone 1-character non-digit detections (typically icons/logos like 'Q', 'P', 'S')."""
    kept: list[RawDetection] = []
    for det in detections:
        stripped = det.text.strip()
        if len(stripped) == 1 and not stripped.isdigit():
            continue
        kept.append(det)
    return kept


def _should_group_vertically(prev: RawDetection, curr: RawDetection) -> bool:
    """Decide whether two vertically adjacent lines belong to the same text block."""
    prev_text = prev.text.strip()
    curr_text = curr.text.strip()

    # Keep standalone @handles separate from platform watermarks or nearby captions
    if STANDALONE_HANDLE_RE.match(prev_text) or STANDALONE_HANDLE_RE.match(curr_text):
        return False

    # Keep platform logo text ('TikTok', etc.) separate from a handle missing '@' directly underneath
    if prev_text.lower() in PLATFORM_LABELS and BARE_HANDLE_RE.match(curr_text):
        return False
    if curr_text.lower() in PLATFORM_LABELS and BARE_HANDLE_RE.match(prev_text):
        return False

    min_h = max(1.0, min(prev.height, curr.height))
    max_h = max(prev.height, curr.height)
    if (max_h / min_h) > 1.75:
        return False

    y_gap = curr.y1 - prev.y2
    if y_gap < -0.25 * min_h or y_gap > 0.9 * min_h:
        return False

    x_overlap = min(prev.x2, curr.x2) - max(prev.x1, curr.x1)
    min_w = max(1.0, min(prev.width, curr.width))
    aligned = (
        abs(prev.x1 - curr.x1) <= 0.85 * min_h
        or abs(prev.x_center - curr.x_center) <= 1.2 * min_h
        or (x_overlap / min_w) >= 0.2
    )
    return aligned


def group_detections(detections: list[RawDetection]) -> list[TextBlock]:
    """Merge horizontal fragments and cluster nearby caption lines into TextBlocks."""
    merged_lines = _merge_horizontal_line_parts(detections)
    clean_lines = _drop_single_char_icon_noise(merged_lines)
    if not clean_lines:
        return []

    clean_lines.sort(key=lambda d: (d.y1, d.x1))
    clusters: list[list[RawDetection]] = []

    for det in clean_lines:
        best_cluster: list[RawDetection] | None = None
        best_gap = float("inf")
        for cluster in clusters:
            anchor = cluster[-1]
            if _should_group_vertically(anchor, det):
                gap = abs(det.y1 - anchor.y2)
                if gap < best_gap:
                    best_gap = gap
                    best_cluster = cluster
        if best_cluster is not None:
            best_cluster.append(det)
        else:
            clusters.append([det])

    blocks: list[TextBlock] = []
    for cluster in clusters:
        cluster.sort(key=lambda d: (d.y1, d.x1))
        text = "\n".join(line.text.strip() for line in cluster).strip()
        if not text:
            continue
        conf = sum(line.confidence for line in cluster) / len(cluster)
        x1 = min(line.x1 for line in cluster)
        y1 = min(line.y1 for line in cluster)
        x2 = max(line.x2 for line in cluster)
        y2 = max(line.y2 for line in cluster)
        handles = extract_handles(text)
        blocks.append(
            TextBlock(
                text=text,
                confidence=conf,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                lines=cluster,
                is_handle=bool(STANDALONE_HANDLE_RE.match(text.strip())),
                handles=handles,
            )
        )
    return blocks
