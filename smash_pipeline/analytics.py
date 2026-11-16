from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "private" / "runs" / "sprint4a"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "private" / "analytics" / "smash_analytics.sqlite"
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "data" / "private" / "analytics" / "power_bi"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "artifacts" / "reports" / "da_sprint1_eda_kpi_report.md"
DEFAULT_DQ_PATH = PROJECT_ROOT / "artifacts" / "reports" / "da_sprint1_data_quality.json"


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _normalized_skill(value: str) -> str:
    return " ".join(value.strip().lower().split())


def load_source_rows(input_dir: Path) -> tuple[list[dict], list[dict], list[dict]]:
    jobs = read_jsonl(input_dir / "jd_profiles.jsonl")
    candidates = read_jsonl(input_dir / "candidate_profiles.jsonl")
    pairs = read_jsonl(input_dir / "pairs.jsonl")
    return jobs, candidates, pairs


def _execute_sql_file(connection: sqlite3.Connection, path: Path) -> None:
    connection.executescript(path.read_text(encoding="utf-8"))


def build_database(input_dir: Path = DEFAULT_INPUT_DIR, db_path: Path = DEFAULT_DB_PATH) -> dict:
    jobs, candidates, pairs = load_source_rows(input_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        _execute_sql_file(connection, PROJECT_ROOT / "analytics" / "schema.sql")

        metadata = {
            "analytics_scope": "50 JDs / 125 synthetic candidates / 1,000 sampled unique pairs",
            "metric_provenance": "synthetic_rule_derived",
            "real_recruiting_outcomes_present": "false",
            "source_license_status": "unverified; database and exports remain private",
            "source_run": input_dir.name,
        }
        connection.executemany(
            "INSERT INTO analytics_metadata(key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )

        connection.executemany(
            """INSERT INTO dim_job VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    row["job_id"], row["title"], row["job_family"], row["split"],
                    row["source_jd_group_id"], row["seniority"], row["yoe"].get("minimum"),
                    row["yoe"].get("maximum"), row["yoe"].get("status", "unknown"),
                    row["education"].get("minimum_level"), row["education"].get("status"),
                    len(row.get("required_skills", [])), len(row.get("preferred_skills", [])),
                    0, "unverified; private use only",
                )
                for row in jobs
            ],
        )
        connection.executemany(
            """INSERT INTO dim_candidate VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    row["candidate_id"], row["job_family"], row["split"], row["source_job_id"],
                    row["source_jd_group_id"], row["archetype"], row["seniority"],
                    row.get("years_experience"), row.get("education"), 1,
                )
                for row in candidates
            ],
        )

        skill_names: set[str] = set()
        for job in jobs:
            for item in job.get("required_skills", []) + job.get("preferred_skills", []):
                skill_names.add(_normalized_skill(item["normalized"]))
        for candidate in candidates:
            skill_names.update(_normalized_skill(item) for item in candidate.get("skills", []))
        for pair in pairs:
            features = pair["label_basis"]["features"]
            skill_names.update(_normalized_skill(item) for item in features.get("missing_required_skills", []))

        skill_map = {name: index for index, name in enumerate(sorted(skill_names), start=1)}
        connection.executemany(
            "INSERT INTO dim_skill(skill_id, skill_name) VALUES (?, ?)",
            [(skill_id, name) for name, skill_id in skill_map.items()],
        )

        job_skill_rows = set()
        for job in jobs:
            for skill_type in ("required", "preferred"):
                for item in job.get(f"{skill_type}_skills", []):
                    name = _normalized_skill(item["normalized"])
                    job_skill_rows.add((job["job_id"], skill_map[name], skill_type))
        connection.executemany(
            "INSERT INTO bridge_job_skill(job_id, skill_id, skill_type) VALUES (?, ?, ?)",
            sorted(job_skill_rows),
        )

        candidate_skill_rows = {
            (candidate["candidate_id"], skill_map[_normalized_skill(skill)])
            for candidate in candidates for skill in candidate.get("skills", [])
        }
        connection.executemany(
            "INSERT INTO bridge_candidate_skill(candidate_id, skill_id) VALUES (?, ?)",
            sorted(candidate_skill_rows),
        )

        pair_rows = []
        missing_skill_rows = set()
        for pair in pairs:
            features = pair["label_basis"]["features"]
            exact = features.get("exact_matching_required_skills", [])
            equivalent = features.get("equivalent_skill_matches", [])
            missing = features.get("missing_required_skills", [])
            pair_rows.append((
                pair["pair_id"], pair["job_id"], pair["candidate_id"], pair["split"],
                pair["label"], pair["skill_coverage"],
                features.get("required_skill_coverage", pair["skill_coverage"]),
                pair.get("yoe_gap"), pair.get("seniority_gap"), len(exact), len(equivalent),
                len(missing), int(pair.get("unknown_neutral_positive", False)),
                pair["label_basis"]["policy"], "synthetic_rule_derived",
            ))
            for skill in missing:
                name = _normalized_skill(skill)
                missing_skill_rows.add((pair["pair_id"], skill_map[name]))
        connection.executemany(
            """INSERT INTO fact_pair VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            pair_rows,
        )
        connection.executemany(
            "INSERT INTO bridge_pair_missing_required_skill(pair_id, skill_id) VALUES (?, ?)",
            sorted(missing_skill_rows),
        )
        _execute_sql_file(connection, PROJECT_ROOT / "analytics" / "views.sql")
        connection.commit()
    finally:
        connection.close()
    return {"jobs": len(jobs), "candidates": len(candidates), "pairs": len(pairs), "skills": len(skill_names)}


def _scalar(connection: sqlite3.Connection, sql: str):
    return connection.execute(sql).fetchone()[0]


def run_data_quality_checks(db_path: Path = DEFAULT_DB_PATH) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        counts = {
            "jobs": _scalar(connection, "SELECT COUNT(*) FROM dim_job"),
            "candidates": _scalar(connection, "SELECT COUNT(*) FROM dim_candidate"),
            "pairs": _scalar(connection, "SELECT COUNT(*) FROM fact_pair"),
            "unique_pair_tuples": _scalar(connection, "SELECT COUNT(*) FROM (SELECT job_id, candidate_id FROM fact_pair GROUP BY job_id, candidate_id)"),
        }
        check_values = {
            "exact_pilot_counts_50_125_1000": counts == {"jobs": 50, "candidates": 125, "pairs": 1000, "unique_pair_tuples": 1000},
            "sqlite_integrity_ok": _scalar(connection, "PRAGMA integrity_check") == "ok",
            "foreign_keys_valid": len(connection.execute("PRAGMA foreign_key_check").fetchall()) == 0,
            "pair_split_matches_job": _scalar(connection, "SELECT COUNT(*) FROM fact_pair p JOIN dim_job j USING(job_id) WHERE p.split <> j.split") == 0,
            "pair_split_matches_candidate": _scalar(connection, "SELECT COUNT(*) FROM fact_pair p JOIN dim_candidate c USING(candidate_id) WHERE p.split <> c.split") == 0,
            "pair_measures_in_range": _scalar(connection, "SELECT COUNT(*) FROM fact_pair WHERE skill_coverage NOT BETWEEN 0 AND 1 OR required_skill_coverage NOT BETWEEN 0 AND 1") == 0,
            "skill_coverage_fields_consistent": _scalar(connection, "SELECT COUNT(*) FROM fact_pair WHERE ABS(skill_coverage-required_skill_coverage) > 0.000001") == 0,
            "missing_skill_counts_consistent": _scalar(connection, "SELECT COUNT(*) FROM fact_pair p WHERE p.missing_required_skill_count <> (SELECT COUNT(*) FROM bridge_pair_missing_required_skill g WHERE g.pair_id=p.pair_id)") == 0,
            "all_jobs_paired": _scalar(connection, "SELECT COUNT(*) FROM dim_job j WHERE NOT EXISTS (SELECT 1 FROM fact_pair p WHERE p.job_id=j.job_id)") == 0,
            "all_candidates_paired": _scalar(connection, "SELECT COUNT(*) FROM dim_candidate c WHERE NOT EXISTS (SELECT 1 FROM fact_pair p WHERE p.candidate_id=c.candidate_id)") == 0,
            "only_rule_derived_metrics": _scalar(connection, "SELECT COUNT(*) FROM fact_pair WHERE metric_provenance <> 'synthetic_rule_derived'") == 0,
            "no_real_recruiting_outcomes_claimed": _scalar(connection, "SELECT value FROM analytics_metadata WHERE key='real_recruiting_outcomes_present'") == "false",
        }
        warnings = [
            "All candidate profiles and pair labels are synthetic/rule-derived; none are recruiter outcomes.",
            "The pilot selected JDs with explicit YOE, so 0% YOE missingness reflects selection bias.",
            "JD source license remains unverified; the SQLite database and CSV exports must remain private.",
            "Sprint 4B blind-review package exists, but human review completion is not asserted here.",
        ]
        return {
            "status": "PASS" if all(check_values.values()) else "FAIL",
            "counts": counts,
            "checks": check_values,
            "warnings": warnings,
        }
    finally:
        connection.close()


EXPORT_OBJECTS = [
    "dim_job", "dim_candidate", "dim_skill", "bridge_job_skill", "bridge_candidate_skill",
    "fact_pair", "bridge_pair_missing_required_skill", "v_overall_kpis", "v_match_distribution",
    "v_required_skill_coverage_distribution", "v_experience_gap_distribution",
    "v_jd_coverage_detail", "v_candidate_coverage_detail", "v_skill_bottlenecks",
    "v_missingness", "v_segment_analysis",
]


def _write_query_csv(connection: sqlite3.Connection, query: str, path: Path) -> int:
    cursor = connection.execute(query)
    rows = cursor.fetchall()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([column[0] for column in cursor.description])
        writer.writerows(rows)
    return len(rows)


def export_power_bi_tables(db_path: Path = DEFAULT_DB_PATH, export_dir: Path = DEFAULT_EXPORT_DIR) -> dict[str, int]:
    export_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        counts = {
            name: _write_query_csv(connection, f'SELECT * FROM "{name}"', export_dir / f"{name}.csv")
            for name in EXPORT_OBJECTS
        }
        metric_catalog = [
            ("positive_pair_rate", "positive pairs / all sampled pairs in scope", "sampled pairs in scope", "synthetic_rule_derived"),
            ("viable_pair_rate", "positive or partial pairs / all sampled pairs in scope", "sampled pairs in scope", "synthetic_rule_derived"),
            ("avg_required_skill_coverage", "mean policy-v2 required-skill coverage", "sampled pairs in scope", "synthetic_rule_derived"),
            ("jd_positive_coverage_rate", "JDs with >=1 positive sampled candidate / all pilot JDs", "all pilot JDs", "synthetic_rule_derived"),
            ("candidate_positive_coverage_rate", "candidates with >=1 positive sampled JD / all pilot candidates", "all pilot candidates", "synthetic_rule_derived"),
            ("skill_missing_pair_rate", "pairs missing required skill / pairs for JDs requiring skill", "relevant sampled pairs per skill", "synthetic_rule_derived"),
        ]
        with (export_dir / "metric_catalog.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["metric", "definition", "denominator", "provenance"])
            writer.writerows(metric_catalog)
        counts["metric_catalog"] = len(metric_catalog)

        dictionary_rows = []
        for name in EXPORT_OBJECTS:
            for row in connection.execute(f'PRAGMA table_info("{name}")'):
                dictionary_rows.append((name, row[1], row[2], row[3], row[5]))
        with (export_dir / "data_dictionary.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["object_name", "column_name", "sqlite_type", "not_null", "primary_key"])
            writer.writerows(dictionary_rows)
        counts["data_dictionary"] = len(dictionary_rows)
        return counts
    finally:
        connection.close()


def _one(connection: sqlite3.Connection, sql: str) -> sqlite3.Row:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise RuntimeError("Expected one result row")
    return row


def generate_eda_report(db_path: Path = DEFAULT_DB_PATH, report_path: Path = DEFAULT_REPORT_PATH) -> None:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        overall = _one(connection, "SELECT * FROM v_overall_kpis")
        labels = connection.execute(
            "SELECT rule_label, COUNT(*) n, ROUND(AVG(skill_coverage), 3) avg_cov FROM fact_pair GROUP BY rule_label ORDER BY n DESC"
        ).fetchall()
        jd_cov = _one(connection, "SELECT COUNT(*) n, SUM(has_positive_candidate) positive, SUM(has_viable_candidate) viable FROM v_jd_coverage_detail")
        candidate_cov = _one(connection, "SELECT COUNT(*) n, SUM(positive_jd_count>0) positive, SUM(viable_jd_count>0) viable FROM v_candidate_coverage_detail")
        bottlenecks = connection.execute(
            "SELECT * FROM v_skill_bottlenecks WHERE relevant_pair_denominator >= 10 ORDER BY missing_pair_rate DESC, relevant_pair_denominator DESC LIMIT 8"
        ).fetchall()
        segments = connection.execute(
            "SELECT * FROM v_segment_analysis WHERE pair_denominator >= 10 ORDER BY viable_pair_rate ASC, pair_denominator DESC LIMIT 6"
        ).fetchall()
        missingness = connection.execute("SELECT * FROM v_missingness ORDER BY entity, field").fetchall()

        label_lines = "\n".join(
            f"| {row['rule_label']} | {row['n']} | {row['n']/overall['pair_count']:.1%} | {row['avg_cov']:.3f} |"
            for row in labels
        )
        bottleneck_lines = "\n".join(
            f"| {row['skill_name']} | {row['requiring_jd_count']} | {row['relevant_pair_denominator']} | {row['missing_pair_rate']:.1%} |"
            for row in bottlenecks
        )
        segment_lines = "\n".join(
            f"| {row['jd_job_family']} | {row['candidate_archetype']} | {row['pair_denominator']} | {row['avg_skill_coverage']:.3f} | {row['viable_pair_rate']:.1%} |"
            for row in segments
        )
        missing_lines = "\n".join(
            f"| {row['entity']} | {row['field']} | {row['missing_count']} / {row['entity_denominator']} | {row['missing_rate']:.1%} |"
            for row in missingness
        )

        report = f"""# Smash DA-first Sprint 1 — SQL Analytics, KPI Framework & Initial EDA

## Executive summary

This reproducible analytics prototype uses the existing **50 JDs, 125 synthetic candidates, and 1,000 unique sampled pairs**. It does not expand the dataset, call a paid API, train a model, or report a randomized experiment.

The headline pair mix is **{overall['positive_pair_rate']:.1%} positive** and **{overall['viable_pair_rate']:.1%} positive-or-partial**, with average required-skill coverage of **{overall['avg_required_skill_coverage']:.1%}**. These are properties of the synthetic candidate generator, pair sampler, and Pair Label Policy v2—not Smash recruiting performance or real hiring outcomes.

## KPI snapshot

| KPI | Value | Denominator / scope |
|---|---:|---|
| Sampled pairs | {overall['pair_count']:,} | All unique pilot pairs |
| Paired JDs | {overall['paired_job_count']} | Distinct JDs appearing in pairs |
| Paired candidates | {overall['paired_candidate_count']} | Distinct synthetic candidates appearing in pairs |
| Positive pair rate | {overall['positive_pair_rate']:.1%} | Positive rule labels / all sampled pairs |
| Viable pair rate | {overall['viable_pair_rate']:.1%} | Positive + partial rule labels / all sampled pairs |
| Average required-skill coverage | {overall['avg_required_skill_coverage']:.1%} | Mean Pair Policy v2 coverage across all pairs |
| JDs with >=1 positive sampled candidate | {jd_cov['positive']}/{jd_cov['n']} ({jd_cov['positive']/jd_cov['n']:.1%}) | All 50 pilot JDs |
| JDs with >=1 viable sampled candidate | {jd_cov['viable']}/{jd_cov['n']} ({jd_cov['viable']/jd_cov['n']:.1%}) | All 50 pilot JDs |
| Candidates with >=1 positive sampled JD | {candidate_cov['positive']}/{candidate_cov['n']} ({candidate_cov['positive']/candidate_cov['n']:.1%}) | All 125 pilot candidates |
| Candidates with >=1 viable sampled JD | {candidate_cov['viable']}/{candidate_cov['n']} ({candidate_cov['viable']/candidate_cov['n']:.1%}) | All 125 pilot candidates |

## Match distribution

| Rule label | Pairs | Pair rate | Avg. skill coverage |
|---|---:|---:|---:|
{label_lines}

The dominant hard-negative share is expected from the configured stratified sampling target. It must not be interpreted as a market rejection rate.

## Required-skill bottlenecks

The table uses a per-skill denominator: sampled pairs attached to JDs that require the skill. A pair is missing the skill when the existing exact/equivalence matching policy records it as missing.

| Required skill | Requiring JDs | Relevant pair denominator | Missing rate |
|---|---:|---:|---:|
{bottleneck_lines}

These bottlenecks describe the simulated candidate pool. They are useful for validating matching logic and designing dashboard drill-downs, but they do not establish real labor-market shortages.

## Lowest-coverage segments

| JD family | Candidate archetype | Pair denominator | Avg. skill coverage | Viable pair rate |
|---|---|---:|---:|---:|
{segment_lines}

Segment comparisons are descriptive because pair sampling is stratified and observations reuse JDs and candidates. Simple pair-level significance tests would understate clustering and selection effects.

## Missingness and data-quality interpretation

| Entity | Field | Missing / total | Missing rate |
|---|---|---:|---:|
{missing_lines}

Minimum YOE missingness is 0% because Sprint 4A preferentially selected JDs with explicit YOE evidence. This is selection bias, not proof that the upstream source is complete. Education missingness remains visible rather than being imputed.

## Provenance boundary

- **Synthetic:** all 125 candidate profiles, resumes, and generated employment histories.
- **Rule-derived:** pair labels, skill coverage, missing skills, YOE gaps, seniority gaps, and every KPI in this report.
- **Source-derived but private:** the 50 cleaned JD profiles and extracted requirements; source license is still unverified.
- **Not present:** recruiter reviews, shortlist decisions, interviews, offers, placements, retention, or randomized treatment assignment.

Therefore, no metric here should be renamed “accuracy,” “conversion,” “hire rate,” or “business lift.” Sprint 4B human review remains pending until a person completes the blind workbook and the analysis script is run.

## Power BI dashboard plan

1. **Executive overview:** pair mix, average skill coverage, JD coverage, candidate coverage, provenance banner.
2. **Skill gaps:** required-skill demand, missing-pair rate, JD-family drill-down, minimum denominator filter.
3. **Experience & segment diagnostics:** YOE gap buckets, job family, archetype, split, and sample sizes.
4. **Data quality:** missingness, entity counts, split isolation, and synthetic/rule-derived warnings.
5. **Strategy comparison (future):** side-by-side offline ranking metrics with cluster-aware confidence intervals; no A/B-test language unless traffic is randomized.

The CSV exports are stored under `data/private/analytics/power_bi/` and must remain private with the SQLite database.

## Reproduce

```bash
cd {PROJECT_ROOT}
python3 -m unittest discover -s tests -v
python3 scripts/run_analytics.py
```

The command rebuilds the SQLite file from scratch, runs data-quality gates, exports Power BI-ready CSVs, and regenerates this report.
"""
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
    finally:
        connection.close()


def write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_all(
    input_dir: Path = DEFAULT_INPUT_DIR,
    db_path: Path = DEFAULT_DB_PATH,
    export_dir: Path = DEFAULT_EXPORT_DIR,
    report_path: Path = DEFAULT_REPORT_PATH,
    dq_path: Path = DEFAULT_DQ_PATH,
) -> dict:
    counts = build_database(input_dir, db_path)
    quality = run_data_quality_checks(db_path)
    write_json(quality, dq_path)
    if quality["status"] != "PASS":
        raise RuntimeError(f"Analytics data-quality checks failed; see {dq_path}")
    exports = export_power_bi_tables(db_path, export_dir)
    generate_eda_report(db_path, report_path)
    return {"database_counts": counts, "data_quality": quality, "export_rows": exports}
