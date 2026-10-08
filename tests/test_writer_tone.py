"""agent_writer v5.8 / agent_publisher v6.6 birim testleri.

Kapsam:
  - prompts.json: post_writer'ın Threads dili + salt bilgi çekirdeği bölümleri,
    ezber 'asıl mesele' kalıbının yalnızca yasak listesinde geçmesi ve diğer iki
    promptun (viral_scorer, story_card_writer) bozulmamış olması.
  - _find_cliches / _enforce_hook_freshness: ezber kalıp tespit edilince tek
    yeniden yazım denenir; başarısızsa ORİJİNAL metin korunur.
  - _build_writer_prompt / _recent_post_hooks: son açılışların prompta eklenmesi.
  - agent_publisher._extract_hook + _build_new_post_record: hook kaydı.
  - _fallback_post: soru yok, 480 sınırı, özet (salt bilgi) korunur.
"""
import json
import os
import unittest
from unittest import mock

from agents import agent_publisher, agent_writer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_PATH = os.path.join(REPO_ROOT, "config", "prompts.json")

ARTICLE = {
    "title": "Yeni crossover Türkiye'de 1.6 milyon TL'den satışa çıktı",
    "summary": "Yeni crossover modeli Türkiye pazarına girdi. Liste fiyatı 1.6 milyon TL olarak açıklandı.",
    "source_name": "Test Kaynak",
}

CLEAN_POST = (
    "1.6 MİLYON TL'LİK LİSTE FİYATI ARTIK KAMPANYA HABERİ DEĞİL.\n\n"
    "Yeni crossover bu hafta 1.6 milyon TL'den listeye girdi, bir önceki "
    "versiyonda bu rakam 1.4 milyon TL'ydi.\n\n"
    "Bu seviyede alıcı modelin adına değil, karşılığında ne aldığına bakıyor."
)

CLICHE_POST = (
    "🚗 BU HABERDE ASIL MESELE OTOMOBİL DEĞİL.\n\n"
    "Asıl mesele fiyatın kendisi: yeni crossover 1.6 milyon TL'den listeye girdi.\n\n"
    "Asıl mesele tüketicinin beklentisi; marka değeri bahanesinin ömrü kısalıyor."
)


def _prompts() -> dict:
    with open(PROMPTS_PATH, encoding="utf-8") as handle:
        return json.load(handle)


class TestPromptsFile(unittest.TestCase):
    def test_prompts_json_valid_and_complete(self):
        prompts = _prompts()
        for key in ("viral_scorer", "post_writer", "story_card_writer"):
            self.assertIn(key, prompts)
            self.assertTrue(prompts[key].strip())

    def test_post_writer_is_threads_voice_with_fact_core(self):
        writer = _prompts()["post_writer"]
        self.assertIn("THREADS/X editörüsün", writer)
        self.assertIn("SALT BİLGİ ÇEKİRDEĞİ", writer)
        self.assertIn("AÇILIŞ (KANCA) BANKASI", writer)
        self.assertIn("PLATFORM TUTARLILIĞI", writer)
        # Facebook için ayrı/yumuşatılmış bir dil tarifi yok: tek ses kuralı sabit.
        self.assertIn("tek bir dil var: otoXtra'nın Threads dili", writer)

    def test_cliche_only_in_ban_section(self):
        writer = _prompts()["post_writer"]
        self.assertEqual(writer.lower().count("asıl mesele"), 1)

        ban_section = writer.split("=== KLİŞE YASAĞI")[1].split("\n", 1)[1].split("\n=== ")[0]
        self.assertIn("asıl mesele", ban_section.lower())
        self.assertIn("YASAK", ban_section)

        # Örnek ton artık o kalıpla başlamıyor (modeli kalıba kilitleyen cümle buydu).
        example_section = writer.split("=== ÖRNEK TON")[1]
        self.assertNotIn("asıl mesele", example_section.lower())

    def test_other_prompts_untouched(self):
        prompts = _prompts()
        # viral_scorer: JSON dizisi sözleşmesi korunuyor.
        self.assertIn("SADECE VE SADECE GECERLI JSON DIZISI DON", prompts["viral_scorer"])
        self.assertIn('"sira"', prompts["viral_scorer"])
        # story_card_writer: başlık + alt metin JSON sözleşmesi korunuyor.
        self.assertIn('{"baslik": "...", "alt_metin": "..."}', prompts["story_card_writer"])


class TestClicheDetection(unittest.TestCase):
    def test_detects_cliche(self):
        # CLICHE_POST hem 'ASIL MESELE' (büyük harf) hem 'Asıl mesele' içerir;
        # iki varyant da yakalanır.
        self.assertEqual(set(agent_writer._find_cliches(CLICHE_POST)), {"asıl mesele", "asil mesele"})

    def test_clean_text_has_no_cliche(self):
        self.assertEqual(agent_writer._find_cliches(CLEAN_POST), [])

    def test_detects_ascii_variant(self):
        self.assertIn("asil mesele", agent_writer._find_cliches("Bu haberde asil mesele fiyat."))


class _FakeAskAi:
    """ask_ai yerine geçen stub: verilen cevapları sırayla döndürür, promptları saklar."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt, stage=""):
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else ""


class TestGeneratePostText(unittest.TestCase):
    def _run(self, responses):
        fake = _FakeAskAi(responses)
        with mock.patch.object(agent_writer, "ask_ai", fake), \
                mock.patch.object(agent_writer, "load_config", return_value={"post_writer": "PROMPT"}), \
                mock.patch.object(agent_writer, "_recent_post_hooks", return_value=[]):
            result = agent_writer.generate_post_text(dict(ARTICLE))
        return result, fake

    def test_clean_post_passes_without_extra_call(self):
        result, fake = self._run([CLEAN_POST])
        self.assertEqual(result, CLEAN_POST)
        self.assertEqual(len(fake.prompts), 1)

    def test_cliche_post_is_rewritten_once(self):
        result, fake = self._run([CLICHE_POST, CLEAN_POST])
        self.assertEqual(result, CLEAN_POST)
        self.assertEqual(len(fake.prompts), 2)
        # Yeniden yazım promptu hem tespit edilen kalıbı hem salt bilgi kuralını taşımalı.
        rewrite_prompt = fake.prompts[1]
        self.assertIn("asıl mesele", rewrite_prompt)
        self.assertIn("SOMUT verisini", rewrite_prompt)
        self.assertIn("480", rewrite_prompt)

    def test_rewrite_still_cliche_keeps_original(self):
        result, fake = self._run([CLICHE_POST, CLICHE_POST])
        self.assertEqual(result, CLICHE_POST)
        self.assertEqual(len(fake.prompts), 2)

    def test_rewrite_failure_keeps_original(self):
        result, fake = self._run([CLICHE_POST, ""])
        self.assertEqual(result, CLICHE_POST)
        self.assertEqual(len(fake.prompts), 2)

    def test_rewrite_too_short_keeps_original(self):
        result, fake = self._run([CLICHE_POST, "Kısa metin."])
        self.assertEqual(result, CLICHE_POST)
        self.assertEqual(len(fake.prompts), 2)

    def test_repair_path_also_goes_through_cliche_guard(self):
        # İlk metin çok kısa -> kalite kontrolü patlar -> onarım -> klişe koruması.
        too_short = "Kısa."
        result, fake = self._run([too_short, CLICHE_POST, CLEAN_POST])
        self.assertEqual(result, CLEAN_POST)
        self.assertEqual(len(fake.prompts), 3)


class TestWriterPromptAntiRepeat(unittest.TestCase):
    def test_recent_hooks_block_added(self):
        prompt = agent_writer._build_writer_prompt(
            dict(ARTICLE), "PROMPT", ["1.6 MİLYON TL'LİK LİSTE FİYATI HABER DEĞİL."]
        )
        self.assertIn("SON PAYLAŞIMLARIN AÇILIŞ CÜMLELERİ", prompt)
        self.assertIn("1.6 MİLYON TL'LİK LİSTE FİYATI HABER DEĞİL.", prompt)
        self.assertIn("TEKRARLAMA", prompt)

    def test_no_hooks_block_when_empty(self):
        prompt = agent_writer._build_writer_prompt(dict(ARTICLE), "PROMPT", [])
        self.assertNotIn("SON PAYLAŞIMLARIN AÇILIŞ CÜMLELERİ", prompt)

    def test_salt_bilgi_kurali_in_critical_rules(self):
        prompt = agent_writer._build_writer_prompt(dict(ARTICLE), "PROMPT", [])
        self.assertIn("somut bilgisini", prompt)
        self.assertIn("ezber bir kalıp olmasın", prompt)

    def test_recent_post_hooks_reads_newest_first(self):
        posted = {
            "posts": [
                {"title": "a", "hook": "ESKİ AÇILIŞ"},
                {"title": "b", "hook": "ORTA AÇILIŞ"},
                {"title": "c", "hook": "YENİ AÇILIŞ"},
                {"title": "d"},
            ]
        }
        with mock.patch.object(agent_writer, "get_posted_news", return_value=posted):
            hooks = agent_writer._recent_post_hooks(limit=2)
        self.assertEqual(hooks, ["YENİ AÇILIŞ", "ORTA AÇILIŞ"])

    def test_recent_post_hooks_survives_bad_state(self):
        with mock.patch.object(agent_writer, "get_posted_news", return_value={"posts": "bozuk"}):
            self.assertEqual(agent_writer._recent_post_hooks(), [])
        with mock.patch.object(agent_writer, "get_posted_news", side_effect=RuntimeError("yok")):
            self.assertEqual(agent_writer._recent_post_hooks(), [])


class TestPublisherHookRecord(unittest.TestCase):
    def test_extract_hook_takes_first_line(self):
        self.assertEqual(
            agent_publisher._extract_hook("İLK SATIR\n\nikinci satır"),
            "İLK SATIR",
        )

    def test_extract_hook_handles_empty(self):
        self.assertEqual(agent_publisher._extract_hook(""), "")
        self.assertEqual(agent_publisher._extract_hook("\n\n  \n"), "")

    def test_extract_hook_truncates(self):
        self.assertEqual(len(agent_publisher._extract_hook("x" * 300)), 120)

    def test_record_contains_hook(self):
        record = agent_publisher._build_new_post_record(
            dict(ARTICLE), "post_1", "scraper", 1, hook="İLK SATIR"
        )
        self.assertEqual(record["hook"], "İLK SATIR")
        self.assertEqual(record["fb_post_id"], "post_1")

    def test_record_backwards_compatible_without_hook(self):
        record = agent_publisher._build_new_post_record(dict(ARTICLE), "post_2", "search", 2)
        self.assertEqual(record["hook"], "")


class TestFallbackPost(unittest.TestCase):
    def test_fallback_keeps_info_and_has_no_question(self):
        post = agent_writer._fallback_post(dict(ARTICLE))
        self.assertLessEqual(len(post), 480)
        self.assertNotIn("?", post)
        self.assertIn("1.6 milyon TL", post)  # salt bilgi korunur
        self.assertIn("YENİ CROSSOVER", post)

    def test_fallback_closing_rotates(self):
        first = agent_writer._fallback_post({"title": "Birinci haber", "summary": "Özet bir."})
        second = agent_writer._fallback_post({"title": "İkinci bambaşka haber", "summary": "Özet iki."})
        closing_first = first.split("\n")[-1]
        closing_second = second.split("\n")[-1]
        self.assertIn(closing_first, agent_writer._FALLBACK_CLOSINGS)
        self.assertIn(closing_second, agent_writer._FALLBACK_CLOSINGS)
        self.assertNotEqual(closing_first, closing_second)

    def test_stable_choice_is_deterministic(self):
        options = ("a", "b", "c")
        self.assertEqual(
            agent_writer._stable_choice("aynı başlık", options),
            agent_writer._stable_choice("aynı başlık", options),
        )


class TestQualityCheckUnchanged(unittest.TestCase):
    def test_clean_post_passes(self):
        ok, reason = agent_writer._quality_check(CLEAN_POST)
        self.assertTrue(ok, reason)

    def test_cliche_post_still_passes_quality_gate(self):
        # Klişe koruması kalite kapısından BAĞIMSIZ çalışır: metin kalite olarak
        # geçerlidir, bu yüzden fallback'e değil yeniden yazıma gitmelidir.
        ok, _ = agent_writer._quality_check(CLICHE_POST)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
