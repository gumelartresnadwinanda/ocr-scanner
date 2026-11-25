"""Tests for the FastAPI web server, bulk directory scanning, and @handle file moves."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from ocr_scanner.pipeline import scan_video
from ocr_scanner.web import create_app


def _make_sample_video(path: Path, label: str = "WEB TEST", handle: str = "@webhandle") -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (640, 360))
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(frame, label, (140, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    cv2.putText(frame, handle, (24, 320), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    for _ in range(10):
        writer.write(frame)
    writer.release()


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(f"/api/jobs/{job_id}")
        assert res.status_code == 200
        data = res.json()
        if data["status"] in ("completed", "cancelled", "failed"):
            return data
        time.sleep(0.2)
    raise AssertionError(f"Job {job_id} did not finish within {timeout}s")


def test_web_empty_state_and_static_assets(tmp_path: Path) -> None:
    app = create_app(output_root=tmp_path / "outputs")
    client = TestClient(app)

    res_home = client.get("/")
    assert res_home.status_code == 200
    assert "OCR Frame Inspector" in res_home.text
    assert "/static/app.js?v=" in res_home.text
    assert "/static/styles.css?v=" in res_home.text
    assert "no-store" in res_home.headers.get("cache-control", "")

    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"

    css_res = client.get("/static/styles.css")
    js_res = client.get("/static/app.js")
    assert css_res.status_code == 200
    assert js_res.status_code == 200
    assert "no-store" in js_res.headers.get("cache-control", "")

    legacy_res = client.post("/api/browse/folder")
    assert legacy_res.status_code == 409
    assert "Reload this browser tab" in legacy_res.json()["detail"]

    res_scans = client.get("/api/scans")
    assert res_scans.status_code == 200
    assert res_scans.json() == {"scans": []}


def test_web_lists_scan_serves_frames_and_streams_video(tmp_path: Path) -> None:
    out_root = tmp_path / "outputs"
    video_path = tmp_path / "demo_clip.mp4"
    _make_sample_video(video_path)
    scan_video(video_path, output_root=out_root, sample_fps=2.0)

    app = create_app(output_root=out_root)
    client = TestClient(app)

    scans_res = client.get("/api/scans")
    assert scans_res.status_code == 200
    scans = scans_res.json()["scans"]
    assert len(scans) == 1
    assert scans[0]["scan_id"] == "demo_clip"
    assert scans[0]["has_text"] is True

    detail_res = client.get("/api/scans/demo_clip")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["video_url"] == "/api/video/demo_clip"
    assert len(detail["distinct_texts"]) >= 1

    first_entry = detail["distinct_texts"][0]
    assert client.get(first_entry["boxed_frame_url"]).status_code == 200
    assert client.get(first_entry["clean_frame_url"]).status_code == 200
    assert client.get(detail["video_url"]).status_code == 200

    assert client.get("/api/scans/missing_id").status_code == 404


def test_web_bulk_directory_scan_and_skip_existing(tmp_path: Path) -> None:
    out_root = tmp_path / "outputs"
    batch_dir = tmp_path / "batch_videos"
    batch_dir.mkdir()
    _make_sample_video(batch_dir / "clip_a.mp4", label="CLIP A", handle="@alphauser")
    _make_sample_video(batch_dir / "clip_b.mp4", label="CLIP B", handle="@betauser")

    app = create_app(output_root=out_root)
    client = TestClient(app)

    bad_res = client.post("/api/scan", json={"path": str(tmp_path / "nonexistent.mp4")})
    assert bad_res.status_code == 400
    assert "does not exist" in bad_res.json()["detail"]

    start_res = client.post("/api/scan", json={"path": str(batch_dir)})
    assert start_res.status_code == 200
    job = _wait_for_job(client, start_res.json()["job_id"])
    assert job["status"] == "completed"
    assert job["total_count"] == 2
    assert job["scanned_count"] == 2
    assert job["skipped_count"] == 0
    assert sorted(job["scan_ids"]) == ["clip_a", "clip_b"]

    # Re-scanning the same directory with skip_existing=True skips already-scanned files
    rescan_res = client.post("/api/scan", json={"path": str(batch_dir), "skip_existing": True})
    assert rescan_res.status_code == 200
    rescan_job = _wait_for_job(client, rescan_res.json()["job_id"])
    assert rescan_job["status"] == "completed"
    assert rescan_job["scanned_count"] == 0
    assert rescan_job["skipped_count"] == 2


def test_web_move_file_to_handle_folder_and_prevent_nesting_or_overwrite(tmp_path: Path) -> None:
    out_root = tmp_path / "outputs"
    src_dir = tmp_path / "unsorted"
    src_dir.mkdir()
    video_path = src_dir / "clip_move.mp4"
    _make_sample_video(video_path)
    scan_video(video_path, output_root=out_root, sample_fps=2.0)

    app = create_app(output_root=out_root)
    client = TestClient(app)

    # First move strips leading @ and creates unsorted/webhandle/clip_move.mp4
    move_res = client.post("/api/scans/clip_move/move", json={"handle": "@webhandle"})
    assert move_res.status_code == 200
    payload = move_res.json()
    expected_dst = src_dir / "webhandle" / "clip_move.mp4"
    assert Path(payload["moved_to"]) == expected_dst
    assert expected_dst.is_file()
    assert not video_path.exists()
    assert payload["already_there"] is False
    assert payload["folder_name"] == "webhandle"
    assert payload["scan"]["moved_folder"] == "webhandle"
    assert client.get("/api/video/clip_move").status_code == 200

    # Moving to same handle again is an idempotent no-op
    same_res = client.post("/api/scans/clip_move/move", json={"handle": "@webhandle"})
    assert same_res.status_code == 200
    assert same_res.json()["already_there"] is True

    # Moving to a second handle moves into sibling unsorted/secondhandle, never webhandle/secondhandle
    move2_res = client.post("/api/scans/clip_move/move", json={"handle": "@secondhandle"})
    assert move2_res.status_code == 200
    expected_dst2 = src_dir / "secondhandle" / "clip_move.mp4"
    assert Path(move2_res.json()["moved_to"]) == expected_dst2
    assert expected_dst2.is_file()
    assert not expected_dst.exists()

    # Collision check: creating a blocking file in blocked prevents overwriting
    blocked_dir = src_dir / "blocked"
    blocked_dir.mkdir()
    (blocked_dir / "clip_move.mp4").write_bytes(b"existing")
    conflict_res = client.post("/api/scans/clip_move/move", json={"handle": "@blocked"})
    assert conflict_res.status_code == 409
    assert expected_dst2.is_file()


def test_web_fs_roots_and_list_endpoints(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    sub_a = media_dir / "alpha_folder"
    sub_b = media_dir / "beta_folder"
    sub_a.mkdir()
    sub_b.mkdir()
    video_file = media_dir / "clip_one.mp4"
    _make_sample_video(video_file)
    (media_dir / "notes.txt").write_text("ignore non-video", encoding="utf-8")

    out_root = tmp_path / "outputs"
    scan_video(video_file, output_root=out_root, sample_fps=2.0)

    app = create_app(output_root=out_root)
    client = TestClient(app)

    roots_res = client.get("/api/fs/roots")
    assert roots_res.status_code == 200
    roots = roots_res.json()
    assert len(roots["drives"]) >= 1
    assert any(Path(item["path"]) == media_dir for item in roots["recent"])

    folder_res = client.get("/api/fs/list", params={"path": str(media_dir), "mode": "folder"})
    assert folder_res.status_code == 200
    folder_data = folder_res.json()
    assert Path(folder_data["path"]) == media_dir.resolve()
    assert folder_data["video_count"] == 1
    assert [f["name"] for f in folder_data["folders"]] == ["alpha_folder", "beta_folder"]
    assert folder_data["files"] == []
    assert folder_data["breadcrumbs"][0] == {"name": "This PC", "path": ""}

    file_res = client.get("/api/fs/list", params={"path": str(media_dir), "mode": "file"})
    assert file_res.status_code == 200
    file_data = file_res.json()
    assert [f["name"] for f in file_data["files"]] == ["clip_one.mp4"]

    missing_res = client.get("/api/fs/list", params={"path": str(media_dir / "missing"), "mode": "folder"})
    assert missing_res.status_code == 404

    fallback_res = client.get(
        "/api/fs/list",
        params={"path": str(media_dir / "missing" / "nested"), "mode": "folder", "fallback": "true"},
    )
    assert fallback_res.status_code == 200
    assert Path(fallback_res.json()["path"]) == media_dir.resolve()

    drives_res = client.get("/api/fs/list", params={"mode": "folder"})
    assert drives_res.status_code == 200
    assert drives_res.json()["path"] == ""
    assert len(drives_res.json()["folders"]) >= 1


def _make_two_scene_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (640, 360))
    frame_one = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(frame_one, "@upghandle", (40, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3)
    for _ in range(15):
        writer.write(frame_one)

    frame_two = np.full((360, 640, 3), 24, dtype=np.uint8)
    cv2.putText(frame_two, "FULL ONLY TEXT", (90, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    for _ in range(20):
        writer.write(frame_two)
    writer.release()


def test_web_handle_only_mode_and_full_text_upgrade(tmp_path: Path) -> None:
    src_dir = tmp_path / "source_videos"
    src_dir.mkdir()
    video_path = src_dir / "clip_upgrade.mp4"
    _make_two_scene_video(video_path)

    out_root = tmp_path / "outputs"
    app = create_app(output_root=out_root)
    client = TestClient(app)

    fast_job = client.post(
        "/api/scan",
        json={
            "path": str(src_dir),
            "sample_fps": 2.0,
            "min_confidence": 0.5,
            "recursive": True,
            "skip_existing": True,
            "scan_mode": "handle_only",
        },
    )
    assert fast_job.status_code == 200
    fast_done = _wait_for_job(client, fast_job.json()["job_id"])
    assert fast_done["scan_mode"] == "handle_only"
    assert fast_done["scanned_count"] == 1

    scans = client.get("/api/scans").json()["scans"]
    assert len(scans) == 1
    assert scans[0]["scan_mode"] == "handle_only"
    assert scans[0]["stopped_early"] is True
    assert "@upghandle" in scans[0]["handles"]
    assert "FULL" not in scans[0]["text_preview"].upper()

    move_res = client.post(f"/api/scans/{scans[0]['scan_id']}/move", json={"handle": "@upghandle"})
    assert move_res.status_code == 200
    assert move_res.json()["scan"]["moved_folder"] == "upghandle"

    upgrade_job = client.post(
        "/api/scan",
        json={
            "path": str(src_dir),
            "sample_fps": 2.0,
            "min_confidence": 0.5,
            "recursive": True,
            "skip_existing": True,
            "scan_mode": "full_text",
        },
    )
    assert upgrade_job.status_code == 200
    upgrade_done = _wait_for_job(client, upgrade_job.json()["job_id"])
    assert upgrade_done["scan_mode"] == "full_text"
    assert upgrade_done["scanned_count"] == 1
    assert upgrade_done["skipped_count"] == 0

    upgraded_scans = client.get("/api/scans").json()["scans"]
    assert len(upgraded_scans) == 1
    assert upgraded_scans[0]["scan_mode"] == "full_text"
    assert upgraded_scans[0]["stopped_early"] is False
    assert upgraded_scans[0]["moved_folder"] == "upghandle"
    assert "FULL" in upgraded_scans[0]["text_preview"].upper()

    repeat_job = client.post(
        "/api/scan",
        json={
            "path": str(src_dir),
            "sample_fps": 2.0,
            "min_confidence": 0.5,
            "recursive": True,
            "skip_existing": True,
            "scan_mode": "full_text",
        },
    )
    assert repeat_job.status_code == 200
    repeat_done = _wait_for_job(client, repeat_job.json()["job_id"])
    assert repeat_done["scanned_count"] == 0
    assert repeat_done["skipped_count"] == 1



def test_web_filename_suggestion_and_move_without_ocr_handle(tmp_path: Path) -> None:
    src_dir = tmp_path / "downloads"
    src_dir.mkdir()
    named_clip = src_dir / "creator_one!!2026-03-01.mp4"
    hash_clip = src_dir / "00626c68f8cf32f7d1ccce6ba80b074a.mp4"

    for vid, label in ((named_clip, "NO HANDLE TEXT"), (hash_clip, "PLAIN TEXT")):
        writer = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (640, 360))
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(frame, label, (80, 180), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
        for _ in range(10):
            writer.write(frame)
        writer.release()

    out_root = tmp_path / "outputs"
    scan_video(named_clip, output_root=out_root, sample_fps=2.0)
    scan_video(hash_clip, output_root=out_root, sample_fps=2.0)

    app = create_app(output_root=out_root)
    client = TestClient(app)

    scans = client.get("/api/scans").json()["scans"]
    by_name = {s["video_name"]: s for s in scans}
    assert by_name["creator_one!!2026-03-01.mp4"]["filename_handle"] == "creator_one"
    assert by_name["00626c68f8cf32f7d1ccce6ba80b074a.mp4"]["filename_handle"] is None

    named_id = by_name["creator_one!!2026-03-01.mp4"]["scan_id"]
    detail = client.get(f"/api/scans/{named_id}").json()
    assert detail["filename_handle"] == "creator_one"
    assert detail["handles"] == []

    move_res = client.post(f"/api/scans/{named_id}/move", json={"handle": detail["filename_handle"]})
    assert move_res.status_code == 200
    assert move_res.json()["folder_name"] == "creator_one"
    assert (src_dir / "creator_one" / "creator_one!!2026-03-01.mp4").is_file()


def test_web_scans_index_cache_reconciliation_and_move(tmp_path: Path) -> None:
    import json as json_mod
    import os
    import shutil

    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    vid_a = src_dir / "clip_a.mp4"
    vid_b = src_dir / "clip_b.mp4"
    vid_c = src_dir / "clip_c.mp4"
    vid_a.write_bytes(b"aaa")
    vid_b.write_bytes(b"bbb")
    vid_c.write_bytes(b"ccc")

    out_root = tmp_path / "outputs"

    def write_scan(scan_id: str, video_path: Path, handles: list[str]) -> Path:
        scan_dir = out_root / scan_id
        scan_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "video_name": video_path.name,
            "video_path": str(video_path),
            "source_dir": str(src_dir),
            "duration_sec": 3.0,
            "width": 720,
            "height": 1280,
            "sampled_frames": 3,
            "ocr_frames": 3,
            "has_text": bool(handles),
            "handles": handles,
            "distinct_count": len(handles),
            "distinct_texts": [
                {
                    "text": h,
                    "first_seen_sec": 0.5,
                    "confidence": 0.95,
                    "clean_frame_path": "frames/frame_0001.jpg",
                    "boxed_frame_path": "frames/frame_0001_boxed.jpg",
                    "handles": [h],
                    "is_handle": True,
                }
                for h in handles
            ],
        }
        result_file = scan_dir / "result.json"
        result_file.write_text(json_mod.dumps(payload), encoding="utf-8")
        return result_file

    write_scan("scan_a", vid_a, ["@alice"])
    res_b = write_scan("scan_b", vid_b, ["@bob"])

    client = TestClient(create_app(out_root))

    # 1. Initial list populates _scans_index.json
    list_res = client.get("/api/scans")
    assert list_res.status_code == 200
    assert {item["scan_id"] for item in list_res.json()["scans"]} == {"scan_a", "scan_b"}
    index_path = out_root / "_scans_index.json"
    assert index_path.is_file()

    # 2. Moving a video updates the cached summary immediately
    move_res = client.post("/api/scans/scan_a/move", json={"handle": "@alice"})
    assert move_res.status_code == 200
    after_move = {item["scan_id"]: item for item in client.get("/api/scans").json()["scans"]}
    assert after_move["scan_a"]["moved_folder"] == "alice"

    # 3. External modification with a newer mtime invalidates only that entry cleanly
    b_data = json_mod.loads(res_b.read_text(encoding="utf-8"))
    b_data["handles"] = ["@extern_bob"]
    res_b.write_text(json_mod.dumps(b_data), encoding="utf-8")
    stat_b = res_b.stat()
    os.utime(res_b, (stat_b.st_atime, stat_b.st_mtime + 5.0))

    after_edit = {item["scan_id"]: item for item in client.get("/api/scans").json()["scans"]}
    assert after_edit["scan_b"]["handles"] == ["@extern_bob"]

    # 4. External addition + deletion reconcile cleanly
    write_scan("scan_c", vid_c, ["@carol"])
    shutil.rmtree(out_root / "scan_b")
    after_sync = {item["scan_id"] for item in client.get("/api/scans").json()["scans"]}
    assert after_sync == {"scan_a", "scan_c"}

    # 5. Corrupt index rebuilds cleanly
    index_path.write_text("{corrupt-json", encoding="utf-8")
    rebuilt = {item["scan_id"]: item for item in client.get("/api/scans").json()["scans"]}
    assert set(rebuilt.keys()) == {"scan_a", "scan_c"}
    assert rebuilt["scan_a"]["moved_folder"] == "alice"

    # 6. Recent dirs in /api/fs/roots uses the index cleanly
    roots_res = client.get("/api/fs/roots")
    assert roots_res.status_code == 200
    recent_paths = [entry["path"] for entry in roots_res.json()["recent"]]
    assert str(src_dir) in recent_paths

    # 7. Fresh app instance loads the persisted index cleanly
    fresh_client = TestClient(create_app(out_root))
    fresh_scans = {item["scan_id"]: item for item in fresh_client.get("/api/scans").json()["scans"]}
    assert fresh_scans["scan_a"]["moved_folder"] == "alice"
    assert fresh_scans["scan_c"]["handles"] == ["@carol"]


def test_web_undo_move(tmp_path: Path) -> None:
    out_root = tmp_path / "outputs"
    src_dir = tmp_path / "source_vids"
    src_dir.mkdir()
    video_path = src_dir / "undo_test.mp4"
    _make_sample_video(video_path, label="UNDO TEST", handle="@revertme")

    scan_video(video_path, output_root=out_root, sample_fps=2.0)
    app = create_app(output_root=out_root)
    client = TestClient(app)

    # 1. Move to handle folder
    move_res = client.post("/api/scans/undo_test/move", json={"handle": "@revertme"})
    assert move_res.status_code == 200
    moved_path = Path(move_res.json()["moved_to"])
    assert moved_path.is_file()
    assert not video_path.exists()

    # 2. Undo move
    undo_res = client.post("/api/scans/undo_test/undo_move")
    assert undo_res.status_code == 200
    undo_data = undo_res.json()
    assert undo_data["undone"] is True
    assert undo_data["folder_name"] is None
    assert undo_data["moved_to"] is None
    assert video_path.is_file()
    assert not moved_path.exists()

    # Verify index updated
    scans_res = client.get("/api/scans")
    assert scans_res.status_code == 200
    scans = scans_res.json()["scans"]
    assert scans[0]["moved_folder"] is None

    # 3. Second undo fails because there is no recorded move
    undo_again = client.post("/api/scans/undo_test/undo_move")
    assert undo_again.status_code == 400


def test_web_dismiss_job(tmp_path: Path) -> None:
    out_root = tmp_path / "outputs"
    app = create_app(output_root=out_root)
    client = TestClient(app)

    # 404 for unknown job
    res_unknown = client.post("/api/jobs/nonexistent-job-id/dismiss")
    assert res_unknown.status_code == 404

    # Run a small scan to get a real job
    vid = tmp_path / "dummy.mp4"
    _make_sample_video(vid)
    scan_res = client.post("/api/scan", json={"path": str(vid)})
    assert scan_res.status_code == 200
    job_id = scan_res.json()["job_id"]
    _wait_for_job(client, job_id)

    # Job is listed
    jobs_res = client.get("/api/jobs")
    assert any(j["job_id"] == job_id for j in jobs_res.json()["jobs"])

    # Dismiss job
    dismiss_res = client.post(f"/api/jobs/{job_id}/dismiss")
    assert dismiss_res.status_code == 200
    assert dismiss_res.json() == {"dismissed": True, "job_id": job_id}

    # Job no longer in listing
    jobs_after = client.get("/api/jobs")
    assert not any(j["job_id"] == job_id for j in jobs_after.json()["jobs"])


def test_web_validate_local_origin(tmp_path: Path) -> None:
    out_root = tmp_path / "outputs"
    app = create_app(output_root=out_root)
    client = TestClient(app)

    # External cross-origin request on POST is blocked
    blocked = client.post("/api/scan", json={"path": "dummy"}, headers={"Origin": "https://evil-site.com"})
    assert blocked.status_code == 403
    assert "Cross-origin requests forbidden." in blocked.json()["detail"]

    # Local origin is allowed (falls through to route handler, e.g. 404/422 on bad path)
    allowed_local = client.post("/api/scan", json={"path": "dummy"}, headers={"Origin": "http://localhost:8000"})
    assert allowed_local.status_code != 403

    # Safe GET with external Origin is not blocked
    safe_get = client.get("/api/scans", headers={"Origin": "https://evil-site.com"})
    assert safe_get.status_code == 200

