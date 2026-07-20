# otoXtra Bot - Operasyon Rehberi (Kod Bilmeyenler Icin)

Bu rehberin amaci:
- Bot post atmadiysa nedenini bulmak
- Hata oldugunda panik yapmadan adim adim cozumlemek
- Temel kontrolu teknik bilgi olmadan yapabilmek

## 1) Bot calisiyor mu? (Ilk bakis)
1. GitHub repo icinde **Actions** sekmesine gir.
2. Son calisan workflow adina bak:
   - `otoXtra Bot` (asil calisma)
3. Sonuc:
   - Yesil: teknik olarak calismis
   - Kirmizi: hata var, asagidaki adimlara gec

## 2) En sik 6 sorun ve hizli cozum

### Sorun 1: Hic post atilmamis
Kontrol:
1. Actions > son `otoXtra Bot` run > loglara gir
2. `fetch` asamasinda `No article found` var mi bak

Cozum:
1. `config/keywords.json` cok dar olabilir, biraz gevset
2. `config/settings.json` icinde `max_article_age_hours` cok dusuk olabilir, yukselt
3. Feed kaynaklari kapali olabilir (`enabled: false`), ac
4. `scoring.json` → `publish_score` degeri cok yuksek olabilir (35→25 dene)

### Sorun 2: Facebook hatasi (token/page)
Kontrol:
1. Logda `FB_PAGE_ID` veya `FB_ACCESS_TOKEN` eksik yaziyor mu?
2. Logda Facebook API error code gorunuyor mu?

Cozum:
1. GitHub > Settings > Secrets and variables > Actions
2. Su secretlar dolu mu kontrol et:
   - `FB_PAGE_ID`
   - `FB_ACCESS_TOKEN`
3. Access token suresi dolduysa yenile (60 gunde bir!)

### Sorun 3: AI uretimi bos donuyor
Kontrol:
1. Loglarda `ai_client.ask_ai` retry/hata satirlari var mi?
2. `GEMINI_API_KEY` veya diger AI keyleri eksik mi?

Cozum:
1. Secretlari tekrar kontrol et:
   - `GEMINI_API_KEY`
   - `GROQ_API_KEY`
   - `OPENROUTER_API_KEY`
   - `HF_API_KEY`
2. Gecici servis sorunu olabilir, bir sonraki schedule'i bekle
3. ai_client.py otomatik yedek modellere geciyor (Gemini→Groq→OpenRouter→HF)

### Sorun 4: Threads paylasim basarisiz
Kontrol:
1. Logda `Threads paylasimi hata` var mi?
2. `THREADS_USER_ID` ve `THREADS_ACCESS_TOKEN` dogru mu?

Cozum:
1. GitHub Secrets'a ekle:
   - `THREADS_USER_ID`
   - `THREADS_ACCESS_TOKEN`
2. `settings.json` → `threads.enabled: true` oldugundan emin ol
3. Threads mode'u kontrol et: `text_only` / `text_and_image` / `carousel`

### Sorun 5: Gorsel bulunamadi / Nitter bos dondu
Kontrol:
1. Logda `Nitter bos` veya `FxTwitter API` hatalari var mi?
2. Twitter/X haberlerinde gorsel yok mu?

Cozum:
1. agent_image.py v8.8 otomatik fallback zinciri kullanir:
   - FxTwitter API (birincil)
   - x.com HTML scrape (ikincil)
   - DuckDuckGo arama (ucuncul)
2. Profil fotosu URL'leri otomatik filtrelenir
3. Gorsel yoksa text-only paylasim yapilir

### Sorun 6: Ingilizce metin geldi / Kalite kontrol basarisiz
Kontrol:
1. Logda `Quality check FAIL: english_ratio_high` var mi?
2. Post metni Ingilizce kelimeler iceriyor mu?

Cozum:
1. agent_writer.py v5.2 otomatik onarim dener
2. Basarisiz olursa fallback post kullanilir (baslik + ozet)
3. Prompt'u daha net Turkce talimatlarla guncelle (`prompts.json`)

## 3) Her degisiklikten sonra zorunlu kontrol listesi
1. Commit attiktan sonra workflow yesil mi?
2. En az bir post atildi mi / neden atilmadigi logda net mi?
3. README ve SCHEMA ile gercek davranis halen uyumlu mu?

## 4) Acil durum (her sey bozulduysa)
1. Son calisan yesil commit'i bul
2. Ona geri don (revert)
3. Sonra degisiklikleri tek tek, kucuk parcalar halinde tekrar uygula

## 5) Secret listesi (minimum)
- `FB_PAGE_ID`
- `FB_ACCESS_TOKEN`
- `GEMINI_API_KEY`
- `THREADS_USER_ID` (Threads aktifse)
- `THREADS_ACCESS_TOKEN` (Threads aktifse)
- `TELEGRAM_BOT_TOKEN` (Bildirimler icin)
- `TELEGRAM_CHAT_ID` (Bildirimler icin)

## 6) Platform Test Modlari
| Test Tipi | ENV Degiskeni | Ne Yapar |
|-----------|---------------|----------|
| Tum platformlar test | `TUM_PLATFORMLAR_TEST=true` | Hicbir platforma gercek paylasim yapmaz |
| Sadece Facebook test | `SADECE_FACEBOOK_TEST=true` | Facebook test modu, digerleri pasif |
| Sadece Threads test | `SADECE_THREADS_TEST=true` | Threads test modu, digerleri pasif |
| Gorsel test modu | `IMAGE_TEST_MODE=true` | Instagram Story karti uretir, Telegram'a gonderir |

## 7) Not
- Botun "hic post atmamasi" her zaman bug degildir.
- Bazen filtreler geregi uygun haber cikmaz.
- Onemli olan: loglarin nedenini acik yazmasi.
- Haftalik rapor her Pazartesi Telegram'a gonderilir.
