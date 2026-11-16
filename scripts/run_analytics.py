#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smash_pipeline.analytics import (  # noqa: E402
    DEFAULT_DB_PATH,
    DEFAULT_DQ_PATH,
    DEFAULT_EXPORT_DIR,
    DEFAULT_INPUT_DIR,
    DEFAULT_REPORT_PATH,
    run_all,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the private Smash DA-first SQLite analytics layer")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT_DIR)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--dq-path", type=Path, default=DEFAULT_DQ_PATH)
    args = parser.parse_args()
    result = run_all(args.input_dir, args.db_path, args.export_dir, args.report_path, args.dq_path)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
