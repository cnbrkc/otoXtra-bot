import os
import tempfile
import unittest

from core import config_loader
from core import state_manager
from core.ai_client import parse_ai_json


class TestConfigSanitize(unittest.TestCase):
    def test_settings_sanitize_bounds(self):
        raw = {
            "posting": {
                "max_daily_posts": -5,
                "skip_probability_percent": 999,
            },
            "images": {
                "logo_opacity": 9,
                "feed_image_width": 10,
            },
            "news": {
                "max_article_age_hours": 9999,
            },
            "duplicate_detection": {
                "title_similarity_threshold": 7,
            },
            "ai": {
                "temperature": -3,
                "max_output_tokens": 999999,
            },
        }

        safe = config_loader._sanitize_settings(raw)

        self.assertGreaterEqual(safe["posting"]["max_daily_posts"], 1)
        self.assertLessEqual(safe["posting"]["skip_probability_percent"], 100)
        self.assertGreaterEqual(safe["images"]["logo_opacity"], 0.0)
        self.assertLessEqual(safe["images"]["logo_opacity"], 1.0)
        self.assertGreaterEqual(safe["images"]["feed_image_width"], 300)
        self.assertLessEqual(safe["news"]["max_article_age_hours"], 168)
        self.assertLessEqual(safe["duplicate_detection"]["title_similarity_threshold"], 1.0)
        self.assertGreaterEqual(safe["ai"]["temperature"], 0.0)
        self.assertLessEqual(safe["ai"]["max_output_tokens"], 8192)

    def test_sources_sanitize(self):
        raw = {
            "feeds": [
                {"name": "ok", "url": "https://a.com/rss", "priority": "high"},
                {"name": "bad-no-url"},
                {"url": "https://b.com/rss", "priority": "weird"},
            ]
        }

        safe = config_loader._sanitize_sources(raw)
        self.assertIn("feeds", safe)
        self.assertEqual(len(safe["feeds"]), 2)
        self.assertEqual(safe["feeds"][1]["priority"], "medium")


class TestStateManager(unittest.TestCase):
    def test_pipeline_lifecycle(self):
        old_path = state_manager._PIPELINE_PATH

        with tempfile.TemporaryDirectory() as tmp_dir:
            state_manager._PIPELINE_PATH = os.path.join(tmp_dir, "pipeline.json")

            ok = state_manager.init_pipeline("unit-test-run")
            self.assertTrue(ok)
            self.assertEqual(state_manager.get_status(), "running")

            self.assertTrue(state_manager.set_stage("fetch", "done", output={"count": 1}))
            self.assertTrue(state_manager.is_stage_done("fetch"))

            self.assertTrue(state_manager.set_stage("score", "error", error="test"))
            self.assertEqual(state_manager.get_status(), "error")

        state_manager._PIPELINE_PATH = old_path


class TestAiJsonParse(unittest.TestCase):
    def test_parse_ai_json_fallback_array(self):
        txt = "cevap: [{'a':1}]"
        txt = txt.replace("'", '"')
        parsed = parse_ai_json(txt)
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["a"], 1)

    def test_parse_ai_json_fallback_object(self):
        txt = 'sonuc -> {"ok": true, "n": 2}'
        parsed = parse_ai_json(txt)

        # Writer parser tek obje bulunca bazen dict, bazen [dict] donebilir.
        if isinstance(parsed, list):
            self.assertGreater(len(parsed), 0)
            self.assertIsInstance(parsed[0], dict)
            self.assertTrue(parsed[0]["ok"])
            self.assertEqual(parsed[0]["n"], 2)
        else:
            self.assertIsInstance(parsed, dict)
            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["n"], 2)


if __name__ == "__main__":
    unittest.main()
