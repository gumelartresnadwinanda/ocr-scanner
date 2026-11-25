"""Save example frames (clean + bounding-box highlight), JSON summary, and visual HTML report."""

from __future__ import annotations

import html
import json
from pathlib import Path

import cv2
import numpy as np

from .grouping import is_standalone_handle
from .models import DistinctTextEntry, VideoScanResult


def draw_highlight_box(
    frame: np.ndarray,
    bbox: tuple[int, int, int, int],
    label: str,
    is_handle: bool,
) -> np.ndarray:
    """Draw a high-contrast bounding box and pill label onto a copy of the frame."""
    annotated = frame.copy()
    h, w = annotated.shape[:2]
    x1, y1, x2, y2 = bbox
    pad = 4
    rx1 = max(0, x1 - pad)
    ry1 = max(0, y1 - pad)
    rx2 = min(w - 1, x2 + pad)
    ry2 = min(h - 1, y2 + pad)

    # Bright cyan-green for handles, vivid amber-yellow for general text
    color = (110, 255, 80) if is_handle else (0, 215, 255)
    cv2.rectangle(annotated, (rx1, ry1), (rx2, ry2), (0, 0, 0), 4)
    cv2.rectangle(annotated, (rx1, ry1), (rx2, ry2), color, 2)

    badge_text = label.replace("\n", " ")
    if len(badge_text) > 36:
        badge_text = badge_text[:33] + "..."
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.5
    thickness = 1
    (tw, th), baseline = cv2.getTextSize(badge_text, font, scale, thickness)

    label_y2 = ry1 - 4 if ry1 - th - 10 >= 0 else min(h - 1, ry2 + th + 10)
    label_y1 = max(0, label_y2 - th - 8)
    label_x1 = max(0, min(rx1, w - tw - 12))
    label_x2 = min(w - 1, label_x1 + tw + 10)

    cv2.rectangle(annotated, (label_x1, label_y1), (label_x2, label_y2), color, -1)
    cv2.putText(
        annotated,
        badge_text,
        (label_x1 + 5, label_y2 - 4),
        font,
        scale,
        (15, 15, 15),
        thickness,
        cv2.LINE_AA,
    )
    return annotated


def save_example_frames(
    out_dir: Path,
    frame_cache: dict[int, tuple[float, np.ndarray]],
    clustered: list[tuple[object, str, object]],
) -> tuple[dict[int, str], dict[int, str]]:
    """Write clean frames (once per timestamp) and boxed frames (once per distinct entry)."""
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old_img in frames_dir.glob("*.jpg"):
        old_img.unlink()

    clean_paths: dict[int, str] = {}
    boxed_paths: dict[int, str] = {}

    for idx, (_cluster, canonical, best_obs) in enumerate(clustered, start=1):
        frame_idx = best_obs.frame_index
        ts_sec, img = frame_cache[frame_idx]
        ts_ms = int(round(ts_sec * 1000))

        if frame_idx not in clean_paths:
            clean_name = f"frame_{frame_idx:05d}_{ts_ms}ms_clean.jpg"
            clean_abs = frames_dir / clean_name
            cv2.imwrite(str(clean_abs), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            clean_paths[frame_idx] = f"frames/{clean_name}"

        b = best_obs.block
        bbox = (int(round(b.x1)), int(round(b.y1)), int(round(b.x2)), int(round(b.y2)))
        is_handle = is_standalone_handle(canonical)
        prefix = f"#{idx} HANDLE" if is_handle else f"#{idx}"
        boxed_img = draw_highlight_box(img, bbox, f"{prefix}: {canonical}", is_handle=is_handle)

        boxed_name = f"entry_{idx:03d}_boxed.jpg"
        boxed_abs = frames_dir / boxed_name
        cv2.imwrite(str(boxed_abs), boxed_img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        boxed_paths[idx] = f"frames/{boxed_name}"

    return clean_paths, boxed_paths


def write_json_report(result: VideoScanResult, out_path: Path) -> None:
    """Write structured JSON output for downstream automation."""
    out_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def _format_ts(sec: float) -> str:
    mins = int(sec // 60)
    rem = sec - mins * 60
    return f"{mins:02d}:{rem:05.2f}" if mins > 0 else f"{sec:.2f}s"


def _render_entry_card(entry: DistinctTextEntry) -> str:
    badge = '<span class="badge handle">@HANDLE</span>' if entry.is_handle else '<span class="badge text">TEXT</span>'
    handles_note = ""
    if entry.handles and not entry.is_handle:
        pills = " ".join(f'<span class="mini-handle">{html.escape(h)}</span>' for h in entry.handles)
        handles_note = f'<div class="sub-handles">Contains handle: {pills}</div>'

    escaped_text = html.escape(entry.text)
    ts_range = f"{_format_ts(entry.first_seen_sec)} → {_format_ts(entry.last_seen_sec)}"
    best_ts = _format_ts(entry.best_timestamp_sec)
    clean_src = html.escape(entry.clean_frame_path)
    boxed_src = html.escape(entry.boxed_frame_path)

    return f"""
    <article class="card {'is-handle' if entry.is_handle else ''}">
      <div class="card-header">
        <span class="entry-id">#{entry.id}</span>
        {badge}
        <span class="meta-pill">Conf: {entry.confidence * 100:.1f}%</span>
        <span class="meta-pill">Seen: {ts_range} ({entry.occurrences}x)</span>
        <span class="meta-pill">Example @ {best_ts}</span>
      </div>
      <pre class="ocr-text">{escaped_text}</pre>
      {handles_note}
      <div class="frame-controls">
        <button type="button" class="toggle-btn active" data-src="{boxed_src}">Boxed</button>
        <button type="button" class="toggle-btn" data-src="{clean_src}">Clean Frame</button>
      </div>
      <div class="frame-wrap">
        <a href="{boxed_src}" target="_blank" class="frame-link">
          <img src="{boxed_src}" alt="Frame example for entry #{entry.id}" loading="lazy" />
        </a>
      </div>
    </article>
    """


def write_html_report(result: VideoScanResult, out_path: Path) -> None:
    """Generate a self-contained visual HTML report for human review."""
    meta = result.metadata
    status_badge = (
        '<span class="status yes">TEXT DETECTED</span>'
        if result.has_text
        else '<span class="status no">NO TEXT DETECTED</span>'
    )
    handles_html = (
        " ".join(f'<span class="handle-chip">{html.escape(h)}</span>' for h in result.handles)
        if result.handles
        else '<span class="muted">None detected</span>'
    )
    cards_html = (
        "\n".join(_render_entry_card(e) for e in result.distinct_texts)
        if result.distinct_texts
        else '<div class="empty">No readable text or @handles detected in this video.</div>'
    )

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OCR Scan Report — {html.escape(meta.video_name)}</title>
  <style>
    :root {{
      --bg: #0f1115;
      --surface: #171a21;
      --border: #282d39;
      --text: #e8ecf3;
      --muted: #9aa4b5;
      --accent: #4ade80;
      --accent-bg: rgba(74, 222, 128, 0.14);
      --warn: #facc15;
      --warn-bg: rgba(250, 204, 21, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 28px;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      line-height: 1.45;
    }}
    header {{
      max-width: 1200px;
      margin: 0 auto 24px;
      padding: 20px 24px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 20px;
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .status {{
      font-size: 12px;
      padding: 3px 10px;
      border-radius: 999px;
      font-weight: 700;
      letter-spacing: 0.04em;
    }}
    .status.yes {{ background: var(--accent-bg); color: var(--accent); border: 1px solid rgba(74,222,128,0.4); }}
    .status.no {{ background: rgba(154,164,181,0.14); color: var(--muted); border: 1px solid var(--border); }}
    .stats {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px 24px;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 14px;
    }}
    .stats strong {{ color: var(--text); }}
    .handles-bar {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      font-size: 13px;
    }}
    .handle-chip, .mini-handle {{
      background: var(--accent-bg);
      color: var(--accent);
      border: 1px solid rgba(74, 222, 128, 0.35);
      padding: 3px 10px;
      border-radius: 999px;
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-weight: 600;
    }}
    .grid {{
      max-width: 1200px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
      gap: 18px;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .card.is-handle {{ border-color: rgba(74, 222, 128, 0.45); }}
    .card-header {{
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
      font-size: 12px;
    }}
    .entry-id {{ font-weight: 700; color: var(--text); }}
    .badge {{
      padding: 2px 7px;
      border-radius: 6px;
      font-weight: 700;
      font-size: 11px;
    }}
    .badge.handle {{ background: var(--accent-bg); color: var(--accent); }}
    .badge.text {{ background: var(--warn-bg); color: var(--warn); }}
    .meta-pill {{
      background: rgba(255,255,255,0.05);
      color: var(--muted);
      padding: 2px 7px;
      border-radius: 6px;
    }}
    .ocr-text {{
      margin: 0;
      padding: 10px 12px;
      background: #0b0d11;
      border: 1px solid var(--border);
      border-radius: 8px;
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: 14px;
      white-space: pre-wrap;
      word-break: break-word;
      color: #ffffff;
    }}
    .sub-handles {{ font-size: 12px; color: var(--muted); }}
    .frame-controls {{ display: flex; gap: 6px; }}
    .toggle-btn {{
      background: transparent;
      color: var(--muted);
      border: 1px solid var(--border);
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 12px;
      cursor: pointer;
    }}
    .toggle-btn.active {{
      background: rgba(255,255,255,0.1);
      color: var(--text);
      border-color: rgba(255,255,255,0.3);
    }}
    .frame-wrap {{
      background: #0b0d11;
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid var(--border);
      text-align: center;
    }}
    .frame-wrap img {{
      max-width: 100%;
      max-height: 460px;
      display: block;
      margin: 0 auto;
      object-fit: contain;
    }}
    .empty, .muted {{ color: var(--muted); }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(meta.video_name)} {status_badge}</h1>
    <div class="stats">
      <span><strong>Mode:</strong> {html.escape("@handle-only (stopped early)" if result.stopped_early else ("@handle-only" if result.scan_mode == "handle_only" else "Full video text"))}</span>
      <span><strong>Resolution:</strong> {meta.width}x{meta.height}</span>
      <span><strong>Duration:</strong> {meta.duration_sec:.2f}s ({meta.fps:.2f} fps)</span>
      <span><strong>Frames:</strong> {result.sampled_frames} sampled / {result.ocr_frames} OCR'd / {result.skipped_frames} skipped</span>
      <span><strong>Distinct items:</strong> {len(result.distinct_texts)}</span>
      <span><strong>Elapsed:</strong> {result.elapsed_sec:.2f}s</span>
    </div>
    <div class="handles-bar">
      <strong>@Handles found:</strong> {handles_html}
    </div>
  </header>
  <main class="grid">
    {cards_html}
  </main>
  <script>
    document.addEventListener('click', (e) => {{
      const btn = e.target.closest('.toggle-btn');
      if (!btn) return;
      const card = btn.closest('.card');
      card.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const src = btn.getAttribute('data-src');
      const img = card.querySelector('.frame-wrap img');
      const link = card.querySelector('.frame-link');
      if (img && src) img.src = src;
      if (link && src) link.href = src;
    }});
  </script>
</body>
</html>
"""
    out_path.write_text(page, encoding="utf-8")
