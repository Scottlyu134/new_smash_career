-- 1. Overall prototype KPIs. Denominator = all 1,000 sampled pairs.
SELECT * FROM v_overall_kpis;

-- 2. Match-label distribution. Denominator = pairs in each split x JD-family segment.
SELECT * FROM v_match_distribution
ORDER BY split, job_family, rule_label;

-- 3. Required-skill coverage distribution. Denominator = all sampled pairs.
SELECT * FROM v_required_skill_coverage_distribution
ORDER BY coverage_bucket;

-- 4. Experience gaps. Known-gap rates exclude unknown minimum-YOE pairs.
SELECT * FROM v_experience_gap_distribution
ORDER BY experience_gap_bucket;

-- 5. JD coverage. Portfolio-level denominator = all pilot JDs, including zero-pair JDs.
SELECT
    COUNT(*) AS jd_denominator,
    SUM(has_positive_candidate) AS jds_with_positive_candidate,
    ROUND(AVG(has_positive_candidate * 1.0), 4) AS jd_positive_coverage_rate,
    SUM(has_viable_candidate) AS jds_with_viable_candidate,
    ROUND(AVG(has_viable_candidate * 1.0), 4) AS jd_viable_coverage_rate
FROM v_jd_coverage_detail;

-- 6. Candidate coverage. Portfolio-level denominator = all pilot candidates.
SELECT
    COUNT(*) AS candidate_denominator,
    SUM(CASE WHEN positive_jd_count > 0 THEN 1 ELSE 0 END) AS candidates_with_positive_jd,
    ROUND(AVG(CASE WHEN positive_jd_count > 0 THEN 1.0 ELSE 0.0 END), 4) AS candidate_positive_coverage_rate,
    SUM(CASE WHEN viable_jd_count > 0 THEN 1 ELSE 0 END) AS candidates_with_viable_jd,
    ROUND(AVG(CASE WHEN viable_jd_count > 0 THEN 1.0 ELSE 0.0 END), 4) AS candidate_viable_coverage_rate
FROM v_candidate_coverage_detail;

-- 7. Skill bottlenecks. Denominator per skill = sampled pairs for JDs requiring that skill.
SELECT * FROM v_skill_bottlenecks
WHERE relevant_pair_denominator >= 10
ORDER BY missing_pair_rate DESC, relevant_pair_denominator DESC, skill_name;

-- 8. Field missingness. Denominator = entities in the stated table.
SELECT * FROM v_missingness ORDER BY entity, field;

-- 9. Segment diagnostics. Denominator = pairs in JD-family x candidate-archetype segment.
SELECT * FROM v_segment_analysis
ORDER BY viable_pair_rate, pair_denominator DESC;
