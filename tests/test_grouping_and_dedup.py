"""Unit and integration tests for OCR grouping, handle preservation, dedup, and pipeline."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ocr_scanner.dedup import deduplicate_observations
from ocr_scanner.grouping import group_detections
from ocr_scanner.models import BlockObservation, RawDetection, TextBlock
from ocr_scanner.ocr_engine import is_valid_latin_detection
from ocr_scanner.pipeline import scan_video


def _det(x1: float, y1: float, x2: float, y2: float, text: str, conf: float = 0.98) -> RawDetection:
    return RawDetection(
        bbox=[(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        text=text,
        confidence=conf,
    )


def test_latin_filter_rejects_non_latin_and_low_conf() -> None:
    assert is_valid_latin_detection("@rosiesapothecary", 0.99)
    assert is_valid_latin_detection("Café menu", 0.95)
    assert is_valid_latin_detection("@", 0.95)
    assert not is_valid_latin_detection("や", 0.61)
    assert not is_valid_latin_detection("TikTok", 0.40)


def test_horizontal_merge_attaches_at_symbol_and_drops_icon_noise() -> None:
    dets = [
        _det(133, 626, 163, 653, "Q", 0.99),  # Search icon
        _det(185, 632, 204, 650, "@", 0.96),
        _det(200, 626, 376, 654, "rosiesapothecar...", 0.98),
        _det(412, 618, 448, 659, "S", 0.56),  # Music icon
    ]
    blocks = group_detections(dets)
    assert [b.text for b in blocks] == ["@rosiesapothecar..."]
    assert blocks[0].is_handle


def test_grouping_separates_platform_and_handle_but_groups_caption_lines() -> None:
    dets = [
        _det(5, 568, 84, 597, "TikTok", 0.99),
        _det(7, 590, 149, 613, "@rosiesapothecary", 1.0),
        _det(180, 620, 410, 650, '"you said create a', 0.98),
        _det(205, 655, 385, 685, "distraction...", 0.99),
    ]
    blocks = group_detections(dets)
    texts = [b.text for b in blocks]
    assert "TikTok" in texts
    assert "@rosiesapothecary" in texts
    assert '"you said create a\ndistraction...' in texts
    assert len(blocks) == 3


def test_dedup_recovers_missing_at_and_truncated_handles() -> None:
    obs = [
        BlockObservation(
            frame_index=0,
            timestamp_sec=0.0,
            block=TextBlock("@rosiesapothecary", 0.99, 7, 590, 149, 613, [], is_handle=True),
        ),
        BlockObservation(
            frame_index=180,
            timestamp_sec=6.0,
            block=TextBlock("rosiesapothecary", 0.99, 447, 899, 573, 920, [], is_handle=False),
        ),
        BlockObservation(
            frame_index=552,
            timestamp_sec=18.4,
            block=TextBlock("@rosiesapothecar...", 0.97, 185, 626, 376, 654, [], is_handle=True),
        ),
        BlockObservation(
            frame_index=465,
            timestamp_sec=15.5,
            block=TextBlock("rosiesapothecar...", 0.96, 199, 626, 376, 655, [], is_handle=False),
        ),
    ]
    clustered = deduplicate_observations(obs, frame_w=576, frame_h=1134)
    assert len(clustered) == 1
    _cluster, canonical, best_obs = clustered[0]
    assert canonical == "@rosiesapothecary"
    assert best_obs.frame_index == 0


def test_dedup_never_merges_distinct_numbered_handles() -> None:
    obs = [
        BlockObservation(
            frame_index=0,
            timestamp_sec=0.0,
            block=TextBlock("@user1", 0.99, 10, 10, 110, 40, [], is_handle=True),
        ),
        BlockObservation(
            frame_index=15,
            timestamp_sec=0.5,
            block=TextBlock("@user2", 0.99, 10, 10, 110, 40, [], is_handle=True),
        ),
    ]
    clustered = deduplicate_observations(obs, frame_w=576, frame_h=1134)
    assert [c[1] for c in clustered] == ["@user1", "@user2"]


def test_end_to_end_synthetic_video(tmp_path: Path) -> None:
    video_path = tmp_path / "sample.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (640, 360))
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(frame, "HELLO WORLD", (120, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    cv2.putText(frame, "@testaccount", (20, 320), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    for _ in range(15):
        writer.write(frame)
    writer.release()

    result = scan_video(video_path, output_root=tmp_path / "out", sample_fps=2.0)
    assert result.has_text is True
    assert Path(result.json_path).is_file()
    assert Path(result.html_path).is_file()
    assert len(result.distinct_texts) >= 1
    for entry in result.distinct_texts:
        assert (Path(result.output_dir) / entry.clean_frame_path).is_file()
        assert (Path(result.output_dir) / entry.boxed_frame_path).is_file()



def test_handle_only_mode_stops_early_and_ignores_truncated_handle(tmp_path: Path) -> None:
    from ocr_scanner.pipeline import _has_complete_handle

    assert not _has_complete_handle(
        [
            TextBlock(
                "@rosiesapothecar...",
                0.98,
                10,
                10,
                200,
                40,
                [],
                is_handle=True,
                handles=["@rosiesapothecar"],
            )
        ]
    )
    assert _has_complete_handle(
        [
            TextBlock(
                "@rosiesapothecar...",
                0.98,
                10,
                10,
                200,
                40,
                [],
                is_handle=True,
                handles=["@rosiesapothecar"],
            ),
            TextBlock(
                "@rosiesapothecary",
                0.99,
                10,
                50,
                220,
                80,
                [],
                is_handle=True,
                handles=["@rosiesapothecary"],
            ),
        ]
    )

    video_path = tmp_path / "early_stop.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (640, 360))
    frame_early = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(frame_early, "@earlyhandle", (40, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3)
    for _ in range(15):
        writer.write(frame_early)

    frame_late = np.full((360, 640, 3), 24, dtype=np.uint8)
    cv2.putText(frame_late, "LATER CAPTION", (100, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    for _ in range(20):
        writer.write(frame_late)
    writer.release()

    fast_res = scan_video(
        video_path,
        output_root=tmp_path / "out_fast",
        sample_fps=2.0,
        scan_mode="handle_only",
    )
    assert fast_res.scan_mode == "handle_only"
    assert fast_res.stopped_early is True
    assert fast_res.ocr_frames == 1
    assert "@earlyhandle" in fast_res.handles
    assert all("LATER" not in entry.text.upper() for entry in fast_res.distinct_texts)

    full_res = scan_video(
        video_path,
        output_root=tmp_path / "out_full",
        sample_fps=2.0,
        scan_mode="full_text",
    )
    assert full_res.scan_mode == "full_text"
    assert full_res.stopped_early is False
    assert full_res.ocr_frames >= 2
    assert "@earlyhandle" in full_res.handles
    assert any("LATER" in entry.text.upper() for entry in full_res.distinct_texts)



def test_suggest_handle_from_filename_patterns() -> None:
    from ocr_scanner.grouping import suggest_handle_from_filename

    assert suggest_handle_from_filename("dlehhhdodo__3a5bfbe56dbef54f2f0f7a7b93ba56ff.mp4") == "dlehhhdodo"
    assert suggest_handle_from_filename("1arsiptiktokekslusif!!2026-02-28_2210.mp4") == "1arsiptiktokekslusif"
    assert suggest_handle_from_filename("gaahpk___19c1d83773d248f36cd73253311f0166.mp4") == "gaahpk_"
    assert suggest_handle_from_filename("@rosies_apothecary__12345.mp4") == "rosies_apothecary"
    assert suggest_handle_from_filename("aakruthi_aggarwal_30733190.mp4") == "aakruthi_aggarwal"
    assert suggest_handle_from_filename("@naruto.mp4") == "naruto"

    for non_handle in (
        "Instagram_564578938.mp4",
        "1665713972159.mp4",
        "00626c68f8cf32f7d1ccce6ba80b074a.mp4",
        "2022-08-08_16-11-12_UTC.mp4",
        "#fyppppppppppppppppp.mp4",
        "AQMc8SVjoj_ef0BgCY10t1XeOA4aIbWy6p3OOPTX0od9GnrXOUms6AXomeRGmX.mp4",
        "clip_1.mp4",
        "demo_mode.mp4",
    ):
        assert suggest_handle_from_filename(non_handle) is None, non_handle
