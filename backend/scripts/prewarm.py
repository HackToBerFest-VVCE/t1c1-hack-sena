"""
scripts/prewarm.py
==================
Fill the extraction cache before a demo so every document in sample_data/
is pre-extracted.  Runs from the backend directory.

Usage (from backend/):
    python scripts/prewarm.py [--mode gemma|regex] [--workers N]

Each bundle is processed with extract_documents_parallel() so Gemma calls
run concurrently.  Results already in the cache are skipped (instant).

Output per bundle:
    bundle_001  3 docs  12.4 s  [gemma-4, gemma-4, regex-fallback]

Exit codes:
    0 – all bundles processed (or already cached)
    1 – at least one document fell back to regex in gemma mode
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# ── ensure backend package root is on the path ───────────────────────────────
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Load .env before any extraction imports read env vars
from pathlib import Path as _P
from dotenv import load_dotenv as _ld
_ld(dotenv_path=_P(__file__).resolve().parent.parent / ".env", override=False)

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from app.extraction import extract_documents_parallel  # noqa: E402 – after path setup

# ── locate sample_data ────────────────────────────────────────────────────────

_SAMPLE_DATA = _BACKEND.parent / "sample_data"

DOC_NAMES = ("claim_form.pdf", "hospital_bill.pdf", "discharge_summary.pdf")


def find_bundles(root: Path) -> list[tuple[str, list[Path]]]:
    """Return [(bundle_name, [existing_pdf_paths])] sorted by name."""
    bundles: list[tuple[str, list[Path]]] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        pdfs = [d / name for name in DOC_NAMES if (d / name).exists()]
        if pdfs:
            bundles.append((d.name, pdfs))
    return bundles


def run(mode: str, max_workers: int) -> int:
    import os
    os.environ["EXTRACTION_MODE"] = mode

    if not _SAMPLE_DATA.exists():
        print(f"ERROR: sample_data not found at {_SAMPLE_DATA}", file=sys.stderr)
        return 1

    bundles = find_bundles(_SAMPLE_DATA)
    if not bundles:
        print("No bundles found in sample_data/.", file=sys.stderr)
        return 1

    print(f"\nPrewarm  mode={mode}  workers={max_workers}  bundles={len(bundles)}\n")
    print(f"{'Bundle':<20} {'Docs':>4} {'Time':>8}  Methods")
    print("-" * 60)

    any_fallback = False
    t_total = time.monotonic()

    for bundle_name, paths in bundles:
        t0 = time.monotonic()
        results = extract_documents_parallel(paths, max_workers=max_workers)
        elapsed = time.monotonic() - t0

        methods = [r["extraction_method"] for r in results]
        if mode == "gemma" and any(m != "gemma-4" for m in methods):
            any_fallback = True

        method_str = ", ".join(methods)
        print(f"{bundle_name:<20} {len(results):>4} {elapsed:>7.1f}s  [{method_str}]")

    total_elapsed = time.monotonic() - t_total
    print("-" * 60)
    print(f"{'TOTAL':<20} {sum(len(b[1]) for b in bundles):>4} {total_elapsed:>7.1f}s")

    if any_fallback:
        print("\nWARNING: some documents fell back to regex. Check logs above.", file=sys.stderr)
        return 1

    print("\nAll documents cached successfully.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-warm the ClaimLens extraction cache from sample_data/"
    )
    parser.add_argument(
        "--mode",
        choices=["gemma", "regex"],
        default="regex",
        help="Extraction mode (default: regex). Use 'gemma' before a live demo.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        metavar="N",
        help="Max parallel workers (default: 3).",
    )
    args = parser.parse_args()
    sys.exit(run(args.mode, args.workers))


if __name__ == "__main__":
    main()
