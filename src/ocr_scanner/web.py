"""FastAPI web server for bulk scanning, browsing results, and organizing files by @handle."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .grouping import suggest_handle_from_filename
from .ocr_engine import OcrEngine
from .pipeline import scan_video

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts"})
STATIC_DIR = Path(__file__).resolve().parent / "static"
_INVALID_WIN_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_HIDDEN_WIN_DIRS = frozenset({"$recycle.bin", "system volume information", "msocache", "recovery"})
_WIN_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class ScanRequest(BaseModel):
    """Payload to start scanning a single video file or a directory of videos."""

    path: str = Field(..., min_length=1, description="Local path to a video file or folder.")
    sample_fps: float = Field(default=2.0, ge=0.2, le=10.0)
    min_confidence: float = Field(default=0.55, ge=0.1, le=0.99)
    recursive: bool = False
    skip_existing: bool = True
    scan_mode: Literal["full_text", "handle_only"] = "full_text"


class MoveRequest(BaseModel):
    """Payload to move a scanned video into a subfolder named after a handle."""

    handle: str = Field(..., min_length=1, description="Target handle (with or without @) or folder name.")
    destination_root: str | None = Field(
        default=None,
        description="Optional parent directory where the handle folder is created.",
    )


@dataclass
class ScanJob:
    """Tracks progress of an asynchronous video or folder scan request."""

    job_id: str
    target_path: str
    scan_mode: str = "full_text"
    status: str = "queued"  # queued | running | completed | cancelled | failed
    total_count: int = 0
    done_count: int = 0
    scanned_count: int = 0
    skipped_count: int = 0
    current_video: str | None = None
    cancel_requested: bool = False
    scan_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "target_path": self.target_path,
            "scan_mode": self.scan_mode,
            "status": self.status,
            "total_count": self.total_count,
            "done_count": self.done_count,
            "scanned_count": self.scanned_count,
            "skipped_count": self.skipped_count,
            "current_video": self.current_video,
            "cancel_requested": self.cancel_requested,
            "scan_ids": list(self.scan_ids),
            "errors": list(self.errors),
        }


def _safe_scan_dir(output_root: Path, scan_id: str) -> Path:
    if not scan_id or "/" in scan_id or "\\" in scan_id or ".." in scan_id:
        raise HTTPException(status_code=400, detail="Invalid scan identifier.")
    resolved_root = output_root.resolve()
    candidate = (resolved_root / scan_id).resolve()
    if candidate.parent != resolved_root or not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"Scan '{scan_id}' not found.")
    return candidate


def _load_scan_json(scan_dir: Path) -> dict[str, Any]:
    result_file = scan_dir / "result.json"
    if not result_file.is_file():
        raise HTTPException(status_code=404, detail=f"Missing result.json for '{scan_dir.name}'.")
    data = json.loads(result_file.read_text(encoding="utf-8"))
    scan_id = scan_dir.name
    data["scan_id"] = scan_id
    data["mtime"] = result_file.stat().st_mtime
    data.setdefault("scan_mode", "full_text")
    data.setdefault("stopped_early", False)
    data["filename_handle"] = suggest_handle_from_filename(data.get("video_name", ""))
    data["video_exists"] = Path(data.get("video_path", "")).is_file()
    data["video_url"] = f"/api/video/{scan_id}" if data["video_exists"] else None
    for entry in data.get("distinct_texts", []):
        entry["clean_frame_url"] = f"/outputs/{scan_id}/{entry['clean_frame_path']}"
        entry["boxed_frame_url"] = f"/outputs/{scan_id}/{entry['boxed_frame_path']}"
    return data


def _save_scan_json_raw(scan_dir: Path, payload: dict[str, Any]) -> None:
    """Persist clean result.json without transient API URL fields."""
    transient_keys = {"scan_id", "mtime", "video_exists", "video_url", "filename_handle"}
    clean_payload = {k: v for k, v in payload.items() if k not in transient_keys}
    clean_entries = []
    for entry in clean_payload.get("distinct_texts", []):
        clean_entries.append({k: v for k, v in entry.items() if k not in {"clean_frame_url", "boxed_frame_url"}})
    clean_payload["distinct_texts"] = clean_entries

    result_file = scan_dir / "result.json"
    tmp_file = scan_dir / "result.json.tmp"
    tmp_file.write_text(json.dumps(clean_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_file.replace(result_file)


def _collect_video_paths(target: Path, recursive: bool) -> list[Path]:
    if target.is_file():
        if target.suffix.lower() not in VIDEO_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file extension '{target.suffix}'. Expected video format.",
            )
        return [target]

    if target.is_dir():
        iterator = target.rglob("*") if recursive else target.iterdir()
        videos = sorted(p for p in iterator if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
        if not videos:
            raise HTTPException(status_code=400, detail=f"No video files found in folder: {target}")
        return videos

    raise HTTPException(status_code=400, detail=f"Path does not exist: {target}")


def _sanitize_folder_name(raw_name: str) -> str:
    """Normalize a target handle or folder name, stripping leading '@' prefixes."""
    cleaned = _INVALID_WIN_CHARS_RE.sub("", raw_name.strip()).lstrip("@").strip().rstrip(". ")
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(status_code=400, detail="Folder/handle name is empty or invalid.")
    if cleaned.split(".")[0].upper() in _WIN_RESERVED_NAMES:
        cleaned = f"{cleaned}_folder"
    return cleaned


def _list_drive_roots() -> list[dict[str, str]]:
    if os.name == "nt":
        return [
            {"name": f"Drive {letter}:", "path": f"{letter}:\\", "kind": "drive"}
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            if os.path.exists(f"{letter}:\\")
        ]
    return [{"name": "Root (/)", "path": "/", "kind": "drive"}]


_INDEX_FILENAME = "_scans_index.json"
_INDEX_VERSION = 1


def _build_scan_summary(scan_id: str, raw: dict[str, Any], mtime: float) -> dict[str, Any]:
    """Project a full scan result dict into a lightweight sidebar summary."""
    return {
        "scan_id": scan_id,
        "video_name": raw["video_name"],
        "video_path": raw.get("video_path", ""),
        "source_dir": raw.get("source_dir", ""),
        "duration_sec": raw["duration_sec"],
        "width": raw["width"],
        "height": raw["height"],
        "has_text": raw["has_text"],
        "handles": raw.get("handles", []),
        "filename_handle": suggest_handle_from_filename(raw.get("video_name", "")),
        "distinct_count": raw["distinct_count"],
        "text_preview": " ".join(e.get("text", "") for e in raw.get("distinct_texts", [])),
        "scan_mode": raw.get("scan_mode", "full_text"),
        "stopped_early": bool(raw.get("stopped_early", False)),
        "ocr_frames": raw["ocr_frames"],
        "sampled_frames": raw["sampled_frames"],
        "moved_folder": raw.get("moved_folder"),
        "moved_to": raw.get("moved_to"),
        "mtime": mtime,
    }


def _read_scans_index(output_root: Path) -> dict[str, dict[str, Any]] | None:
    """Load cached scan summaries from disk if valid, otherwise return None."""
    index_path = output_root / _INDEX_FILENAME
    if not index_path.is_file():
        return None
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != _INDEX_VERSION:
            return None
        scans = payload.get("scans")
        if not isinstance(scans, dict):
            return None
        valid: dict[str, dict[str, Any]] = {}
        for scan_id, entry in scans.items():
            if (
                isinstance(scan_id, str)
                and isinstance(entry, dict)
                and "video_name" in entry
                and "mtime" in entry
            ):
                valid[scan_id] = entry
        return valid
    except Exception:
        return None


def _write_scans_index(output_root: Path, entries: dict[str, dict[str, Any]]) -> None:
    """Atomically write the scan summary index to disk."""
    output_root.mkdir(parents=True, exist_ok=True)
    index_path = output_root / _INDEX_FILENAME
    tmp_path = output_root / f"{_INDEX_FILENAME}.tmp"
    payload = {"version": _INDEX_VERSION, "scans": entries}
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp_path.replace(index_path)


def _recent_dirs_from_summaries(
    summaries: list[dict[str, Any]],
    limit: int = 6,
) -> list[dict[str, str]]:
    candidates: list[tuple[float, Path]] = []
    for item in summaries:
        dir_str = item.get("source_dir")
        video_path = item.get("video_path", "")
        folder = Path(dir_str) if dir_str else (Path(video_path).parent if video_path else None)
        if folder is not None and folder.is_dir():
            candidates.append((float(item.get("mtime", 0.0)), folder))
    candidates.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    recent: list[dict[str, str]] = []
    for _, folder in candidates:
        key = str(folder).lower() if os.name == "nt" else str(folder)
        if key in seen:
            continue
        seen.add(key)
        recent.append({"name": folder.name or str(folder), "path": str(folder), "kind": "recent"})
        if len(recent) >= limit:
            break
    return recent


def _collect_recent_dirs(output_root: Path, limit: int = 6) -> list[dict[str, str]]:
    if not output_root.is_dir():
        return []
    cached = _read_scans_index(output_root)
    if cached is not None:
        return _recent_dirs_from_summaries(list(cached.values()), limit=limit)
    candidates: list[tuple[float, Path]] = []
    for child in output_root.iterdir():
        result_file = child / "result.json"
        if not child.is_dir() or not result_file.is_file():
            continue
        try:
            raw = json.loads(result_file.read_text(encoding="utf-8"))
            dir_str = raw.get("source_dir")
            folder = Path(dir_str) if dir_str else Path(raw.get("video_path", "")).parent
            if folder.is_dir():
                candidates.append((result_file.stat().st_mtime, folder))
        except Exception:
            continue

    candidates.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    recent: list[dict[str, str]] = []
    for _, folder in candidates:
        key = str(folder).lower() if os.name == "nt" else str(folder)
        if key in seen:
            continue
        seen.add(key)
        recent.append({"name": folder.name or str(folder), "path": str(folder), "kind": "recent"})
        if len(recent) >= limit:
            break
    return recent


def _collect_quick_places(output_root: Path) -> dict[str, Any]:
    home = Path.home()
    place_specs = [
        ("Home", home),
        ("Desktop", home / "Desktop"),
        ("Downloads", home / "Downloads"),
        ("Videos", home / "Videos"),
        ("Documents", home / "Documents"),
    ]
    places = [
        {"name": label, "path": str(folder), "kind": "place"}
        for label, folder in place_specs
        if folder.is_dir()
    ]
    return {
        "drives": _list_drive_roots(),
        "places": places,
        "recent": _collect_recent_dirs(output_root),
    }


def _build_breadcrumbs(target: Path) -> list[dict[str, str]]:
    chain = [target, *target.parents]
    crumbs = [{"name": "This PC", "path": ""}]
    for ancestor in reversed(chain):
        crumbs.append({"name": ancestor.name or str(ancestor), "path": str(ancestor)})
    return crumbs


def _list_directory_entries(target: Path, mode: str) -> dict[str, Any]:
    folders: list[dict[str, str]] = []
    files: list[dict[str, str]] = []
    video_count = 0

    try:
        with os.scandir(target) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue

                if is_dir:
                    if os.name == "nt" and entry.name.lower() in _HIDDEN_WIN_DIRS:
                        continue
                    folders.append({"name": entry.name, "path": entry.path, "kind": "dir"})
                    continue

                try:
                    is_file = entry.is_file(follow_symlinks=False)
                except OSError:
                    continue
                if not is_file:
                    continue

                ext = Path(entry.name).suffix.lower()
                if ext in VIDEO_EXTENSIONS:
                    video_count += 1
                    if mode == "file":
                        files.append(
                            {
                                "name": entry.name,
                                "path": entry.path,
                                "kind": "file",
                                "ext": ext,
                            }
                        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Access denied: {target}") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot read folder {target}: {exc}") from exc

    folders.sort(key=lambda item: item["name"].lower())
    files.sort(key=lambda item: item["name"].lower())
    parent_path = str(target.parent) if target.parent != target else ""

    return {
        "path": str(target),
        "parent": parent_path,
        "breadcrumbs": _build_breadcrumbs(target),
        "mode": mode,
        "video_count": video_count,
        "folders": folders,
        "files": files,
    }


def _existing_scans_index(output_root: Path) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Return (resolved_video_path_lower -> (scan_id, scan_mode), scan_id -> resolved_video_path_lower)."""
    by_video_path: dict[str, tuple[str, str]] = {}
    by_scan_id: dict[str, str] = {}
    if not output_root.is_dir():
        return by_video_path, by_scan_id
    for child in output_root.iterdir():
        if not child.is_dir():
            continue
        result_file = child / "result.json"
        if not result_file.is_file():
            continue
        try:
            raw = json.loads(result_file.read_text(encoding="utf-8"))
            vp = str(Path(raw.get("video_path", "")).resolve()).lower()
            mode = str(raw.get("scan_mode") or "full_text")
            by_video_path[vp] = (child.name, mode)
            by_scan_id[child.name] = vp
        except Exception:
            continue
    return by_video_path, by_scan_id


def _preserve_move_metadata(scan_dir: Path) -> dict[str, Any]:
    """Read any previous file-move fields before a re-scan overwrites result.json."""
    result_file = scan_dir / "result.json"
    if not result_file.is_file():
        return {}
    try:
        raw = json.loads(result_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        k: raw[k]
        for k in ("source_dir", "moved_folder", "moved_to", "moved_at")
        if raw.get(k) is not None
    }


def _resolve_scan_id(video: Path, by_scan_id: dict[str, str]) -> str:
    resolved_str = str(video.resolve()).lower()
    stem = video.stem
    existing_vp = by_scan_id.get(stem)
    if existing_vp is None or existing_vp == resolved_str:
        return stem
    short_hash = hashlib.sha1(resolved_str.encode("utf-8")).hexdigest()[:6]
    return f"{stem}_{short_hash}"


def create_app(output_root: str | Path = "outputs") -> FastAPI:
    """Create the OCR Scanner FastAPI web application."""
    root_path = Path(output_root).resolve()
    root_path.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="OCR Frame Inspector", version="0.2.0")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.mount("/outputs", StaticFiles(directory=str(root_path)), name="outputs")

    @app.middleware("http")
    async def no_cache_ui_assets(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        req_path = request.url.path
        if req_path == "/" or req_path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    jobs_lock = threading.Lock()
    engine_lock = threading.Lock()
    index_lock = threading.Lock()
    reconcile_lock = threading.Lock()
    jobs: dict[str, ScanJob] = {}
    shared_engine: dict[tuple[float, bool], OcrEngine] = {}
    index_entries: dict[str, dict[str, Any]] = {}
    index_loaded = False

    def _load_index_locked() -> None:
        nonlocal index_loaded
        if index_loaded:
            return
        cached = _read_scans_index(root_path)
        if cached is not None:
            index_entries.update(cached)
        index_loaded = True

    def _upsert_index_entry(scan_dir: Path, raw: dict[str, Any], save: bool = True) -> None:
        result_file = scan_dir / "result.json"
        try:
            mtime = result_file.stat().st_mtime
        except OSError:
            mtime = 0.0
        summary = _build_scan_summary(scan_dir.name, raw, mtime)
        with index_lock:
            _load_index_locked()
            index_entries[scan_dir.name] = summary
            if save:
                _write_scans_index(root_path, index_entries)

    def _reconcile_index() -> list[dict[str, Any]]:
        with reconcile_lock:
            with index_lock:
                _load_index_locked()
                cached_mtimes = {sid: entry.get("mtime") for sid, entry in index_entries.items()}
                disk_missing = not (root_path / _INDEX_FILENAME).is_file()

            seen_ids: set[str] = set()
            updated: dict[str, dict[str, Any]] = {}
            if root_path.is_dir():
                with os.scandir(root_path) as it:
                    for entry in it:
                        try:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                        except OSError:
                            continue
                        result_file = Path(entry.path) / "result.json"
                        try:
                            mtime = os.stat(result_file).st_mtime
                        except OSError:
                            continue
                        scan_id = entry.name
                        seen_ids.add(scan_id)
                        if cached_mtimes.get(scan_id) == mtime:
                            continue
                        try:
                            raw = json.loads(result_file.read_text(encoding="utf-8"))
                            updated[scan_id] = _build_scan_summary(scan_id, raw, mtime)
                        except Exception:
                            continue

            removed_ids = [sid for sid in cached_mtimes if sid not in seen_ids]
            with index_lock:
                for sid in removed_ids:
                    if index_entries.get(sid, {}).get("mtime") == cached_mtimes.get(sid):
                        index_entries.pop(sid, None)
                for sid, summary in updated.items():
                    existing = index_entries.get(sid)
                    if existing is None or float(summary.get("mtime", 0.0)) >= float(existing.get("mtime", 0.0)):
                        index_entries[sid] = summary
                if updated or removed_ids or disk_missing:
                    _write_scans_index(root_path, index_entries)
                return list(index_entries.values())

    def get_engine(min_confidence: float) -> OcrEngine:
        key = (round(min_confidence, 2), True)
        if key not in shared_engine:
            shared_engine[key] = OcrEngine(min_confidence=min_confidence, latin_only=True)
        return shared_engine[key]

    def run_job(
        job: ScanJob,
        videos: list[Path],
        sample_fps: float,
        min_confidence: float,
        skip_existing: bool,
        scan_mode: str,
    ) -> None:
        with jobs_lock:
            job.status = "running"

        by_video_path, by_scan_id = _existing_scans_index(root_path)

        for video in videos:
            with jobs_lock:
                if job.cancel_requested:
                    job.status = "cancelled"
                    job.current_video = None
                    return
                job.current_video = video.name

            resolved_key = str(video.resolve()).lower()
            existing = by_video_path.get(resolved_key)
            if skip_existing and existing is not None:
                existing_id, existing_mode = existing
                # Upgrading a handle-only scan to full_text should still run even with skip_existing=True
                if scan_mode == "handle_only" or existing_mode == "full_text":
                    with jobs_lock:
                        job.scan_ids.append(existing_id)
                        job.skipped_count += 1
                        job.done_count += 1
                    continue

            target_scan_id = _resolve_scan_id(video, by_scan_id)
            preserved_move = _preserve_move_metadata(root_path / target_scan_id)
            try:
                with engine_lock:
                    engine = get_engine(min_confidence)
                    result = scan_video(
                        video_path=video,
                        output_root=root_path,
                        sample_fps=sample_fps,
                        min_confidence=min_confidence,
                        engine=engine,
                        scan_id=target_scan_id,
                        scan_mode=scan_mode,
                    )
                scan_dir = Path(result.output_dir)
                saved = _load_scan_json(scan_dir)
                if preserved_move:
                    saved.update(preserved_move)
                    _save_scan_json_raw(scan_dir, saved)
                _upsert_index_entry(scan_dir, saved, save=True)

                by_video_path[resolved_key] = (target_scan_id, scan_mode)
                by_scan_id[target_scan_id] = resolved_key
                with jobs_lock:
                    job.scan_ids.append(scan_dir.name)
                    job.scanned_count += 1
                    job.done_count += 1
            except Exception as exc:
                with jobs_lock:
                    job.errors.append(f"{video.name}: {exc}")
                    job.done_count += 1

        with jobs_lock:
            job.current_video = None
            if job.cancel_requested:
                job.status = "cancelled"
            elif job.errors and job.scanned_count == 0 and job.skipped_count == 0:
                job.status = "failed"
            else:
                job.status = "completed"

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        css_v = int((STATIC_DIR / "styles.css").stat().st_mtime)
        js_v = int((STATIC_DIR / "app.js").stat().st_mtime)
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace('href="/static/styles.css"', f'href="/static/styles.css?v={css_v}"')
        html = html.replace('src="/static/app.js"', f'src="/static/app.js?v={js_v}"')
        return HTMLResponse(html)

    @app.get("/api/scans")
    def list_scans() -> dict[str, Any]:
        items = _reconcile_index()
        items.sort(key=lambda item: (item.get("mtime", 0.0), item.get("video_name", "")), reverse=True)
        return {"scans": items}

    @app.get("/api/scans/{scan_id}")
    def get_scan(scan_id: str) -> dict[str, Any]:
        scan_dir = _safe_scan_dir(root_path, scan_id)
        return _load_scan_json(scan_dir)

    @app.post("/api/scans/{scan_id}/move")
    def move_scan_video(scan_id: str, payload: MoveRequest) -> dict[str, Any]:
        scan_dir = _safe_scan_dir(root_path, scan_id)
        data = _load_scan_json(scan_dir)
        src_path = Path(data.get("video_path", ""))
        if not src_path.is_file():
            raise HTTPException(status_code=404, detail=f"Video file not found on disk: {src_path}")

        folder_name = _sanitize_folder_name(payload.handle)

        if payload.destination_root and payload.destination_root.strip():
            base_dir = Path(payload.destination_root.strip().strip('"'))
        elif data.get("source_dir"):
            base_dir = Path(data["source_dir"])
        elif data.get("moved_folder") and src_path.parent.name == data["moved_folder"]:
            base_dir = src_path.parent.parent
        elif src_path.parent.name.startswith("@"):
            base_dir = src_path.parent.parent
        else:
            base_dir = src_path.parent

        target_dir = base_dir / folder_name
        dst_path = target_dir / src_path.name

        if src_path.resolve() == dst_path.resolve():
            data.setdefault("source_dir", str(base_dir))
            data["moved_folder"] = folder_name
            data["moved_to"] = str(dst_path)
            _save_scan_json_raw(scan_dir, data)
            _upsert_index_entry(scan_dir, data, save=True)
            return {
                "already_there": True,
                "folder_name": folder_name,
                "moved_to": str(dst_path),
                "scan": _load_scan_json(scan_dir),
            }

        if dst_path.exists():
            raise HTTPException(
                status_code=409,
                detail=f"A file with the same name already exists at: {dst_path}",
            )

        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src_path), str(dst_path))
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail="File is locked by another process. Pause video playback or close external players and retry.",
            ) from exc

        data.setdefault("source_dir", str(base_dir))
        data["video_path"] = str(dst_path)
        data["moved_folder"] = folder_name
        data["moved_to"] = str(dst_path)
        data["moved_at"] = datetime.now(timezone.utc).isoformat()
        _save_scan_json_raw(scan_dir, data)
        _upsert_index_entry(scan_dir, data, save=True)

        return {
            "already_there": False,
            "folder_name": folder_name,
            "moved_to": str(dst_path),
            "scan": _load_scan_json(scan_dir),
        }

    @app.get("/api/video/{scan_id}")
    def stream_video(scan_id: str) -> FileResponse:
        scan_dir = _safe_scan_dir(root_path, scan_id)
        data = _load_scan_json(scan_dir)
        video_path = Path(data.get("video_path", ""))
        if not video_path.is_file() or video_path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise HTTPException(status_code=404, detail="Source video file is no longer accessible on disk.")
        media_type, _ = mimetypes.guess_type(str(video_path))
        return FileResponse(video_path, media_type=media_type or "video/mp4")

    @app.post("/api/scan")
    def start_scan(payload: ScanRequest) -> dict[str, Any]:
        target = Path(payload.path.strip().strip('"'))
        videos = _collect_video_paths(target, recursive=payload.recursive)
        job = ScanJob(
            job_id=uuid.uuid4().hex[:10],
            target_path=str(target),
            scan_mode=payload.scan_mode,
            total_count=len(videos),
        )
        with jobs_lock:
            jobs[job.job_id] = job
        thread = threading.Thread(
            target=run_job,
            args=(
                job,
                videos,
                payload.sample_fps,
                payload.min_confidence,
                payload.skip_existing,
                payload.scan_mode,
            ),
            daemon=True,
        )
        thread.start()
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Scan job not found.")
            if job.status in {"queued", "running"}:
                job.cancel_requested = True
            return job.to_dict()

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, Any]:
        with jobs_lock:
            return {"jobs": [j.to_dict() for j in reversed(list(jobs.values()))]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Scan job not found.")
            return job.to_dict()

    @app.post("/api/browse/{mode}")
    def legacy_browse_notice(mode: str) -> dict[str, Any]:
        raise HTTPException(
            status_code=409,
            detail="Web UI was updated. Reload this browser tab (F5 or Ctrl+R) to use the folder browser.",
        )

    @app.get("/api/fs/roots")
    def fs_roots() -> dict[str, Any]:
        return _collect_quick_places(root_path)

    @app.get("/api/fs/list")
    def fs_list(
        path: str | None = None,
        mode: str = "folder",
        fallback: bool = False,
    ) -> dict[str, Any]:
        if mode not in {"folder", "file"}:
            raise HTTPException(status_code=400, detail=f"Unsupported browse mode: {mode}")

        raw = (path or "").strip().strip('"')
        if not raw:
            return {
                "path": "",
                "parent": None,
                "breadcrumbs": [{"name": "This PC", "path": ""}],
                "mode": mode,
                "video_count": 0,
                "folders": _list_drive_roots(),
                "files": [],
            }

        if os.name == "nt" and len(raw) == 2 and raw[1] == ":" and raw[0].isalpha():
            raw = f"{raw}\\"

        candidate = Path(raw)
        if not candidate.exists():
            if fallback:
                while not candidate.exists() and candidate.parent != candidate:
                    candidate = candidate.parent
                if not candidate.exists():
                    return {
                        "path": "",
                        "parent": None,
                        "breadcrumbs": [{"name": "This PC", "path": ""}],
                        "mode": mode,
                        "video_count": 0,
                        "folders": _list_drive_roots(),
                        "files": [],
                    }
            else:
                raise HTTPException(status_code=404, detail=f"Path does not exist: {raw}")

        target = candidate.parent if candidate.is_file() else candidate
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a folder: {target}")
        return _list_directory_entries(target.resolve(), mode)

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-scanner-web",
        description="Start the local OCR Scanner web viewer & scanner UI.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000).")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("outputs"),
        help="Root directory containing scan outputs (default: outputs).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = create_app(output_root=args.out_dir)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
