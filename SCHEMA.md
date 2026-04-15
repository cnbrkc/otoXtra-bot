# otoXtra BOT - ANA SEMA v3.1

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

```txt
Proje Adi      : otoXtra Facebook Haber Botu
Mimari         : Moduler Ajan Sistemi v3.x
Son Guncelleme : 2026-04-15
Aktif Branch   : main
Bot Durumu     : Calisiyor
DOSYA YAPISI (Tam ve Guncel)
txt

otoXtra-bot/
│
├── SCHEMA.md
├── README.md
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
│   └── facebook.py
│
├── queue/
│   └── pipeline.json
│
├── data/
│   └── posted_news.json
│
├── assets/
│   └── logo.png
│
└── .github/workflows/
    └── bot.yml
VERI AKISI
txt

bot.yml tetikler
  -> core/orchestrator.py baslar
    -> [1] agents/agent_fetcher.py
    -> [2] agents/agent_scorer.py
    -> [3] agents/agent_writer.py
    -> [4] agents/agent_image.py
    -> [5] agents/agent_publisher.py
DOSYA DETAYLARI
config/ ayarlari
config/sources.json
Haber kaynaklarinin RSS adreslerini icerir.

config/settings.json
Botun genel davranisini kontrol eder.
Bolumler:

posting
images
news
ai
Not:

news.max_article_age_hours aktif kullanilir.
news.max_articles_per_source aktif kullanilir (agent_fetcher tarafinda uygulanir).
config/keywords.json
Include/exclude kelime filtresi.

config/scoring.json
Yayin esikleri:

publish_score
slow_day_score
config/prompts.json
YZ komutlari:

viral_scorer
post_writer
core/ ortak araclar
core/orchestrator.py
Ajanlari sirayla calistirir, pipeline akis kontrolunu yapar.

core/ai_client.py
YZ cagrilarinin tek merkezi katmani.
Provider fallback sirasi:

Gemini
Groq
OpenRouter
HuggingFace
Ek ozellikler:

Retry/backoff
Provider bazli hata loglama
JSON parse fallback yardimi
core/config_loader.py
Config dosyalarini okur/yazar.

core/logger.py
Zaman damgali log yazar.

core/helpers.py
Temel yardimci fonksiyonlar:

clean_html
get_turkey_now
is_similar_title
generate_topic_fingerprint
is_topic_already_posted
is_already_posted (URL + baslik + konu benzerligi)
get_posted_news
save_posted_news (30 gunluk temizlik)
get_last_check_time (None/bozuk/eski/gelecek durumlarina korumali)
save_last_check_time
get_today_post_count
core/state_manager.py
queue/pipeline.json yonetimi:

init_pipeline
get_stage
set_stage
get_status
is_stage_done
agents/ bagimsiz ajanlar
agents/agent_fetcher.py
RSS ceker, filtreler, tekrar/duplikasyon temizler, trend sinyali ekler.
Onemli:

max_articles_per_source uygulanir.
last_check_time korumali sekilde kullanilir.
agents/agent_scorer.py
Haberleri YZ ile puanlar, threshold ustu en iyi adayi secer.
YZ parse sonucu dict/list farkina karsi korumali calisir.

agents/agent_writer.py
Secilen haberden Facebook metni uretir.
Onemli:

AI cagrilari core.ai_client.py uzerinden yapilir.
Kalite kontrol + AI ile tamir + fallback metin akisi vardir.
Top-level init_pipeline importu yoktur (sadece test blogunda lokal import).
agents/agent_image.py
Gorsel toplar/uretir, yeniden boyutlar, watermark uygular.

agents/agent_publisher.py
Facebook paylasimini yapar.
Onemli:

DRY_RUN / RANDOM_DELAY / RANDOM_SKIP env kontrolleri.
PERSIST_STATE=false ise posted kaydi yazmaz.
Top-level init_pipeline importu yoktur (sadece test blogunda lokal import).
platforms/
platforms/facebook.py
Sadece Facebook Graph API cagrilarini yapar.
Fonksiyonlar:

post_photo(image_path, message)
post_text(message)
Varsa coklu gorsel fonksiyonlari (post_photos / post_multi_photo / post_album)
API:

Graph API v25.0
data/
data/posted_news.json
Paylasilan haber gecmisi.
Minimum guvenli format:

JSON

{"posts": [], "daily_counts": {}, "last_check_time": null}
queue/
queue/pipeline.json
Ajanlar arasi veri tasima dosyasi.
Elle duzenlenmez.

.github/workflows/bot.yml
Tetiklenme:

Gun icinde coklu saatlerde cron
TR saatleri:

08:00, 09:00, 10:00, 11:00, 13:00, 15:00, 17:00, 19:00, 20:00, 21:00, 22:00
Not:

workflow_dispatch icin dry_run default degeri false.
API KEYS (GitHub Secrets)
txt

FB_PAGE_ID
FB_ACCESS_TOKEN
GEMINI_API_KEY
GROQ_API_KEY
OPENROUTER_API_KEY
HF_API_KEY
Kural:

Asla kod icine yazilmaz.
Sadece GitHub Secrets'ta tutulur.
GUNCEL OZELLIKLER
txt

RSS haber cekme                 : Var
Keyword filtresi                : Var
Zaman filtresi                  : Var
Tekrar kontrolu                 : Var (URL + baslik + konu)
Konu parmak izi                 : Var
Trend dedektoru                 : Var
YZ puanlama                     : Var
YZ fallback (4 provider)        : Var
Tazelik bonusu                  : Var
Facebook paylasimi              : Var (Graph API v25.0)
Gorsel cekme/uretme             : Var
Logo watermark                  : Var
Gunluk limit                    : Var
Sakin gun modu                  : Var
Rastgele bekleme/skip           : Var
Gecmis temizlik                 : Var (30 gun)
Instagram paylasimi             : Yok
Telegram bildirimi              : Yok
Twitter/X paylasimi             : Yok
ALTIN KURALLAR
txt

Config degisikligi  -> JSON dosyasinda yap
Kod degisikligi     -> YZ'den tam dosya al, komple degistir
pipeline.json       -> Elle dokunma
posted_news.json    -> Elle dokunma
API key             -> Asla koda yazma
Versiyon: 3.1
Bu dosya degistiginde versiyon ve tarihi guncelle.

