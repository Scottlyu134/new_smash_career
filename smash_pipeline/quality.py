from __future__ import annotations

from collections import Counter
import re

from .io_utils import sha256_text
from .generation import label_from_features


def quality_report(jds: list[dict], profiles: list[dict], candidates: list[dict], resumes: list[dict], pairs: list[dict], failures: list[dict]) -> dict:
    resume_by_candidate = {r["candidate_id"]: r for r in resumes}
    consistency_failures = []
    for candidate in candidates:
        text = resume_by_candidate[candidate["candidate_id"]]["text"].lower()
        missing = [skill for skill in candidate["skills"] if skill.lower() not in text]
        expected = {
            "candidate_id": candidate["candidate_id"].lower(),
            "seniority": candidate["seniority"].lower(),
            "job_family": candidate["job_family"].replace("_", " ").lower(),
            "years_experience": str(candidate["years_experience"]),
            "education": candidate["education"].lower(),
            "role": candidate["employment"][0]["role"].lower(),
            "achievement": candidate["employment"][0]["achievement"].lower(),
        }
        missing_facts = [name for name, value in expected.items() if value not in text]
        if missing or missing_facts:
            consistency_failures.append({"candidate_id": candidate["candidate_id"], "missing_skills": missing, "missing_facts": missing_facts})
    resume_hashes = [sha256_text(r["text"].lower()) for r in resumes]
    normalized_resume_hashes = []
    for resume in resumes:
        text = resume["text"].lower()
        text = re.sub(r"cand-\d+-\d+", "cand-id", text)
        text = re.sub(r"scenario \d+-\d+", "scenario-id", text)
        normalized_resume_hashes.append(sha256_text(text))
    candidate_keys = [sha256_text(str(sorted((k, v) for k, v in c.items() if k not in {"candidate_id", "source_job_id", "provenance"}))) for c in candidates]
    semantic_candidate_keys = [
        (
            c["job_family"], c["archetype"], c["seniority"], c["years_experience"],
            tuple(sorted(c["skills"])), c["education"],
        )
        for c in candidates
    ]
    duplicate_resume_texts = len(resume_hashes) - len(set(resume_hashes))
    normalized_duplicate_resume_texts = len(normalized_resume_hashes) - len(set(normalized_resume_hashes))
    duplicate_candidate_content = len(candidate_keys) - len(set(candidate_keys))
    semantic_duplicate_candidates = len(semantic_candidate_keys) - len(set(semantic_candidate_keys))
    profile_by_job_id = {p["job_id"]: p for p in profiles}
    jd_by_job_id = {j["job_id"]: j for j in jds}
    hashes_match = all(
        p["provenance"]["source_sha256"] == jd_by_job_id[p["job_id"]]["clean_description_sha256"]
        for p in profiles if p["job_id"] in jd_by_job_id
    ) and len(profile_by_job_id) == len(jd_by_job_id)
    label_warnings = []
    for pair in pairs:
        expected_label, expected_reason = label_from_features(pair["label_basis"]["features"])
        if pair["label"] != expected_label:
            label_warnings.append({"pair_id": pair["pair_id"], "label": pair["label"], "expected_label": expected_label, "expected_reason": expected_reason})
    report = {
        "counts": {"jds": len(jds), "jd_profiles": len(profiles), "candidate_profiles": len(candidates), "resumes": len(resumes), "pairs": len(pairs), "extraction_failures": len(failures)},
        "schema_and_traceability": {
            "profiles_with_evidence": sum(bool(p["evidence_spans"]) for p in profiles),
            "profiles_with_unknown": sum(bool(p["unknown"]) for p in profiles),
            "all_source_hashes_match": hashes_match
        },
        "fact_consistency": {"resume_profile_checks": len(candidates), "failures": consistency_failures, "pass_rate": round(1 - len(consistency_failures) / max(1, len(candidates)), 4)},
        "deduplication": {
            "duplicate_resume_texts": duplicate_resume_texts,
            "normalized_duplicate_resume_texts": normalized_duplicate_resume_texts,
            "duplicate_candidate_content": duplicate_candidate_content,
            "semantic_duplicate_candidate_profiles": semantic_duplicate_candidates,
            "semantic_duplicate_candidate_rate": round(semantic_duplicate_candidates / max(1, len(candidates)), 4),
        },
        "label_sanity": {"warning_count": len(label_warnings), "warnings": label_warnings},
        "resume_diversity": {"template_layouts": 1 if resumes else 0, "template_dominance_rate": 1.0 if resumes else 0.0},
        "distributions": {
            "labels": dict(Counter(p["label"] for p in pairs)),
            "job_families": dict(Counter(p["job_family"] for p in profiles)),
            "candidate_archetypes": dict(Counter(c["archetype"] for c in candidates))
        },
        "coverage": {"job_families": sorted({p["job_family"] for p in profiles}), "family_count": len({p["job_family"] for p in profiles})},
        "failed_samples": failures,
        "limitations": [
            "All labels are synthetic curriculum labels, not observed recruiter decisions or human relevance judgments.",
            "Mock extraction is deterministic and useful for integration testing, but is not an accuracy evaluation of LLM extraction.",
            "Rule-derived labels must not be used as matching ground truth; evaluate separately on expert/human-labeled data.",
            "No embedding similarity is used to assign labels."
        ]
    }
    report["quality_gates"] = {
        "zero_extraction_failures": len(failures) == 0,
        "resume_fact_consistency_100pct": len(consistency_failures) == 0,
        "zero_duplicate_resumes": duplicate_resume_texts == 0,
        "zero_duplicate_candidate_content": duplicate_candidate_content == 0,
        "zero_label_sanity_warnings": len(label_warnings) == 0,
        "all_source_hashes_match": report["schema_and_traceability"]["all_source_hashes_match"],
        "all_labels_present": set(report["distributions"]["labels"]) == {"positive", "partial", "hard_negative"},
        "no_embedding_ground_truth": all(p["label_basis"]["embedding_used"] is False for p in pairs)
    }
    report["quality_gates"]["all_passed"] = all(report["quality_gates"].values())
    return report

