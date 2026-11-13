"""Local OCR engine wrapper using RapidOCR with Latin/icon noise filtering."""

from __future__ import annotations

import os
import re
import unicodedata

import numpy as np
from rapidocr import RapidOCR

from .models import RawDetection

_HAS_LATIN_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


def is_valid_latin_detection(text: str, confidence: float, min_confidence: float = 0.55) -> bool:
    """Filter out empty strings, low-confidence detections, and non-Latin icon hallucinations."""
    cleaned = text.strip()
    if not cleaned or confidence < min_confidence:
        return False

    # Reject detections that are purely non-Latin script (e.g., Hiragana/CJK hallucinated from logos)
    if not _HAS_LATIN_ALNUM_RE.search(cleaned) and cleaned != "@":
        return False

    for ch in cleaned:
        cat = unicodedata.category(ch)
        if cat.startswith("L"):
            # Keep Latin letters (including accented European letters), reject CJK/Hiragana/etc.
            name = unicodedata.name(ch, "")
            if "LATIN" not in name:
                return False
    return True


class OcrEngine:
    """Thin wrapper around RapidOCR configured for video overlay text."""

    def __init__(
        self,
        min_confidence: float = 0.55,
        latin_only: bool = True,
        use_cls: bool = False,
    ) -> None:
        self.min_confidence = min_confidence
        self.latin_only = latin_only
        threads = min(6, max(2, (os.cpu_count() or 4) // 2))
        self._engine = RapidOCR(
            params={
                "Global.use_cls": use_cls,
                "Global.log_level": "warning",
                "Global.text_score": min(0.5, min_confidence),
                "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            }
        )

    def detect(self, image: np.ndarray) -> list[RawDetection]:
        """Run OCR on a BGR frame and return filtered line detections."""
        result = self._engine(image)
        if not result.txts or result.boxes is None or result.scores is None:
            return []

        detections: list[RawDetection] = []
        for box, txt, score in zip(result.boxes, result.txts, result.scores):
            text = str(txt).strip()
            conf = float(score)
            if self.latin_only:
                if not is_valid_latin_detection(text, conf, self.min_confidence):
                    continue
            elif not text or conf < self.min_confidence:
                continue

            pts = [(float(pt[0]), float(pt[1])) for pt in box]
            xs = [pt[0] for pt in pts]
            ys = [pt[1] for pt in pts]
            detections.append(
                RawDetection(
                    bbox=pts,
                    x1=min(xs),
                    y1=min(ys),
                    x2=max(xs),
                    y2=max(ys),
                    text=text,
                    confidence=conf,
                )
            )
        return detections
