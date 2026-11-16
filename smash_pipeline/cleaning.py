from __future__ import annotations

import csv
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from .io_utils import sha256_text


URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
SPACE_RE = re.compile(r"[ \t]+")
EEO_RE = re.compile(r"(?is)(?:equal opportunity employer|eeo statement|we are an equal opportunity).*?(?=\n\s*\n|$)")


def normalize_text(text: str) -> str:
    return SPACE_RE.sub(" ", text.strip().lower())


def clean_description(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = EEO_RE.sub(" ", text)
    text = URL_RE.sub("[URL]", text)
    text = EMAIL_RE.sub("[EMAIL]", text)
    text = PHONE_RE.sub("[PHONE]", text)
    lines = [SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    compact: list[str] = []
    for line in lines:
        if not line:
            if compact and compact[-1] != "":
                compact.append("")
            continue
        if re.search(r"(?i)^(apply (now|here)|about (us|the company)|company description)\b", line):
            continue
        compact.append(line)
    return "\n".join(compact).strip()


def classify_family(title: str, families: dict[str, list[str]]) -> str | None:
    normalized = normalize_text(title)
    for family, needles in families.items():
        for needle in needles:
            phrase = normalize_text(needle)
            pattern = r"(?<![a-z0-9])" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
            if re.search(pattern, normalized):
                return family
    return None


def prepare_stratified_pilot(csv_path: Path, families: dict[str, list[str]], per_family: int, seed: int) -> tuple[list[dict], dict]:
    pools: dict[str, list[dict]] = defaultdict(list)
    seen: set[str] = set()
    input_rows = 0
    empty_description = 0
    duplicate_rows = 0
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            input_rows += 1
            description = row.get("description", "").strip()
            if not description:
                empty_description += 1
                continue
            family = classify_family(row.get("title", ""), families)
            if family is None:
                continue
            cleaned = clean_description(description)
            key = sha256_text("|".join([normalize_text(row.get("title", "")), normalize_text(cleaned), row.get("company_id", "")]))
            if key in seen:
                duplicate_rows += 1
                continue
            seen.add(key)
            pools[family].append({
                "job_id": str(row.get("job_id", "")),
                "company_id": str(row.get("company_id", "")),
                "title": row.get("title", "").strip(),
                "job_family": family,
                "description_clean": cleaned,
                "source_description_sha256": sha256_text(description),
                "clean_description_sha256": sha256_text(cleaned),
                "source_experience_level": row.get("formatted_experience_level", "").strip() or None,
                "source_location": row.get("location", "").strip() or None,
                "provenance": {"source_file": csv_path.name, "cleaner": "smash-cleaner-v1"}
            })
    rng = random.Random(seed)
    selected: list[dict] = []
    for family in families:
        rng.shuffle(pools[family])
        if len(pools[family]) < per_family:
            raise ValueError(f"Not enough unique rows for {family}: {len(pools[family])} < {per_family}")
        selected.extend(pools[family][:per_family])
    report = {
        "input_rows": input_rows,
        "empty_description_rows": empty_description,
        "target_family_unique_rows": {key: len(pools[key]) for key in families},
        "duplicates_removed_in_target_pool": duplicate_rows,
        "pilot_rows": len(selected),
        "pilot_by_family": {key: sum(row["job_family"] == key for row in selected) for key in families},
        "pilot_unique_job_ids": len({row["job_id"] for row in selected}),
        "pilot_unique_clean_hashes": len({row["clean_description_sha256"] for row in selected}),
        "contact_tokens_remaining": sum(bool(EMAIL_RE.search(row["description_clean"]) or PHONE_RE.search(row["description_clean"])) for row in selected),
        "raw_text_publication_warning": "Pilot rows contain transformed recruitment text and must remain under data/private; do not commit or publish."
    }
    return selected, report


def prepare_allocated_pilot(
    csv_path: Path,
    families: dict[str, list[str]],
    allocation: dict[str, int],
    seed: int,
    max_per_company: int = 2,
) -> tuple[list[dict], dict]:
    """Select a reproducible private JD pilot with family and company caps.

    Rows with an explicit years-of-experience phrase are ranked first, but rows
    without one remain unknown; no value is inferred or filled.
    """
    pools: dict[str, list[dict]] = defaultdict(list)
    seen: set[str] = set()
    input_rows = empty_description = duplicate_rows = 0
    yoe_pattern = re.compile(r"(?i)\b\d{1,2}(?:\s*[-–]\s*\d{1,2})?\+?\s+years?(?:['’])?(?:\s+of)?(?:\s+[a-z][a-z/-]*){0,6}\s+experience\b")
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            input_rows += 1
            description = row.get("description", "").strip()
            if not description:
                empty_description += 1
                continue
            family = classify_family(row.get("title", ""), families)
            if family is None:
                continue
            cleaned = clean_description(description)
            key = sha256_text("|".join([normalize_text(row.get("title", "")), normalize_text(cleaned), row.get("company_id", "")]))
            if key in seen:
                duplicate_rows += 1
                continue
            seen.add(key)
            pools[family].append({
                "job_id": str(row.get("job_id", "")),
                "company_id": str(row.get("company_id", "")),
                "title": row.get("title", "").strip(),
                "job_family": family,
                "description_clean": cleaned,
                "source_description_sha256": sha256_text(description),
                "clean_description_sha256": sha256_text(cleaned),
                "source_experience_level": row.get("formatted_experience_level", "").strip() or None,
                "source_location": row.get("location", "").strip() or None,
                "provenance": {"source_file": csv_path.name, "cleaner": "smash-cleaner-v1"},
                "selection_has_explicit_yoe": bool(yoe_pattern.search(cleaned)),
            })

    rng = random.Random(seed)
    company_counts: dict[str, int] = defaultdict(int)
    selected: list[dict] = []
    for family in families:
        target = allocation.get(family, 0)
        family_pool = pools[family][:]
        rng.shuffle(family_pool)
        family_pool.sort(key=lambda row: not row["selection_has_explicit_yoe"])
        chosen: list[dict] = []
        for row in family_pool:
            company = row["company_id"] or "unknown-" + row["job_id"]
            if company_counts[company] >= max_per_company:
                continue
            chosen.append(row)
            company_counts[company] += 1
            if len(chosen) == target:
                break
        if len(chosen) < target:
            raise ValueError(f"Not enough capped unique rows for {family}: {len(chosen)} < {target}")
        selected.extend(chosen)

    report = {
        "input_rows": input_rows,
        "empty_description_rows": empty_description,
        "target_family_unique_rows": {key: len(pools[key]) for key in families},
        "duplicates_removed_in_target_pool": duplicate_rows,
        "pilot_rows": len(selected),
        "pilot_by_family": dict(Counter(row["job_family"] for row in selected)),
        "pilot_unique_job_ids": len({row["job_id"] for row in selected}),
        "pilot_unique_clean_hashes": len({row["clean_description_sha256"] for row in selected}),
        "max_jds_per_company": max(company_counts.values(), default=0),
        "selected_with_explicit_yoe_hint": sum(row["selection_has_explicit_yoe"] for row in selected),
        "contact_tokens_remaining": sum(bool(EMAIL_RE.search(row["description_clean"]) or PHONE_RE.search(row["description_clean"])) for row in selected),
        "raw_text_publication_warning": "Pilot rows contain transformed recruitment text and must remain under data/private; do not commit or publish.",
    }
    return selected, report

