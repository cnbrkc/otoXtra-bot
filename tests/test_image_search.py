"""image_search v3.0 birim testleri: yıl çıkarımı, marka/model, URL sinyali,
vision kapısı ve Wikipedia/Commons API parametre üreticileri."""
import unittest

from agents.image_search import (
    _extract_model_year,
    _extract_brand_model,
    _deterministic_search_queries,
    _url_signal_score,
    vision_gate_passed,
    _wikipedia_api_url,
    _wikipedia_search_params,
    _pageimages_params,
    _commons_api_url,
    _commons_search_params,
    _commons_category_params,
)


class TestModelYear(unittest.TestCase):
    def test_year_from_title(self):
        self.assertEqual(_extract_model_year("BMW iX3 2025 Türkiye'de satışta"), "2025")

    def test_year_from_summary_fallback(self):
        self.assertEqual(
            _extract_model_year("Yeni Corolla tanıtıldı", "Toyota 2026 modelini duyurdu"),
            "2026",
        )

    def test_no_year(self):
        self.assertEqual(_extract_model_year("Yeni model geldi"), "")

    def test_old_years_ignored(self):
        # 1985 otomobil haberlerinde model yılı değildir; ama 1990+ kabul edilir
        self.assertEqual(_extract_model_year("1985 klasikleri sergide"), "")
        self.assertEqual(_extract_model_year("1998 model Civic"), "1998")

    def test_year_from_build_queries(self):
        queries = _deterministic_search_queries("Toyota Corolla 2026 fiyatı açıklandı")
        self.assertTrue(any("2026" in q for q in queries))
        self.assertIn("Toyota Corolla 2026", queries[0])


class TestBrandModel(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_extract_brand_model("Yeni BMW iX3 Türkiye'de satışta"), ("BMW", "iX3"))

    def test_year_excluded_from_model(self):
        brand, model = _extract_brand_model("Toyota Corolla 2026 fiyatı açıklandı")
        self.assertEqual(brand, "Toyota")
        self.assertEqual(model, "Corolla")

    def test_no_brand(self):
        self.assertEqual(_extract_brand_model("Otomobil pazarı daraldı"), ("", ""))

    def test_multiword_brand(self):
        brand, model = _extract_brand_model("Alfa Romeo Giulietta yenilendi")
        self.assertEqual(brand, "Alfa Romeo")
        self.assertEqual(model, "Giulietta")


class TestUrlSignal(unittest.TestCase):
    def test_match_scores_higher(self):
        queries = ["BMW iX3 2025", "BMW iX3"]
        relevant = _url_signal_score("https://cdn.example.com/bmw-ix3-2025.jpg", queries)
        generic = _url_signal_score("https://cdn.example.com/generic-car-photo.jpg", queries)
        self.assertGreater(relevant, generic)

    def test_no_queries(self):
        self.assertEqual(_url_signal_score("http://a/b.jpg", []), 0)

    def test_no_url(self):
        self.assertEqual(_url_signal_score("", ["BMW iX3"]), 0)


class TestVisionGate(unittest.TestCase):
    def test_fail_closed(self):
        # require_vision=True: yalnızca True kabul
        self.assertTrue(vision_gate_passed(True, True))
        self.assertFalse(vision_gate_passed(False, True))
        self.assertFalse(vision_gate_passed(None, True))

    def test_fail_open(self):
        # require_vision=False: yalnızca False red
        self.assertTrue(vision_gate_passed(True, False))
        self.assertTrue(vision_gate_passed(None, False))
        self.assertFalse(vision_gate_passed(False, False))


class TestApiBuilders(unittest.TestCase):
    def test_wikipedia_url(self):
        self.assertEqual(_wikipedia_api_url("tr"), "https://tr.wikipedia.org/w/api.php")
        self.assertEqual(_wikipedia_api_url("en"), "https://en.wikipedia.org/w/api.php")

    def test_wikipedia_search_params(self):
        params = _wikipedia_search_params("BMW iX3")
        self.assertEqual(params["action"], "query")
        self.assertEqual(params["list"], "search")
        self.assertEqual(params["srsearch"], "BMW iX3")
        self.assertEqual(params["format"], "json")

    def test_pageimages_params(self):
        params = _pageimages_params("BMW iX3")
        self.assertEqual(params["prop"], "pageimages")
        self.assertEqual(params["piprop"], "thumbnail|original")
        self.assertEqual(params["redirects"], "1")

    def test_commons_params(self):
        params = _commons_search_params("BMW iX3")
        self.assertEqual(params["generator"], "search")
        self.assertEqual(params["gsrnamespace"], "6")
        self.assertIn("filetype:bitmap", params["gsrsearch"])
        self.assertEqual(params["prop"], "imageinfo")
        self.assertEqual(params["iiurlwidth"], 1600)

    def test_commons_category_params(self):
        params = _commons_category_params("BMW iX3")
        self.assertEqual(params["cmtitle"], "Category:BMW iX3")
        self.assertEqual(params["cmtype"], "file")


if __name__ == "__main__":
    unittest.main()
