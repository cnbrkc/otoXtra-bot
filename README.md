

# 🚗 otoXtra Bot

Otomotiv haberlerini RSS kaynaklarından çekip, yapay zeka ile puanlayıp, Facebook/Threads/Instagram'a otomatik paylaşan bot.

GitHub Actions ile Türkiye saatine göre 08:00–22:00 arasında akıllı zamanlamayla günde 10 kez tetiklenir. Günde 3-7 kaliteli haber paylaşır.

> 📌 **Projenin detaylı haritası için:** [SCHEMA.md](SCHEMA.md) dosyasını okuyun.
> Bir şeyi değiştirmeden önce oraya bakın.

---

## ⚡ Hızlı Bakış

```
Gün içinde planlı tetikleme ile:  RSS tara → YZ ile puanla → Metin yaz → Görsel hazırla → Facebook/Threads/IG Story'e paylaş
```

**Özellikler:**
- 4 farklı YZ servisi (Gemini → Groq → OpenRouter → HuggingFace)
- Akıllı tekrar/benzerlik kontrolü (topic fingerprint)
- Logo watermark ekleme (özelleştirilebilir pozisyon/boyut/opaklık)
- Anti-bot stratejisi (rastgele gecikme, puan bazlı atlama)
- Test modu desteği (tüm platformlar veya tek tek)
- **YENİ**: Instagram Story kart üretimi (IMAGE_TEST_MODE)
- **YENİ**: Threads carousel (çoklu görsel) desteği
- **YENİ**: FxTwitter API ile Nitter/Twitter görsel çekimi
- **YENİ**: DuckDuckGo görsel arama fallback

---

## 📋 Kurulum

### 1. Repo'yu oluştur

GitHub'da yeni **private** repo oluştur, dosyaları yükle.

> ⚠️ `src/__init__.py` dosyası **oluşturma** — gerek yok, sorun çıkarır.

### 2. Logo yükle

`assets/logo.png` — Şeffaf PNG, tercihen 500x500px.

### 3. API Key'leri al (hepsi ücretsiz)

| Servis | Nereden Alınır | Ne İçin |
|--------|---------------|---------|
| Gemini | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) | Ana YZ servisi |
| Groq | [console.groq.com/keys](https://console.groq.com/keys) | Yedek YZ |
| OpenRouter | https://openrouter.ai/keys | Yedek YZ |
| HuggingFace | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) | Yedek YZ |
| Facebook Token | [developers.facebook.com/tools/explorer](https://developers.facebook.com/tools/explorer/) | Sayfa paylaşımı |
| Facebook Page ID | Sayfa → Hakkında → Sayfa Kimliği | Hangi sayfaya paylaşılacak |
| Threads User ID + Token | [Threads Developer Portal](https://developers.facebook.com/docs/threads) | Threads paylaşımı |
| Telegram Bot Token | [@BotFather](https://t.me/BotFather) | Bildirimler için |
| Telegram Chat ID | [Bu bot](https://t.me/getmyid_bot) | Hangi sohbete bildirim |

#### Facebook Token alma özeti:
1. [developers.facebook.com](https://developers.facebook.com) → uygulama oluştur
2. Graph API Explorer → sayfa token'ı al (kısa süreli)
3. Kısa token'ı uzun süreli token'a çevir:
```
https://graph.facebook.com/v19.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=KISA_TOKEN
```
4. Dönen `access_token` değeri = 60 günlük token

> ⚠️ Token 60 günde bir yenilenmeli. Takviminize hatırlatma koyun.

### 4. GitHub Secrets'a kaydet

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret Adı | Değer |
|------------|-------|
| `GEMINI_API_KEY` | Gemini API key |
| `GROQ_API_KEY` | Groq API key |
| `HF_API_KEY` | HuggingFace token |
| `FB_ACCESS_TOKEN` | Facebook uzun süreli token |
| `FB_PAGE_ID` | Facebook sayfa ID |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `THREADS_USER_ID` | Threads kullanıcı ID |
| `THREADS_ACCESS_TOKEN` | Threads erişim tokeni |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram sohbet/grup ID |
| `IMGBB_API_KEY` | (Opsiyonel) ImgBB upload API key |
| `IG_USER_ID` | Instagram Business hesap ID (Graph API) |
| `IG_ACCESS_TOKEN` | Instagram uzun süreli erişim tokeni |

> ⚠️ **Instagram Story için** `IG_USER_ID`, `IG_ACCESS_TOKEN` ve `IMGBB_API_KEY` **zorunludur**.

### 5. Workflow izinlerini aç

Repo → **Settings** → **Actions** → **General** → en altta:
**"Read and write permissions"** seç → **Save**

### 6. Test et

Repo → **Actions** → **otoXtra News Bot** → **Run workflow**

| Simge | Anlam |
|-------|-------|
| 🟡 | Çalışıyor |
| ✅ | Başarılı |
| ❌ | Hata — tıkla, log'u oku |

---

## ⚙️ Ayar Değişiklikleri

Config dosyaları (`config/` klasörü) kod bilmeden düzenlenebilir:

| Ne Yapmak İstiyorsun | Hangi Dosya | Ne Değiştir |
|---------------------|-------------|-------------|
| Günlük post sayısı | `settings.json` | `max_daily_posts` |
| Puan eşiği | `scoring.json` | `publish_score` |
| Kaynak ekle/çıkar | `sources.json` | Feed ekle/sil |
| Kelime engelle | `keywords.json` | `exclude_keywords` listesi |
| Yazım üslubu | `prompts.json` | `post_writer` promptu |
| Threads modu | `settings.json` | `threads.mode` (text_only/text_and_image/carousel) |
| Logo ayarları | `settings.json` | `images.logo_position/size/opacity` |
| Görsel test modu | ENV | `IMAGE_TEST_MODE=true` (kart üretir) |

> Kod değişikliği gerekirse → [SCHEMA.md](SCHEMA.md) dosyasını YZ'ye yapıştırıp sorun.

---

## ❓ Sık Sorunlar

| Sorun | Çözüm |
|-------|-------|
| Facebook'a paylaşmıyor | Token süresi dolmuş → yenile |
| Hiç haber paylaşmıyor | `scoring.json` → `publish_score` düşür (35→25) |
| Çok fazla paylaşıyor | `settings.json` → `max_daily_posts` düşür |
| Actions çalışmıyor | Settings → Actions → "Read and write permissions" |
| API version hatası | `platforms/facebook.py` içinde Graph API versiyonunu kontrol et (v25.0) |
| Dakika limiti doldu | Repo'yu public yap (secret'lar güvende kalır) |
| Görsel gelmiyor | agent_image.py v7.0 FxTwitter API kullanıyor, Nitter boş dönüyor |
| İngilizce metin geldi | agent_writer.py v5.2 otomatik engelliyor, fallback devreye giriyor |
| Threads görsel yüklenmiyor | 5 aşamalı fallback zinciri var (Catbox → 0x0 → Telegraph → ImgBB → text-only) |

Hata log'unu okumak için: **Actions** → başarısız çalışma → **log**

---

## 📂 Dosya Yapısı

```
otoXtra-bot/
├── config/           → Ayarlar (kendin düzenle)
├── data/             → Bot verileri (dokunma!)
├── assets/           → Logo + Fontlar
├── core/             → Ana akış ve yardımcılar
├── agents/           → Ajan modülleri (fetch/score/write/image/publish)
├── platforms/        → Facebook/Threads/Telegram API katmanı
├── .github/workflows → Zamanlayıcı
└── SCHEMA.md         → Proje haritası
```

Detaylar için → [SCHEMA.md](SCHEMA.md)

