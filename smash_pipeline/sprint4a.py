from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import random
import re

from .generation import FALLBACK_SKILLS, _pair, _skill_names, _seniority_for, label_from_features
from .io_utils import sha256_text


SPLITS = ("train", "validation", "test")
ARCHETYPES = ("strong", "adjacent", "junior", "gap", "mismatch")
LAYOUTS = ("classic", "skills_first", "project_brief", "compact")
ADJACENT_SKILLS = {
    "data_analyst": ["power bi", "data visualization", "dbt"],
    "data_scientist": ["pytorch", "deep learning", "nlp"],
    "data_engineer": ["airflow", "kafka", "aws"],
    "machine_learning": ["tensorflow", "kubernetes", "aws"],
    "ai_llm": ["machine learning", "deep learning", "docker"],
    "software_engineer": ["javascript", "react", "kubernetes"],
}


def _stable_int(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return int(digest[:16], 16)


def assign_splits(jds: list[dict], split_counts: dict[str, int], seed: int) -> list[dict]:
    """Partition source-hash groups before extraction/candidate generation."""
    if sum(split_counts.values()) != len(jds):
        raise ValueError("Split JD counts must sum to selected JD count")
    if len({row["clean_description_sha256"] for row in jds}) != len(jds):
        raise ValueError("Duplicate source JD hash cannot be split safely")
    ordered = sorted(jds, key=lambda row: _stable_int(seed, row["job_family"], row["job_id"]))
    by_family: dict[str, list[dict]] = defaultdict(list)
    for row in ordered:
        by_family[row["job_family"]].append(row)
    interleaved: list[dict] = []
    while any(by_family.values()):
        for family in sorted(by_family):
            if by_family[family]:
                interleaved.append(by_family[family].pop(0))
    result: list[dict] = []
    cursor = 0
    for split in SPLITS:
        count = split_counts[split]
        for row in interleaved[cursor:cursor + count]:
            copy = dict(row)
            copy["split"] = split
            copy["source_jd_group_id"] = "jdg-" + row["clean_description_sha256"][:16]
            result.append(copy)
        cursor += count
    return result


def attach_profile_groups(profiles: list[dict], jds: list[dict]) -> list[dict]:
    metadata = {row["job_id"]: row for row in jds}
    for profile in profiles:
        row = metadata[profile["job_id"]]
        profile["split"] = row["split"]
        profile["source_jd_group_id"] = row["source_jd_group_id"]
    return profiles


def _candidate_variant(profile: dict, ordinal: int, split: str) -> dict:
    archetype_offset = _stable_int(profile["job_id"], "archetype") % len(ARCHETYPES)
    archetype = ARCHETYPES[(ordinal + archetype_offset) % len(ARCHETYPES)]
    base = _skill_names(profile)
    required = [item["normalized"] for item in profile["required_skills"]] or FALLBACK_SKILLS[profile["job_family"]]
    adjacent = ADJACENT_SKILLS[profile["job_family"]]
    target = profile["yoe"]["minimum"] if profile["yoe"]["minimum"] is not None else 3
    variation = ordinal // len(ARCHETYPES)
    if archetype == "strong":
        skills = base + FALLBACK_SKILLS[profile["job_family"]] + adjacent[:1 + variation % 2]
        years = max(target, 4) + variation
    elif archetype == "adjacent":
        skills = base[:max(1, len(base) - 1)] + FALLBACK_SKILLS[profile["job_family"]][:3] + adjacent[:2]
        years = max(target, 3) + variation % 2
    elif archetype == "junior":
        skills, years = required[:max(1, len(required) // 2)] + adjacent[:1], max(0, target - 2 + variation % 2)
    elif archetype == "gap":
        skills, years = required[:1] + adjacent[variation % len(adjacent):variation % len(adjacent) + 1], max(target, 3) + variation
    else:
        mismatch_sets = (["customer service", "scheduling"], ["sales", "crm"], ["operations", "project coordination"])
        skills, years = mismatch_sets[variation % len(mismatch_sets)], 1 + variation % 3
    skills = list(dict.fromkeys(skills))
    education = profile["education"]["minimum_level"]
    if education == "unknown":
        education = ("associate", "bachelor", "master")[(_stable_int(profile["job_id"], ordinal) % 3)]
    seniority = _seniority_for(archetype, profile["seniority"])
    candidate_id = f"cand-s4a-{split[:3]}-{profile['job_id']}-{ordinal + 1:02d}"
    action = ("automated", "redesigned", "scaled", "validated")[_stable_int(candidate_id) % 4]
    outcome = 8 + (_stable_int(candidate_id, "outcome") % 43)
    workflow_count = 2 + (_stable_int(candidate_id, "scope") % 17)
    role = f"{profile['job_family'].replace('_', ' ').title()} {('Specialist', 'Engineer', 'Analyst', 'Contributor')[ordinal % 4]}"
    achievement = f"{action.title()} {workflow_count} synthetic {profile['job_family'].replace('_', ' ')} workflows using {', '.join(skills)} and improved a simulated quality metric by {outcome}%."
    employment = [{"role": role, "years": years, "skills_used": skills, "achievement": achievement}]
    if ordinal % 3 == 1 and years >= 2:
        prior_years = max(1, years // 2)
        prior_skills = list(dict.fromkeys((skills[-2:] or skills) + ["git"]))
        employment.append({
            "role": f"Associate {profile['job_family'].replace('_', ' ').title()}",
            "years": prior_years,
            "skills_used": prior_skills,
            "achievement": f"Built a synthetic internal project with {', '.join(prior_skills)} across {prior_years} years of representative work.",
        })
    return {
        "schema_version": "1.0", "candidate_id": candidate_id, "source_job_id": profile["job_id"],
        "source_jd_group_id": profile["source_jd_group_id"], "split": split, "archetype": archetype,
        "job_family": profile["job_family"], "seniority": seniority, "years_experience": years,
        "skills": skills, "education": education, "employment": employment,
        "provenance": {
            "generator": "deterministic-profile-v2", "source_jd_profile": profile["job_id"],
            "archetype_rule": archetype, "variant_ordinal": ordinal,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def generate_candidates_exact(profiles: list[dict], count: int, split: str) -> list[dict]:
    if not profiles and count:
        raise ValueError("Cannot generate candidates without profiles")
    candidates: list[dict] = []
    ordinals: Counter[str] = Counter()
    ordered = sorted(profiles, key=lambda row: _stable_int(split, row["job_id"]))
    for index in range(count):
        profile = ordered[index % len(ordered)]
        ordinal = ordinals[profile["job_id"]]
        candidates.append(_candidate_variant(profile, ordinal, split))
        ordinals[profile["job_id"]] += 1
    return candidates


def render_resume_v2(candidate: dict, index: int) -> dict:
    layout = LAYOUTS[index % len(LAYOUTS)]
    identity = f"CANDIDATE {candidate['candidate_id']}"
    summary = f"{candidate['seniority'].title()} {candidate['job_family'].replace('_', ' ')} professional with {candidate['years_experience']} years of experience."
    skills = ", ".join(candidate["skills"])
    jobs = "\n".join(f"{item['role']} — {item['years']} years\n{item['achievement']}" for item in candidate["employment"])
    education = f"{candidate['education'].title()} level education."
    if layout == "classic":
        text = f"{identity}\n\nSUMMARY\n{summary}\n\nSKILLS\n{skills}\n\nEXPERIENCE\n{jobs}\n\nEDUCATION\n{education}"
    elif layout == "skills_first":
        text = f"{identity}\nCORE CAPABILITIES: {skills}\n\nPROFILE\n{summary}\n\nCAREER HISTORY\n{jobs}\n\nEDUCATION\n{education}"
    elif layout == "project_brief":
        text = f"{identity}\n{summary}\n\nSELECTED IMPACT\n{jobs}\n\nTOOLKIT\n{skills}\n\nACADEMIC BACKGROUND\n{education}"
    else:
        text = f"{identity} | {summary}\nSKILLS: {skills}\nEXPERIENCE\n{jobs}\nEDUCATION: {education}"
    return {
        "resume_id": "resume-" + candidate["candidate_id"], "candidate_id": candidate["candidate_id"],
        "split": candidate["split"], "layout": layout, "text": text,
        "provenance": {"renderer": "template-v2", "profile_id": candidate["candidate_id"], "layout": layout},
    }


def _round_robin(rows: list[dict], limit: int, seed: int) -> list[dict]:
    by_job: dict[str, list[dict]] = defaultdict(list)
    for row in sorted(rows, key=lambda p: _stable_int(seed, p["pair_id"])):
        by_job[row["job_id"]].append(row)
    selected: list[dict] = []
    jobs = sorted(by_job, key=lambda job_id: _stable_int(seed, job_id))
    while len(selected) < limit and any(by_job.values()):
        for job_id in jobs:
            if by_job[job_id] and len(selected) < limit:
                selected.append(by_job[job_id].pop())
    return selected


def generate_pairs_exact(
    profiles: list[dict], candidates: list[dict], count: int, split: str, seed: int,
    label_mix: dict[str, float], unknown_positive_cap: float = 0.30,
) -> list[dict]:
    if any(row.get("split") != split for row in profiles + candidates):
        raise ValueError("Cross-split entity supplied to pair generator")
    capacity = len(profiles) * len(candidates)
    if count > capacity:
        raise ValueError(f"Requested {count} pairs but split capacity is {capacity}")
    pool: list[dict] = []
    jd_by_id = {row["job_id"]: row for row in profiles}
    candidate_by_id = {row["candidate_id"]: row for row in candidates}
    for jd in profiles:
        for candidate in candidates:
            relation = "same-source-JD" if candidate["source_job_id"] == jd["job_id"] else ("cross-JD-same-family" if candidate["job_family"] == jd["job_family"] else "cross-family")
            pair = _pair(candidate, jd, f"sprint4a stratified pool: {relation}")
            pair["split"] = split
            pair["source_jd_group_id"] = candidate["source_jd_group_id"]
            pair["sampling_reason"] = "policy-v2 label retained; sampled after split assignment"
            pair["unknown_neutral_positive"] = pair["label"] == "positive" and jd["yoe"]["minimum"] is None
            pool.append(pair)
    if count == capacity:
        quotas = dict(Counter(row["label"] for row in pool))
    else:
        quotas = {label: int(count * label_mix[label]) for label in label_mix}
        quotas["hard_negative"] += count - sum(quotas.values())
    selected: list[dict] = []
    for label in ("positive", "partial", "hard_negative"):
        eligible = [row for row in pool if row["label"] == label]
        quota = quotas.get(label, 0)
        if label == "positive":
            known = [row for row in eligible if not row["unknown_neutral_positive"]]
            unknown = [row for row in eligible if row["unknown_neutral_positive"]]
            unknown_limit = int(quota * unknown_positive_cap)
            eligible = known + _round_robin(unknown, min(unknown_limit, len(unknown)), seed + 17)
        if len(eligible) < quota:
            raise ValueError(f"Not enough {label} pairs in {split}: {len(eligible)} < {quota}")
        selected.extend(_round_robin(eligible, quota, seed + len(selected)))
    if len({(p["job_id"], p["candidate_id"]) for p in selected}) != count:
        raise ValueError("Pair sampler produced a duplicate tuple")
    for pair in selected:
        candidate = candidate_by_id[pair["candidate_id"]]
        jd = jd_by_id[pair["job_id"]]
        if candidate["split"] != jd["split"]:
            raise ValueError("Pair leakage detected during generation")
    return sorted(selected, key=lambda row: row["pair_id"])


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]+", value.lower()))


def _near_duplicate_count(texts: list[str], threshold: float = 0.80) -> int:
    token_sets = [_tokens(text) for text in texts]
    count = 0
    for i, left in enumerate(token_sets):
        for right in token_sets[i + 1:]:
            union = left | right
            if union and len(left & right) / len(union) >= threshold:
                count += 1
    return count


def audit_sprint4a(jds: list[dict], profiles: list[dict], candidates: list[dict], resumes: list[dict], pairs: list[dict]) -> dict:
    by_split = {split: {
        "jds": [row for row in jds if row["split"] == split],
        "candidates": [row for row in candidates if row["split"] == split],
        "pairs": [row for row in pairs if row["split"] == split],
    } for split in SPLITS}
    leakage_checks: dict[str, dict] = {}
    for field, collection in (("job_ids", "jds"), ("jd_source_hashes", "jds"), ("candidate_ids", "candidates"), ("candidate_source_groups", "candidates")):
        key = {"job_ids": "job_id", "jd_source_hashes": "clean_description_sha256", "candidate_ids": "candidate_id", "candidate_source_groups": "source_jd_group_id"}[field]
        sets = {split: {row[key] for row in by_split[split][collection]} for split in SPLITS}
        overlaps = {f"{a}_{b}": len(sets[a] & sets[b]) for i, a in enumerate(SPLITS) for b in SPLITS[i + 1:]}
        leakage_checks[field] = {"overlaps": overlaps, "passed": not any(overlaps.values())}
    label_conflicts: dict[tuple[str, str], set[str]] = defaultdict(set)
    policy_warnings = []
    for pair in pairs:
        label_conflicts[(pair["job_id"], pair["candidate_id"])].add(pair["label"])
        expected, _ = label_from_features(pair["label_basis"]["features"])
        if expected != pair["label"]:
            policy_warnings.append(pair["pair_id"])
    normalized_resumes = []
    for resume in resumes:
        text = re.sub(r"cand-s4a-[a-z]+-\d+-\d+", "candidate-id", resume["text"].lower())
        normalized_resumes.append(sha256_text(text))
    semantic_keys = [(
        row["job_family"], row["archetype"], row["seniority"], row["years_experience"],
        tuple(row["skills"]), row["education"], tuple((e["role"], e["years"], e["achievement"]) for e in row["employment"]),
    ) for row in candidates]
    label_counts = Counter(row["label"] for row in pairs)
    positive_count = label_counts["positive"]
    yoe_unknown = [row for row in profiles if row["yoe"]["minimum"] is None]
    family_unknown = {family: {
        "unknown": sum(row["yoe"]["minimum"] is None for row in profiles if row["job_family"] == family),
        "total": sum(row["job_family"] == family for row in profiles),
    } for family in sorted({row["job_family"] for row in profiles})}
    layout_counts = Counter(row["layout"] for row in resumes)
    jd_by_id = {row["job_id"]: row for row in jds}
    resume_by_candidate = {row["candidate_id"]: row for row in resumes}
    evidence_failures = []
    hash_failures = []
    for profile in profiles:
        source = jd_by_id[profile["job_id"]]
        if profile["provenance"]["source_sha256"] != source["clean_description_sha256"]:
            hash_failures.append(profile["job_id"])
        for evidence in profile["evidence_spans"]:
            if source["description_clean"][evidence["start_char"]:evidence["end_char"]] != evidence["quote"]:
                evidence_failures.append({"job_id": profile["job_id"], "evidence_id": evidence["id"]})
    resume_fact_failures = []
    for candidate in candidates:
        text = resume_by_candidate[candidate["candidate_id"]]["text"].lower()
        required_facts = [
            candidate["candidate_id"], candidate["seniority"], candidate["job_family"].replace("_", " "),
            str(candidate["years_experience"]), candidate["education"], *candidate["skills"],
            *(item["role"] for item in candidate["employment"]),
            *(item["achievement"] for item in candidate["employment"]),
        ]
        missing = [fact for fact in required_facts if str(fact).lower() not in text]
        if missing:
            resume_fact_failures.append({"candidate_id": candidate["candidate_id"], "missing": missing})
    normalized_titles = [re.sub(r"[^a-z0-9]+", " ", row["title"].lower()).strip() for row in jds]
    company_counts = Counter(row["company_id"] for row in jds)
    jd_degree = Counter(row["job_id"] for row in pairs)
    candidate_degree = Counter(row["candidate_id"] for row in pairs)
    fallback_profiles = [row["job_id"] for row in profiles if not row["required_skills"]]
    split_distributions = {split: {
        "counts": {"jds": len(by_split[split]["jds"]), "candidates": len(by_split[split]["candidates"]), "pairs": len(by_split[split]["pairs"])},
        "labels": dict(Counter(row["label"] for row in by_split[split]["pairs"])),
        "yoe_unknown": sum(row["yoe"]["minimum"] is None for row in profiles if row["split"] == split),
        "yoe_total": sum(row["split"] == split for row in profiles),
    } for split in SPLITS}
    audit = {
        "counts": {"jds": len(jds), "candidates": len(candidates), "resumes": len(resumes), "pairs": len(pairs), "unique_pair_tuples": len({(p['job_id'], p['candidate_id']) for p in pairs})},
        "schema_and_traceability": {
            "runtime_json_schema_validated": {"jd_profiles": len(profiles), "candidate_profiles": len(candidates), "pairs": len(pairs)},
            "source_hash_failures": hash_failures,
            "evidence_offset_failures": evidence_failures,
            "resume_profile_fact_failures": resume_fact_failures,
            "fallback_required_skill_profiles": fallback_profiles,
        },
        "split_unit": "JD clean-description SHA-256 group; candidates inherit their source JD group; pairs are generated only inside a preassigned split",
        "split_distributions": split_distributions,
        "leakage": {"checks": leakage_checks, "all_passed": all(item["passed"] for item in leakage_checks.values())},
        "duplicates": {
            "exact_jd_descriptions": len(jds) - len({row["clean_description_sha256"] for row in jds}),
            "jd_lexical_near_duplicate_pairs_at_0_80": _near_duplicate_count([row["description_clean"] for row in jds]),
            "exact_candidate_profiles": len(semantic_keys) - len(set(semantic_keys)),
            "semantic_duplicate_candidate_profiles": len(semantic_keys) - len(set(semantic_keys)),
            "exact_resumes": len(resumes) - len({sha256_text(row["text"].lower()) for row in resumes}),
            "normalized_duplicate_resumes": len(normalized_resumes) - len(set(normalized_resumes)),
        },
        "resume_diversity": {"layouts": dict(layout_counts), "largest_layout_rate": round(max(layout_counts.values(), default=0) / max(1, len(resumes)), 4)},
        "jd_diversity": {
            "family_count": len({row["job_family"] for row in jds}),
            "unique_normalized_titles": len(set(normalized_titles)),
            "unique_normalized_title_rate": round(len(set(normalized_titles)) / max(1, len(jds)), 4),
            "max_jds_per_company": max(company_counts.values(), default=0),
            "max_company_rate": round(max(company_counts.values(), default=0) / max(1, len(jds)), 4),
        },
        "candidate_diversity": {
            "archetypes": dict(Counter(row["archetype"] for row in candidates)),
            "employment_record_counts": dict(Counter(len(row["employment"]) for row in candidates)),
            "skill_set_count": len({tuple(row["skills"]) for row in candidates}),
        },
        "labels": {
            "overall": dict(label_counts),
            "overall_rates": {label: round(value / len(pairs), 4) for label, value in label_counts.items()},
            "by_family": {family: dict(Counter(p["label"] for p in pairs if next(j for j in profiles if j["job_id"] == p["job_id"])["job_family"] == family)) for family in sorted({p["job_family"] for p in profiles})},
            "policy_recomputation_warnings": policy_warnings,
            "conflicting_pair_tuples": sum(len(labels) > 1 for labels in label_conflicts.values()),
            "unknown_neutral_positives": sum(p["unknown_neutral_positive"] for p in pairs),
            "unknown_neutral_positive_rate": round(sum(p["unknown_neutral_positive"] for p in pairs) / max(1, positive_count), 4),
        },
        "yoe_missingness": {
            "unknown": len(yoe_unknown), "total": len(profiles), "rate": round(len(yoe_unknown) / max(1, len(profiles)), 4),
            "by_family": family_unknown,
            "source_analysis": "YOE remains unknown only when the cleaned source JD has no extraction evidence. Selection preferred explicit evidence; generation did not impute JD YOE.",
        },
        "privacy_and_rights": {
            "contact_details_remaining": sum(bool(
                re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", row["description_clean"], re.I)
                or re.search(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)", row["description_clean"])
            ) for row in jds),
            "source_license_status": "unverified; JD source and derived text remain private",
            "public_release_approved": False,
        },
        "entity_degree": {
            "jd_min_median_max": [min(jd_degree.values()), sorted(jd_degree.values())[len(jd_degree) // 2], max(jd_degree.values())],
            "candidate_min_median_max": [min(candidate_degree.values()), sorted(candidate_degree.values())[len(candidate_degree) // 2], max(candidate_degree.values())],
        },
    }
    gates = {
        "exact_counts": audit["counts"] == {"jds": 50, "candidates": 125, "resumes": 125, "pairs": 1000, "unique_pair_tuples": 1000},
        "zero_group_leakage": audit["leakage"]["all_passed"],
        "zero_label_conflicts": audit["labels"]["conflicting_pair_tuples"] == 0 and not policy_warnings,
        "schema_traceability_and_fact_consistency_100pct": not hash_failures and not evidence_failures and not resume_fact_failures,
        "zero_exact_duplicates": all(audit["duplicates"][key] == 0 for key in ("exact_jd_descriptions", "exact_candidate_profiles", "exact_resumes")),
        "jd_near_duplicate_pairs_lte_2pct": audit["duplicates"]["jd_lexical_near_duplicate_pairs_at_0_80"] / max(1, len(jds) * (len(jds) - 1) / 2) <= 0.02,
        "jd_title_uniqueness_gte_85pct": audit["jd_diversity"]["unique_normalized_title_rate"] >= 0.85,
        "all_six_families": audit["jd_diversity"]["family_count"] == 6,
        "company_rate_lte_5pct": audit["jd_diversity"]["max_company_rate"] <= 0.05,
        "candidate_semantic_duplicate_rate_lte_5pct": audit["duplicates"]["semantic_duplicate_candidate_profiles"] / max(1, len(candidates)) <= 0.05,
        "normalized_resume_duplicate_rate_lte_5pct": audit["duplicates"]["normalized_duplicate_resumes"] / max(1, len(resumes)) <= 0.05,
        "largest_resume_layout_lte_50pct": audit["resume_diversity"]["largest_layout_rate"] <= 0.50,
        "yoe_unknown_lte_40pct": audit["yoe_missingness"]["rate"] <= 0.40,
        "yoe_unknown_lte_50pct_each_family": all(row["unknown"] / max(1, row["total"]) <= 0.50 for row in family_unknown.values()),
        "yoe_unknown_lte_50pct_each_split": all(row["yoe_unknown"] / max(1, row["yoe_total"]) <= 0.50 for row in split_distributions.values()),
        "unknown_neutral_positives_lte_30pct": audit["labels"]["unknown_neutral_positive_rate"] <= 0.30,
        "all_three_labels_each_split": all(set(row["labels"]) == {"positive", "partial", "hard_negative"} for row in split_distributions.values()),
        "label_distribution_in_range": 0.12 <= label_counts["positive"] / len(pairs) <= 0.25 and 0.25 <= label_counts["partial"] / len(pairs) <= 0.40 and 0.35 <= label_counts["hard_negative"] / len(pairs) <= 0.60,
        "contact_details_zero": audit["privacy_and_rights"]["contact_details_remaining"] == 0,
    }
    gates["automated_gates_passed"] = all(gates.values())
    audit["quality_gates"] = gates
    audit["manual_review"] = {"required_pairs": 60, "completed_pairs": 0, "status": "pending independent human review"}
    audit["recommendation"] = "NO-GO for 10,000 until the 60-pair human review is completed and any failed automated gate is resolved" if not gates["automated_gates_passed"] else "CONDITIONAL GO after the pending 60-pair human review; do not scale before sign-off"
    return audit
