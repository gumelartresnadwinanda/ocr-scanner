"""Video OCR scanner package."""

from .cli import main
from .pipeline import scan_video

__all__ = ["main", "scan_video"]
