# otoXtra BOT - ANA SEMA v3.3

> BU DOSYA NEDIR?
> Projenin tam haritasi. Her yeni sohbette YZ'ye SADECE BU DOSYAYI yapistir.
> YZ bunu okuyunca projeyi tanir, senden neye ihtiyaci oldugunu soyler.

---

## PROJE DURUMU

```txt
Proje Adi      : otoXtra Facebook Haber Botu
Mimari         : Moduler Ajan Sistemi v3.x
Son Guncelleme : 2026-04-15
Aktif Branch   : main
Bot Durumu     : Calisiyor
```

## DOSYA YAPISI (Tam ve Guncel)

```txt
otoXtra-bot/
├── SCHEMA.md
├── README.md
├── requirements.txt
├── config/
│   ├── sources.json
│   ├── settings.json
│   ├── keywords.json
│   ├── scoring.json
│   └── prompts.json
├── core/
│   ├── orchestrator.py
│   ├── ai_client.py
│   ├── config_loader.py
│   ├── logger.py
│   ├── helpers.py
│   └── state_manager.py
├── agents/
│   ├── agent_fetcher.py
│   ├── agent_scorer.py
│   ├── agent_writer.py
│   ├── agent_image.py
│   └── agent_publisher.py     <-- v4.8 (Threads Gorsel Fallback)
├── platforms/
│   ├── facebook.py
│   ├── threads.py              <-- v5.0 (Coklu Gorsel Fallback)
│   └── telegram.py
├── queue/
│   └── pipeline.json
├── data/
│   └── posted_news.json
├── assets/
│   └── logo.png
└── .github/workflows/
    └── bot.yml                 <-- IMGBB_API_KEY eklendi
```

## THREADS GORSEL FALLBACK ZINCIRI

```
post_with_image(message, image_path, article)
  │
  ├─ ADIM 1: Orijinal URL (article'dan) ← EN HIZLI, upload gerektirmez!
  │   └─ article.image_candidates, image_url, rss_image_url
  │   └─ Basarili → BITIR ✅
  │   └─ Basarisiz → ADIM 2
  │
  ├─ ADIM 2: Catbox.moe upload ← Ucretsiz, API key YOK
  │   └─ Basarili → BITIR ✅
  │   └─ Basarisiz → ADIM 3
  │
  ├─ ADIM 3: 0x0.st upload ← Ucretsiz, API key YOK
  │   └─ Basarili → BITIR ✅
  │   └─ Basarisiz → ADIM 4
  │
  ├─ ADIM 4: Telegraph upload ← Ucretsiz, API key YOK
  │   └─ Basarili → BITIR ✅
  │   └─ Basarisiz → ADIM 5
  │
  ├─ ADIM 5: ImgBB upload ← Ucretsiz, IMGBB_API_KEY opsiyonel
  │   └─ Basarili → BITIR ✅
  │   └─ Basarisiz → ADIM 6
  │
  └─ ADIM 6: Metin-only fallback ← SON CARE
      └─ post_text(message)
```

## DOSYA DETAYLARI

### platforms/threads.py (v5.0)

Fonksiyonlar:
- `post_text(message)` - Metin paylasimi (500 kar. otomatik kesme)
- `post_image(message, image_url)` - Public URL ile gorsel (dusuk seviye)
- `post_with_image(message, image_path, article)` - ANA FONKSIYON, tam fallback zinciri
- `post_carousel(message, image_paths, article)` - Coklu gorsel (2-10)

Upload servisleri (hepsi ucretsiz):
- `_upload_catbox(image_path)` - Catbox.moe (key yok, 200MB limit)
- `_upload_0x0(image_path)` - 0x0.st (key yok, 512MB limit)
- `_upload_telegraph(image_path)` - Telegraph (key yok, 5MB limit)
- `_upload_imgbb(image_path)` - ImgBB (IMGBB_API_KEY opsiyonel, 32MB limit)

Yardimci fonksiyonlar:
- `_extract_original_urls(article)` - Article'dan public URL cikarir
- `_resolve_public_url(image_path, article, index)` - Tek gorsel icin URL cozumler
- `_truncate_for_threads(text)` - 500 karakter limiti

### agents/agent_publisher.py (v4.8)

Threads bolumu degisiklikleri:
- `post_image(text, local_path)` → `post_with_image(text, local_path, article=article)`
- `article` dict artik Threads'a gecirilir (orijinal URL'ler icin)
- Carousel modu: `post_carousel(text, image_paths, article=article)`
- 3 mod: text_only | text_and_image | text_image_carousel

### config/settings.json - threads bolumu

```json
"threads": {
    "enabled": true,
    "mode": "text_and_image"
}
```

mode degerleri:
- `text_only` - Sadece metin
- `text_and_image` - Metin + tek gorsel (fallback zinciri)
- `text_image_carousel` - Metin + coklu gorsel (2-10)

### .github/workflows/bot.yml

Eklendi: `IMGBB_API_KEY: ${{ secrets.IMGBB_API_KEY }}` (opsiyonel)

## API KEYS (GitHub Secrets)

```txt
FB_PAGE_ID               # Zorunlu
FB_ACCESS_TOKEN           # Zorunlu
GEMINI_API_KEY            # Zorunlu
GROQ_API_KEY              # Zorunlu
OPENROUTER_API_KEY        # Zorunlu
HF_API_KEY                # Zorunlu
THREADS_USER_ID           # Zorunlu
THREADS_ACCESS_TOKEN      # Zorunlu
IMGBB_API_KEY             # OPSIYONEL (sadece ImgBB fallback icin)
TELEGRAM_BOT_TOKEN        # Zorunlu
TELEGRAM_CHAT_ID          # Zorunlu
```

### IMGBB_API_KEY Nasil Alinir? (Opsiyonel - diger servisler key gerektirmez)

1. https://api.imgbb.com/ → ucretsiz kaydol
2. API key al
3. GitHub Secrets'a ekle: IMGBB_API_KEY

NOT: ImgBB olmadan da bot calisir! Catbox, 0x0.st ve Telegraph key gerektirmez.

## GUNCEL OZELLIKLER

```txt
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
Threads metin paylasimi         : Var
Threads gorsel paylasimi        : Var (FALLBACK ZINCIRI!)
  - Orijinal URL deneme         : Var (article'dan)
  - Catbox.moe upload           : Var (ucretsiz, key yok)
  - 0x0.st upload               : Var (ucretsiz, key yok)
  - Telegraph upload            : Var (ucretsiz, key yok)
  - ImgBB upload                : Var (opsiyonel key)
  - Metin fallback              : Var
Threads carousel paylasimi      : Var (2-10 gorsel)
Threads metin 500 kar. limiti   : Var (otomatik kesme)
Instagram paylasimi             : Yok
Twitter/X paylasimi             : Yok
```

## ALTIN KURALLAR

```txt
Config degisikligi  -> JSON dosyasinda yap
Kod degisikligi     -> YZ'den tam dosya al, komple degistir
pipeline.json       -> Elle dokunma
posted_news.json    -> Elle dokunma
API key             -> Asla koda yazma
Upload servisleri   -> Catbox/0x0/Telegraph key gerektirmez
ImgBB               -> Opsiyonel, sadece ekstra fallback
```

Versiyon: 3.3
