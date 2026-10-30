
import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

import requests

# ---------------- default settings ----------------
BASE = "http://localhost:8080"        # where FineParser is running
INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
TIMEOUT_SECONDS = 300                 # give up on one FineParser job after this
SUPPORTED = {".pdf"}
# --------------------------------------------------

FINEPARSER_FORMATS = {"doclang": ".doclang", "json": ".json", "txt": ".txt"}
ALL_FORMATS = list(FINEPARSER_FORMATS) + ["markdown"]


# ============ converters ============

def parse_with_fineparser(path, output_type):
    """Submit one file to FineParser, wait for the job, return the result text."""
    path = Path(path)
    with open(path, "rb") as f:
        submitted = requests.post(
            f"{BASE}/parse",
            files={"file": (path.name, f)},
            data={"outputType": output_type},
        )
    submitted.raise_for_status()
    job_id = submitted.json()["jobId"]

    start = time.time()
    while True:
        job = requests.get(f"{BASE}/jobs/{job_id}").json()
        if job["status"] not in ("pending", "in-progress"):
            break
        if time.time() - start > TIMEOUT_SECONDS:
            raise TimeoutError(f"no result after {TIMEOUT_SECONDS}s")
        time.sleep(1)

    if job["status"] != "successful":
        raise RuntimeError(job.get("error", job["status"]))

    result = requests.get(f"{BASE}/jobs/{job['file']}")
    result.raise_for_status()
    return result.content.decode("utf-8")


_markitdown = None


def to_markdown(path):
    """Convert a PDF to Markdown with Microsoft MarkItDown."""
    global _markitdown
    if _markitdown is None:
        from markitdown import MarkItDown
        _markitdown = MarkItDown()
    return _markitdown.convert(str(path)).text_content


# ============ the one function the rest of the flow uses ============

def convert_document(path, fmt):
    """Convert one PDF to the requested format and return it as text."""
    if fmt in FINEPARSER_FORMATS:
        return parse_with_fineparser(path, fmt)
    if fmt == "markdown":
        return to_markdown(path)
    raise ValueError(f"Unknown format '{fmt}'. Choose from: {', '.join(ALL_FORMATS)}")


def output_path(path, fmt, output_dir=OUTPUT_DIR):
    """Where the result for this file and format is saved."""
    path = Path(path)
    if fmt == "markdown":
        return Path(output_dir) / "markdown_markitdown" / (path.name + ".md")
    return Path(output_dir) / fmt / (path.name + FINEPARSER_FORMATS[fmt])


# ============ folder runner ============

def run_folder(formats, input_dir=INPUT_DIR, output_dir=OUTPUT_DIR, skip_done=True):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    input_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    files = sorted(p for p in input_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in SUPPORTED)
    if not files:
        print(f"No PDF files found in '{input_dir}'.")
        return

    print(f"Found {len(files)} PDF file(s). Formats: {', '.join(formats)}\n")

    log_path = output_dir / "parse_log.csv"
    new_log = not log_path.exists()
    fineparser_down = False

    with open(log_path, "a", newline="", encoding="utf-8") as log_file:
        log = csv.writer(log_file)
        if new_log:
            log.writerow(["time", "file", "format", "tool", "status",
                          "seconds", "characters", "error"])

        def record(name, fmt, tool, status, secs="", chars="", error=""):
            log.writerow([datetime.now().isoformat(timespec="seconds"),
                          name, fmt, tool, status, secs, chars, error])
            log_file.flush()

        for i, path in enumerate(files, start=1):
            for fmt in formats:
                tool = "markitdown" if fmt == "markdown" else "fineparser"
                target = output_path(path, fmt, output_dir)
                label = f"[{i}/{len(files)}] {path.name} -> {fmt}"

                if fmt in FINEPARSER_FORMATS and fineparser_down:
                    print(f"{label}: skipped (FineParser not reachable)")
                    continue
                if skip_done and target.exists():
                    print(f"{label}: skipped (already done)")
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                t0 = time.time()
                try:
                    text = convert_document(path, fmt)
                    target.write_text(text, encoding="utf-8")
                    secs = round(time.time() - t0, 1)
                    print(f"{label}: done in {secs}s ({len(text)} characters)")
                    if len(text.strip()) < 50:
                        if fmt == "markdown":
                            print("   WARNING: almost no text. If this PDF is a scan, "
                                  "MarkItDown cannot read it (no OCR). Note it in the report.")
                        else:
                            print("   WARNING: almost no text came out.")
                    record(path.name, fmt, tool, "ok", secs, len(text))
                except requests.exceptions.ConnectionError:
                    fineparser_down = True
                    print(f"{label}: FAILED - cannot reach FineParser at {BASE}. "
                          f"Is the container running? Skipping FineParser formats.")
                    record(path.name, fmt, tool, "failed", error="connection refused")
                except ImportError as e:
                    print(f"{label}: FAILED - MarkItDown not installed ({e}). "
                          f'Run: pip install "markitdown[pdf]"')
                    record(path.name, fmt, tool, "failed", error=f"not installed: {e}")
                except Exception as e:  # keep going with the next file
                    secs = round(time.time() - t0, 1)
                    print(f"{label}: FAILED - {e}")
                    record(path.name, fmt, tool, "failed", secs, error=str(e))

    print(f"\nFinished. Results in '{output_dir}', log in '{log_path}'.")


def main():
    ap = argparse.ArgumentParser(description="Convert a folder of PDFs.")
    ap.add_argument("--formats", nargs="+", default=["doclang"], choices=ALL_FORMATS,
                    help="one or more of: " + ", ".join(ALL_FORMATS))
    ap.add_argument("--input", default=str(INPUT_DIR))
    ap.add_argument("--output", default=str(OUTPUT_DIR))
    ap.add_argument("--force", action="store_true",
                    help="re-convert files that already have a result")
    args = ap.parse_args()
    run_folder(args.formats, args.input, args.output, skip_done=not args.force)


if __name__ == "__main__":
    main()