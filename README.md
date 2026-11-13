# OCR Scanner

A desktop application and CLI tool that extracts on-screen text and creator handles from videos, lets you review detected frames in a web interface, and sorts videos into destination folders.

## Features

- Local optical character recognition using RapidOCR and ONNX Runtime.
- Two scanning modes:
  - Full text: scans frames throughout the video and indexes all distinct text entries.
  - Handle-only: stops scanning as soon as a creator handle (@username) is detected to save time.
- Deduplication and text grouping across frames with timestamps, confidence scores, clean frame snapshots, and bounding boxes.
- Web viewer with a side-by-side list, jump-to-timestamp video player, and dark mode toggle.
- Fast file organizer: moves videos into folders named after detected handles or filename patterns with auto-advance to the next unmoved clip.
- Fast metadata caching: tracks scans incrementally with mtime reconciliation so large libraries load in milliseconds.
- Command-line scanner for batch scripting and automated pipelines.

## Installation

### Prerequisites

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) (recommended) or standard pip

### Setup

Clone the repository and install dependencies:

```bash
git clone https://github.com/gumelartresnadwinanda/ocr-scanner.git
cd ocr-scanner
uv sync
```

Alternatively, install using pip:

```bash
pip install -e .
```

## Running the Web Interface

### On Windows

Double-click `OCR Scanner.exe` or `Start OCR Scanner.bat` in the project root. This starts the local server and opens your web browser to `http://127.0.0.1:8000`.

### From the Terminal

```bash
uv run ocr-scanner-web --host 127.0.0.1 --port 8000
```

Available options:

- `--host`: Bind host (default: `127.0.0.1`).
- `--port`: Bind port (default: `8000`).
- `--out-dir`: Directory storing scan results and extracted frames (default: `outputs`).

## Command-Line Usage

You can scan individual video files directly from the terminal:

```bash
# Scan full text
uv run ocr-scanner path/to/video.mp4

# Stop at the first detected @handle
uv run ocr-scanner path/to/video.mp4 --mode handle_only

# Adjust frame sampling rate and minimum confidence
uv run ocr-scanner path/to/video.mp4 --sample-fps 1.0 --min-confidence 0.60
```

### CLI Options

- `--out-dir`: Directory where result JSON and cropped frames are saved (default: `outputs`).
- `--sample-fps`: Sampling rate in frames per second (default: `2.0`).
- `--min-confidence`: Minimum OCR confidence threshold between 0 and 1 (default: `0.55`).
- `--mode`: `full_text` to scan every sampled frame, or `handle_only` to halt on the first handle (default: `full_text`).
- `--allow-non-latin`: Disable the default Latin script filter to include non-Latin characters.

## Running Tests

Run the test suite with pytest:

```bash
uv run pytest -v
```

## License

MIT
