import unittest
import json
from pathlib import Path

from smash_pipeline.cleaning import classify_family, clean_description
from smash_pipeline.extraction import mock_extract, validate_jd_profile
from smash_pipeline.generation import _features, generate_candidates, generate_pairs, label_from_features, render_resume
from smash_pipeline.quality import quality_report
from smash_pipeline.io_utils import sha256_text
from smash_pipeline.schema_validation import validate as validate_json_schema
from smash_pipeline.sprint4a import assign_splits, generate_candidates_exact, generate_pairs_exact, render_resume_v2


class PipelineTest(unittest.TestCase):
    def setUp(self):
        text = "Required: 3+ years of experience with Python and SQL. Bachelor's degree required. Tableau is preferred."
        self.jd = {"job_id": "1", "title": "Senior Data Analyst", "job_family": "data_analyst", "description_clean": text, "clean_description_sha256": sha256_text(text), "source_experience_level": "Mid-Senior level"}

    def test_cleaning_redacts_contact_details(self):
        cleaned = clean_description("Email me at a@example.com or call 415-555-1212. Equal opportunity employer")
        self.assertIn("[EMAIL]", cleaned)
        self.assertIn("[PHONE]", cleaned)
        self.assertNotIn("Equal opportunity", cleaned)

    def test_family_matching_uses_token_boundaries(self):
        families = {"ai_llm": ["llm", " ai "]}
        self.assertIsNone(classify_family("Salesforce Fulfillment Analyst", families))
        self.assertIsNone(classify_family("BOSS Fulfillment Outfitter", families))
        self.assertEqual(classify_family("LLM Engineer", families), "ai_llm")

    def test_traceable_extraction_and_generation(self):
        schema = json.loads((Path(__file__).parents[1] / "schemas/jd_profile.schema.json").read_text())
        profile = mock_extract(self.jd, schema=schema)
        validate_jd_profile(profile, self.jd["description_clean"])
        validate_json_schema(profile, schema)
        self.assertEqual(profile["yoe"]["minimum"], 3)
        self.assertTrue(profile["evidence_spans"])
        candidates = generate_candidates([profile])
        self.assertEqual(len(candidates), 5)
        resume = render_resume(candidates[0])
        self.assertTrue(all(skill in resume["text"].lower() for skill in candidates[0]["skills"]))
        self.assertIn(candidates[0]["employment"][0]["achievement"], resume["text"])

    def test_extraction_avoids_master_word_false_positives(self):
        text = "Build master data management systems and master the pipeline."
        profile = mock_extract({**self.jd, "description_clean": text, "clean_description_sha256": sha256_text(text)})
        self.assertEqual(profile["education"]["status"], "unknown")

    def test_extraction_handles_qualified_years_phrase(self):
        text = "Requires 7+ years of relevant industry or academic experience and 2+ years experience managing a team."
        profile = mock_extract({**self.jd, "description_clean": text, "clean_description_sha256": sha256_text(text)})
        self.assertEqual(profile["yoe"]["minimum"], 7)

    def test_pair_labels_not_embedding_ground_truth(self):
        profile = mock_extract(self.jd)
        jd2 = dict(self.jd, job_id="2", title="Software Engineer", job_family="software_engineer")
        profile2 = mock_extract(jd2)
        candidates = generate_candidates([profile, profile2])
        pairs = generate_pairs([profile, profile2], candidates)
        self.assertEqual(len(pairs), 30)
        self.assertEqual({p["label"] for p in pairs}, {"positive", "partial", "hard_negative"})
        self.assertTrue(all(p["label_basis"]["embedding_used"] is False for p in pairs))
        self.assertTrue(all(p["label_basis"]["policy"] == "pair-label-policy-v2" for p in pairs))
        self.assertTrue(all(all(key in p for key in ("skill_coverage", "yoe_gap", "seniority_gap", "label_reason")) for p in pairs))

    def test_positive_candidates_cover_required_fallback(self):
        profile = mock_extract({**self.jd, "description_clean": "Docker is preferred.", "clean_description_sha256": sha256_text("Docker is preferred.")})
        candidates = generate_candidates([profile])
        pairs = generate_pairs([profile, mock_extract({**self.jd, "job_id": "2"})], candidates + generate_candidates([mock_extract({**self.jd, "job_id": "2"})]))
        own_positive = [p for p in pairs if p["job_id"] == "1" and p["label"] == "positive"]
        self.assertTrue(all(p["label_basis"]["features"]["required_skill_coverage"] >= 0.5 for p in own_positive))

    def test_pair_policy_boundaries(self):
        base = {"skill_coverage": 0.75, "yoe_gap": 0, "seniority_gap": 0}
        self.assertEqual(label_from_features(base)[0], "positive")
        self.assertEqual(label_from_features({**base, "skill_coverage": 0.7499})[0], "partial")
        self.assertEqual(label_from_features({**base, "yoe_gap": -1})[0], "partial")
        self.assertEqual(label_from_features({**base, "seniority_gap": -1})[0], "partial")
        self.assertEqual(label_from_features({**base, "skill_coverage": 0.2499})[0], "hard_negative")
        self.assertEqual(label_from_features({**base, "skill_coverage": 0.49, "yoe_gap": -3})[0], "hard_negative")
        self.assertEqual(label_from_features({**base, "skill_coverage": 0.5, "yoe_gap": -3})[0], "partial")
        self.assertEqual(label_from_features({**base, "yoe_gap": None, "seniority_gap": None})[0], "positive")

    def test_skill_equivalence_is_one_to_one(self):
        jd = mock_extract({**self.jd, "description_clean": "Tableau and data visualization are required.", "clean_description_sha256": sha256_text("Tableau and data visualization are required.")})
        candidate = generate_candidates([jd])[0]
        candidate["skills"] = ["tableau"]
        features = _features(candidate, jd)
        self.assertEqual(features["skill_coverage"], 0.5)
        self.assertEqual(features["equivalent_skill_matches"], [])

    def test_sprint4a_counts_are_independent(self):
        profiles = [mock_extract({**self.jd, "job_id": str(i)}) for i in range(1, 4)]
        for profile in profiles:
            profile["split"] = "train"
            profile["source_jd_group_id"] = "jdg-" + profile["job_id"]
        candidates = generate_candidates_exact(profiles, 7, "train")
        pairs = generate_pairs_exact(profiles, candidates, 21, "train", 7, {"positive": 0.18, "partial": 0.32, "hard_negative": 0.50})
        self.assertEqual((len(profiles), len(candidates), len(pairs)), (3, 7, 21))
        self.assertEqual(len({(p["job_id"], p["candidate_id"]) for p in pairs}), 21)

    def test_split_first_rejects_cross_split_entities(self):
        profile = mock_extract(self.jd)
        profile["split"] = "train"
        profile["source_jd_group_id"] = "jdg-1"
        candidate = generate_candidates_exact([profile], 1, "train")[0]
        candidate["split"] = "test"
        with self.assertRaises(ValueError):
            generate_pairs_exact([profile], [candidate], 1, "train", 7, {"positive": 1.0, "partial": 0.0, "hard_negative": 0.0})

    def test_sprint4a_resume_layouts_are_materially_varied(self):
        profile = mock_extract(self.jd)
        profile["split"] = "train"
        profile["source_jd_group_id"] = "jdg-1"
        candidates = generate_candidates_exact([profile], 4, "train")
        resumes = [render_resume_v2(candidate, index) for index, candidate in enumerate(candidates)]
        self.assertEqual({row["layout"] for row in resumes}, {"classic", "skills_first", "project_brief", "compact"})

    def test_split_capacity_fails_closed(self):
        profile = mock_extract(self.jd)
        profile["split"] = "test"
        profile["source_jd_group_id"] = "jdg-1"
        candidates = generate_candidates_exact([profile], 2, "test")
        with self.assertRaises(ValueError):
            generate_pairs_exact([profile], candidates, 3, "test", 7, {"positive": 0.18, "partial": 0.32, "hard_negative": 0.50})


if __name__ == "__main__":
    unittest.main()

