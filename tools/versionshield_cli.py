#!/usr/bin/env python3
"""Run VersionShield static/OpenAPI analysis in local automation or CI.

This tool never executes source code from the uploaded ZIPs. It analyzes source
patterns and declared OpenAPI contracts only; runtime compatibility is not tested.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
from app.analyzer import analyze_archives  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare old client/backend with a proposed backend.")
    parser.add_argument("--old-frontend", required=True, type=Path, help="ZIP of old client source")
    parser.add_argument("--old-backend", required=True, type=Path, help="ZIP of old backend source")
    parser.add_argument("--new-backend", required=True, type=Path, help="ZIP of proposed backend source")
    parser.add_argument("--output", type=Path, help="Write JSON report to this path (default: stdout)")
    parser.add_argument("--fail-on", choices=["never", "high", "critical"], default="high",
                        help="Exit 2 if report contains this severity or above; default: high")
    args = parser.parse_args()

    for path in (args.old_frontend, args.old_backend, args.new_backend):
        if not path.is_file():
            parser.error(f"File not found: {path}")

    try:
        report = analyze_archives(args.old_frontend.read_bytes(), args.old_backend.read_bytes(), args.new_backend.read_bytes())
    except (ValueError, OSError) as exc:
        print(f"VersionShield analysis failed: {exc}", file=sys.stderr)
        return 1

    output = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)

    if args.fail_on == "never":
        return 0
    threshold = {"critical": 4, "high": 3}
    rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    if any(rank.get(f.get("severity", "low"), 0) >= threshold[args.fail_on] for f in report.get("findings", [])):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
