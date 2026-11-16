from __future__ import annotations

from datetime import datetime, timezone


SENIORITY_RANK = {"intern": 0, "entry": 1, "unknown": 2, "mid": 2, "senior": 3, "lead": 4, "executive": 5}
FALLBACK_SKILLS = {
    "data_analyst": ["sql", "excel", "tableau", "statistics"],
    "data_scientist": ["python", "sql", "machine learning", "statistics"],
    "data_engineer": ["python", "sql", "etl", "spark"],
    "machine_learning": ["python", "machine learning", "pytorch", "docker"],
    "ai_llm": ["python", "llm", "nlp", "pytorch"],
    "software_engineer": ["python", "git", "docker", "aws"]
}

# Directional, conservative equivalence credit. Exact matches score 1.0;
# related skills score 0.5 so equivalence alone cannot create a positive pair.
SKILL_EQUIVALENCE = {
    "aws": {"azure", "gcp"}, "azure": {"aws", "gcp"}, "gcp": {"aws", "azure"},
    "pytorch": {"tensorflow"}, "tensorflow": {"pytorch"},
    "tableau": {"power bi", "data visualization"},
    "power bi": {"tableau", "data visualization"},
    "data visualization": {"tableau", "power bi"},
    "machine learning": {"deep learning"}, "deep learning": {"machine learning"},
    "nlp": {"llm"}, "llm": {"nlp"},
    "javascript": {"react"}, "react": {"javascript"},
    "spark": {"etl"}, "etl": {"spark"},
}


def _skill_names(profile: dict) -> list[str]:
    required = [x["normalized"] for x in profile["required_skills"]] or FALLBACK_SKILLS[profile["job_family"]]
    preferred = [x["normalized"] for x in profile["preferred_skills"]]
    return list(dict.fromkeys(required + preferred))


def _seniority_for(archetype: str, target: str) -> str:
    if archetype == "junior": return "entry"
    if archetype == "mismatch": return "entry"
    if target == "unknown": return "mid"
    return target


def generate_candidates(profiles: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    archetypes = ["strong", "adjacent", "junior", "gap", "mismatch"]
    for profile in profiles:
        base = _skill_names(profile)
        required = [x["normalized"] for x in profile["required_skills"]] or FALLBACK_SKILLS[profile["job_family"]]
        target_yoe = profile["yoe"]["minimum"] if profile["yoe"]["minimum"] is not None else 3
        for index, archetype in enumerate(archetypes):
            if archetype == "strong": skills, years = base, max(target_yoe, 4)
            elif archetype == "adjacent": skills, years = base[:max(1, len(base)-1)], max(target_yoe, 3)
            elif archetype == "junior": skills, years = required[:max(1, len(required)//2)], max(0, target_yoe-2)
            elif archetype == "gap": skills, years = required[:1], max(target_yoe, 3)
            else: skills, years = ["customer service", "scheduling"], 1
            candidate_id = f"cand-{profile['job_id']}-{index+1}"
            education = profile["education"]["minimum_level"]
            if education == "unknown": education = "bachelor"
            scenario = f"Delivered {profile['title']} scenario {profile['job_id'][-4:]}-{index+1} using {', '.join(skills)}."
            candidates.append({
                "schema_version": "1.0", "candidate_id": candidate_id, "source_job_id": profile["job_id"], "archetype": archetype,
                "job_family": profile["job_family"], "seniority": _seniority_for(archetype, profile["seniority"]), "years_experience": years,
                "skills": list(dict.fromkeys(skills)), "education": education,
                "employment": [{"role": f"{profile['title']} — synthetic {archetype} variant", "years": years, "skills_used": list(dict.fromkeys(skills)), "achievement": scenario}],
                "provenance": {"generator": "deterministic-profile-v1", "source_jd_profile": profile["job_id"], "archetype_rule": archetype, "created_at": datetime.now(timezone.utc).isoformat()}
            })
    return candidates


def render_resume(candidate: dict) -> dict:
    skills = ", ".join(candidate["skills"])
    resume = (
        f"CANDIDATE {candidate['candidate_id']}\n\n"
        f"SUMMARY\n{candidate['seniority'].title()} {candidate['job_family'].replace('_', ' ')} professional with {candidate['years_experience']} years of experience.\n\n"
        f"SKILLS\n{skills}\n\n"
        f"EXPERIENCE\n{candidate['employment'][0]['role']} — {candidate['years_experience']} years\n"
        f"{candidate['employment'][0]['achievement']}\n\n"
        f"EDUCATION\n{candidate['education'].title()} level education."
    )
    return {"resume_id": "resume-" + candidate["candidate_id"], "candidate_id": candidate["candidate_id"], "text": resume, "provenance": {"renderer": "template-v1", "profile_id": candidate["candidate_id"]}}


def _features(candidate: dict, jd: dict) -> dict:
    required = {x["normalized"] for x in jd["required_skills"]}
    if not required: required = set(FALLBACK_SKILLS[jd["job_family"]])
    skills = set(candidate["skills"])
    exact = required & skills
    equivalent: list[dict] = []
    credit = float(len(exact))
    available_candidate_skills = set(skills - exact)
    for required_skill in sorted(required - exact):
        matches = sorted(SKILL_EQUIVALENCE.get(required_skill, set()) & available_candidate_skills)
        if matches:
            equivalent.append({"required_skill": required_skill, "candidate_skill": matches[0], "credit": 0.5})
            available_candidate_skills.remove(matches[0])
            credit += 0.5
    coverage = credit / len(required) if required else 0.0
    target_yoe = jd["yoe"]["minimum"]
    yoe_gap = None if target_yoe is None else candidate["years_experience"] - target_yoe
    seniority_gap = None if "unknown" in {candidate["seniority"], jd["seniority"]} else SENIORITY_RANK[candidate["seniority"]] - SENIORITY_RANK[jd["seniority"]]
    covered_required = exact | {item["required_skill"] for item in equivalent}
    return {
        "skill_coverage": round(coverage, 4),
        "required_skill_coverage": round(coverage, 4),
        "exact_matching_required_skills": sorted(exact),
        "equivalent_skill_matches": equivalent,
        "missing_required_skills": sorted(required - covered_required),
        "yoe_gap": yoe_gap,
        "seniority_gap": seniority_gap,
    }


def label_from_features(features: dict) -> tuple[str, str]:
    coverage = features["skill_coverage"]
    yoe_gap = features["yoe_gap"]
    seniority_gap = features["seniority_gap"]
    yoe_meets = yoe_gap is None or yoe_gap >= 0
    seniority_meets = seniority_gap is None or seniority_gap >= 0
    if coverage >= 0.75 and yoe_meets and seniority_meets:
        return "positive", "skill_coverage>=0.75 and known YOE/seniority requirements are met"
    severe_yoe_gap = yoe_gap is not None and yoe_gap <= -3
    severe_seniority_gap = seniority_gap is not None and seniority_gap <= -2
    if coverage < 0.25 or (coverage < 0.5 and (severe_yoe_gap or severe_seniority_gap)):
        reasons = []
        if coverage < 0.25: reasons.append("skill_coverage<0.25")
        if severe_yoe_gap: reasons.append("yoe_gap<=-3")
        if severe_seniority_gap: reasons.append("seniority_gap<=-2")
        return "hard_negative", " and ".join(reasons)
    reasons = []
    if coverage < 0.75: reasons.append("skill_coverage<0.75")
    if not yoe_meets: reasons.append("YOE below requirement")
    if not seniority_meets: reasons.append("seniority below requirement")
    return "partial", "; ".join(reasons) or "mixed evidence"


def generate_pairs(profiles: list[dict], candidates: list[dict]) -> list[dict]:
    by_jd = {p["job_id"]: p for p in profiles}
    grouped = {p["job_id"]: [c for c in candidates if c["source_job_id"] == p["job_id"]] for p in profiles}
    pairs: list[dict] = []
    for i, jd in enumerate(profiles):
        own = grouped[jd["job_id"]]
        for c in own:
            pairs.append(_pair(c, jd, f"same-JD {c['archetype']} candidate source"))
        same_family = [p for p in profiles if p["job_family"] == jd["job_family"] and p["job_id"] != jd["job_id"]]
        partial_source = same_family[i % len(same_family)] if same_family else profiles[(i + 1) % len(profiles)]
        for c in grouped[partial_source["job_id"]]:
            pairs.append(_pair(c, jd, "cross-JD same/adjacent-family candidate source"))
        other = next((p for p in profiles[i+1:] + profiles[:i+1] if p["job_family"] != jd["job_family"]), profiles[(i + 1) % len(profiles)])
        for c in grouped[other["job_id"]]:
            rule = "cross-family contrast selected by provenance rule, not embedding similarity" if other["job_family"] != jd["job_family"] else "same-family adversarial contrast fallback for single-family test run"
            pairs.append(_pair(c, jd, rule))
    return pairs


def _pair(candidate: dict, jd: dict, source_rule: str) -> dict:
    features = _features(candidate, jd)
    label, label_reason = label_from_features(features)
    return {
        "pair_id": f"pair-{jd['job_id']}-{candidate['candidate_id']}", "job_id": jd["job_id"], "candidate_id": candidate["candidate_id"],
        "label": label, "label_scope": "synthetic curriculum label; not a real hiring judgment or evaluation ground truth",
        "skill_coverage": features["skill_coverage"], "yoe_gap": features["yoe_gap"], "seniority_gap": features["seniority_gap"],
        "label_reason": label_reason,
        "label_basis": {"policy": "pair-label-policy-v2", "source_rule": source_rule, "features": features, "embedding_used": False},
        "provenance": {"pair_generator": "rule-based-v2", "jd_profile_id": jd["job_id"], "candidate_profile_id": candidate["candidate_id"]}
    }

