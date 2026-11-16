/*
All views below describe the 1,000 sampled synthetic/rule-derived pairs.
They are not measures of recruiter decisions, interviews, offers, or hires.
*/

CREATE VIEW v_overall_kpis AS
SELECT
    COUNT(*) AS pair_count,
    COUNT(DISTINCT job_id) AS paired_job_count,
    COUNT(DISTINCT candidate_id) AS paired_candidate_count,
    ROUND(AVG(skill_coverage), 4) AS avg_required_skill_coverage,
    ROUND(AVG(CASE WHEN yoe_gap IS NOT NULL THEN yoe_gap END), 4) AS avg_yoe_gap,
    ROUND(AVG(CASE WHEN rule_label = 'positive' THEN 1.0 ELSE 0.0 END), 4) AS positive_pair_rate,
    ROUND(AVG(CASE WHEN rule_label IN ('positive', 'partial') THEN 1.0 ELSE 0.0 END), 4) AS viable_pair_rate,
    'synthetic_rule_derived' AS metric_provenance
FROM fact_pair;

/* Denominator: all sampled pairs within split + job_family. */
CREATE VIEW v_match_distribution AS
SELECT
    p.split,
    j.job_family,
    p.rule_label,
    COUNT(*) AS pair_count,
    SUM(COUNT(*)) OVER (PARTITION BY p.split, j.job_family) AS segment_pair_denominator,
    ROUND(1.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY p.split, j.job_family), 4) AS pair_rate,
    'synthetic_rule_derived' AS metric_provenance
FROM fact_pair p
JOIN dim_job j USING (job_id)
GROUP BY p.split, j.job_family, p.rule_label;

/* Denominator: all sampled pairs in the bucketed population. */
CREATE VIEW v_required_skill_coverage_distribution AS
SELECT
    CASE
        WHEN skill_coverage < 0.25 THEN '0-<25%'
        WHEN skill_coverage < 0.50 THEN '25-<50%'
        WHEN skill_coverage < 0.75 THEN '50-<75%'
        ELSE '75-100%'
    END AS coverage_bucket,
    COUNT(*) AS pair_count,
    (SELECT COUNT(*) FROM fact_pair) AS pair_denominator,
    ROUND(1.0 * COUNT(*) / (SELECT COUNT(*) FROM fact_pair), 4) AS pair_rate,
    ROUND(AVG(skill_coverage), 4) AS avg_coverage_in_bucket,
    'synthetic_rule_derived' AS metric_provenance
FROM fact_pair
GROUP BY coverage_bucket;

/* Denominator excludes pairs whose JD minimum YOE is unknown. */
CREATE VIEW v_experience_gap_distribution AS
SELECT
    CASE
        WHEN yoe_gap IS NULL THEN 'unknown'
        WHEN yoe_gap <= -3 THEN '<=-3 years'
        WHEN yoe_gap < 0 THEN '-2 to -1 years'
        WHEN yoe_gap = 0 THEN 'meets exactly'
        ELSE 'exceeds minimum'
    END AS experience_gap_bucket,
    COUNT(*) AS pair_count,
    CASE WHEN yoe_gap IS NULL
         THEN (SELECT COUNT(*) FROM fact_pair)
         ELSE (SELECT COUNT(*) FROM fact_pair WHERE yoe_gap IS NOT NULL)
    END AS denominator,
    ROUND(1.0 * COUNT(*) /
        CASE WHEN yoe_gap IS NULL
             THEN (SELECT COUNT(*) FROM fact_pair)
             ELSE (SELECT COUNT(*) FROM fact_pair WHERE yoe_gap IS NOT NULL)
        END, 4) AS pair_rate,
    'synthetic_rule_derived' AS metric_provenance
FROM fact_pair
GROUP BY experience_gap_bucket;

/* Per-JD denominators are the distinct candidates actually sampled for that JD. */
CREATE VIEW v_jd_coverage_detail AS
SELECT
    j.job_id,
    j.job_family,
    j.split,
    COUNT(DISTINCT p.candidate_id) AS sampled_candidate_denominator,
    COUNT(DISTINCT CASE WHEN p.rule_label = 'positive' THEN p.candidate_id END) AS positive_candidate_count,
    COUNT(DISTINCT CASE WHEN p.rule_label IN ('positive', 'partial') THEN p.candidate_id END) AS viable_candidate_count,
    ROUND(1.0 * COUNT(DISTINCT CASE WHEN p.rule_label = 'positive' THEN p.candidate_id END)
          / NULLIF(COUNT(DISTINCT p.candidate_id), 0), 4) AS positive_candidate_rate,
    ROUND(1.0 * COUNT(DISTINCT CASE WHEN p.rule_label IN ('positive', 'partial') THEN p.candidate_id END)
          / NULLIF(COUNT(DISTINCT p.candidate_id), 0), 4) AS viable_candidate_rate,
    MAX(CASE WHEN p.rule_label = 'positive' THEN 1 ELSE 0 END) AS has_positive_candidate,
    MAX(CASE WHEN p.rule_label IN ('positive', 'partial') THEN 1 ELSE 0 END) AS has_viable_candidate,
    'synthetic_rule_derived' AS metric_provenance
FROM dim_job j
LEFT JOIN fact_pair p USING (job_id)
GROUP BY j.job_id, j.job_family, j.split;

/* Per-candidate denominators are the distinct JDs actually sampled for that candidate. */
CREATE VIEW v_candidate_coverage_detail AS
SELECT
    c.candidate_id,
    c.job_family AS candidate_job_family,
    c.archetype,
    c.split,
    COUNT(DISTINCT p.job_id) AS sampled_jd_denominator,
    COUNT(DISTINCT CASE WHEN p.rule_label = 'positive' THEN p.job_id END) AS positive_jd_count,
    COUNT(DISTINCT CASE WHEN p.rule_label IN ('positive', 'partial') THEN p.job_id END) AS viable_jd_count,
    ROUND(1.0 * COUNT(DISTINCT CASE WHEN p.rule_label = 'positive' THEN p.job_id END)
          / NULLIF(COUNT(DISTINCT p.job_id), 0), 4) AS positive_jd_rate,
    ROUND(1.0 * COUNT(DISTINCT CASE WHEN p.rule_label IN ('positive', 'partial') THEN p.job_id END)
          / NULLIF(COUNT(DISTINCT p.job_id), 0), 4) AS viable_jd_rate,
    'synthetic_rule_derived' AS metric_provenance
FROM dim_candidate c
LEFT JOIN fact_pair p USING (candidate_id)
GROUP BY c.candidate_id, c.job_family, c.archetype, c.split;

/*
Denominator: sampled pairs linked to JDs requiring the skill.
Missingness follows the pair generator's auditable exact/equivalence policy.
*/
CREATE VIEW v_skill_bottlenecks AS
SELECT
    s.skill_name,
    COUNT(DISTINCT p.job_id) AS requiring_jd_count,
    COUNT(DISTINCT p.pair_id) AS relevant_pair_denominator,
    COUNT(DISTINCT g.pair_id) AS missing_pair_count,
    ROUND(1.0 * COUNT(DISTINCT g.pair_id) / NULLIF(COUNT(DISTINCT p.pair_id), 0), 4) AS missing_pair_rate,
    'synthetic_rule_derived' AS metric_provenance
FROM dim_skill s
JOIN bridge_job_skill js ON js.skill_id = s.skill_id AND js.skill_type = 'required'
JOIN fact_pair p ON p.job_id = js.job_id
LEFT JOIN bridge_pair_missing_required_skill g
    ON g.pair_id = p.pair_id AND g.skill_id = s.skill_id
GROUP BY s.skill_id, s.skill_name;

/* Missing count / entity count for each field; zero is still explicitly reported. */
CREATE VIEW v_missingness AS
SELECT 'job' AS entity, 'minimum_yoe' AS field,
       SUM(CASE WHEN minimum_yoe IS NULL THEN 1 ELSE 0 END) AS missing_count,
       COUNT(*) AS entity_denominator,
       ROUND(AVG(CASE WHEN minimum_yoe IS NULL THEN 1.0 ELSE 0.0 END), 4) AS missing_rate
FROM dim_job
UNION ALL
SELECT 'job', 'education_minimum_level',
       SUM(CASE WHEN education_minimum_level IS NULL OR education_minimum_level = 'unknown' THEN 1 ELSE 0 END),
       COUNT(*), ROUND(AVG(CASE WHEN education_minimum_level IS NULL OR education_minimum_level = 'unknown' THEN 1.0 ELSE 0.0 END), 4)
FROM dim_job
UNION ALL
SELECT 'candidate', 'years_experience',
       SUM(CASE WHEN years_experience IS NULL THEN 1 ELSE 0 END),
       COUNT(*), ROUND(AVG(CASE WHEN years_experience IS NULL THEN 1.0 ELSE 0.0 END), 4)
FROM dim_candidate
UNION ALL
SELECT 'pair', 'yoe_gap',
       SUM(CASE WHEN yoe_gap IS NULL THEN 1 ELSE 0 END),
       COUNT(*), ROUND(AVG(CASE WHEN yoe_gap IS NULL THEN 1.0 ELSE 0.0 END), 4)
FROM fact_pair;

/* Denominator: all sampled pairs in each JD-family x candidate-archetype segment. */
CREATE VIEW v_segment_analysis AS
SELECT
    j.job_family AS jd_job_family,
    c.archetype AS candidate_archetype,
    COUNT(*) AS pair_denominator,
    ROUND(AVG(p.skill_coverage), 4) AS avg_skill_coverage,
    ROUND(AVG(CASE WHEN p.yoe_gap IS NOT NULL THEN p.yoe_gap END), 4) AS avg_yoe_gap,
    ROUND(AVG(CASE WHEN p.rule_label = 'positive' THEN 1.0 ELSE 0.0 END), 4) AS positive_pair_rate,
    ROUND(AVG(CASE WHEN p.rule_label IN ('positive', 'partial') THEN 1.0 ELSE 0.0 END), 4) AS viable_pair_rate,
    'synthetic_rule_derived' AS metric_provenance
FROM fact_pair p
JOIN dim_job j USING (job_id)
JOIN dim_candidate c USING (candidate_id)
GROUP BY j.job_family, c.archetype;
