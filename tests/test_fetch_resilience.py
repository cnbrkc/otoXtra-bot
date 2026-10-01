"""
tests/test_fetch_resilience.py - Fetch dayanıklılık düzeltmeleri icin regresyon testleri

Kapsam (2026-08-22 paylasim-dususunu incelemesi sonrasi eklenen duzeltmeler):
  1. Akilli zaman filtresi kesme noktasi son PAYLASIM zamanina bagli olmali
     (eski davranis: son calisma zamani -> paylasilmayan haberler pencere
     disinda kalip bir daha paylasilamiyordu).
  2. Nitter RSS kaynaklari icin instance failover: kaynak instance RSS kapatsa
     bile ayni yol baska instance hostlariyla denenmeli.
  3. is_already_posted gecmis kontrolu, kalip basinliklari (orn.
     'Yeni X Turkiye'de satisa sunuldu') farkli haber saymali; ayni haberin
     sitelerarasi kopyalarini ise yakalamali.

Kapsam (2026-10-01 nitter veri cekme sorunu sonrasi eklenen duzeltmeler):
  4. Instance havuzu guncel/saglikli instance'lardan olusmali; olu instance'lar
     (nitter.cf, nitter.net, xcancel.com, nitter.catsarch.com, nuku.trabun.org ...)
     havuzda ve aday listesinde yer almamali.
  5. Instance rotasyonu (sequential round-robin / random / sticky) yukun tek
     instance'a yigilmasini engellemeli.
  6. Rate-limit / bot-challenge (Anubis, Cloudflare) gorulen instance gecici
     olarak devre disi birakilmali (circuit breaker) ve adaylardan dusmeli.
  7. Instance'lar arasi bekleme (delay + jitter) yapilmali.
  8. 'nitter' ile baslamayan instance host'lari (shitter.thepixora.com, nt.vern.cc)
     nitter olarak taninmali.
  9. Nitter linkleri kanoniklestirilmeli (https + x.com) ki rotasyon degisen
     host'lari duplike tespitini ve posted_news kayitlarini bozmasin.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

# Testlerde instance saglik durumu diske (data/nitter_health.json) yazilmasin.
os.environ.setdefault("NITTER_HEALTH_STATE", "false")

from agents import agent_fetcher
from agents.fetcher_utils import (
    _DEFAULT_NITTER_INSTANCE_HOSTS,
    _RETIRED_NITTER_INSTANCE_HOSTS,
    _VERIFIED_NITTER_INSTANCE_HOSTS,
    _canonicalize_nitter_link,
    _detect_nitter_block_reason,
    _is_nitter_feed,
    _is_nitter_url,
    _nitter_candidate_urls,
    _nitter_instance_hosts,
    _nitter_instance_state,
    _order_nitter_hosts_by_health,
    _record_nitter_instance_result,
    _request_with_retry,
    _rotate_nitter_hosts,
    _should_block_nitter_instance,
    reset_nitter_health_state,
)
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


# ══════════════════════════════════════════════════════════════════════════════
# NITTER INSTANCE HAVUZU / ROTASYON / SAGLIK
# ══════════════════════════════════════════════════════════════════════════════

class TestNitterInstancePool(unittest.TestCase):
    def setUp(self):
        reset_nitter_health_state()
        self._env_backup = {
            key: os.environ.get(key)
            for key in ("NITTER_INSTANCES", "NITTER_INSTANCE_ROTATION", "NITTER_MAX_INSTANCES_PER_FEED")
        }
        for key in self._env_backup:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_nitter_health_state()

    def test_healthy_instances_are_in_pool(self):
        """2026-10-01'de canli dogrulanan instance'lar havuzda olmali."""
        hosts = _nitter_instance_hosts()
        for host in (
            "nitter.kareem.one", "nitter.meowing.monster", "nitter.netbub.com", "shitter.thepixora.com",
        ):
            self.assertIn(host, hosts, f"{host} havuzda yok")

    def test_dead_instances_are_not_in_pool(self):
        """Log'larda olu gorunen instance'lar havuzda yer almamali."""
        hosts = set(_nitter_instance_hosts())
        for host in (
            "nitter.cf", "nitter.net", "nitter.poast.org", "nitter.privacydev.net",
            "xcancel.com", "nitter.catsarch.com", "nuku.trabun.org", "nitter.privacyredirect.com",
            "shi.meowing.de", "nitter.tiekoetter.com",
        ):
            self.assertNotIn(host, hosts, f"{host} hala havuzda")
            self.assertIn(host, _RETIRED_NITTER_INSTANCE_HOSTS)

    def test_verified_hosts_are_subset_of_defaults(self):
        for host in _VERIFIED_NITTER_INSTANCE_HOSTS:
            self.assertIn(host, _DEFAULT_NITTER_INSTANCE_HOSTS)

    def test_settings_json_matches_code_default_pool(self):
        """config/settings.json ile koddaki varsayilan havuz ayni kalmali."""
        import json
        from core.config_loader import get_project_root
        settings_path = os.path.join(get_project_root(), "config", "settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        configured = settings.get("posting", {}).get("nitter_instances", [])
        self.assertEqual(list(configured), list(_DEFAULT_NITTER_INSTANCE_HOSTS))
        for host in configured:
            self.assertNotIn(host, _RETIRED_NITTER_INSTANCE_HOSTS)

    def test_non_nitter_prefixed_hosts_are_recognized(self):
        """'nitter' ile baslamayan instance host'lari da nitter sayilmali."""
        for url in (
            "https://shitter.thepixora.com/sekizsilindir/status/1",
            "https://nt.vern.cc/sekizsilindir/rss",
            "https://x.n0g.xyz/sekizsilindir/rss",
            "http://nitter.meowing.monster/u/status/2",
        ):
            self.assertTrue(_is_nitter_url(url), url)
            self.assertTrue(_is_nitter_feed(url), url)

    def test_regular_news_urls_are_not_nitter(self):
        for url in ("https://www.donanimhaber.com/rss/anasayfa", "https://x.com/user/status/1"):
            self.assertFalse(_is_nitter_url(url), url)
            self.assertFalse(_is_nitter_feed(url), url)

    def test_env_override_normalizes_hosts(self):
        os.environ["NITTER_INSTANCES"] = "HTTPS://Nitter.Kareem.One/, nitter.netbub.com/ , , bad"
        self.assertEqual(_nitter_instance_hosts(), ["nitter.kareem.one", "nitter.netbub.com"])


class TestNitterRotation(unittest.TestCase):
    HOSTS = ["a.example", "b.example", "c.example", "d.example"]

    def setUp(self):
        reset_nitter_health_state()
        self._mode = os.environ.get("NITTER_INSTANCE_ROTATION")

    def tearDown(self):
        if self._mode is None:
            os.environ.pop("NITTER_INSTANCE_ROTATION", None)
        else:
            os.environ["NITTER_INSTANCE_ROTATION"] = self._mode
        reset_nitter_health_state()

    def test_sequential_rotation_is_round_robin(self):
        os.environ["NITTER_INSTANCE_ROTATION"] = "sequential"
        first = _rotate_nitter_hosts(self.HOSTS)
        second = _rotate_nitter_hosts(self.HOSTS)
        third = _rotate_nitter_hosts(self.HOSTS)
        self.assertEqual(sorted(first), sorted(self.HOSTS))
        self.assertNotEqual(first[0], second[0])
        self.assertNotEqual(second[0], third[0])
        # round-robin: bir sonraki tur, bir onceki turun bir kaydirmasi olmali
        self.assertEqual(second, first[1:] + first[:1])

    def test_sequential_rotation_spreads_load_over_many_calls(self):
        os.environ["NITTER_INSTANCE_ROTATION"] = "sequential"
        firsts = [_rotate_nitter_hosts(self.HOSTS)[0] for _ in range(8)]
        self.assertEqual(set(firsts), set(self.HOSTS))

    def test_random_rotation_shuffles_but_keeps_pool(self):
        os.environ["NITTER_INSTANCE_ROTATION"] = "random"
        orders = {tuple(_rotate_nitter_hosts(self.HOSTS)) for _ in range(12)}
        self.assertTrue(all(sorted(o) == sorted(self.HOSTS) for o in orders))
        self.assertGreater(len(orders), 1, "random modda liste hic karismadi")

    def test_sticky_rotation_keeps_configured_order(self):
        os.environ["NITTER_INSTANCE_ROTATION"] = "sticky"
        self.assertEqual(_rotate_nitter_hosts(self.HOSTS), self.HOSTS)

    def test_single_host_pool_is_untouched(self):
        self.assertEqual(_rotate_nitter_hosts(["only.example"]), ["only.example"])


class TestNitterCandidateUrls(unittest.TestCase):
    def setUp(self):
        reset_nitter_health_state()
        self._env_backup = {k: os.environ.get(k) for k in ("NITTER_INSTANCES", "NITTER_INSTANCE_ROTATION")}
        os.environ["NITTER_INSTANCES"] = "nitter.kareem.one,nitter.netbub.com,shitter.thepixora.com"
        os.environ["NITTER_INSTANCE_ROTATION"] = "sticky"

    def tearDown(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_nitter_health_state()

    def test_retired_source_host_is_replaced_by_pool(self):
        """nitter.cf kaynaklarda yazili olsa bile aday olarak denenmemeli."""
        urls = _nitter_candidate_urls("https://nitter.cf/sekizsilindir/rss")
        self.assertTrue(urls)
        for url in urls:
            self.assertNotIn("nitter.cf", url)
            self.assertTrue(url.endswith("/sekizsilindir/rss"), url)
        hosts = [u.split("/")[2] for u in urls]
        self.assertEqual(hosts, ["nitter.kareem.one", "nitter.netbub.com", "shitter.thepixora.com"])

    def test_pool_host_source_uses_rotation_not_fixed_first(self):
        """Kaynak host havuz icindeyse sabit 'ilk aday' olmaz, rotasyon uygulanir."""
        os.environ["NITTER_INSTANCE_ROTATION"] = "sequential"
        first_starts = set()
        for _ in range(3):
            urls = _nitter_candidate_urls("https://nitter.kareem.one/eozpeynirci/rss")
            first_starts.add(urls[0].split("/")[2])
            hosts = [u.split("/")[2] for u in urls]
            self.assertEqual(len(hosts), len(set(hosts)), "ayni host iki kere listelenmis")
        self.assertGreater(len(first_starts), 1, "rotasyon calismadi, hep ayni instance ilk sirada")

    def test_custom_self_hosted_instance_stays_first(self):
        """Havuz disi (self-host) instance kullanici tercihi olarak ilk sirada kalir."""
        urls = _nitter_candidate_urls("https://nitter.kendi-sunucum.com/eozpeynirci/rss")
        self.assertEqual(urls[0], "https://nitter.kendi-sunucum.com/eozpeynirci/rss")
        self.assertIn("https://nitter.kareem.one/eozpeynirci/rss", urls)

    def test_max_candidates_limits_attempts(self):
        urls = _nitter_candidate_urls("https://nitter.kareem.one/u/rss", max_candidates=2)
        self.assertEqual(len(urls), 2)

    def test_query_string_is_preserved(self):
        urls = _nitter_candidate_urls("https://nitter.cf/search/rss?f=tweets&q=togg")
        self.assertTrue(all(u.endswith("/search/rss?f=tweets&q=togg") for u in urls))

    def test_blocked_instance_drops_out_of_candidates(self):
        _record_nitter_instance_result("nitter.netbub.com", success=False, reason="http_429", status_code=429, blocked=True)
        urls = _nitter_candidate_urls("https://nitter.kareem.one/u/rss")
        for url in urls:
            self.assertNotIn("nitter.netbub.com", url)

    def test_degraded_instance_moves_to_end(self):
        _record_nitter_instance_result("nitter.kareem.one", success=False, reason="empty_feed")
        _record_nitter_instance_result("nitter.kareem.one", success=False, reason="empty_feed")
        urls = _nitter_candidate_urls("https://nitter.cf/u/rss")
        self.assertEqual(urls[-1].split("/")[2], "nitter.kareem.one")
        self.assertEqual(_nitter_instance_state("nitter.kareem.one"), "degraded")


class TestNitterHealth(unittest.TestCase):
    def setUp(self):
        reset_nitter_health_state()

    def tearDown(self):
        reset_nitter_health_state()

    def test_success_resets_failures(self):
        _record_nitter_instance_result("a.example", success=False, reason="timeout")
        state = _record_nitter_instance_result("a.example", success=True, reason="ok")
        self.assertEqual(state, "ok")
        self.assertEqual(_nitter_instance_state("a.example"), "ok")

    def test_blocked_until_expiry_then_retried(self):
        _record_nitter_instance_result("b.example", success=False, reason="bot_challenge", blocked=True)
        self.assertEqual(_nitter_instance_state("b.example"), "blocked")
        from agents import fetcher_utils
        fetcher_utils._nitter_health_entry("b.example")["blocked_until"] = 1.0  # suresi dolmus
        self.assertEqual(_nitter_instance_state("b.example"), "ok")

    def test_all_blocked_falls_back_to_full_list(self):
        """Havuzun tamami blokluysa hic denememek yerine liste denenmeye devam eder."""
        hosts = ["c1.example", "c2.example"]
        for host in hosts:
            _record_nitter_instance_result(host, success=False, reason="http_429", status_code=429, blocked=True)
        self.assertEqual(_order_nitter_hosts_by_health(hosts), hosts)

    def test_challenge_detection(self):
        self.assertEqual(
            _detect_nitter_block_reason("<html><body>Making sure you're not a bot! Calculating...</body></html>"),
            "bot_challenge",
        )
        self.assertEqual(
            _detect_nitter_block_reason("<html>/.within.website/x/cmd/anubis/static/img/pensive.webp</html>"),
            "bot_challenge",
        )
        self.assertEqual(_detect_nitter_block_reason("<html>Checking your browser. Fighting scrapers sucks.</html>"), "bot_challenge")
        self.assertEqual(
            _detect_nitter_block_reason("<html><h1>Error</h1>Instance has no auth tokens, or is fully rate limited.</html>"),
            "rate_limited",
        )
        self.assertEqual(_detect_nitter_block_reason("<html>XCancel service is suspended.</html>"), "rss_disabled")
        self.assertEqual(_detect_nitter_block_reason("<html>503 Nitter shutdown</html>"), "rss_disabled")
        self.assertEqual(_detect_nitter_block_reason("", 429), "http_429")
        self.assertEqual(_detect_nitter_block_reason("", 403), "http_403")

    def test_valid_rss_body_does_not_trigger_challenge_detection(self):
        """Tweet metninde 'rate limit' gecmesi instance'i bloklamamali."""
        rss = (
            '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>'
            "<item><title>Twitter rate limit exceeded haberi</title>"
            "<description>too many requests / access denied / no auth tokens</description></item>"
            "</channel></rss>"
        )
        self.assertEqual(_detect_nitter_block_reason(rss, 200), "")

    def test_block_reason_classification(self):
        for reason in ("bot_challenge", "rate_limited", "rss_disabled", "http_429", "http_403"):
            self.assertTrue(_should_block_nitter_instance(reason), reason)
        for reason in ("", "timeout", "empty_feed", "http_500"):
            self.assertFalse(_should_block_nitter_instance(reason), reason)


class TestNitterLinkCanonicalization(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("NITTER_CANONICALIZE_LINKS", None)

    def test_status_link_becomes_x_com_and_keeps_nitter_page(self):
        canonical, nitter_page = _canonicalize_nitter_link("http://shitter.thepixora.com/sekizsilindir/status/2105226138918203776")
        self.assertEqual(canonical, "https://x.com/sekizsilindir/status/2105226138918203776")
        self.assertEqual(nitter_page, "https://shitter.thepixora.com/sekizsilindir/status/2105226138918203776")

    def test_http_link_is_upgraded_to_https(self):
        canonical, nitter_page = _canonicalize_nitter_link("http://nitter.meowing.monster/u/status/1")
        self.assertTrue(nitter_page.startswith("https://"), nitter_page)
        self.assertTrue(canonical.startswith("https://"), canonical)

    def test_non_status_nitter_link_stays_on_instance(self):
        canonical, nitter_page = _canonicalize_nitter_link("https://nitter.netbub.com/i/article/2102507661111726322")
        self.assertEqual(canonical, "https://nitter.netbub.com/i/article/2102507661111726322")
        self.assertEqual(nitter_page, canonical)

    def test_canonicalization_can_be_disabled(self):
        os.environ["NITTER_CANONICALIZE_LINKS"] = "false"
        canonical, nitter_page = _canonicalize_nitter_link("https://nitter.kareem.one/u/status/1")
        self.assertEqual(canonical, "https://nitter.kareem.one/u/status/1")
        self.assertEqual(nitter_page, "https://nitter.kareem.one/u/status/1")

    def test_regular_news_link_untouched(self):
        canonical, nitter_page = _canonicalize_nitter_link("https://www.donanimhaber.com/haber/123")
        self.assertEqual(canonical, "https://www.donanimhaber.com/haber/123")
        self.assertEqual(nitter_page, "")


class TestNitterFailover(unittest.TestCase):
    def setUp(self):
        reset_nitter_health_state()
        self._env_backup = {
            k: os.environ.get(k)
            for k in ("NITTER_INSTANCES", "NITTER_INSTANCE_ROTATION", "NITTER_MAX_INSTANCES_PER_FEED")
        }
        os.environ["NITTER_INSTANCES"] = "nitter.kareem.one,nitter.netbub.com,shitter.thepixora.com"
        os.environ["NITTER_INSTANCE_ROTATION"] = "sticky"

    def tearDown(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_nitter_health_state()

    def _rss_with_entry(self) -> bytes:
        return (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<rss version="2.0"><channel><title>t</title>'
            b"<item><title>Test tweet</title><link>https://nitter.kareem.one/u/status/1</link>"
            b"<pubDate>Fri, 22 Aug 2026 10:00:00 GMT</pubDate></item>"
            b"</channel></rss>"
        )

    def _empty_rss(self) -> bytes:
        return b'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title></channel></rss>'

    def _challenge_html(self) -> bytes:
        return (
            b"<html><head><title>Making sure you're not a bot!</title></head>"
            b"<body>Anubis could not load its JavaScript. /.within.website/x/cmd/anubis/</body></html>"
        )

    def _run(self, feed_url, fake_request, feed_name="test"):
        with mock.patch.object(agent_fetcher, "_request_with_retry", side_effect=fake_request) as patched:
            with mock.patch.object(agent_fetcher.time, "sleep") as sleeper:
                response = agent_fetcher._fetch_nitter_feed_response(
                    feed_url, feed_name, timeout=12, http_attempts=2, http_base_wait=0.1
                )
        return response, patched, sleeper

    def test_failover_skips_dead_instance_and_uses_working_one(self):
        calls = []

        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5, **kwargs):
            calls.append(url)
            if "nitter.kareem.one" in url:
                raise RuntimeError("connection failed")
            response = mock.Mock()
            response.content = self._rss_with_entry()
            response.status_code = 200
            return response

        response, _, _ = self._run("https://nitter.cf/eozpeynirci/rss", fake_request, "Emre Ozpeynirci")
        # olu kaynak host (nitter.cf) hic denenmez, havuz sirayla denenir
        self.assertTrue(all("nitter.cf" not in c for c in calls), calls)
        self.assertEqual(calls[0], "https://nitter.kareem.one/eozpeynirci/rss")
        self.assertTrue(any("nitter.netbub.com/eozpeynirci/rss" in c for c in calls[1:]), calls)
        self.assertIn(b"Test tweet", response.content)

    def test_failover_skips_empty_feed_instance(self):
        calls = []

        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5, **kwargs):
            calls.append(url)
            response = mock.Mock()
            response.status_code = 200
            # ilk instance bos feed dondurur, ikincisi entry dondurur
            response.content = self._empty_rss() if len(calls) == 1 else self._rss_with_entry()
            return response

        response, _, _ = self._run("https://nitter.cf/u/rss", fake_request)
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn(b"Test tweet", response.content)

    def test_bot_challenge_response_blocks_instance_and_continues(self):
        calls = []

        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5, **kwargs):
            calls.append(url)
            response = mock.Mock()
            if len(calls) == 1:
                response.status_code = 200
                response.content = self._challenge_html()
                return response
            response.status_code = 200
            response.content = self._rss_with_entry()
            return response

        response, _, _ = self._run("https://nitter.cf/u/rss", fake_request)
        self.assertIn(b"Test tweet", response.content)
        self.assertEqual(_nitter_instance_state("nitter.kareem.one"), "blocked")
        self.assertEqual(_nitter_instance_state("nitter.netbub.com"), "ok")

    def test_rate_limited_instance_is_blocked(self):
        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5, **kwargs):
            if "nitter.kareem.one" in url:
                error_response = mock.Mock()
                error_response.status_code = 429
                error_response.text = "Too Many Requests"
                exc = __import__("requests").exceptions.HTTPError("429 Client Error")
                exc.response = error_response
                raise exc
            response = mock.Mock()
            response.status_code = 200
            response.content = self._rss_with_entry()
            return response

        response, _, _ = self._run("https://nitter.cf/u/rss", fake_request)
        self.assertIn(b"Test tweet", response.content)
        self.assertEqual(_nitter_instance_state("nitter.kareem.one"), "blocked")

    def test_all_instances_empty_returns_response_for_no_entries_status(self):
        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5, **kwargs):
            response = mock.Mock()
            response.status_code = 200
            response.content = self._empty_rss()
            return response

        response, _, _ = self._run("https://nitter.cf/u/rss", fake_request)
        self.assertIsNotNone(response)

    def test_all_instances_http_error_raises(self):
        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5, **kwargs):
            raise RuntimeError("connection failed")

        with mock.patch.object(agent_fetcher, "_request_with_retry", side_effect=fake_request):
            with mock.patch.object(agent_fetcher.time, "sleep"):
                with self.assertRaises(Exception):
                    agent_fetcher._fetch_nitter_feed_response(
                        "https://nitter.cf/u/rss", "test", timeout=12, http_attempts=2, http_base_wait=0.1
                    )

    def test_delay_between_instance_attempts(self):
        """Instance degistirirken bekleme yapilmali (ban/rate-limit korumasi)."""
        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5, **kwargs):
            response = mock.Mock()
            response.status_code = 200
            response.content = self._empty_rss() if "kareem" in url or "netbub" in url else self._rss_with_entry()
            return response

        _, patched, sleeper = self._run("https://nitter.cf/u/rss", fake_request)
        self.assertEqual(patched.call_count, 3)
        # 3 istek -> 2 instance arasi bekleme
        self.assertEqual(sleeper.call_count, 2)

    def test_rss_accept_header_is_sent(self):
        captured = {}

        def fake_request(url, timeout=20, attempts=3, base_wait_seconds=1.5, **kwargs):
            captured.update(kwargs)
            response = mock.Mock()
            response.status_code = 200
            response.content = self._rss_with_entry()
            return response

        self._run("https://nitter.cf/u/rss", fake_request)
        self.assertIn("Accept", captured.get("extra_headers", {}))


class TestHttpRequestRetryPolicy(unittest.TestCase):
    def test_client_errors_are_not_retried(self):
        """403/429 gibi kalici istemci hatalari tekrar denenmemeli (ban riski)."""
        import requests

        error_response = mock.Mock()
        error_response.status_code = 429
        calls = []

        def fake_get(url, headers=None, timeout=None, allow_redirects=True):
            calls.append(url)
            response = mock.Mock()
            response.raise_for_status.side_effect = requests.exceptions.HTTPError("429 Too Many Requests", response=error_response)
            return response

        with mock.patch("agents.fetcher_utils.requests.get", side_effect=fake_get):
            with mock.patch.object(__import__("time"), "sleep"):
                with self.assertRaises(requests.exceptions.HTTPError):
                    _request_with_retry("https://nitter.kareem.one/u/rss", timeout=5, attempts=3, base_wait_seconds=0.1)
        self.assertEqual(len(calls), 1)

    def test_server_errors_are_retried(self):
        import requests

        calls = []

        def fake_get(url, headers=None, timeout=None, allow_redirects=True):
            calls.append(url)
            response = mock.Mock()
            response.raise_for_status.side_effect = requests.exceptions.HTTPError("503 Server Error")
            return response

        with mock.patch("agents.fetcher_utils.requests.get", side_effect=fake_get):
            with mock.patch.object(__import__("time"), "sleep"):
                with self.assertRaises(requests.exceptions.HTTPError):
                    _request_with_retry("https://nitter.kareem.one/u/rss", timeout=5, attempts=3, base_wait_seconds=0.1)
        self.assertEqual(len(calls), 3)


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
