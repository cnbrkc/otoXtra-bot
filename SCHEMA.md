# 🗺️ otoXtra BOT — ANA ŞEMA v3.0

> **BU DOSYA NEDİR?**
> Projenin tam haritası. Her yeni sohbette YZ'ye SADECE BU DOSYAYI yapıştır.
> YZ bunu okuyunca projeyi tanır, senden neye ihtiyacı olduğunu söyler.
>
> **NE ZAMAN KULLANIRSIN?**
> - Yeni özellik eklemek istediğinde → bu dosyayı yapıştır
> - Bir şey bozulduğunda → bu dosyayı yapıştır
> - "Hangi dosyayı değiştireyim?" diye merak ettiğinde → bu dosyayı oku
>
> **ÖNEMLİ:** Bu dosyayı her büyük değişiklikten sonra güncelle.

---

## 📊 PROJE DURUMU

```
Proje Adı    : otoXtra Facebook Haber Botu
Mimari       : Modüler Ajan Sistemi v3.0
Son Güncelleme: [TARİH GİR]
Aktif Branch : main
Bot Durumu   : ✅ Çalışıyor / 🔄 Geçişte / ❌ Sorunlu  ← birini seç
```

---

## 📂 DOSYA YAPISI (Tam ve Güncel)

```
otoXtra-bot/
│
├── 📄 SCHEMA.md                        ← BU DOSYA
├── 📄 README.md                        ← Kurulum rehberi
├── 📄 requirements.txt                 ← Python kütüphaneleri
│
├── 📁 config/                          ← SENİN AYARLARIN (kendin değiştirirsin)
│   ├── sources.json                       → RSS haber kaynakları
│   ├── settings.json                      → Genel ayarlar
│   ├── keywords.json                      → Filtre kelimeleri
│   ├── scoring.json                       → Puan eşikleri
│   └── prompts.json                       → YZ talimatları
│
├── 📁 core/                            ← ORTAK ARAÇLAR (ajanlar bunları kullanır)
│   ├── orchestrator.py                    → Ana dirijан, tüm ajanları sırayla çalıştırır
│   ├── config_loader.py                   → Config dosyalarını okur
│   ├── logger.py                          → Log yazar
│   ├── helpers.py                         → Yardımcı fonksiyonlar
│   └── state_manager.py                   → pipeline.json okur/yazar
│
├── 📁 agents/                          ← BAĞIMSIZ AJANLAR (her biri tek iş yapar)
│   ├── agent_fetcher.py                   → RSS çeker, filtreler
│   ├── agent_scorer.py                    → YZ ile puanlar
│   ├── agent_writer.py                    → YZ ile metin yazar
│   ├── agent_image.py                     → Görsel hazırlar
│   └── agent_publisher.py                 → Paylaşımı tetikler
│
├── 📁 platforms/                       ← PLATFORM BAĞLANTILARI
│   └── facebook.py                        → Facebook Graph API
│
├── 📁 queue/                           ← AJANLAR ARASI VERİ KUTUSU
│   └── pipeline.json                      → ⚠️ Bot günceller, elle dokunma
│
├── 📁 data/                            ← VERİ DEPOSU
│   └── posted_news.json                   → ⚠️ Bot günceller, elle dokunma
│
├── 📁 assets/
│   └── logo.png                           → Görsellere eklenen watermark
│
└── 📁 .github/workflows/
    └── bot.yml                             → Zamanlayıcı (08:00-22:00 arası akıllı cron)


```

---

## 🔄 VERİ AKIŞI

```
bot.yml (gün içinde akıllı zamanlamayla tetikler)
    └─→ core/orchestrator.py başlar
            │
            ├─→ [1] agents/agent_fetcher.py
            │       OKU  : config/sources.json
            │       OKU  : config/keywords.json
            │       OKU  : config/settings.json
            │       OKU  : data/posted_news.json (tekrar kontrolü)
            │       YAZ  : queue/pipeline.json → stages.fetch
            │
            ├─→ [2] agents/agent_scorer.py
            │       OKU  : queue/pipeline.json → stages.fetch.output
            │       OKU  : config/prompts.json (viral_scorer)
            │       OKU  : config/scoring.json
            │       YAZ  : queue/pipeline.json → stages.score
            │
            ├─→ [3] agents/agent_writer.py
            │       OKU  : queue/pipeline.json → stages.score.output
            │       OKU  : config/prompts.json (post_writer)
            │       YAZ  : queue/pipeline.json → stages.write
            │
            ├─→ [4] agents/agent_image.py
            │       OKU  : queue/pipeline.json → stages.write.output
            │       OKU  : config/settings.json (images bölümü)
            │       OKU  : assets/logo.png
            │       YAZ  : queue/pipeline.json → stages.image
            │
            └─→ [5] agents/agent_publisher.py
                    OKU  : queue/pipeline.json → stages.image.output
                    KULLAN: platforms/facebook.py
                    YAZ  : data/posted_news.json
                    YAZ  : queue/pipeline.json → stages.publish
```

---

## 📄 DOSYA DETAYLARI

### config/ — Ayar Dosyaları

---

#### config/sources.json
**Ne yapar:** Haber kaynaklarının RSS adreslerini içerir.

**Format:**
```json
[
  {
    "name": "Kaynak Adı",
    "url": "https://rss-adresi.com/feed",
    "category": "otomotiv",
    "priority": "high",
    "language": "tr",
    "can_scrape_image": true,
    "enabled": true
  }
]
```

**Alan açıklamaları:**
```
name             → Log'larda görünür, istediğini yaz
url              → RSS feed adresi
category         → "otomotiv", "teknoloji" vb.
priority         → "high" / "medium" / "low"
                   Benzer haberlerden hangisi seçilir bunu belirler
can_scrape_image → true: Bu siteden görsel çekilebilir
                   false: Yedek görsel kullan (lacivert + logo)
enabled          → false yaparak kaynağı geçici kapatırsın
```

---

#### config/settings.json
**Ne yapar:** Botun genel davranışını kontrol eder. 4 bölüm var.

**Format:**
```json
{
  "posting": {
    "max_daily_posts": 9,
    "max_posts_per_run": 1,
    "min_post_interval_hours": 1,
    "random_delay_max_minutes": 8,
    "skip_probability_percent": 10
  },
  "images": {
    "add_logo": true,
    "logo_position": "bottom_right",
    "logo_opacity": 0.7,
    "logo_size_percent": 15,
    "feed_image_width": 1200,
    "feed_image_height": 630
  },
  "news": {
    "max_article_age_hours": 12
  },
  "ai": {
    "timeout_seconds": 30,
    "max_retries": 2
  }
}
```

**Alan açıklamaları:**
```
posting bölümü:
  max_daily_posts           → Günlük max post sayısı
  max_posts_per_run         → Her çalışmada max post
  min_post_interval_hours   → İki post arası minimum süre (saat)
  random_delay_max_minutes  → Rastgele bekleme (doğal görünsün diye)
  skip_probability_percent  → Rastgele atlama olasılığı %

images bölümü:
  add_logo                  → Logo eklensin mi
  logo_opacity              → Logo saydamlığı (0.0-1.0)
  logo_size_percent         → Logo boyutu (görselin %kaçı)
  feed_image_width/height   → Çıktı görsel boyutu (piksel)

news bölümü:
  max_article_age_hours     → Bu saatten eski haberler atılır

ai bölümü:
  timeout_seconds           → YZ cevap vermezse ne kadar bekle
  max_retries               → Kaç kez tekrar dene
```

---

#### config/keywords.json
**Ne yapar:** Hangi haberlerin alınıp hangilerinin atılacağını belirler.

**Format:**
```json
{
  "include_keywords": ["elektrikli", "SUV", "lansman", "fiyat"],
  "exclude_keywords": ["kaza", "ölüm", "mahkeme", "yangın"]
}
```

**Önemli kurallar:**
```
include_keywords → Bu kelimelerden en az biri geçmeli
                   Geçmiyorsa haber atılır
exclude_keywords → Bu kelimelerden biri geçerse haber atılır
                   Kısmi eşleşme çalışır: "kaza" → "kazası"'nı da yakalar
                   Kök analizi YOK: "çarpıştı" ayrı, "çarpışma" ayrı yazılmalı
```

---

#### config/scoring.json
**Ne yapar:** Haberin yayınlanmaya değer olup olmadığının eşiğini belirler.

**Format:**
```json
{
  "thresholds": {
    "publish_score": 65,
    "slow_day_score": 50
  }
}
```

**Alan açıklamaları:**
```
publish_score   → Normal günlerde minimum puan (0-100)
slow_day_score  → Bugün 2'den az post yapılmışsa kullanılır
                  Sakin günlerde de içerik çıksın diye daha düşük
```

> **Puanlama KRİTERLERİ nerede?**
> config/prompts.json → viral_scorer içinde.
> YZ o promptu okuyarak puanlıyor.

---

#### config/prompts.json
**Ne yapar:** YZ'ye gönderilen komutları içerir.

**Format:**
```json
{
  "viral_scorer": "Haberi şu kriterlere göre 0-100 arası puan ver: ...",
  "post_writer": "Facebook için Türkçe post yaz. Üslup: ..."
}
```

**Alan açıklamaları:**
```
viral_scorer → agent_scorer.py kullanır
               6 kriter var: bilgi değeri, paylaşılabilirlik,
               etki alanı, özgünlük, duygusal etki, güncellik
               Her kriter 1-100, sonuç ortalama

post_writer  → agent_writer.py kullanır
               Üslup, emoji kullanımı, uzunluk gibi kurallar burada
```

---

### core/ — Ortak Araçlar

---

#### core/orchestrator.py
**Ne yapar:** Tüm ajanları sırayla çalıştırır. Karar vermez, sadece yönetir.

**Çalışma mantığı:**
```
1. Yeni run_id ile pipeline başlat
2. agent_fetcher çalıştır → hata varsa dur, log yaz
3. agent_scorer çalıştır  → hata varsa dur, log yaz
4. agent_writer çalıştır  → hata varsa dur, log yaz
5. agent_image çalıştır   → hata varsa dur, log yaz
6. agent_publisher çalıştır → hata varsa dur, log yaz
7. Genel sonucu logla
```

**Bağımlılıklar:** core/logger.py, core/state_manager.py, tüm agents/

---

#### core/config_loader.py
**Ne yapar:** Config klasöründeki JSON dosyalarını okur.

**Fonksiyonlar:**
```
load_config(name) → config/{name}.json dosyasını okur, dict döner
                    Örnek: load_config("settings") → settings.json içeriği
```

**Bağımlılıklar:** core/logger.py

---

#### core/logger.py
**Ne yapar:** Zaman damgalı log mesajları yazar.

**Fonksiyonlar:**
```
log(message, level) → "[2024-01-15 14:23:01] [INFO] mesaj" formatında yazar
                       level: "INFO", "WARNING", "ERROR"
```

**Bağımlılıklar:** Yok (bağımsız)

---

#### core/helpers.py
**Ne yapar:** Genel yardımcı fonksiyonlar.

**Fonksiyonlar:**
```
clean_html(text)                    → HTML tag'lerini temizler
get_turkey_now()                    → Türkiye saatini döner (UTC+3)
is_already_posted(url, title, data) → Bu haber daha önce paylaşıldı mı?
is_similar_title(t1, t2)           → İki başlık %60+ benzer mi?
get_last_check_time(data)           → Son kontrol zamanını döner
get_posted_news()                   → data/posted_news.json okur
save_posted_news(data)              → data/posted_news.json yazar
generate_topic_fingerprint(title)    → Başlıktan normalize parmak izi üretir (YENİ)
is_topic_already_posted(fp, data)    → Konu bazlı tekrar kontrolü (YENİ)
is_already_posted()                  → URL + başlık + KONU (3 katmanlı, güncellendi)
save_posted_news()                   → 30 günlük otomatik temizlik (güncellendi)
```

**Bağımlılıklar:** core/logger.py, core/config_loader.py

---

#### core/state_manager.py
**Ne yapar:** queue/pipeline.json okur ve yazar. Ajanlar arası veri taşır.

**Fonksiyonlar:**
```
init_pipeline(run_id)                        → Yeni çalışma başlatır
get_stage(stage_name)                        → Aşama çıktısını döner
set_stage(stage_name, status, output)        → Aşama sonucunu kaydeder
get_status()                                 → Genel pipeline durumunu döner
is_stage_done(stage_name)                    → Aşama bitti mi? True/False
```

**pipeline.json formatı:**
```json
{
  "run_id": "2024-01-15-14:00",
  "status": "running",
  "stages": {
    "fetch":   { "status": "done",    "output": [...] },
    "score":   { "status": "done",    "output": {...} },
    "write":   { "status": "running", "output": null  },
    "image":   { "status": "waiting", "output": null  },
    "publish": { "status": "waiting", "output": null  }
  }
}
```

**status değerleri:** `"waiting"` / `"running"` / `"done"` / `"error"`

**Bağımlılıklar:** core/logger.py

---

### agents/ — Bağımsız Ajanlar

---

#### agents/agent_fetcher.py
**Ne yapar:** Haber kaynaklarından RSS çeker, filtreler, pipeline'a yazar.

**İşlem sırası:**
```
1. fetch_all_feeds()         → Tüm RSS kaynaklarını çek
2. resolve_google_news_url() → Google News yönlendirmelerini çöz
3. apply_keyword_filter()    → include/exclude kelime filtresi
4. apply_time_filter()       → max_article_age_hours'dan eski haberleri at
5. remove_already_posted()   → posted_news.json'da olanları at
6. remove_duplicates()       → Benzer başlıkları tekil yap (priority'e göre)
7. pipeline.json'a yaz       → stages.fetch
```

**Yardımcı fonksiyonlar:**
```
scrape_full_article(url)      → Haber sitesinden tam metin çeker
_extract_image_from_entry()   → RSS'ten görsel URL çıkarır (6 farklı yöntem dener)
```

**Okuduğu dosyalar:** sources.json, keywords.json, settings.json, posted_news.json
**Yazdığı dosya:** pipeline.json → stages.fetch
**Bağımlılıklar:** core/logger.py, core/config_loader.py, core/helpers.py, core/state_manager.py

---

#### agents/agent_scorer.py
**Ne yapar:** pipeline'daki haberleri YZ ile puanlar, en iyisini seçer.

**İşlem sırası:**
```
1. pipeline.json'dan fetch çıktısını oku
2. Her haber için YZ'ye viral_scorer promptu gönder
3. Puan al (0-100)
4. Bugünkü post sayısına göre eşik belirle
   (2'den az post → slow_day_score, değilse → publish_score)
5. Eşiğin üstündeki en yüksek puanlı haberi seç
6. pipeline.json'a yaz → stages.score
```

**Okuduğu dosyalar:** pipeline.json (fetch), prompts.json, scoring.json
**Yazdığı dosya:** pipeline.json → stages.score
**Bağımlılıklar:** core/logger.py, core/config_loader.py, core/state_manager.py

---

#### agents/agent_writer.py
**Ne yapar:** Seçilen haber için Facebook post metni yazar.

**İşlem sırası:**
```
1. pipeline.json'dan score çıktısını oku (seçilen haber)
2. post_writer promptunu hazırla
3. YZ servislerini sırayla dene:
   Gemini → Groq → OpenRouter → HuggingFace
4. Türkçe olmayan karakterleri temizle
5. Yarım kalan JSON'u tamir et (gerekirse)
6. pipeline.json'a yaz → stages.write
```

**Yardımcı fonksiyonlar:**
```
ask_ai(prompt)                → YZ'ye sor, 4 servisi sırayla dener
parse_ai_json(response)       → YZ cevabını JSON'a çevirir
_clean_non_turkish_chars()    → Türkçe dışı karakterleri temizler
_fix_truncated_json_array()   → Yarım JSON'u tamir eder
```

**Okuduğu dosyalar:** pipeline.json (score), prompts.json
**Yazdığı dosya:** pipeline.json → stages.write
**Bağımlılıklar:** core/logger.py, core/config_loader.py, core/state_manager.py

---

#### agents/agent_image.py
**Ne yapar:** Haber için görsel hazırlar, logo ekler.

**İşlem sırası:**
```
1. pipeline.json'dan write çıktısını oku
2. Görsel bul (sırayla dener):
   a) RSS'ten gelen görsel URL'si
   b) Haber sitesinden og:image çek (scrape)
   c) Hiçbiri yoksa → yedek görsel üret
      (1200x630, lacivert #1a1a2e arkaplan, ortada logo)
3. Görseli indir ve boyutlandır (1200x630)
4. Logo watermark ekle (sağ alt köşe, yarı saydam)
5. pipeline.json'a yaz → stages.image
```

**Okuduğu dosyalar:** pipeline.json (write), settings.json, assets/logo.png
**Yazdığı dosya:** pipeline.json → stages.image (görsel dosya yolu)
**Bağımlılıklar:** core/logger.py, core/config_loader.py, core/state_manager.py

---

#### agents/agent_publisher.py
**Ne yapar:** Hazırlanan içeriği Facebook'a gönderir, kayıt tutar.

**İşlem sırası:**
```
1. pipeline.json'dan image çıktısını oku
2. Günlük limit kontrolü (max_daily_posts)
3. Rastgele atlama kontrolü (skip_probability_percent)
4. platforms/facebook.py ile Facebook'a paylaş
5. data/posted_news.json güncelle
6. Rastgele bekle (random_delay_max_minutes)
7. pipeline.json'a yaz → stages.publish
```

**Okuduğu dosyalar:** pipeline.json (image), settings.json, posted_news.json
**Yazdığı dosyalar:** posted_news.json, pipeline.json → stages.publish
**Bağımlılıklar:** core/logger.py, core/config_loader.py, core/helpers.py, core/state_manager.py, platforms/facebook.py

---

### platforms/ — Platform Bağlantıları

---

#### platforms/facebook.py
**Ne yapar:** SADECE Facebook Graph API çağrısı yapar. Karar vermez.

**Fonksiyonlar:**
```
post_photo(image_path, message) → Fotoğraflı post atar, post_id döner
post_text(message)              → Sadece metin post atar, post_id döner
```

**API:** Graph API v11.0
**Bağımlılıklar:** core/logger.py

> **NOT:** Yeni platform eklemek istersen → platforms/ altına yeni dosya ekle.
> Örnek: platforms/instagram.py, platforms/twitter.py
> agent_publisher.py ve orchestrator.py'da küçük güncelleme yeterli.

---

### data/ — Veri Deposu

---

#### data/posted_news.json
**Ne yapar:** Paylaşılan haberlerin geçmişini tutar.

**Format:**
```json
{
  "posts": [
    {
      "title": "Haber başlığı",
      "url": "https://...",
      "score": 78,
      "posted_at": "2024-01-15T14:23:01"
    }
  ],
  "daily_counts": {
    "2024-01-15": 3,
    "2024-01-16": 5
  },
  "last_check_time": "2024-01-15T14:23:01"
}
```

**⚠️ KURALI:** Elle düzenleme. Bot günceller.
500+ kayıt birikince eski kayıtlar otomatik temizlenir.
Silersen veya boşaltırsan bot bozulur.
Minimum içerik: `{"posts": [], "daily_counts": {}, "last_check_time": null}`

---

### queue/ — Veri Kutusu

---

#### queue/pipeline.json
**Ne yapar:** Ajanlar arasında veri taşır. Her çalışmada sıfırlanır.

**⚠️ KURALI:** Elle düzenleme. Bot günceller.

---

### .github/workflows/bot.yml
**Ne yapar:** GitHub Actions zamanlayıcısı.

**Ayarlar:**
```
Tetiklenme  : Her 2 saatte bir (cron)
Çalışma saati: 06:00 - 00:00 Türkiye saati
Çalıştırdığı : python core/orchestrator.py
```

**Ne zaman değiştirirsin:** Çalışma saatlerini değiştirmek istersen.

---

## 🔑 API KEY'LER (GitHub Secrets)

```
FACEBOOK_PAGE_ID       → Facebook sayfa ID'si
FACEBOOK_ACCESS_TOKEN  → Facebook Graph API token'ı
GEMINI_API_KEY         → Google Gemini (birincil YZ)
GROQ_API_KEY           → Groq (yedek YZ 1)
OPENROUTER_API_KEY     → OpenRouter (yedek YZ 2)
HF_API_KEY             → HuggingFace (yedek YZ 3)
```

> **⚠️ KURAL:** Bu anahtarları asla kod içine yazma. Sadece GitHub Secrets'ta.

---

## 🤖 YZ İLE ÇALIŞMA REHBERİ

### Yeni Sohbet Açtığında Her Seferinde:

```
ADIM 1: Yeni sohbet aç
ADIM 2: Bu SCHEMA.md'nin TAMAMINI yapıştır
ADIM 3: Ne istediğini söyle
ADIM 4: YZ senden hangi dosyaları istediğini söyler
ADIM 5: O dosyaları GitHub'dan kopyala, YZ'ye yapıştır
ADIM 6: YZ sana dosyaların TAMAMINI verir
ADIM 7: GitHub'da dosyayı aç → kalem ✏️ → CTRL+A → sil → yapıştır → commit
```

---

### Senaryo Rehberi

---

#### 🔧 Senaryo 1: Bir Şey Bozuldu

```
YZ'ye söyle:
"otoXtra botumda sorun var. SCHEMA.md'yi veriyorum.
 Şu hata çıkıyor: [HATAYI YAPISTIR]
 Hangi dosyada sorun olabilir?"

YZ sana şunu söyler:
"Bu hata agents/agent_fetcher.py'dan geliyor.
 O dosyayı gönder."

Sen gönderirsin → YZ düzeltilmiş halini verir.
```

---

#### 📰 Senaryo 2: Yeni Haber Kaynağı Ekle

```
YZ'ye gerek yok, kendin yaparsın:

1. GitHub'da config/sources.json aç
2. Kalem ✏️ tıkla
3. Mevcut bir kaynağı kopyala
4. name ve url'yi değiştir
5. Commit changes
BİTTİ ✅
```

---

#### ✍️ Senaryo 3: Yazım Üslubunu Değiştir

```
YZ'ye gerek yok, kendin yaparsın:

1. GitHub'da config/prompts.json aç
2. Kalem ✏️ tıkla
3. post_writer içindeki talimatları değiştir
4. Commit changes
BİTTİ ✅
```

---

#### 📊 Senaryo 4: Puan Eşiğini Değiştir

```
YZ'ye gerek yok, kendin yaparsın:

1. GitHub'da config/scoring.json aç
2. Kalem ✏️ tıkla
3. publish_score değerini değiştir
4. Commit changes
BİTTİ ✅
```

---

#### 📱 Senaryo 5: Instagram'a da Paylaşsın

```
YZ'ye söyle:
"otoXtra botuma Instagram paylaşımı eklemek istiyorum.
 SCHEMA.md'yi veriyorum."

YZ sana şunu söyler:
"Şu dosyaları gönder:
 - core/orchestrator.py
 - agents/agent_publisher.py
 - requirements.txt"

Sen gönderirsin → YZ şunları verir:
 - platforms/instagram.py (YENİ dosya)
 - agents/agent_publisher.py (güncellenmiş)
 - core/orchestrator.py (güncellenmiş)
 - requirements.txt (güncellenmiş)

YENİ dosya için: GitHub'da "Add file" → "Create new file"
Diğerleri için: Aç → kalem ✏️ → CTRL+A → sil → yapıştır → commit
```

---

#### 📅 Senaryo 6: Çalışma Saatlerini Değiştir

```
YZ'ye söyle:
"Bot sadece sabah 08:00 - akşam 20:00 arası çalışsın.
 SCHEMA.md'yi veriyorum."

YZ sana şunu söyler:
".github/workflows/bot.yml dosyasını gönder."

Sen gönderirsin → YZ güncellenmiş bot.yml verir.
```

---

#### 🔔 Senaryo 7: Paylaşım Olunca Telegram Bildirimi

```
YZ'ye söyle:
"Her paylaşımdan sonra Telegram'a bildirim göndersin.
 SCHEMA.md'yi veriyorum."

YZ sana şunu söyler:
"Şu dosyaları gönder:
 - agents/agent_publisher.py
 - core/orchestrator.py"

Ayrıca GitHub Secrets'a TELEGRAM_BOT_TOKEN ve
TELEGRAM_CHAT_ID eklememizi söyler.
```

---

#### 🕐 Senaryo 8: Günlük Post Sayısını Değiştir

```
YZ'ye gerek yok, kendin yaparsın:

1. GitHub'da config/settings.json aç
2. Kalem ✏️ tıkla
3. max_daily_posts değerini değiştir
4. Commit changes
BİTTİ ✅
```

---

## 🏆 ALTIN KURALLAR

```
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║  Config değişikliği  → KENDİN YAP (JSON dosyasını düzenle)      ║
║  Kod değişikliği     → YZ'YE SOR, SCHEMA.md'yi ver              ║
║  Yeni özellik        → YZ'YE SOR, SCHEMA.md'yi ver              ║
║                                                                  ║
║  YZ SANA HER ZAMAN DOSYANIN TAMAMINI VERİR                      ║
║  SEN DE TAMAMEN DEĞİŞTİRİRSİN (satır satır değil)               ║
║                                                                  ║
║  ⚠️ pipeline.json    → DOKUNMA (bot günceller)                   ║
║  ⚠️ posted_news.json → DOKUNMA (bot günceller)                   ║
║  ⚠️ __init__.py      → HİÇBİR YERE OLUŞTURMA                    ║
║  ⚠️ API key'ler      → ASLA KODA YAZMA                           ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## 📋 GÜNCEL ÖZELLIKLER

> Bu tabloyu her yeni özellik eklendiğinde güncelle.
> YZ, bu tabloyu okuyarak botun şu an ne yapıp yapamadığını anlar.

```
┌─────────────────────────────────┬────────┬─────────────────────────┐
│           ÖZELLİK               │ DURUM  │          NOT            │
├─────────────────────────────────┼────────┼─────────────────────────┤
│ RSS haber çekme                 │  ✅    │                         │
│ Google News URL çözme           │  ✅    │                         │
│ Keyword filtresi                │  ✅    │                         │
│ Zaman filtresi                  │  ✅    │ max 12 saat             │
│ Tekrar kontrolü                 │  ✅    │ URL + başlık + KONU     │ ← güncellendi
│ Konu parmak izi (hafıza)        │  ✅    │ 30 gün, topic_fp        │ ← YENİ
│ Benzerlik kontrolü              │  ✅    │ %60 eşik                │
│ Trend dedektörü                 │  ✅    │ 2→+5, 3→+10, 5→+15 puan │ ← YENİ
│ YZ ile puanlama                 │  ✅    │ 6 kriter                │
│ YZ yedekleme (4 servis)         │  ✅    │ Gemini→Groq→OR→HF       │
│ Tazelik bonusu                  │  ✅    │ 0-2s:+7, 2-4s:+3        │
│ Facebook paylaşımı              │  ✅    │ Graph API v11.0         │
│ Görsel çekme (og:image)         │  ✅    │                         │
│ Logo watermark                  │  ✅    │ sağ alt köşe            │
│ Yedek görsel üretme             │  ✅    │ lacivert + logo         │
│ Günlük limit                    │  ✅    │ max_daily_posts         │
│ Sakin gün modu                  │  ✅    │ slow_day_score          │
│ Rastgele bekleme                │  ✅    │ doğal görünsün diye     │
│ Geçmiş temizliği                │  ✅    │ 30 günlük otomatik      │ ← YENİ
│ Instagram paylaşımı             │  ❌    │ Planlanmadı             │
│ Telegram bildirimi              │  ❌    │ Planlanmadı             │
│ Twitter/X paylaşımı             │  ❌    │ Planlanmadı             │
└─────────────────────────────────┴────────┴─────────────────────────┘
```

---

*Versiyon: 3.0 — Modüler Ajan Sistemi*
*Bu dosya değiştiğinde versiyon numarasını ve tarihi güncelle.*
