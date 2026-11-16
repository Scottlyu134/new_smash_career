# Windows Power BI handoff

This repository intentionally contains no row-level Power BI CSVs, SQLite database, raw job descriptions, candidate text, or other restricted derivatives. The source JD license has not been verified, so those files must remain outside GitHub even though this repository is private.

## What is included

- Reproducible Python ETL and export code
- SQLite schema, KPI views, and example analyst queries
- JSON Schema contracts and tests
- A metric catalog and data dictionary containing definitions only

## Prepare the private input on Windows

Use an approved private transfer channel to copy these three files from the Mac into the cloned repository. Do not add them to Git:

```text
data\private\runs\sprint4a\jd_profiles.jsonl
data\private\runs\sprint4a\candidate_profiles.jsonl
data\private\runs\sprint4a\pairs.jsonl
```

The repository's `.gitignore` excludes this entire private tree.

## Regenerate the Power BI tables

From PowerShell in the repository root, with Python 3.10 or later installed:

```powershell
py -3 -m unittest discover -s tests -v
py -3 scripts\run_analytics.py
```

The second command rebuilds the private SQLite database and exports the Power BI tables to:

```text
data\private\analytics\power_bi\
```

The pipeline expects the 50-JD / 125-candidate / 1,000-pair Sprint 4A input and fails its data-quality gate if those exact counts or referential-integrity checks do not pass.

## Import into Power BI Desktop

1. Open Power BI Desktop and choose **Get data > Text/CSV**.
2. Import the CSVs from `data\private\analytics\power_bi\`.
3. In Model view, create one-to-many relationships from `dim_job[job_id]` and `dim_candidate[candidate_id]` to `fact_pair`, and connect the skill bridge tables using `dim_skill[skill_id]`.
4. Keep cross-filter direction single unless a specific visual requires otherwise.
5. Use `docs\reference\metric_catalog.csv` for KPI definitions and `docs\reference\data_dictionary.csv` for columns and types.
6. Save the `.pbix` locally. It is ignored by Git and must not be committed while the JD license remains unverified.

If the already-generated CSVs are copied from the Mac instead, place them in the same private `power_bi` directory and begin at the import step. Use only an approved private transfer channel.
