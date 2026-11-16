#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smash_pipeline.cleaning import prepare_allocated_pilot
from smash_pipeline.extraction import mock_extract, validate_jd_profile
from smash_pipeline.io_utils import read_json, write_json, write_jsonl
from smash_pipeline.schema_validation import validate as validate_json_schema
from smash_pipeline.sprint4a import (
    SPLITS, assign_splits, attach_profile_groups, audit_sprint4a,
    generate_candidates_exact, generate_pairs_exact, render_resume_v2,
)


def _allocate(total: int, weights: dict[str, int]) -> dict[str, int]:
    weight_total = sum(weights.values())
    result = {key: total * value // weight_total for key, value in weights.items()}
    remainder = total - sum(result.values())
    order = sorted(weights, key=lambda key: (-(total * weights[key] / weight_total - result[key]), key))
    for key in order[:remainder]:
        result[key] += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the private Sprint 4A split-first pilot")
    parser.add_argument("--jd-count", type=int, default=None)
    parser.add_argument("--candidate-count", type=int, default=None)
    parser.add_argument("--pair-count", type=int, default=None)
    parser.add_argument("--output-name", default="sprint4a")
    args = parser.parse_args()
    if args.output_name in {"full", "smoke"}:
        raise ValueError("Sprint 1-3 run names are protected")
    config = read_json(ROOT / "config/sprint4a.json")
    jd_count = args.jd_count or config["targets"]["jds"]
    candidate_count = args.candidate_count or config["targets"]["candidates"]
    pair_count = args.pair_count or config["targets"]["pairs"]
    if min(jd_count, candidate_count, pair_count) <= 0:
        raise ValueError("All independently controlled counts must be positive")

    family_allocation = _allocate(jd_count, config["family_allocation"])
    jds, selection_report = prepare_allocated_pilot(
        Path(config["input_csv"]), config["families"], family_allocation,
        config["random_seed"], config["max_jds_per_company"],
    )
    jd_splits = _allocate(jd_count, {split: config["split_allocation"][split]["jds"] for split in SPLITS})
    candidate_splits = _allocate(candidate_count, {split: config["split_allocation"][split]["candidates"] for split in SPLITS})
    pair_splits = _allocate(pair_count, {split: config["split_allocation"][split]["pairs"] for split in SPLITS})
    jds = assign_splits(jds, jd_splits, config["random_seed"])

    jd_schema = read_json(ROOT / "schemas/jd_profile.schema.json")
    profiles = []
    for jd in jds:
        profile = mock_extract(jd, config["extraction"]["model"], jd_schema)
        profiles.append(profile)
    profiles = attach_profile_groups(profiles, jds)
    for profile in profiles:
        validate_jd_profile(profile, next(row["description_clean"] for row in jds if row["job_id"] == profile["job_id"]), jd_schema)

    candidates = []
    for split in SPLITS:
        split_profiles = [row for row in profiles if row["split"] == split]
        candidates.extend(generate_candidates_exact(split_profiles, candidate_splits[split], split))
    candidate_schema = read_json(ROOT / "schemas/candidate_profile.schema.json")
    for candidate in candidates:
        validate_json_schema(candidate, candidate_schema)
    resumes = [render_resume_v2(candidate, index) for index, candidate in enumerate(candidates)]

    pairs = []
    for offset, split in enumerate(SPLITS):
        pairs.extend(generate_pairs_exact(
            [row for row in profiles if row["split"] == split],
            [row for row in candidates if row["split"] == split],
            pair_splits[split], split, config["random_seed"] + offset,
            config["label_mix"], config["unknown_neutral_positive_cap"],
        ))
    pair_schema = read_json(ROOT / "schemas/pair.schema.json")
    for pair in pairs:
        validate_json_schema(pair, pair_schema)

    run_dir = ROOT / "data/private/runs" / args.output_name
    write_jsonl(run_dir / "jds.jsonl", jds)
    write_jsonl(run_dir / "jd_profiles.jsonl", profiles)
    write_jsonl(run_dir / "candidate_profiles.jsonl", candidates)
    write_jsonl(run_dir / "resumes.jsonl", resumes)
    write_jsonl(run_dir / "pairs.jsonl", pairs)
    review_sample = []
    for label in ("positive", "partial", "hard_negative"):
        review_sample.extend(sorted((p for p in pairs if p["label"] == label), key=lambda p: p["pair_id"])[:20])
    write_jsonl(run_dir / "manual_review_sample_60.jsonl", review_sample)

    audit = audit_sprint4a(jds, profiles, candidates, resumes, pairs)
    audit["run"] = {
        "name": args.output_name, "seed": config["random_seed"], "provider": "mock",
        "paid_api_calls": 0, "model_training": False,
        "requested": {"jds": jd_count, "candidates": candidate_count, "pairs": pair_count},
        "family_allocation": family_allocation,
        "split_allocation": {split: {"jds": jd_splits[split], "candidates": candidate_splits[split], "pairs": pair_splits[split]} for split in SPLITS},
        "allocation_note": config["allocation_note"],
        "label_scope": "synthetic curriculum labels; not independent ground truth",
    }
    audit["selection"] = selection_report
    write_json(ROOT / "artifacts/reports" / f"{args.output_name}_quality.json", audit)
    manifest = {
        "run": args.output_name, "seed": config["random_seed"], "counts": audit["counts"],
        "policy": "pair-label-policy-v2", "generator": "deterministic-profile-v2",
        "splits": audit["split_distributions"], "private_data": True,
        "source_license_status": "unverified; no redistribution approved",
        "commands": [
            "python3 -m unittest discover -s tests -v",
            f"python3 scripts/run_sprint4a.py --jd-count {jd_count} --candidate-count {candidate_count} --pair-count {pair_count} --output-name {args.output_name}",
        ],
    }
    write_json(ROOT / "artifacts/reports" / f"{args.output_name}_manifest.json", manifest)
    print(json.dumps({"counts": audit["counts"], "labels": audit["labels"]["overall"], "gates": audit["quality_gates"], "recommendation": audit["recommendation"]}, indent=2))


if __name__ == "__main__":
    main()
