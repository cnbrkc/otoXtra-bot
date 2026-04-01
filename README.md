

# 🚗 otoXtra Bot

Otomotiv haberlerini RSS kaynaklarından çekip, yapay zeka ile puanlayıp, Facebook sayfasına otomatik paylaşan bot.

GitHub Actions ile 2 saatte bir çalışır. Günde 3-7 kaliteli haber paylaşır.

> 📌 **Projenin detaylı haritası için:** [SCHEMA.md](SCHEMA.md) dosyasını okuyun.
> Bir şeyi değiştirmeden önce oraya bakın.

---

## ⚡ Hızlı Bakış

```
Her 2 saatte bir:
  RSS tara → YZ ile puanla → Metin yaz → Görsel hazırla → Facebook'a paylaş
```

**Özellikler:**
- 4 farklı YZ servisi (Gemini → Groq → OpenRouter → HuggingFace)
- Akıllı tekrar/benzerlik kontrolü
- Logo watermark ekleme
- Anti-bot stratejisi (rastgele gecikme, atlama)
- Test modu desteği

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
| HuggingFace | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) | Yedek YZ |
| Facebook Token | [developers.facebook.com/tools/explorer](https://developers.facebook.com/tools/explorer/) | Sayfa paylaşımı |
| Facebook Page ID | Sayfa → Hakkında → Sayfa Kimliği | Hangi sayfaya paylaşılacak |

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

> Kod değişikliği gerekirse → [SCHEMA.md](SCHEMA.md) dosyasını YZ'ye yapıştırıp sorun.

---

## ❓ Sık Sorunlar

| Sorun | Çözüm |
|-------|-------|
| Facebook'a paylaşmıyor | Token süresi dolmuş → yenile |
| Hiç haber paylaşmıyor | `scoring.json` → `publish_score` düşür (65→50) |
| Çok fazla paylaşıyor | `settings.json` → `max_daily_posts` düşür |
| Actions çalışmıyor | Settings → Actions → "Read and write permissions" |
| API version hatası | `facebook_poster.py`'de `v19.0` → güncel versiyon |
| Dakika limiti doldu | Repo'yu public yap (secret'lar güvende kalır) |

Hata log'unu okumak için: **Actions** → başarısız çalışma → **log**

---

## 📂 Dosya Yapısı

```
otoXtra-bot/
├── config/           → Ayarlar (kendin düzenle)
├── data/             → Bot verileri (dokunma!)
├── assets/           → Logo
├── src/              → Kod (YZ'ye sor)
├── .github/workflows → Zamanlayıcı
└── SCHEMA.md         → Proje haritası
```

Detaylar için → [SCHEMA.md](SCHEMA.md)
