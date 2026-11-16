from pathlib import Path
import sqlite3
import tempfile
import unittest

from smash_pipeline.analytics import (
    DEFAULT_INPUT_DIR,
    build_database,
    export_power_bi_tables,
    generate_eda_report,
    run_data_quality_checks,
)


class AnalyticsTest(unittest.TestCase):
    def setUp(self):
        required_inputs = [
            DEFAULT_INPUT_DIR / "jd_profiles.jsonl",
            DEFAULT_INPUT_DIR / "candidate_profiles.jsonl",
            DEFAULT_INPUT_DIR / "pairs.jsonl",
        ]
        if not all(path.exists() for path in required_inputs):
            self.skipTest("private Sprint 4A inputs are not present")
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "analytics.sqlite"
        build_database(DEFAULT_INPUT_DIR, self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_etl_loads_expected_normalized_counts(self):
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM dim_job").fetchone()[0], 50)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM dim_candidate").fetchone()[0], 125)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM fact_pair").fetchone()[0], 1000)
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM dim_skill").fetchone()[0], 10)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()

    def test_kpi_denominators_and_provenance_are_explicit(self):
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute("SELECT * FROM v_overall_kpis").fetchone()
            self.assertEqual(row[0], 1000)
            self.assertEqual(row[-1], "synthetic_rule_derived")
            self.assertEqual(connection.execute("SELECT SUM(pair_count) FROM v_required_skill_coverage_distribution").fetchone()[0], 1000)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM v_jd_coverage_detail").fetchone()[0], 50)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM v_candidate_coverage_detail").fetchone()[0], 125)
        finally:
            connection.close()

    def test_quality_exports_and_report(self):
        quality = run_data_quality_checks(self.db_path)
        self.assertEqual(quality["status"], "PASS")
        self.assertTrue(all(quality["checks"].values()))
        export_dir = self.root / "power_bi"
        counts = export_power_bi_tables(self.db_path, export_dir)
        self.assertEqual(counts["fact_pair"], 1000)
        self.assertTrue((export_dir / "metric_catalog.csv").exists())
        report_path = self.root / "report.md"
        generate_eda_report(self.db_path, report_path)
        report = report_path.read_text(encoding="utf-8")
        self.assertIn("synthetic candidate", report)
        self.assertIn("not Smash recruiting performance", report)
        self.assertIn("no metric here should be renamed", report)


if __name__ == "__main__":
    unittest.main()
