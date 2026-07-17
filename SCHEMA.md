# otoXtra BOT — ANA ŞEMA v4.0
> **BU DOSYA NEDİR?**
> Projenin tam haritası. Her yeni YZ sohbetinde SADECE BU DOSYAYI yapıştır.
> YZ bunu okuyunca projeyi tanır, senden neye ihtiyacı olduğunu söyler.
> Bir şeyi değiştirmeden önce buraya bak.
---
## PROJE DURUMU
```
Proje Adı      : otoXtra Facebook Haber Botu
Mimari         : Modüler Ajan Sistemi v5.x
Son Güncelleme : 2026-05-05
Aktif Branch   : main
Bot Durumu     : Çalışıyor
```
---
## DOSYA YAPISI (Tam ve Güncel)
```
otoXtra-bot/
├── SCHEMA.md                         ← Proje haritası (bu dosya)
├── README.md                         ← Kurulum kılavuzu
├── requirements.txt                  ← Python bağımlılıkları
│
├── config/                           ← Kod bilmeden düzenlenebilir ayarlar
│   ├── sources.json                  ← RSS + Nitter feed kaynakları
│   ├── settings.json                 ← Tüm bot ayarları (posting/images/ai/threads)
│   ├── keywords.json                 ← Dahil/hariç kelime listeleri
│   ├── scoring.json                  ← YZ puan eşikleri
│   └── prompts.json                  ← YZ prompt metinleri (viral_scorer + post_writer)
│
├── core/                             ← Ana motor katmanı (iş mantığı burada)
│   ├── orchestrator.py               ← v4.2 — Tüm ajanları sırayla çalıştırır
│   ├── ai_client.py                  ← v5.3 — Çok sağlayıcılı YZ istemcisi (Gemini/Groq/OpenRouter/HF)
│   ├── config_loader.py              ← JSON okuma/yazma + config doğrulama
│   ├── logger.py                     ← Merkezi log sistemi (UTC+3 Türkiye saati)
│   ├── helpers.py                    ← Genel yardımcılar (tekrar kontrolü, istatistik, tarih)
│   └── state_manager.py              ← pipeline.json yöneticisi (aşama takibi)
│
├── agents/                           ← Her ajan bir boru hattı aşamasını yönetir
│   ├── agent_fetcher.py              ← v5.0 — RSS çekme, Nitter desteği, FxTwitter API
│   ├── agent_scorer.py               ← v5.2 — YZ ile viral puanlama (batch=8)
│   ├── agent_writer.py               ← v5.1 — YZ ile Türkçe post yazma + kalite kontrolü
│   ├── agent_image.py                ← v7.0 — Görsel bulma/indirme/logo watermark
│   └── agent_publisher.py            ← v5.0 — Facebook + Threads + Telegram yayıncısı
│
├── platforms/                        ← Platform API katmanları (karar vermez, sadece API çağrısı yapar)
│   ├── facebook.py                   ← v3.3 — Graph API v25.0 (tek/çoklu görsel)
│   ├── threads.py                    ← v5.0 — Threads API (text/görsel/carousel + fallback zinciri)
│   └── telegram.py                   ← Telegram mesaj gönderimi + görsel kuyruğu yönetimi
│
├── queue/
│   └── pipeline.json                 ← ⚠️ ELLE DOKUNMA — Bot her çalışmada yazar/okur
│
├── data/
│   ├── posted_news.json              ← ⚠️ ELLE DOKUNMA — Paylaşılan haberler + istatistikler
│   └── telegram_updates_state.json   ← ⚠️ ELLE DOKUNMA — Telegram güncelleme durumu
│
├── assets/
│   └── logo.png                      ← Şeffaf PNG, 500×500px önerilir
│
└── .github/workflows/
    └── bot.yml                       ← GitHub Actions zamanlayıcısı + environment değişkenleri
```
---
## BORU HATTI (Pipeline) — Çalışma Akışı
```
python -m core.orchestrator
         │
         ├─ 0. Başlangıç kontrolleri
         │   ├─ Günlük limit doldu mu? (max_daily_posts)
         │   ├─ Son paylaşımdan yeterli süre geçti mi? (min_post_interval)
         │   └─ Haftalık rapor gönderilecek mi? (Pazartesi kontrolü)
         │
         ├─ 1. FETCH — agent_fetcher.py
         │   ├─ sources.json → RSS feed'leri çek (feedparser)
         │   ├─ Nitter feed'leri özel işlenir (FxTwitter API ile görsel)
         │   ├─ Zaman filtresi (max_article_age_hours)
         │   ├─ Keyword filtresi (include/exclude keywords)
         │   ├─ Tekrar kontrolü (URL + başlık benzerliği + topic fingerprint)
         │   └─ Çıktı: pipeline.json → stages.fetch.output.articles[]
         │
         ├─ 2. SCORE — agent_scorer.py
         │   ├─ Haberleri 8'lik gruplar (batch) halinde YZ'ye gönder
         │   ├─ Viral puanlama: 0-100 arası tam sayı
         │   ├─ Tazelik bonusu eklenir (1-10 puan arası, saate göre)
         │   ├─ Trend bonusu eklenir (aynı konudan birden fazla kaynak varsa)
         │   ├─ Eşiğin altındaki haberler elenir (publish_score / slow_day_score)
         │   └─ Çıktı: pipeline.json → stages.score.output.articles[] (puanlı)
         │
         ├─ 3. WRITE — agent_writer.py
         │   ├─ En yüksek puanlı haber seçilir
         │   ├─ YZ'ye Türkçe Facebook postu yazdırılır (post_writer promptu)
         │   ├─ Kalite kontrol: uzunluk, satır sayısı, yabancı alfabe, İngilizce oran
         │   ├─ Başarısız olursa → otomatik onarım denemesi (1 kez)
         │   ├─ O da başarısız olursa → fallback post (başlık + özet)
         │   └─ Çıktı: pipeline.json → stages.write.output.post_text
         │
         ├─ 4. IMAGE — agent_image.py
         │   ├─ Görsel aday listesi derlenir (meta_og, meta_twitter, article_img, rss_field...)
         │   ├─ Nitter/Twitter haberlerde FxTwitter API kullanılır
         │   ├─ Profil fotosu URL'leri filtrelenir (/profile_images/)
         │   ├─ Boyut/oran kontrolü (min 738×400, oran 0.7-2.3)
         │   ├─ Perceptual hash ile duplikasyon elenir
         │   ├─ Logo watermark eklenir (sağ-alt köşe, %12 boyut, %70 opaklık)
         │   ├─ Görsel yoksa → image_source="no_image" (publisher text-only geçer)
         │   └─ Çıktı: pipeline.json → stages.image.output{image_path, image_paths[], image_source}
         │
         └─ 5. PUBLISH — agent_publisher.py
             ├─ Puan eşiği ve rastgele atlama kontrolü
             ├─ Facebook paylaşımı (tek/çoklu görsel veya text-only)
             ├─ Threads paylaşımı (mode'a göre: text_only/text_and_image/carousel)
             │   └─ Görsel fallback zinciri (bkz. aşağıdaki bölüm)
             ├─ Telegram bildirim gönderimi
             ├─ posted_news.json güncellenir (kayıt + istatistik)
             └─ Çıktı: pipeline.json → stages.publish.output.fb_post_id
```
---
## THREADS GÖRSEL FALLBACK ZİNCİRİ
```
post_with_image(message, image_path, article)
  │
  ├─ ADIM 1: Orijinal URL (article dict'inden) ← EN HIZLI, upload gerektirmez
  │   └─ article["image_candidates"], article["image_url"], article["rss_image_url"]
  │   └─ Başarılı → BİTİR ✅
  │   └─ Başarısız → ADIM 2
  │
  ├─ ADIM 2: Catbox.moe upload ← Ücretsiz, API key YOK, limit 200MB
  │   └─ Başarılı → BİTİR ✅
  │   └─ Başarısız → ADIM 3
  │
  ├─ ADIM 3: 0x0.st upload ← Ücretsiz, API key YOK, limit 512MB
  │   └─ Başarılı → BİTİR ✅
  │   └─ Başarısız → ADIM 4
  │
  ├─ ADIM 4: Telegraph upload ← Ücretsiz, API key YOK, limit 5MB
  │   └─ Başarılı → BİTİR ✅
  │   └─ Başarısız → ADIM 5
  │
  ├─ ADIM 5: ImgBB upload ← Ücretsiz tier, IMGBB_API_KEY opsiyonel, limit 32MB
  │   └─ Başarılı → BİTİR ✅
  │   └─ Başarısız → ADIM 6
  │
  └─ ADIM 6: Metin-only fallback ← SON ÇARE
      └─ post_text(message) — görsel olmadan paylaşılır
```
---
## YZ SAĞLAYICI YEDEKLEME ZİNCİRİ (ai_client.py v5.3)
```
ask_ai(prompt)
  │
  ├─ GEMINI STACK (5 model, cascade)
  │   ├─ gemini-2.5-flash          ← Ana model (thinking destekli, thinking_budget=0)
  │   ├─ gemini-2.5-flash-lite     ← Hafif/hızlı
  │   ├─ gemini-2.0-flash          ← Stabil
  │   ├─ gemini-2.0-flash-lite     ← Stabil hafif
  │   └─ gemini-1.5-flash          ← Son yedek
  │
  ├─ GROQ STACK (3 model, cascade)
  │   ├─ llama-3.3-70b-versatile   ← Groq birincil
  │   ├─ llama-3.1-70b-versatile   ← Yedek 1
  │   └─ llama-3.1-8b-instant      ← Yedek 2 (hızlı)
  │
  ├─ OPENROUTER (openai/gpt-4o-mini)
  │
  └─ HUGGINGFACE (mistralai/Mistral-7B-Instruct-v0.2)
Hata sınıflandırması:
  timeout        → 1 kez retry (exponential backoff 3-10s)
  rate_limit     → retry YOK, sonraki modele geç
  quota_exceeded → retry YOK, sonraki modele geç
  token_limit    → retry YOK, sonraki modele geç
  unavailable    → retry YOK, sonraki modele geç (503 FIX)
  internal_error → retry YOK, sonraki modele geç (500 FIX)
```
---
## DOSYA DETAYLARI
### config/settings.json — Tüm Bölümler
```jsonc
{
  "posting": {
    "max_daily_posts": 10,              // Günlük maksimum paylaşım (1-50)
    "random_delay_max_minutes": 5,      // Çalışmadan önce maksimum rastgele bekleme (dakika)
    "min_post_interval_hours": 0,       // Paylaşımlar arası minimum süre (saat kısmı)
    "min_post_interval_minutes": 30,    // Paylaşımlar arası minimum süre (dakika kısmı)
    "skip_probability_percent": 0,      // Rastgele atlama olasılığı (0=asla, 100=hep)
    "max_posts_per_run": 1,             // Tek çalışmada maksimum paylaşım
    "dry_run": false,                   // true = gerçek paylaşım YAPMA (test)
    "feed_fetch_delay_seconds": 0.35,   // Feed'ler arası bekleme (saniye)
    "feed_fetch_delay_jitter_seconds": 0.4, // Beklemeye eklenen rastgele jitter
    "feed_fetch_attempts": 1,           // Normal feed için tekrar deneme sayısı
    "feed_http_attempts": 3,            // Normal feed HTTP istek deneme sayısı
    "feed_http_base_wait_seconds": 1.5, // HTTP retry baz bekleme süresi
    "feed_http_timeout_seconds": 20,    // HTTP istek timeout
    "nitter_feed_fetch_attempts": 3,    // Nitter feed için tekrar deneme sayısı
    "nitter_http_attempts": 3,          // Nitter HTTP istek deneme sayısı
    "nitter_http_base_wait_seconds": 1.8,
    "nitter_http_timeout_seconds": 22
  },
  "images": {
    "add_logo": true,                   // Görsele logo watermark eklensin mi?
    "logo_position": "bottom_right",    // Logo konumu: bottom_right / bottom_left / top_right / top_left
    "logo_opacity": 0.7,                // Logo saydamlığı (0.0-1.0)
    "logo_size_percent": 12,            // Logo boyutu: görsel genişliğinin yüzdesi (1-100)
    "feed_image_width": 1200,           // Fallback görsel genişliği (piksel)
    "feed_image_height": 630,           // Fallback görsel yüksekliği (piksel)
    "enable_article_image_scrape": true,  // Makale sayfasından görsel çekilsin mi?
    "enable_fetch_article_image_scrape": true, // Fetch aşamasında da scrape yapılsın mı?
    "max_candidates_per_article": 20,   // Makale başına maksimum görsel aday sayısı
    "max_article_scrapes_per_feed": 10, // Feed başına maksimum makale scrape sayısı
    "max_images_per_news": 4,           // Haber başına maksimum görsel sayısı (Facebook çoklu görsel)
    "perceptual_hash_threshold": 8      // Görsel duplikasyon eşiği (0-64, düşük=katı)
  },
  "news": {
    "max_article_age_hours": 24,        // Bu saatten eski haberler elenir
    "max_articles_per_source": 10,      // Kaynak başına maksimum haber sayısı
    "min_summary_length": 30,           // Özet minimum karakter uzunluğu
    "shared_variant_cooldown_hours": 3  // Aynı konunun varyantı için bekleme süresi
  },
  "duplicate_detection": {
    "title_similarity_threshold": 0.8,  // Başlık benzerlik eşiği (0.0-1.0, 0.8=çok benzer)
    "keyword_overlap_threshold": 0.7    // Anahtar kelime örtüşme eşiği
  },
  "ai": {
    "temperature": 0.65,                // YZ yaratıcılık seviyesi (0.0-2.0)
    "max_output_tokens": 1400,          // Maksimum YZ çıktı token sayısı
    "enable_gemini": true,              // Gemini stack aktif mi?
    "enable_groq": true,                // Groq stack aktif mi?
    "enable_openrouter": true,          // OpenRouter aktif mi?
    "enable_huggingface": true,         // HuggingFace aktif mi?
    "retry_attempts": 2,                // Her model için maksimum deneme sayısı
    "retry_base_wait_seconds": 3.0,     // Retry baz bekleme süresi
    "retry_max_wait_seconds": 10.0,     // Retry maksimum bekleme süresi
    "gemini_model": "gemini-2.5-flash", // ← Varsayılan Gemini modeli (ai_client.py GEMINI_MODELS listesi önceliklidir)
    "groq_model": "llama-3.3-70b-versatile",
    "openrouter_model": "openai/gpt-4o-mini",
    "hf_model": "mistralai/Mistral-7B-Instruct-v0.2"
  },
  "threads": {
    "enabled": true,                    // Threads paylaşımı aktif mi?
    "mode": "text_and_image"            // text_only | text_and_image | text_image_carousel
  }
}
```
### config/scoring.json
```jsonc
{
  "thresholds": {
    "publish_score": 35,    // Normal günde bu puanın üstündeki haberler paylaşılır
    "slow_day_score": 25    // Günde 2'den az paylaşım varsa bu düşük eşik kullanılır
  }
}
```
### config/sources.json — Feed Yapısı
```jsonc
{
  "feeds": [
    {
      "name": "Motor1 TR",              // Ekranda görünen ad
      "url": "https://tr.motor1.com/rss/news/all/",  // RSS feed URL
      "category": "otomobil",          // Bilgi amaçlı (şu an filtrelemede kullanılmıyor)
      "priority": "medium",            // high / medium / low — yüksek öncelikli feed'ler önce işlenir
      "language": "tr",                // Dil kodu
      "can_scrape_image": true,        // Bu siteden makale sayfası görsel çekme izni var mı?
      "enabled": true                  // false = feed atlanır
    }
    // Nitter feed örneği:
    // { "name": "Emre Ozpeynirci", "url": "https://nitter.net/eozpeynirci/rss", ... }
    // Nitter feed'ler otomatik tanınır, FxTwitter API ile görsel çekilir
  ]
}
```
### config/keywords.json
```jsonc
{
  "include_keywords": [
    // Bu kelimelerden EN AZ BİRİ başlık/özette geçmelidir
    // Hiçbiri geçmiyorsa haber elenir
    "otomobil", "araba", "elektrikli", "Tesla", "BMW", ...
  ],
  "exclude_keywords": [
    // Bu kelimelerden HERHANGİ BİRİ geçiyorsa haber anında elenir
    "kaza", "çarpıştı", "yangın", "ölüm", "Nvidia", ...
  ]
}
```
### config/prompts.json — İki Prompt
```
viral_scorer  → agent_scorer.py tarafından kullanılır
               Haberlerin 0-100 puanlanması, JSON dizisi formatı
               ÇIKTI: [{sira, baslik, puan, gerekce, detay{...}}]
post_writer   → agent_writer.py tarafından kullanılır
               Türkçe Facebook post yazma kuralları
               ÇIKTI: Sadece post metni (JSON değil, düz metin)
```
---
## MODÜL DETAYLARI
### core/orchestrator.py (v4.2)
**Görev:** Tüm ajanları sırayla çalıştırır, hataları yakalar, istatistik tutar.
**Önemli fonksiyonlar:**
- `_check_daily_limit()` → Günlük maksimum paylaşım kontrolü
- `_check_min_interval()` → Paylaşımlar arası minimum süre kontrolü
- `_send_weekly_report_if_needed()` → Her Pazartesi haftalık istatistik raporu → Telegram
- `_run_agent()` → Agent çağrısını try/except içinde güvenli çalıştırır
- `_record_error_stat()` / `_record_skip_stat()` → Haftalık istatistiklere kayıt
**Çevre değişkenleri (env) ile kontrol:**
```
PERSIST_STATE=true/false           → posted_news.json + pipeline.json yazılsın mı?
IGNORE_MIN_POST_INTERVAL=true/false → Min süre kontrolunu atla
```
---
### core/ai_client.py (v5.3 FIXED)
**Görev:** Çok sağlayıcılı YZ istemcisi. Tüm agent'lar buradan YZ çağrısı yapar.
**Ana fonksiyonlar:**
- `ask_ai(prompt, stage=None)` → Gemini → Groq → OpenRouter → HF sırasıyla dener
- `parse_ai_json(text)` → YZ yanıtından JSON parse eder (thinking modu artıkları temizlenir)
**Kritik düzeltmeler (v5.3):**
- `gemini-3.5-flash` gibi var olmayan modeller kaldırıldı → gerçek model listesi kullanılıyor
- `gemini-2.5-*` modellerinde `thinking_budget=0` ayarı → thinking metni + JSON karışıklığı önlendi
- 503 UNAVAILABLE ve 500 INTERNAL_ERROR → retry YOK, direkt sonraki modele geç
**GEMINI_MODELS listesi (ai_client.py içinde hardcoded, sırayla denenir):**
```python
GEMINI_MODELS = [
    "gemini-2.5-flash",        # Birincil
    "gemini-2.5-flash-lite",   # İkincil
    "gemini-2.0-flash",        # Üçüncül
    "gemini-2.0-flash-lite",   # Dördüncül
    "gemini-1.5-flash",        # Son yedek
]
```
---
### core/state_manager.py
**Görev:** `queue/pipeline.json` dosyasını okur/yazar. Aşama durumlarını takip eder.
**Aşamalar:** `fetch` → `score` → `write` → `image` → `publish`
**Durum değerleri:** `waiting` → `running` → `done` veya `error`
**Fonksiyonlar:**
- `init_pipeline(run_id)` → Yeni bir çalışma başlatır
- `set_stage(name, status, output, error)` → Aşama günceller
- `get_stage(name)` → Aşama verisini okur
- `get_status()` → Pipeline genel durumu: idle/running/completed/error
**pipeline.json yapısı:**
```json
{
  "run_id": "2026-05-05-09:03",
  "status": "completed",
  "started_at": "2026-05-05T09:03:12+03:00",
  "updated_at": "2026-05-05T09:08:44+03:00",
  "stages": {
    "fetch":   { "status": "done", "output": {"articles": [...]}, "error": null },
    "score":   { "status": "done", "output": {"articles": [...]}, "error": null },
    "write":   { "status": "done", "output": {"post_text": "..."}, "error": null },
    "image":   { "status": "done", "output": {"image_path": "...", "image_source": "meta_og"}, "error": null },
    "publish": { "status": "done", "output": {"fb_post_id": "..."}, "error": null }
  }
}
```
---
### core/helpers.py
**Görev:** Genel yardımcı fonksiyonlar. Her modülden import edilir.
**Önemli fonksiyonlar:**
- `get_turkey_now()` → UTC+3 Türkiye saati
- `get_posted_news()` / `save_posted_news()` → data/posted_news.json okur/yazar
- `is_already_posted(article, data)` → URL/başlık/fingerprint ile tekrar kontrolü
- `is_duplicate_article(a1, a2)` → İki haber arasında benzerlik kontrolü
- `generate_topic_fingerprint(title)` → Başlıktan stop-word temizlenmiş anahtar kümesi
- `is_similar_title(t1, t2, threshold)` → difflib ile başlık benzerliği
- `increment_action_trigger(data)` → Günlük tetiklenme sayacı
- `increment_weekly_share(data)` → Haftalık paylaşım sayacı
- `record_weekly_error(data, code, name)` → Haftalık hata kaydı
- `record_weekly_skip(data, reason)` → Haftalık atlama kaydı
- `get_weekly_stats(data, week_key)` → Haftalık istatistik özeti
- `is_shared_variant_in_cooldown(article, data)` → Aynı konunun son X saatte paylaşılıp paylaşılmadığı
**data/posted_news.json yapısı:**
```json
{
  "posts": [
    {
      "title": "Haber başlığı",
      "url": "https://...",
      "topic_fingerprint": "elektrikli-togg-uretim",
      "source": "Motor1 TR",
      "score": 72,
      "trend_count": 2,
      "posted_at": "2026-05-05T09:08:44+03:00",
      "fb_post_id": "123456789_987654321",
      "image_source": "meta_og",
      "image_count": 1
    }
  ],
  "daily_counts": { "2026-05-05": 1 },
  "last_check_time": "2026-05-05T09:08:44+03:00",
  "stats": {
    "daily_actions": { "2026-05-05": 3 },
    "weekly": {
      "2026-W18": {
        "actions": 11,
        "shares": 4,
        "error_total": 1,
        "errors": { "FETCH_ERROR: timeout": 1 },
        "skip_total": 6,
        "skips": { "score_below_threshold_skip(score=28, threshold=35)": 4 },
        "report_sent": false
      }
    }
  }
}
```
---
### agents/agent_fetcher.py (v5.0)
**Görev:** RSS ve Nitter feed'lerinden haber çeker, filtreler, görsel adayları toplar.
**Akış:**
1. `sources.json` → feed listesi
2. Her feed için: feedparser ile RSS çek → `feedparser.FeedDict`
3. Her makale için:
   - Zaman filtresi (`max_article_age_hours`)
   - Keyword filtresi (include/exclude)
   - Tekrar/benzerlik kontrolü
   - Görsel URL adayları toplanır (`image_candidates[]`)
4. Nitter feed'ler → FxTwitter API (`api.fxtwitter.com`) ile görsel çekilir
5. Tweet URL çevirisi: `nitter.net/user/status/ID` → `x.com/user/status/ID`
**Çevre değişkenleri:**
```
FEED_FETCH_DELAY_SECONDS        → Feed'ler arası bekleme
FEED_HTTP_ATTEMPTS              → HTTP istek deneme sayısı
NITTER_FEED_FETCH_ATTEMPTS      → Nitter için tekrar deneme
TEST_MODE=true                  → Filtreleri gevşetir, test için
```
**Makale dict yapısı (çıktı):**
```python
{
    "title": str,
    "link": str,                    # Haber URL
    "summary": str,                 # Temizlenmiş özet (HTML tag'leri kaldırıldı)
    "published_at": datetime,       # Yayın tarihi
    "source_name": str,             # Feed adı (sources.json'daki "name")
    "source_priority": str,         # high / medium / low
    "image_url": str,               # Birincil görsel URL (varsa)
    "rss_image_url": str,           # RSS'teki <enclosure> / <media:content> URL
    "image_candidates": [str],      # Tüm görsel adayları (öncelik sırasına göre)
    "topic_fingerprint": str,       # Anahtar kelime parmak izi
    "freshness_hours": float,       # Yayından bu yana geçen süre (saat)
    "trend_count": int,             # Bu konuyu kaç kaynak ele alıyor
    "is_nitter": bool,              # Nitter kaynağından mı geldi?
}
```
---
### agents/agent_scorer.py (v5.2 FIXED)
**Görev:** Haberleri YZ ile 0-100 arasında puanlar, tazelik ve trend bonusu ekler.
**Kritik sabitler:**
```python
BATCH_SIZE = 8              # Tek YZ çağrısında maksimum haber sayısı (20→8 düşürüldü, token limit aşımı önlendi)
BATCH_DELAY_SECONDS = 3     # Batch'ler arası bekleme
_SCORER_MAX_TOKENS = 4000   # Puanlama için token limiti (1400→4000 artırıldı)
CROSS_VALIDATE_THRESHOLD = 0.35  # Sıra eşleşmesi onayı için başlık benzerlik eşiği
```
**Tazelik bonusu:**
```
0-1 saat  → +10 puan
1-3 saat  → +7 puan
3-6 saat  → +4 puan
6-12 saat → +1 puan
12+ saat  → -4 puan
```
**Trend bonusu (aynı konudan birden fazla kaynak):**
```
5+ kaynak → +15 puan
3+ kaynak → +10 puan
2  kaynak → +5 puan
Maksimum trend bonusu: +18 puan (TREND_BONUS_CAP)
```
**Eşleşme algoritması (AI yanıtı ↔ makale):**
1. `_match_by_order()` → `sira` alanı ile sıra eşleşmesi (v5.2: artık cross-validate olmadan kabul eder)
2. `_match_by_exact_title()` → Tam başlık eşleşmesi
3. `_match_by_fuzzy_title()` → Bulanık başlık eşleşmesi (threshold=0.50)
**Puan alanları (AI'dan beklenen JSON):**
```json
{
  "sira": 1,
  "baslik": "Tam haber başlığı",
  "puan": 72,
  "gerekce": "Kısa Türkçe gerekçe",
  "detay": {
    "guncellik": 18,
    "etkilesim_potansiyeli": 20,
    "benzersizlik": 14,
    "gundem_gucu": 12,
    "paylasilabilirlik": 8
  }
}
```
---
### agents/agent_writer.py (v5.1 ULTRA FIXED)
**Görev:** En yüksek puanlı haber için Türkçe Facebook postu yazar.
**Kalite kontrol kuralları:**
```
✅ Uzunluk: 80-1800 karakter
✅ Satır sayısı: 3-15 satır
✅ Yabancı alfabe: Çince/Japonca/Arapça/Kiril karakterler YASAK
✅ İngilizce oran: Tüm kelimelerin %20'sinden azı İngilizce olmalı
✅ Yasaklı CTA: "beğenmeyi unutmayın", "paylaşmayı unutmayın" YASAK
✅ Halüsinasyon tetikleyiciler: "işte o araçlar", "işte liste" YASAK
```
**Hata durumu akışı:**
```
YZ post üretir
  → Kalite kontrol geçerse → Kullan
  → Başarısız olursa → 1 kez "onarım" dene (farklı YZ promptu)
    → O da başarısız olursa → fallback_post() (başlık + özet, YZ kullanmaz)
```
**Fallback post formatı:**
```
BAŞLIK BÜYÜK HARF (max 90 karakter)
Özet (max 420 karakter)
Siz bu gelişme hakkında ne düşünüyorsunuz?
```
---
### agents/agent_image.py (v7.0)
**Görev:** Haberler için görsel bulur, indirir, boyut/oran kontrolü yapar, logo ekler.
**Görsel kaynak önceliği (düşük sayı = yüksek öncelik):**
```
0: meta_og, meta_twitter, nitter_still   ← En güvenilir
1: nitter_card, article_script, article_img
2: article_field, rss_field, article_candidates_field
3: unknown
```
**Minimum görsel boyutu:**
```
Genişlik: 738px
Yükseklik: 400px
Alan: 337.500px²
En-boy oranı: 0.7 — 2.3
```
**Nitter/Twitter görsel çekimi (v7.0 FxTwitter):**
```
Nitter tweet URL → FxTwitter API → JSON → media.photos[].url
  Profil fotosu URL'leri (/profile_images/, /profile_banners/) FİLTRELENİR
  Başarısız olursa → x.com HTML scrape (son çare)
```
**Görsel gürültü filtreleri:**
```
URL'de şunlar varsa GÖRSELİ ATLA:
  logo, icon, avatar, sprite, favicon, ads, pixel, author, profile,
  yazar, cookie, uygulama-indir, dh-oneriyor, dh-cookie,
  instagram-big, populer-
Path'te şunlar varsa GÖRSELİ ATLA (v7.0: path-bazlı, daha hassas):
  /banner/, /banners/, /ad-banner, /images/editor/,
  /profile_images/
```
**Logo watermark:**
```
Logo: assets/logo.png (şeffaf PNG)
Konum: settings.json → images.logo_position
Boyut: Görsel genişliğinin %logo_size_percent'i
Opaklık: logo_opacity (0.0-1.0)
```
---
### agents/agent_publisher.py (v5.0)
**Görev:** Facebook, Threads ve Telegram'a paylaşım yapar.
**Mod tespiti:**
```python
image_source == "no_image" veya "fallback"
  → image_paths = []  →  text-only akışı
  → Logo eklenmez, görsel paylaşılmaz (v5.0: logo fallback KALDIRILDI)
image_source başka bir değer
  → Görsel listesi dolu  →  görsel ile paylaşım
```
**Facebook paylaşım akışı:**
```
image_paths boş   → facebook.post_text(message)
1 görsel          → facebook.post_photo(image_path, message)
2+ görsel         → facebook.post_photos(image_paths, message)
```
**Threads paylaşım akışı (settings.json threads.mode'a göre):**
```
text_only          → threads.post_text(message)
text_and_image     → threads.post_with_image(message, image_path, article)
                     (tam fallback zinciri — bkz. Threads Görsel Fallback)
text_image_carousel → threads.post_carousel(message, image_paths, article)
```
**Test modları (env değişkeni):**
```
TUM_PLATFORMLAR_TEST=true   → Facebook VE Threads'e paylaşım yok
SADECE_FACEBOOK_TEST=true   → Facebook'a paylaşım yok, Threads çalışır
SADECE_THREADS_TEST=true    → Threads'e paylaşım yok, Facebook çalışır
```
**Puan eşiği ve atlama:**
```
score < publish_score (veya slow_day_score)  → %100 atla
score >= threshold, margin < 10              → %2 şans atla
score >= threshold, margin 10-20             → %1 şans atla
score >= threshold, margin 20+               → hiç atla
```
---
### platforms/facebook.py (v3.3 ULTRA FIXED)
**API:** Graph API v25.0 (`https://graph.facebook.com/v25.0`)
**Fonksiyonlar:**
- `post_text(message)` → Sadece metin paylaşımı
- `post_photo(image_path, message)` → Tek görsel
- `post_photos(image_paths, message)` → Çoklu görsel (2-10 arası)
  - Her görsel önce `published=false` olarak yüklenir
  - Sonra `attached_media[]` ile tek post olarak yayınlanır
  - Payload boyut kontrolü: 1MB limit aşılırsa alternatif format denenir
**Retry mekanizması:**
- HTTP 5xx → retry (exponential backoff)
- API hata kodu 1/2/4/17/32/613 → retry
- "temporarily" / "try again" / "rate limit" → retry
- Diğer API hataları → anında çık
---
### platforms/threads.py (v5.0)
**API:** `https://graph.threads.net/v1.0`
**Fonksiyonlar:**
- `post_text(message)` → Metin paylaşımı (500 karakter otomatik kesme)
- `post_image(message, image_url)` → Public URL ile görsel (düşük seviye)
- `post_with_image(message, image_path, article)` → **ANA FONKSİYON** (tam fallback zinciri)
- `post_carousel(message, image_paths, article)` → Çoklu görsel (2-10 arası)
**Upload servisleri:**
```
_upload_catbox(image_path)    → Catbox.moe (API key yok, 200MB limit)
_upload_0x0(image_path)       → 0x0.st (API key yok, 512MB limit)
_upload_telegraph(image_path) → Telegraph (API key yok, 5MB limit)
_upload_imgbb(image_path)     → ImgBB (IMGBB_API_KEY opsiyonel, 32MB limit)
```
**Token temizleme:**
```python
# THREADS_ACCESS_TOKEN'daki gizli satır sonu/boşluk/tırnak otomatik temizlenir
token = token.replace('"', '').replace("'", '').replace('\n', '').strip()
```
**Metin kesme:**
```
500 karakterden uzun metinler son kelimeden (boşluktan) kesilir → "..." eklenir
```
---
### platforms/telegram.py
**Görev:** Telegram'a metin mesajı gönderir. Haftalık rapor ve hata bildirimleri için.
**Ana fonksiyonlar:**
- `send_message(text)` → Düz metin mesaj gönderir
- `_build_grouped_candidates()` → Media group'ları birleştirir (Telegram görsel kuyruğu)
- `_load_state()` / `_save_state()` → `data/telegram_updates_state.json` yönetimi
---
## API KEYS (GitHub Secrets)
```
# ZORUNLU — bunlar eksikse bot çalışmaz
FB_PAGE_ID                  Hedef Facebook sayfa ID'si
FB_ACCESS_TOKEN             Facebook sayfa erişim tokeni (60 günde bir yenilenmeli!)
GEMINI_API_KEY              Google AI Studio API key
GROQ_API_KEY                Groq API key
OPENROUTER_API_KEY          OpenRouter API key
HF_API_KEY                  HuggingFace API token
THREADS_USER_ID             Threads kullanıcı ID'si
THREADS_ACCESS_TOKEN        Threads erişim tokeni
TELEGRAM_BOT_TOKEN          Telegram bot token
TELEGRAM_CHAT_ID            Telegram sohbet/grup ID'si
# OPSİYONEL — yoksa da bot çalışır, sadece o fallback adımı atlanır
IMGBB_API_KEY               ImgBB upload API key (Threads görsel fallback zincirinde ADIM 5)
```
---
## GITHUB ACTIONS ZAMANLAYICI
```yaml
# bot.yml cron — UTC saatleri (Türkiye = UTC+3)
# Hedef TR saatleri: 06, 07, 08, 09, 11, 13, 15, 17, 19, 20
# UTC karşılıkları:  03, 04, 05, 06, 08, 10, 12, 14, 16, 17
- cron: '3 3,4,5,6,8,10,12,14,16,17 * * *'
# Günde 10 tetiklenme → max_daily_posts=10 ile günde en fazla 10 paylaşım
```
**workflow_dispatch (manuel çalıştırma) parametreleri:**
```
debug_score_breakdown        → Puan dökümü logunu aç (true/false)
enable_random_delay          → Rastgele beklemeyi aktif et
tum_platformlar_test         → Tüm platformları test moduna al
sadece_facebook_test         → Sadece Facebook'u test moduna al
sadece_threads_test          → Sadece Threads'i test moduna al
ignore_min_post_interval     → Min süre kontrolunu atla
persist_state                → JSON dosyaları Git'e yazılsın mı?
```
**Başarısız çalışmada Telegram bildirimi gönderilir:**
```
"Paylaşım yapılmadı.
Sebep: workflow_failure
Workflow başlangıcı: 2026-05-05 09:03:00 TR"
```
---
## ÇEVRE DEĞİŞKENLERİ (ENV) — Tam Liste
```
# GitHub Secrets olarak ayarlanır:
GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, HF_API_KEY
FB_PAGE_ID, FB_ACCESS_TOKEN
THREADS_USER_ID, THREADS_ACCESS_TOKEN
IMGBB_API_KEY (opsiyonel)
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# bot.yml içinde hardcoded (actions env):
NEWS_MAX_AGE_HOURS=24
SHARED_VARIANT_COOLDOWN_HOURS=3
ENABLE_FETCH_ARTICLE_IMAGE_SCRAPE=false
MAX_IMAGES_PER_NEWS=4
FEED_FETCH_DELAY_SECONDS=0.35
FEED_FETCH_DELAY_JITTER_SECONDS=0.40
FEED_FETCH_ATTEMPTS=1
FEED_HTTP_ATTEMPTS=3
FEED_HTTP_BASE_WAIT_SECONDS=1.5
FEED_HTTP_TIMEOUT_SECONDS=20
NITTER_FEED_FETCH_ATTEMPTS=3
NITTER_HTTP_ATTEMPTS=3
NITTER_HTTP_BASE_WAIT_SECONDS=1.8
NITTER_HTTP_TIMEOUT_SECONDS=22
# workflow_dispatch ile ayarlanır:
DEBUG_SCORE_BREAKDOWN=false
ENABLE_RANDOM_DELAY=false
TUM_PLATFORMLAR_TEST=false
SADECE_FACEBOOK_TEST=false
SADECE_THREADS_TEST=false
IGNORE_MIN_POST_INTERVAL=true (dispatch'te varsayılan true)
PERSIST_STATE=true
# Kod içinde okunur, doğrudan koda yazılmaz:
PERSIST_STATE         → orchestrator + publisher: JSON kaydı aktif mi?
IGNORE_MIN_POST_INTERVAL → orchestrator: Süre kontrolunu atla
DEBUG_SCORE_BREAKDOWN → agent_scorer: Detaylı puan logu
```
---
## GELİŞTİRME REHBERİ
### Ayar Değişikliği (Kod Yazmadan)
| Ne Yapmak İstiyorsun            | Hangi Dosya    | Ne Değiştir                     |
|---------------------------------|----------------|---------------------------------|
| Günlük post sayısını artır      | settings.json  | posting.max_daily_posts         |
| Puan eşiğini düşür              | scoring.json   | thresholds.publish_score (35→20) |
| Yeni RSS kaynağı ekle           | sources.json   | feeds[] dizisine yeni obje ekle |
| Nitter kaynağı ekle             | sources.json   | url = "https://nitter.net/kullanici/rss" |
| Kelime filtresi ekle            | keywords.json  | exclude_keywords listesine ekle |
| Yazım üslubunu değiştir         | prompts.json   | post_writer promptunu düzenle   |
| Görsel devre dışı bırak         | settings.json  | images.add_logo: false          |
| Threads'i kapat                 | settings.json  | threads.enabled: false          |
| Threads carousel modu           | settings.json  | threads.mode: "text_image_carousel" |
| Paylaşımlar arası süreyi artır  | settings.json  | posting.min_post_interval_minutes |
### Kod Değişikliği Gerekiyorsa
```
1. Bu SCHEMA.md'yi YZ'ye yapıştır
2. Hangi modülü, hangi fonksiyonu değiştirmek istediğini söyle
3. YZ'den TAM DOSYA al (kısmi değil)
4. GitHub'da mevcut dosyanın üzerine yaz
```
---
## ÖNEMLI KURALLAR
```
config/*.json   → Kendin düzenleyebilirsin
data/           → ELLE DOKUNMA (bot yazar)
queue/          → ELLE DOKUNMA (bot yazar)
API anahtarları → ASLA koda yazma, sadece GitHub Secrets'a
ImgBB           → Opsiyonel, olmadan da çalışır (Catbox/0x0/Telegraph key gerektirmez)
Logo yoksa      → assets/logo.png eksikse logo watermark atlanır (hata vermez)
Token süresi    → FB_ACCESS_TOKEN 60 günde bir yenilenmeli! Takvime hatırlatma koy.
```
---
## SIK SORUNLAR
| Sorun                          | Çözüm                                                             |
|--------------------------------|-------------------------------------------------------------------|
| Facebook'a paylaşmıyor         | FB_ACCESS_TOKEN süresi dolmuş → yenile (60 günde bir!)           |
| Hiç haber paylaşmıyor          | scoring.json → publish_score değerini düşür (35→20)              |
| Çok fazla paylaşıyor           | settings.json → max_daily_posts değerini düşür                   |
| Actions çalışmıyor             | Settings → Actions → "Read and write permissions" seç            |
| Görsel gelmiyor                | agent_image.py v7.0 FxTwitter API deniyor, Nitter artık boş dönüyor |
| İngilizce metin geldi          | agent_writer.py v5.1 engelliyor, fallback devreye giriyor        |
| Groq "quota exceeded"          | ai_client.py otomatik OpenRouter/HF'e geçiyor                    |
| Gemini "thinking" metni geldi  | ai_client.py v5.3'te thinking_budget=0 ile düzeltildi            |
| pipeline.json bozuk            | data/ klasörünü ve queue/ klasörünü git'ten sıfırla              |
| Dakika limiti doldu            | Repo'yu public yap (secret'lar güvende kalır)                    |
---
## GÜNCEL ÖZELLİKLER
```
RSS haber çekme                 : VAR (feedparser)
Nitter/Twitter desteği          : VAR (FxTwitter API v7.0)
Keyword filtresi                : VAR (include + exclude)
Zaman filtresi                  : VAR (max_article_age_hours)
Tekrar kontrolü                 : VAR (URL + başlık benzerliği + topic fingerprint)
Konu parmak izi                 : VAR (stop-word temizlenmiş anahtar kümesi)
Trend dedektörü                 : VAR (+5/10/15 puan, maks +18)
YZ puanlama                     : VAR (0-100, batch=8, Gemini birincil)
YZ fallback zinciri             : VAR (Gemini 5 model → Groq 3 model → OpenRouter → HF)
YZ Türkçe kalite kontrolü       : VAR (İngilizce oran, yabancı alfabe, uzunluk, satır)
Tazelik bonusu                  : VAR (+10/+7/+4/+1/-4 puan, saate göre)
Facebook tek görsel paylaşımı   : VAR (Graph API v25.0)
Facebook çoklu görsel            : VAR (2-10 görsel, unpublished upload + attached_media)
Facebook text-only paylaşımı    : VAR (görsel bulunamazsa)
Logo watermark                  : VAR (pozisyon/boyut/opaklık ayarlanabilir)
Günlük limit                    : VAR (max_daily_posts)
Sakin gün modu                  : VAR (slow_day_score: günde <2 paylaşım varsa daha düşük eşik)
Rastgele bekleme/atlama         : VAR (random_delay_max_minutes)
Geçmiş temizlik                 : VAR (30 gün — 30 günden eski kayıtlar silinir)
Haftalık rapor (Telegram)       : VAR (Pazartesi otomatik, actions/shares/errors/skips)
Threads metin paylaşımı         : VAR
Threads görsel paylaşımı        : VAR (fallback zinciri: Orijinal URL → Catbox → 0x0 → Telegraph → ImgBB → text-only)
Threads carousel paylaşımı      : VAR (2-10 görsel)
Threads 500 karakter limiti     : VAR (otomatik kesme, kelime ortasında kesmez)
Telegram hata bildirimi         : VAR (workflow başarısız olursa)
Nitter görsel çekimi (FxTwitter): VAR (profil fotosu filtrelenir)
Görsel duplikasyon kontrolü     : VAR (perceptual hash, threshold ayarlanabilir)
Instagram paylaşımı             : YOK
Twitter/X paylaşımı             : YOK
```
---
**Versiyon: 4.0** — Tüm kaynak kodlar satır satır okunarak hazırlanmıştır.
