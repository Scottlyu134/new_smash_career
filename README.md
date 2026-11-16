# Smash Bullet 1 — Synthetic Training Data Pipeline

> **Repository scope:** code, SQL, schemas, tests, and definition-only documentation. Row-level datasets, SQLite files, Power BI CSVs, raw JD/candidate text, and `.pbix` files are deliberately excluded because the source JD license is unverified.

This local-first pipeline turns a private, cleaned JD pilot into traceable JD profiles, structured synthetic candidate profiles, natural-language resumes, and explicitly rule-derived matching pairs.

## Safety and scope

- Original and transformed LinkedIn-derived text stays in `data/private/`, which is gitignored. Do not publish it.
- API keys are read only from the environment variable named in `config/pilot.json`; they are never accepted as CLI arguments or written to logs.
- Default provider is `mock`, which makes no network calls and costs $0.
- Synthetic labels are curriculum data only. They are not real hiring decisions or evaluation ground truth.
- Pair labels use auditable rules and never use embedding similarity as ground truth.

## Run

Requires Python 3.10+ and no third-party packages.

```bash
cd smash_synthetic_pipeline
python3 scripts/prepare_pilot.py
python3 -m unittest discover -s tests -v
python3 scripts/run_pipeline.py --limit 3 --output-name smoke
python3 scripts/run_pipeline.py --output-name full
```

To use a paid OpenAI-compatible API, first edit the model, pricing, and nonzero `max_estimated_usd` in `config/pilot.json`, then explicitly set the configured environment variable and opt in:

```bash
export OPENAI_API_KEY='...'
python3 scripts/run_pipeline.py --provider openai_compatible --limit 3 --output-name api-smoke
```

The cost budget is checked before each request. Retries log only job IDs, error types, attempts, and returned usage; raw prompts and keys are not logged.

## Outputs

- `artifacts/reports/jd_cleaning_quality.json`: cleaning and stratified-sampling audit.
- `artifacts/reports/smoke_quality.json`: 3-JD dry-run gate.
- `artifacts/reports/full_quality.json`: 30-JD dry-run report.
- `data/private/runs/<run>/`: profiles, resumes, pairs (local only; do not commit).
- `artifacts/logs/<run>.jsonl`: operational logs (gitignored).

The JSON Schema files are the interchange contract. Runtime validation additionally verifies that every evidence offset resolves to the exact quoted substring in the cleaned JD.

## Pair Label Policy v2 (Sprint 3)

Every pair stores `skill_coverage`, `yoe_gap`, `seniority_gap`, and `label_reason`. Required skills receive 1.0 credit for exact matches and 0.5 credit for a conservative, one-to-one equivalence match.

- `positive`: coverage >= 0.75 and every known YOE/seniority requirement is met.
- `hard_negative`: coverage < 0.25, or coverage < 0.50 plus a severe YOE (<= -3) or seniority (<= -2) gap.
- `partial`: all remaining mixed-evidence cases.

Unknown YOE or seniority is neutral rather than treated as a failure. Labels remain synthetic curriculum labels and are not evaluation ground truth.

## Sprint 4A — split-first 1,000-pair pilot

Sprint 4A has a separate entry point and run directory, so the Sprint 1–3
`smoke` and `full` artifacts are not overwritten. JD, candidate, and pair
counts are independent command-line parameters:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run_sprint4a.py \
  --jd-count 50 \
  --candidate-count 125 \
  --pair-count 1000 \
  --output-name sprint4a
```

The pipeline assigns immutable JD source-hash groups to train, validation, or
test before candidate or pair generation. Candidates inherit their source JD
group, and pairs can only be sampled within the same split. The machine-readable
audit is written to `artifacts/reports/sprint4a_quality.json`; private entity and
pair rows remain under `data/private/runs/sprint4a/`.

The readiness table proposed 15 test candidates for 6 test JDs and 120 unique
pairs. That combination has capacity for only 90 unique tuples. Sprint 4A uses
85/20/20 candidates across train/validation/test, retaining the proposed
35/9/6 JDs and 700/180/120 pairs. The total remains exactly 125 candidates and
1,000 unique pairs.

## DA-first Sprint 1 — SQLite analytics, KPIs, and EDA

The DA-first layer reuses the existing Sprint 4A pilot; it does not generate
10,000 pairs or train a model. It builds a normalized private SQLite database,
runs data-quality gates, creates documented KPI views, exports private CSVs for
Power BI, and generates an initial EDA report:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run_analytics.py
```

Key files:

- `analytics/schema.sql`: normalized JD, candidate, pair, and skill model.
- `analytics/views.sql`: KPI views with explicit denominator comments.
- `analytics/kpi_queries.sql`: analyst-facing example queries.
- `docs/da_sprint1_methodology.md`: KPI definitions, offline comparison design,
  and a future true A/B test framework.
- `artifacts/reports/da_sprint1_eda_kpi_report.md`: generated initial findings.
- `artifacts/reports/da_sprint1_data_quality.json`: generated ETL/DQ gates.
- `data/private/analytics/smash_analytics.sqlite`: rebuilt private database.
- `data/private/analytics/power_bi/`: private Power BI-ready tables and metric
  catalog.

All current match metrics are explicitly marked `synthetic_rule_derived`.
There are no recruiter outcomes or randomized-experiment results in this layer.

## Windows Power BI

See [`docs/WINDOWS_POWER_BI.md`](docs/WINDOWS_POWER_BI.md) for the private-file handoff, reproducible export commands, model relationships, and import steps. The committed metric catalog and data dictionary under `docs/reference/` contain definitions only, not project data rows.
