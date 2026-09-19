#!/usr/bin/env python
"""M9 upload-pipeline CLI: process PDFs without the Streamlit UI.

    python scripts/ingest_upload.py path/to/document.pdf
    python scripts/ingest_upload.py data/external_test/AUTOSAR_EXP_PlatformDesign.pdf --json
    python scripts/ingest_upload.py file.pdf --profile application_hld
    python scripts/ingest_upload.py file.pdf --reset      # reprocess from scratch

The pipeline is identical to the UI's Upload screen (M1 -> M2 -> profile ->
profile-gated M4) and honours the same storage/security rules (data/uploads/,
SHA-256 dedupe, isolated upload collection, gitignored runtime artifacts).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import ensure_runtime_dirs  # noqa: E402

ensure_runtime_dirs()


def main() -> int:
    ap = argparse.ArgumentParser(description="ArchSense M9 upload pipeline")
    ap.add_argument("pdf", nargs="+", help="PDF file(s) to process")
    ap.add_argument("--profile", choices=[
        "application_hld", "autosar_adaptive_platform", "generic"],
        default=None, help="force a profile instead of evidence detection")
    ap.add_argument("--version", default=None,
                    help="override the version label (default: detect or "
                         "'unversioned')")
    ap.add_argument("--reset", action="store_true",
                    help="reprocess even if identical content is registered")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output")
    args = ap.parse_args()

    from backend.uploads.pipeline import process_upload

    failures = 0
    results = []
    for raw in args.pdf:
        path = Path(raw)
        if not path.is_file():
            print(f"ERROR: {raw}: no such file", file=sys.stderr)
            failures += 1
            continue
        data = path.read_bytes()
        try:
            res = process_upload(data, path.name, manual_profile=args.profile,
                                 version=args.version, reset=args.reset)
            results.append(res.to_dict())
        except Exception as exc:  # per-file error reporting
            results.append({"original_name": path.name, "ok": False,
                            "error": str(exc)})
            failures += 1

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for res in results:
            if not res.get("ok"):
                print(f"✗ {res['original_name']}: {res.get('error')}")
                continue
            det = res.get("detection", {})
            print(f"✓ {res['original_name']} -> {res['safe_name']}")
            print(f"    profile : {res['profile']} "
                  f"(confidence {det.get('confidence', 'n/a')}, "
                  f"manual: {det.get('manual', False)})")
            print(f"    version : {res['version_label']}"
                  + ("  (duplicate — nothing re-processed)"
                     if res.get("duplicate") else ""))
            print(f"    pages   : {res['page_count']}  chunks: "
                  f"{res['chunk_count']}")
            if res.get("structured"):
                print(f"    M4      : {res['entity_count']} entities / "
                      f"{res['fact_count']} facts")
            else:
                print("    M4      : not available for this profile "
                      "(Copilot retrieval only)")
            t = res.get("timings_ms", {})
            print("    timing  : ingest {ingest_ms:.0f} ms · index "
                  "{index_ms:.0f} ms · extract {extract_ms:.0f} ms · total "
                  "{total_ms:.0f} ms".format(**{**t, "extract_ms":
                                                t.get("extract_ms", 0)}))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
