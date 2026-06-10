# otoXtra BOT - ANA SEMA v3.2

> BU DOSYA NEDIR?
> Projenin tam haritasi. Her yeni sohbette YZ'ye SADECE BU DOSYAYI yapistir.
> YZ bunu okuyunca projeyi tanir, senden neye ihtiyaci oldugunu soyler.
>
> NE ZAMAN KULLANIRSIN?
> - Yeni ozellik eklemek istediginde
> - Bir sey bozuldugunda
> - "Hangi dosyayi degistireyim?" dediginde
>
> ONEMLI: Bu dosyayi her buyuk degisiklikten sonra guncelle.

---

## PROJE DURUMU

```
Proje Adi      : otoXtra Facebook Haber Botu
Mimari         : Moduler Ajan Sistemi v3.x
Son Guncelleme : 2026-06-10
Aktif Branch   : bubu
Bot Durumu     : Calisiyor
```

---

## DOSYA YAPISI (Tam ve Guncel)

```
otoXtra-bot/
│
├── SCHEMA.md
├── README.md
├── PRIVACY.md
├── OPERASYON_REHBERI.md
├── requirements.txt
│
├── config/
│   ├── sources.json
│   ├── settings.json
│   ├── keywords.json
│   ├── scoring.json
│   └── prompts.json
│
├── core/
│   ├── orchestrator.py
│   ├── ai_client.py
│   ├── config_loader.py
│   ├── logger.py
│   ├── helpers.py
│   └── state_manager.py
│
├── agents/
│   ├── agent_fetcher.py
│   ├── agent_scorer.py
│   ├── agent_writer.py
│   ├── agent_image.py
│   └── agent_publisher.py
│
├── platforms/
│   ├── facebook.py
│   └── telegram.py
│
├── queue/
│   └── pipeline.json
│
├── data/
│   ├── posted_news.json
│   ├── telegram_updates_state.json
│   └── telegram_media/
│
├── assets/
│   └── logo.png
│
└── .github/workflows/
    └── bot.yml
```

---

## VERI AKISI

```
bot.yml tetikler
  -> core/orchestrator.py baslar
    -> Telegram priority check (manuel kuyrukta bekleyen varsa once onu paylasir)
    -> Gunluk limit + min interval kontrolu
    -> [1] agents/agent_fetcher.py
    -> 🆕 Otomotiv konu dogrulama (AI - _verify_automotive_relevance)
    -> [2] agents/agent_scorer.py
    -> [3] agents/agent_writer.py
    -> [4] agents/agent_image.py
    -> [5] agents/agent_publisher.py
    -> Telegram basarili/basarisiz bildirimi
```

---

## DOSYA DETAYLARI

### config/ ayarlari

**config/sources.json**
Haber kaynaklarinin RSS adreslerini icerir. 13 aktif kaynak.

**config/settings.json**
Botun genel davranisini kontrol eder.
Bolumler: posting, images, news, ai, duplicate_detection

Not:
- `news.max_article_age_hours`: 24 saat (aktif).
- `news.max_articles_per_source`: kaynak basina max haber (agent_fetcher tarafinda).
- `news.relaxed_max_article_age_hours`: havuz daralinca fallback pencere (72 saat).
- `news.min_candidates_after_time_filter`: bu sayinin altina dusunce relaxed pencere.

**config/keywords.json**
Include/exclude kelime filtresi.
- `include_keywords`: 58 otomotiv terimi (marka, model, teknoloji). **"araç" CIKARILDI** (cok genel, Bim/A101 katalogu geciyordu).
- `exclude_keywords`: kaza, olum, yangin, teror, mahkeme, Nvidia/GPU vb.

**config/scoring.json**
Yayin esikleri:
- `publish_score`: 65
- `slow_day_score`: 50 (gunde 2'den az post atildiysa)

**config/prompts.json**
YZ komutlari:
- `viral_scorer`: 5 kriterli 100 puan sistemi. **🆕 OTOMOTIV DISI = 0 PUAN kurali** (market katalogu, elektronik, beyaz esya vb.)
- `post_writer`: Facebook post yazim promptu (emoji + buyuk harf baslik + samimi ton).

---

### core/ ortak araclar

**core/orchestrator.py (v4.1)**
Ajanlari sirayla calistirir, pipeline akis kontrolu.
- Telegram priority share (manuel kuyruk)
- Hata/skip ayrimi (soft-skip: no_article_found, random_skip, ai_invalid_scale_10)
- Haftalik Telegram raporu (Pazartesi)
- No-share detayli Telegram raporu

**core/ai_client.py**
YZ merkezi katmani.
Fallback: Gemini → Groq → OpenRouter → HuggingFace
Retry/backoff (3 deneme), JSON parse 4 katmanli fallback.

**core/config_loader.py**
Config okuma/yazma. Her config tipine ozel sanitizasyon.

**core/logger.py**
TR saatli log.

**core/helpers.py**
Yardimci fonksiyonlar:
- clean_html, get_turkey_now
- is_similar_title, generate_topic_fingerprint
- is_topic_recently_posted, is_already_posted
- get_posted_news, save_posted_news (30 gunluk temizlik)
- get_last_check_time, save_last_check_time
- shared_variant_cooldown mekanizmasi
- Haftalik istatistik (weekly stats)

**core/state_manager.py**
pipeline.json yonetimi (fetch → score → write → image → publish).

---

### agents/

**agents/agent_fetcher.py (v4.1)**
RSS ceker, filtreler, dedup temizler, trend sinyali.
- Smart cutoff + relaxed fallback (72 saat)
- Posted filtresi (strict → url-only fallback)
- Shared variant cooldown
- Gorsel adayi cikarma
- Full article scrape

**agents/agent_scorer.py (v4.3) 🆕**
AI puanlama + konu dogrulama.
- **YENI: `_verify_automotive_relevance()`** — AI puanlama ONCESI otomotiv konu kontrolu. Market katalogu, elektronik, beyaz esya vb. → score=0 + "non_automotive".
- 3 katmanli matching (sira → tam baslik → fuzzy)
- Coverage kontrolu + force-full-coverage retry
- 10'luk olcek tespiti ve strict retry
- Single rescue (eslesmeyenleri tek tek puanlama)
- Freshness bonus (+10/+7/+4/+1/-4)
- Trend bonus (max +18)
- 24 saat duplicate topic elemesi

**agents/agent_writer.py**
AI ile Facebook postu yazar.
- Kalite kontrol (80-1800 karakter, 3-15 satir, yabanci alfabe engeli)
- AI tamir + fallback metin
- Full text scrape entegrasyonu

**agents/agent_image.py (v5.0)**
Gorsel toplama, secme, watermark, resize.
- DonanimHaber ozel URL varyantlari
- Gurultu eleme (logo/icon/cookie/banner)
- Perceptual hash dedup
- Kalite skorlamasi
- Fallback gorsel (logolu koyu arka plan)

**agents/agent_publisher.py (v4.1)**
Facebook paylasimi.
- 3 deneme: multi-image → single → text-only
- Skora bagli skip (score<80 = %100)
- Telegram bildirimi + shared variant cooldown

---

### platforms/

**platforms/facebook.py (v3.2)**
Graph API v25.0.
- `post_photo()`, `post_text()`, `post_photos()` (unpublished upload + attached_media)
- SHA256 dedup, retry/backoff

**platforms/telegram.py 🆕**
Bildirim + manuel kuyruk.
- `send_message()` — bildirim
- `consume_pending_shareable_content()` — manuel paylasim
- `finalize_consumed_shareable_content()` — temizlik
- `/kuyruk` komutu

---

### data/

**data/posted_news.json** — Paylasim gecmisi (30 gunluk temizlik)
**data/telegram_updates_state.json 🆕** — Manuel kuyruk durumu
**data/telegram_media/ 🆕** — Indirilen gecici gorseller

---

## API KEYS (GitHub Secrets)

```
FB_PAGE_ID          FB_ACCESS_TOKEN
GEMINI_API_KEY      GROQ_API_KEY
OPENROUTER_API_KEY  HF_API_KEY
TELEGRAM_BOT_TOKEN  TELEGRAM_CHAT_ID
```

Kural: Asla kod icine yazilmaz. Sadece GitHub Secrets'ta tutulur.

---

## GUNCEL OZELLIKLER

```
RSS haber cekme                      : Var
Keyword filtresi                     : Var
Zaman filtresi                       : Var
Tekrar kontrolu                      : Var (URL + baslik + konu)
Konu parmak izi                      : Var
Trend dedektoru                      : Var
🆕 Otomotiv konu dogrulama (AI)      : Var
YZ puanlama                          : Var
YZ fallback (4 provider)             : Var
Tazelik bonusu                       : Var
Facebook paylasimi                   : Var (Graph API v25.0)
Coklu gorsel paylasimi               : Var
Gorsel cekme/uretme                  : Var
Logo watermark                       : Var
Gunluk limit                         : Var
Sakin gun modu                       : Var
Rastgele bekleme/skip                : Var
Gecmis temizlik                      : Var (30 gun)
🆕 Telegram bildirimi                : Var (basarili/basarisiz/haftalik rapor)
🆕 Telegram manuel kuyruk            : Var (gorsel+aciklama ile oncelikli paylasim)
Instagram paylasimi                  : Yok
Twitter/X paylasimi                  : Yok
```

---

## ALTIN KURALLAR

```
Config degisikligi  -> JSON dosyasinda yap
Kod degisikligi     -> YZ'den tam dosya al, komple degistir
pipeline.json       -> Elle dokunma
posted_news.json    -> Elle dokunma
API key             -> Asla koda yazma
```

---

**Versiyon: 3.2**
**Son Guncelleme: 2026-06-10**
Bu dosya degistiginde versiyon ve tarihi guncelle.