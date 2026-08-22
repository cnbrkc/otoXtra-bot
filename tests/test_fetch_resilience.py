"""
tests/test_fetch_resilience.py - Fetch dayanıklılık düzeltmeleri icin regresyon testleri

Kapsam (2026-08-22 paylasim-dususunu incelemesi sonrasi eklenen duzeltmeler):
  1. Akilli zaman filtresi kesme noktasi son PAYLASIM zamanina bagli olmali
     (eski davranis: son calisma zamani -> paylasilmayan haberler pencere
     disinda kalip bir daha paylasilamiyordu).
  2. Nitter RSS kaynaklari icin instance failover: nitter.net RSS kapatsa
     bile ayni yol baska instance hostlariyla denenmeli.
  3. is_already_posted gecmis kontrolu, kalip basinliklari (orn.
     'Yeni X Turkiye'de satisa sunuldu') farkli haber saymali; ayni haberin
     sitelerarasi kopyalarini ise yakalamali.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from agents import agent_fetcher
from agents.fetcher_utils import _nitter_candidate_urls
from core.helpers import generate_topic_fingerprint, is_already_posted

_TR_TZ = timezone(timedelta(hours=3))


def _make_article(title: str, published_utc: datetime, link: str) -> dict:
    return {
        "title": title,
        "published": published_utc.isoformat(),
        "link": link,
        "source_priority": "medium",
    }


class TestSmartCutoffAnchor(unittest.TestCase):
    def _run_filter(self, articles, posted_data):
        with mock.patch.object(agent_fetcher, "get_posted_news", return_value=posted_data):
            return agent_fetcher._apply_time_filter_with_hours(
                articles=articles, max_age_hours=24, use_smart_cutoff=True
            )

    def test_cutoff_anchors_to_last_post_not_last_check(self):
        now_tr = datetime.now(_TR_TZ)
        # Son paylasim 5 saat once; son calisma (last_check) 10 dakika once.
        # Eski davranis kesmeyi last_check-30dk'ya cekip 4 saat onceki taze
        # haberi kaybediyordu; yeni davranis son paylasima kadar geri gitmeli.
        posted_data = {
            "posts": [{"posted_at": (now_tr - timedelta(hours=5)).isoformat(), "title": "x", "url": "u"}],
            "last_check_time": (now_tr - timedelta(minutes=10)).isoformat(),
        }
        articles = [
            _make_article("Dort saat once cikmis haber", datetime.now(timezone.utc) - timedelta(hours=4), "https://x/1"),
            _make_article("Az once cikmis haber", datetime.now(timezone.utc) - timedelta(minutes=5), "https://x/2"),
        ]
        passed, cutoff = self._run_filter(articles, posted_data)
        titles = {a["title"] for a in passed}
        self.assertIn("Dort saat once cikmis haber", titles)
        self.assertIn("Az once cikmis haber", titles)

    def test_cutoff_expands_when_no_recent_post(self):
        now_tr = datetime.now(_TR_TZ)
        posted_data = {
            "posts": [{"posted_at": (now_tr - timedelta(hours=20)).isoformat(), "title": "x", "url": "u"}],
            "last_check_time": (now_tr - timedelta(minutes=10)).isoformat(),
        }
        articles = [
            _make_article("12 saat onceki haber", datetime.now(timezone.utc) - timedelta(hours=12), "https://x/1"),
            _make_article("30 saat onceki haber", datetime.now(timezone.utc) - timedelta(hours=30), "https://x/2"),
        ]
        passed, _ = self._run_filter(articles, posted_data)
        titles = {a["title"] for a in passed}
        self.assertIn("12 saat onceki haber", titles)
        # 24 saatlik maksimum yas siniri her durumda korunur
        self.assertNotIn("30 saat onceki haber", titles)

    def test_no_posts_falls_back_to_last_check(self):
        now_tr = datetime.now(_TR_TZ)
        posted_data = {"posts": [], "last_check_time": (now_tr - timedelta(minutes=5)).isoformat()}
        articles = [
            _make_article("Az onceki haber", datetime.now(timezone.utc) - timedelta(minutes=1), "https://x/1"),
            _make_article("2 saat onceki haber", datetime.now(timezone.utc) - timedelta(hours=2), "https://x/2"),
        ]
        passed, _ = self._run_filter(articles, posted_data)
        titles = {a["title"] for a in passed}
        # Paylasim gecmisi yokken anchor last_check_time'dir; 90dk grace ile
        # 2 saat onceki haber pencere disinda kalir (yeni haber gecer).
        self.assertIn("Az onceki haber", titles)
        self.assertNotIn("2 saat onceki haber", titles)


class TestNitterFailover(unittest.TestCase):
    def test_candidate_urls_keep_original_first(self):
        urls = _nitter_candidate_urls("https://nitter.net/eozpeynirci/rss")
        self.assertEqual(urls[0], "https://nitter.net/eozpeynirci/rss")
        self.assertTrue(any("xcancel.com/eozpeynirci/rss" in u for u in urls))
        # ayni host iki kere listeye girmemeli
        hosts = [u.split("/")[2] for u in urls]
        self.assertEqual(len(hosts), len(set(hosts)))

    def _rss_with_entry(self) -> bytes:
        return (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b"<rss version=\"2.0\"><channel><title>t</title>"
            b"<item><title>Test tweet</title><link>https://nitter.net/u/status/1</link>"
            b"<pubDate>Fri, 22 Aug 2026 10:00:00 GMT</pubDate></item>"
            b"</channel></rss>"
        )

    def _empty_rss(self) -> bytes:
        return b'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title></channel></rss>'

    def test_failover_skips_dead_instance_and_uses_working_one(self):
        calls = []

        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5):
            calls.append(url)
            if "nitter.net" in url:
                raise RuntimeError("RSS feed is disabled")
            response = mock.Mock()
            response.content = self._rss_with_entry()
            return response

        with mock.patch.object(agent_fetcher, "_request_with_retry", side_effect=fake_request):
            response = agent_fetcher._fetch_nitter_feed_response(
                "https://nitter.net/eozpeynirci/rss", "Emre Ozpeynirci", timeout=12, http_attempts=2, http_base_wait=0.1
            )
        self.assertTrue(calls[0].startswith("https://nitter.net/"))
        self.assertTrue(any("xcancel.com" in c for c in calls[1:]))
        self.assertIn(b"Test tweet", response.content)

    def test_failover_skips_empty_feed_instance(self):
        calls = []

        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5):
            calls.append(url)
            response = mock.Mock()
            # ilk instance bos feed dondurur, ikincisi entry dondurur
            response.content = self._empty_rss() if len(calls) == 1 else self._rss_with_entry()
            return response

        with mock.patch.object(agent_fetcher, "_request_with_retry", side_effect=fake_request):
            response = agent_fetcher._fetch_nitter_feed_response(
                "https://nitter.net/u/rss", "test", timeout=12, http_attempts=2, http_base_wait=0.1
            )
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn(b"Test tweet", response.content)

    def test_all_instances_empty_returns_response_for_no_entries_status(self):
        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5):
            response = mock.Mock()
            response.content = self._empty_rss()
            return response

        with mock.patch.object(agent_fetcher, "_request_with_retry", side_effect=fake_request):
            response = agent_fetcher._fetch_nitter_feed_response(
                "https://nitter.net/u/rss", "test", timeout=12, http_attempts=2, http_base_wait=0.1
            )
        self.assertIsNotNone(response)

    def test_all_instances_http_error_raises(self):
        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5):
            raise RuntimeError("connection failed")

        with mock.patch.object(agent_fetcher, "_request_with_retry", side_effect=fake_request):
            with self.assertRaises(Exception):
                agent_fetcher._fetch_nitter_feed_response(
                    "https://nitter.net/u/rss", "test", timeout=12, http_attempts=2, http_base_wait=0.1
                )


class TestPostedCheckThresholds(unittest.TestCase):
    def _history(self, titles):
        return {
            "posts": [
                {"url": f"https://site/{i}", "title": t, "topic_fingerprint": generate_topic_fingerprint(t)}
                for i, t in enumerate(titles)
            ]
        }

    def test_template_headlines_are_not_blocked(self):
        history = self._history(["Yeni Peugeot 308, Türkiye'de satışa sunuldu"])
        self.assertFalse(
            is_already_posted("https://baska-site/rav4", "Yeni Toyota RAV4 Hybrid, Türkiye'de son çeyrekte satışa sunulacak!", history)
        )

    def test_same_story_cross_site_is_blocked(self):
        history = self._history(["Tesla Semi, resmi olarak Avrupa'ya getiriliyor!"])
        self.assertTrue(
            is_already_posted("https://shiftdelete.net/tesla-semi", "Tesla Semi Avrupa Yollarına Çıkıyor", history)
        )

    def test_exact_url_always_blocked(self):
        history = self._history(["Bambaşka bir başlık"])
        self.assertTrue(is_already_posted("https://site/0", "Tamamen farklı bir başlık!", history))


if __name__ == "__main__":
    unittest.main()
