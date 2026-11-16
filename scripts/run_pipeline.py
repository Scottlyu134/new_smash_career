#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smash_pipeline.extraction import OpenAICompatibleExtractor, mock_extract
from smash_pipeline.generation import generate_candidates, generate_pairs, render_resume
from smash_pipeline.io_utils import read_json, read_jsonl, write_json, write_jsonl
from smash_pipeline.quality import quality_report
from smash_pipeline.schema_validation import validate as validate_json_schema


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Number of JDs; omit for all 30")
    parser.add_argument("--provider", choices=["mock", "openai_compatible"], default=None)
    parser.add_argument("--output-name", default="full")
    args = parser.parse_args()
    config = read_json(ROOT / "config/pilot.json")
    extraction_config = config["extraction"]
    if args.provider: extraction_config["provider"] = args.provider
    jds = read_jsonl(ROOT / "data/private/pilot_jds_30.jsonl")
    if args.limit is not None:
        # Round-robin across job families so a small smoke run still exercises
        # cross-family pairing instead of taking one contiguous stratum.
        by_family: dict[str, list[dict]] = {}
        for jd in jds:
            by_family.setdefault(jd["job_family"], []).append(jd)
        interleaved: list[dict] = []
        depth = 0
        while len(interleaved) < min(args.limit, len(jds)):
            for family_rows in by_family.values():
                if depth < len(family_rows):
                    interleaved.append(family_rows[depth])
                    if len(interleaved) == min(args.limit, len(jds)):
                        break
            depth += 1
        jds = interleaved

    log_path = ROOT / f"artifacts/logs/{args.output_name}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("smash")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(logging.FileHandler(log_path, mode="w", encoding="utf-8"))
    schema = read_json(ROOT / "schemas/jd_profile.schema.json")
    api_extractor = OpenAICompatibleExtractor(extraction_config, schema, logger) if extraction_config["provider"] == "openai_compatible" else None

    profiles, failures = [], []
    for jd in jds:
        try:
            profile = api_extractor.extract(jd) if api_extractor else mock_extract(jd, extraction_config["model"], schema)
            profiles.append(profile)
            logger.info(json.dumps({"event": "extract_success", "job_id": jd["job_id"], "provider": extraction_config["provider"]}))
        except Exception as exc:
            failures.append({"job_id": jd["job_id"], "error_type": type(exc).__name__, "message": str(exc)[:300]})
            logger.error(json.dumps({"event": "extract_failure", **failures[-1]}))

    successful_ids = {p["job_id"] for p in profiles}
    successful_jds = [j for j in jds if j["job_id"] in successful_ids]
    candidates = generate_candidates(profiles)
    candidate_schema = read_json(ROOT / "schemas/candidate_profile.schema.json")
    for candidate in candidates:
        validate_json_schema(candidate, candidate_schema)
    resumes = [render_resume(candidate) for candidate in candidates]
    pairs = generate_pairs(profiles, candidates) if len(profiles) > 1 else []
    pair_schema = read_json(ROOT / "schemas/pair.schema.json")
    for pair in pairs:
        validate_json_schema(pair, pair_schema)
    private = ROOT / f"data/private/runs/{args.output_name}"
    write_jsonl(private / "jd_profiles.jsonl", profiles)
    write_jsonl(private / "candidate_profiles.jsonl", candidates)
    write_jsonl(private / "resumes.jsonl", resumes)
    write_jsonl(private / "pairs.jsonl", pairs)
    report = quality_report(successful_jds, profiles, candidates, resumes, pairs, failures)
    report["run"] = {"name": args.output_name, "provider": extraction_config["provider"], "model": extraction_config["model"], "requested_jds": len(jds), "paid_api_calls": api_extractor.budget.calls if api_extractor else 0, "estimated_api_cost_usd": api_extractor.budget.estimated_usd if api_extractor else 0.0}
    write_json(ROOT / f"artifacts/reports/{args.output_name}_quality.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

