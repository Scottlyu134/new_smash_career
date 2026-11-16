PRAGMA foreign_keys = ON;

CREATE TABLE analytics_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE dim_job (
    job_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    job_family TEXT NOT NULL,
    split TEXT NOT NULL CHECK (split IN ('train', 'validation', 'test')),
    source_jd_group_id TEXT NOT NULL,
    seniority TEXT NOT NULL,
    minimum_yoe INTEGER,
    maximum_yoe INTEGER,
    yoe_status TEXT NOT NULL,
    education_minimum_level TEXT,
    education_status TEXT,
    required_skill_count INTEGER NOT NULL CHECK (required_skill_count >= 0),
    preferred_skill_count INTEGER NOT NULL CHECK (preferred_skill_count >= 0),
    is_synthetic INTEGER NOT NULL DEFAULT 0 CHECK (is_synthetic IN (0, 1)),
    source_license_status TEXT NOT NULL
);

CREATE TABLE dim_candidate (
    candidate_id TEXT PRIMARY KEY,
    job_family TEXT NOT NULL,
    split TEXT NOT NULL CHECK (split IN ('train', 'validation', 'test')),
    source_job_id TEXT NOT NULL,
    source_jd_group_id TEXT NOT NULL,
    archetype TEXT NOT NULL,
    seniority TEXT NOT NULL,
    years_experience INTEGER CHECK (years_experience >= 0),
    education_level TEXT,
    is_synthetic INTEGER NOT NULL DEFAULT 1 CHECK (is_synthetic IN (0, 1))
);

CREATE TABLE dim_skill (
    skill_id INTEGER PRIMARY KEY,
    skill_name TEXT NOT NULL UNIQUE
);

CREATE TABLE bridge_job_skill (
    job_id TEXT NOT NULL REFERENCES dim_job(job_id),
    skill_id INTEGER NOT NULL REFERENCES dim_skill(skill_id),
    skill_type TEXT NOT NULL CHECK (skill_type IN ('required', 'preferred')),
    PRIMARY KEY (job_id, skill_id, skill_type)
);

CREATE TABLE bridge_candidate_skill (
    candidate_id TEXT NOT NULL REFERENCES dim_candidate(candidate_id),
    skill_id INTEGER NOT NULL REFERENCES dim_skill(skill_id),
    PRIMARY KEY (candidate_id, skill_id)
);

CREATE TABLE fact_pair (
    pair_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES dim_job(job_id),
    candidate_id TEXT NOT NULL REFERENCES dim_candidate(candidate_id),
    split TEXT NOT NULL CHECK (split IN ('train', 'validation', 'test')),
    rule_label TEXT NOT NULL CHECK (rule_label IN ('positive', 'partial', 'hard_negative')),
    skill_coverage REAL NOT NULL CHECK (skill_coverage BETWEEN 0.0 AND 1.0),
    required_skill_coverage REAL NOT NULL CHECK (required_skill_coverage BETWEEN 0.0 AND 1.0),
    yoe_gap INTEGER,
    seniority_gap INTEGER,
    exact_required_match_count INTEGER NOT NULL CHECK (exact_required_match_count >= 0),
    equivalent_required_match_count INTEGER NOT NULL CHECK (equivalent_required_match_count >= 0),
    missing_required_skill_count INTEGER NOT NULL CHECK (missing_required_skill_count >= 0),
    unknown_neutral_positive INTEGER NOT NULL CHECK (unknown_neutral_positive IN (0, 1)),
    label_policy TEXT NOT NULL,
    metric_provenance TEXT NOT NULL CHECK (metric_provenance = 'synthetic_rule_derived'),
    UNIQUE (job_id, candidate_id)
);

CREATE TABLE bridge_pair_missing_required_skill (
    pair_id TEXT NOT NULL REFERENCES fact_pair(pair_id),
    skill_id INTEGER NOT NULL REFERENCES dim_skill(skill_id),
    PRIMARY KEY (pair_id, skill_id)
);

CREATE INDEX idx_pair_job ON fact_pair(job_id);
CREATE INDEX idx_pair_candidate ON fact_pair(candidate_id);
CREATE INDEX idx_pair_segment ON fact_pair(split, rule_label);
CREATE INDEX idx_job_family ON dim_job(job_family);
CREATE INDEX idx_job_skill_skill ON bridge_job_skill(skill_id, skill_type);
CREATE INDEX idx_pair_gap_skill ON bridge_pair_missing_required_skill(skill_id);
