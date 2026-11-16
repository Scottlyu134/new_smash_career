#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smash_pipeline.cleaning import prepare_stratified_pilot
from smash_pipeline.io_utils import read_json, write_json, write_jsonl


def main() -> None:
    config = read_json(ROOT / "config/pilot.json")
    rows, report = prepare_stratified_pilot(Path(config["input_csv"]), config["families"], config["per_family"], config["random_seed"])
    write_jsonl(ROOT / "data/private/pilot_jds_30.jsonl", rows)
    write_json(ROOT / "artifacts/reports/jd_cleaning_quality.json", report)
    print(f"Prepared {len(rows)} private pilot JDs; report written to artifacts/reports/jd_cleaning_quality.json")


if __name__ == "__main__":
    main()

