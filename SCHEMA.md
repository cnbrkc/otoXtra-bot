

# 🗺️ otoXtra BOT — PROJE ŞEMASI v2.0

> **Bu dosya projenin HARİTASIDIR.**
> Hangi dosya ne iş yapar, nerede durur, ne zaman değiştirilir — hepsi burada.
>
> 🔑 **Ne zaman kullanırsın?**
> - Bir şeyi değiştirmek istediğinde → önce buraya bak
> - YZ'ye (ChatGPT/Claude) soru soracaksan → bu dosyayı yapıştır
> - "Hangi dosyayı düzenlemem lazım?" diye merak ediyorsan → buraya bak
>
> 📅 **Son güncelleme:** Büyük Temizlik Operasyonu v1.0 sonrası
> **Versiyon:** 2.0

---

## 📂 DOSYA YAPISI (Tam Liste)

```
otoXtra-bot/
├── 📄 README.md                    ← Kurulum rehberi
├── 📄 SCHEMA.md                    ← PROJE HARİTASI (bu dosya)
├── 📁 config/                      ← SENİN AYARLARIN
│   ├── sources.json                   → Haber kaynakları (RSS linkleri)
│   ├── settings.json                  → Genel ayarlar (limit, zamanlama, görsel)
│   ├── keywords.json                  → Dahil/hariç kelimeler
│   ├── scoring.json                   → Puanlama eşikleri
│   └── prompts.json                   → YZ'ye gönderilen talimatlar (2 prompt)
├── 📁 data/                        ← BOT KENDİSİ GÜNCELLER
│   └── posted_news.json               → Paylaşılmış haberlerin kaydı
├── 📁 assets/                      ← LOGON
│   └── logo.png                       → Görsellere eklenen watermark
├── 📁 src/                         ← KOD DOSYALARI
│   ├── main.py                        → Ana kontrol (orkestrasyon)
│   ├── news_fetcher.py                → RSS'ten haber çekme + filtreleme
│   ├── content_filter.py              → YZ ile puanlama
│   ├── ai_processor.py                → YZ ile metin üretimi
│   ├── image_handler.py               → Görsel çekme/üretme/logo ekleme
│   ├── facebook_poster.py             → Facebook'a paylaşım + kayıt
│   └── utils.py                       → Yardımcı fonksiyonlar
├── 📁 .github/workflows/          ← ZAMANLAYICI
│   └── bot.yml                        → 2 saatte bir çalıştır
└── 📄 requirements.txt            ← Python bağımlılıkları
```

---

## 📁 config/ — SENİN AYARLARIN (İstediğin zaman değiştir)

Bu klasördeki dosyalar botun AYARLARIDIR. Kod bilmene gerek yok,
sadece sayıları ve kelimeleri değiştirirsin.

| Dosya | Ne İşe Yarar | Örnek Değişiklik |
|-------|-------------|-----------------|
| `sources.json` | Haber kaynakları listesi (RSS linkleri) | Yeni haber sitesi ekle/çıkar |
| `settings.json` | Genel ayarlar (limit, zamanlama, görsel) | Günlük post sayısını değiştir |
| `keywords.json` | Dahil/hariç kelimeler | "Tesla" haberlerini hariç tut |
| `scoring.json` | Puanlama eşikleri | Minimum puanı 65'ten 50'ye düşür |
| `prompts.json` | YZ'ye gönderilen talimatlar | Yazım üslubunu değiştir |

---

### 📄 config/sources.json — Haber Kaynakları

Haber sitelerinin RSS adreslerini içerir.
Her kaynak için şu bilgiler var:

```
name          → Kaynak adı (log'larda görünür)
url           → RSS feed adresi
category      → Kategori ("otomotiv", "teknoloji" vb.)
priority      → Öncelik ("high" / "medium" / "low")
                ⚠️ Benzer haberlerden hangisinin seçileceğini belirler
                   (news_fetcher.py → remove_duplicates() kullanır)
language      → Dil ("tr")
can_scrape_image → true/false
                   true = Bu siteden haber görseli çekilebilir
                   false = Görsel çekme, yedek görsel (lacivert+logo) kullan
enabled       → true/false — Kaynağı geçici kapatmak için false yap
```

> **💡 İPUCU:** Yeni kaynak eklerken mevcut bir kaynağı kopyala,
> URL ve name'i değiştir. En kolay yol bu.

---

### 📄 config/settings.json — Genel Ayarlar

Botun davranışını kontrol eden ana ayar dosyası.
**4 bölümden** oluşur:

```
📌 posting (Paylaşım ayarları):
  max_daily_posts           → Günlük maksimum post sayısı (varsayılan: 9)
  random_delay_max_minutes  → Rastgele bekleme süresi (varsayılan: 8)
                               ⚠️ Artırırsan GitHub Actions dakikası daha çok harcanır!
  min_post_interval_hours   → İki post arası minimum süre (varsayılan: 1)
                               ⚠️ 0 yaparsan art arda spam riski olur!
  skip_probability_percent  → Rastgele atlama olasılığı % (varsayılan: 10)
  max_posts_per_run         → Her çalışmada max post (varsayılan: 1)

📌 images (Görsel ayarları):
  add_logo                  → Logo eklensin mi (true/false)
  logo_position             → Logo konumu ("bottom_right")
  logo_opacity              → Logo saydamlığı (0.0 - 1.0, varsayılan: 0.7)
  logo_size_percent         → Logo boyutu % (varsayılan: 15)
  feed_image_width          → Görsel genişliği piksel (varsayılan: 1200)
  feed_image_height         → Görsel yüksekliği piksel (varsayılan: 630)

📌 news (Haber ayarları):
  max_article_age_hours     → Maksimum haber yaşı saat (varsayılan: 12)

📌 ai (Yapay zeka ayarları):
  timeout_seconds           → YZ isteği zaman aşımı (varsayılan: 30)
  max_retries               → Başarısız olursa kaç kez dene (varsayılan: 2)
```

---

### 📄 config/keywords.json — Anahtar Kelimeler

İki liste içerir:

```
include_keywords → Bu kelimeler geçen haberler ÖNCELİKLİ
                   Örnek: "elektrikli", "SUV", "lansman", "fiyat"
                   ⚠️ Listede OLMAYAN kelime geçen haberler elenir!

exclude_keywords → Bu kelimeler geçen haberler ATILIR
                   Örnek: "kaza", "ölüm", "mahkeme", "yangın"
                   ⚠️ Kısmi eşleşme: "kaza" kelimesi "kazası" içinde de eşleşir
                   ⚠️ Ama "çarpıştı" ile "çarpışma" FARKLI kelimeler sayılır
                      (kelime kökü analizi yok, her form ayrı yazılmalı)
```

> **💡 İPUCU:** Aynı kelimeyi 2 kez yazma. Bir kez yeterli.

---

### 📄 config/scoring.json — Puanlama Eşikleri

Sadece **2 sayı** içerir — sade ve net:

```json
{
  "thresholds": {
    "publish_score": 65,
    "slow_day_score": 50
  }
}
```

```
publish_score   → Normal günlerde minimum puan (varsayılan: 65)
                  Bu puanın altındaki haberler paylaşılmaz

slow_day_score  → Sakin günlerde minimum puan (varsayılan: 50)
                  Bugün 2'den az post yapılmışsa bu eşik kullanılır
                  Böylece sakin günlerde de içerik çıkar
```

> **⚠️ Puanlama KRİTERLERİ (bilgi değeri, paylaşılabilirlik vb.) nerede?**
> `prompts.json` → `viral_scorer` promptunun içinde yazılı.
> YZ o promptu okuyarak puanlıyor. Kriterleri değiştirmek istersen
> `prompts.json` dosyasındaki `viral_scorer` promptunu düzenle.

---

### 📄 config/prompts.json — YZ Talimatları

Yapay zekaya gönderilen komutları içerir. **2 prompt** var:

```
viral_scorer  → Haberi puanlama talimatı
                content_filter.py tarafından kullanılır
                İçinde 6 kriter var (bilgi değeri, paylaşılabilirlik,
                etki alanı, özgünlük, duygusal etki, güncellik)
                Her kriter 1-100 arası puan, sonuç ortalaması alınır

post_writer   → Facebook metni yazma talimatı
                ai_processor.py → generate_post_text() tarafından kullanılır
                Üslup, emoji kullanımı, uzunluk gibi kurallar burada
```

> **✍️ Üslup değiştirmek istersen** → `post_writer` promptunu düzenle!
> **📊 Puanlama kriterlerini değiştirmek istersen** → `viral_scorer` promptunu düzenle!

---

## 📁 data/ — BOT KENDİSİ GÜNCELLER (DOKUNMA!)

| Dosya | Ne İşe Yarar | Dikkat |
|-------|-------------|--------|
| `posted_news.json` | Paylaşılmış haberlerin kaydı | ❌ Elle düzenleme! Bot günceller |

Bu dosya şunları tutar:

```
posts         → Paylaşılan her haberin başlığı, linki, puanı, zamanı
daily_counts  → Her gün kaç post yapıldığı
last_check_time → Son kontrol zamanı (akıllı zaman filtresi için)
```

Bot her paylaşımda bu dosyayı günceller ve GitHub'a kaydeder.
500+ kayıt birikince eski kayıtlar otomatik temizlenir.

```
⚠️ Bu dosyayı SİLME veya BOŞALTMA. Bot bozulur.
   İçeriği şu olmalı (minimum): {"posts": [], "daily_counts": {}}
```

---

## 📁 assets/ — LOGON

| Dosya | Ne İşe Yarar | Format |
|-------|-------------|--------|
| `logo.png` | Görsellere eklenen watermark | Şeffaf PNG, tercihen 500x500px |

Logo her görselin sağ alt köşesine yarı saydam olarak eklenir.
Değiştirmek için: Yeni `logo.png` dosyasını aynı isimle yükle.

---

## 📁 src/ — KOD DOSYALARI (YZ'ye sorup değiştirirsin)

> ⚠️ **Bu dosyaları KENDİN değiştirme.**
> Değişiklik istiyorsan YZ'ye (ChatGPT/Claude) sor.
> YZ sana düzeltilmiş dosyanın TAMAMINI verir, sen kopyala-yapıştır yaparsın.

| # | Dosya | Ne Yapar | Kullandığı Config | Ne Zaman Değişir |
|---|-------|----------|-------------------|-----------------|
| 1 | `utils.py` | Yardımcı fonksiyonlar (log, config okuma, tarih, benzerlik) | Tüm config'leri okur | Nadiren |
| 2 | `news_fetcher.py` | RSS'ten haber çeker, filtreler (keyword, zaman, tekrar, benzerlik) | sources, keywords, settings | Yeni kaynak türü eklenirse |
| 3 | `content_filter.py` | YZ ile viral puanlama yapar, eşik kontrolü | scoring, prompts (viral_scorer) | Puanlama mantığı değişirse |
| 4 | `ai_processor.py` | YZ'ye metin yazdırır (Gemini → Groq → OpenRouter → HuggingFace sırasıyla) | prompts (post_writer) | Yeni YZ servisi eklenirse |
| 5 | `image_handler.py` | Görsel çeker (og:image), logo ekler, yedek görsel üretir | settings (images bölümü) | Görsel işleme değişirse |
| 6 | `facebook_poster.py` | Facebook Graph API v11.0 ile paylaşır, posted_news.json günceller | — | Facebook API değişirse |
| 7 | `main.py` | Ana kontrol — tüm adımları sırayla çalıştırır | settings (posting bölümü) | Yeni özellik eklenirse |

### Dosya Detayları:

**🔧 utils.py** — Yardımcı fonksiyonlar
```
Fonksiyonlar:
  load_config(name)          → Config dosyası okur (sources, settings vb.)
  clean_html(text)           → HTML tag'lerini temizler
  log(message, level)        → Zaman damgalı log yazar
  get_turkey_now()           → Türkiye saatini döner
  get_posted_news()          → posted_news.json okur
  save_posted_news(data)     → posted_news.json yazar
  is_already_posted(url, title, data) → Bu haber paylaşılmış mı?
  is_similar_title(t1, t2)   → İki başlık benzer mi? (%60 eşleşme)
  get_last_check_time(data)  → Son kontrol zamanını döner
```

**📰 news_fetcher.py** — Haber çekme + filtreleme
```
Ana fonksiyon:
  fetch_and_filter_news()    → Tüm haberleri çek → filtrele → döndür

Çalışma sırası:
  1. fetch_all_feeds()          → RSS feed'leri çek
  2. resolve_google_news_url()  → Google News yönlendirmelerini çöz
  3. apply_keyword_filter()     → include/exclude kelime filtresi
  4. apply_time_filter()        → Eski haberleri ele (12 saat)
  5. remove_already_posted()    → Daha önce paylaşılanları çıkar
  6. remove_duplicates()        → Benzer haberleri tekil yap

Diğer fonksiyonlar:
  scrape_full_article(url)   → Haber sitesinden tam metin çeker
  _extract_image_from_entry()→ RSS'ten görsel URL'si çıkarır (6 yöntem)

⚠️ TEST MODU aktifken:
  - Zaman filtresi gevşetilir (sadece max saat kullanılır)
  - Tekrar kontrolü atlanır
```

**🔍 content_filter.py** — YZ ile puanlama
```
Ana fonksiyon:
  score_and_filter(articles) → Her haberi YZ ile puanla, eşik altını ele

Kullanır:
  prompts.json → viral_scorer promptu
  scoring.json → publish_score ve slow_day_score eşikleri
```

**🤖 ai_processor.py** — YZ metin üretimi
```
Ana fonksiyonlar:
  ask_ai(prompt)             → YZ'ye soru sor (4 servis sırayla denenir)
  generate_post_text(news)   → Facebook post metni üret
  parse_ai_json(response)    → YZ cevabını JSON'a çevir

YZ servis sırası (biri başarısız olursa sonrakine geçer):
  1. Gemini (Google)
  2. Groq
  3. OpenRouter
  4. HuggingFace

Yardımcı:
  _clean_non_turkish_chars() → Türkçe dışı karakterleri temizler
  _fix_truncated_json_array()→ Yarım kalan JSON'u tamir eder
```

**🖼️ image_handler.py** — Görsel işleme
```
Çalışma sırası:
  1. RSS'ten gelen görsel URL'sini dene
  2. Yoksa → haber sitesinden og:image çek (scrape)
  3. O da yoksa → yedek görsel üret (lacivert arkaplan + logo)
  4. Görsele logo watermark ekle

Yedek görsel: 1200x630, lacivert (#1a1a2e) arkaplan, ortada logo
```

**📣 facebook_poster.py** — Facebook paylaşım
```
Ana fonksiyon:
  post_to_facebook(text, image_path) → Facebook sayfasına paylaş

Özellikler:
  - Graph API v11.0 kullanır
  - Fotoğraflı post (image + message)
  - Paylaşım sonrası posted_news.json günceller
  - Hata durumunda detaylı log yazar
```

**🧠 main.py** — Ana kontrol
```
Çalışma sırası:
  1. Haberleri çek ve filtrele (news_fetcher)
  2. Günlük limit kontrolü (max_daily_posts)
  3. Rastgele atlama kontrolü (skip_probability)
  4. En iyi haberi seç
  5. Tam metni çek (scrape_full_article)
  6. YZ ile puanla (content_filter)
  7. YZ ile post metni yaz (ai_processor)
  8. Görseli hazırla (image_handler)
  9. Facebook'a paylaş (facebook_poster)
  10. Rastgele bekleme (doğal görünsün diye)
  11. posted_news.json güncelle → GitHub'a kaydet
```

### ⚠️ ÖNEMLİ NOT: `src/__init__.py` dosyası YOK ve OLUŞTURMA!

```
Python "python src/main.py" komutuyla çalıştığında src/ klasörünü
otomatik olarak tanır. __init__.py dosyasına gerek yok.
Oluşturursan SORUN ÇIKABİLİR.
```

---

## 📄 Diğer Dosyalar

| Dosya | Ne İşe Yarar | Ne Zaman Değişir |
|-------|-------------|-----------------|
| `requirements.txt` | Python kütüphaneleri listesi | Yeni kütüphane eklenirse |
| `.github/workflows/bot.yml` | Zamanlayıcı (2 saatte bir, 06:00-00:00 TR) | Çalışma saatlerini değiştirmek istersen |
| `README.md` | Kurulum rehberi | Yeni bölüm eklenirse |
| `SCHEMA.md` | Bu dosya (proje haritası) | Proje değiştiğinde güncelle |

---

## 🔄 VERİ AKIŞI (Büyük Resim)

```
Her 2 saatte bir GitHub Actions tetiklenir (bot.yml):
│
▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  📰 HABERLERİ    │───▶│  🔍 PUANLA       │───▶│  🤖 METNİ YAZ   │
│  TARA + FİLTRELE │    │  (YZ ile 0-100)  │    │  (YZ ile üret)   │
│                  │    │                  │    │                  │
│ news_fetcher.py  │    │ content_filter.py│    │ ai_processor.py  │
│ sources.json     │    │ prompts.json     │    │ prompts.json     │
│ keywords.json    │    │ scoring.json     │    │                  │
└──────────────────┘    └──────────────────┘    └────────┬─────────┘
                                                         │
                    ┌────────────────────────────────────┘
                    ▼
┌──────────────────┐    ┌──────────────────┐
│  🖼️ GÖRSELİ     │───▶│  📣 FACEBOOK'A   │
│  HAZIRLA         │    │  PAYLAŞ          │
│  + LOGO EKLE     │    │  + KAYDET        │
│                  │    │                  │
│ image_handler.py │    │ facebook_poster  │
│ settings.json    │    │ posted_news.json │
└──────────────────┘    └──────────────────┘
```

---

## 🔑 API KEY'LER (GitHub Secrets)

Bot çalışmak için şu API anahtarlarına ihtiyaç duyar.
Bunlar GitHub → Settings → Secrets → Actions'da saklanır:

```
FACEBOOK_PAGE_ID          → Facebook sayfa ID'si
FACEBOOK_ACCESS_TOKEN     → Facebook Graph API token'ı
GEMINI_API_KEY            → Google Gemini API anahtarı
GROQ_API_KEY              → Groq API anahtarı (yedek YZ)
OPENROUTER_API_KEY        → OpenRouter API anahtarı (yedek YZ)
HF_API_KEY                → HuggingFace API anahtarı (yedek YZ)
```

> **⚠️ Bu anahtarları KOD İÇİNDE yazma!** Sadece GitHub Secrets'ta sakla.
> bot.yml dosyası bunları otomatik olarak ortam değişkeni yapıyor.

---

## 🏆 ALTIN KURALLAR

```
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║  Config değişikliği → KENDİN YAP (JSON dosyasını düzenle)   ║
║  Kod değişikliği    → YZ'YE SOR (ChatGPT / Claude)          ║
║                                                              ║
║  ⚠️ posted_news.json → DOKUNMA (bot günceller)              ║
║  ⚠️ __init__.py      → OLUŞTURMA (gereksiz, sorun çıkarır) ║
║  ⚠️ API key'ler      → KODA YAZMA (Secrets'ta sakla)        ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

---

## 🤖 YZ'YE SORARKEN ADIMLAR

Bir şeyi değiştirmek veya yeni özellik eklemek istiyorsan:

```
ADIM 1: "Şu özelliği istiyorum" diye YZ'ye yaz
        Örnek: "Bot Instagram'a da paylaşsın istiyorum"

ADIM 2: Bu SCHEMA.md dosyasının TAMAMINI kopyala, YZ'ye yapıştır
        "İşte projemin haritası:" de

ADIM 3: YZ sana hangi dosyaları istediğini söyler
        Örnek: "Bana main.py ve facebook_poster.py dosyalarını gönder"

ADIM 4: GitHub'da o dosyaları aç → içeriğini kopyala → YZ'ye yapıştır

ADIM 5: YZ düzeltilmiş dosyaların TAMAMINI verir
        (Sadece değişen satırı değil, dosyanın HEPSİNİ verir)

ADIM 6: GitHub'da dosyayı aç → kalem ✏️ tıkla → CTRL+A → SİL
        → Yeni kodu YAPIŞTIR → "Commit changes" tıkla
```

> **📌 NOT:** YZ ASLA "şu satırı bul, değiştir" DEMEZ.
> HER ZAMAN dosyanın TAMAMINI verir. Sen de TAMAMEN değiştirirsin.
> Bu sayede yarım yamalak değişiklik riski olmaz.

---

## 💡 ÖZELLİK EKLEME ÖRNEKLERİ

### Senaryo 1: "Puanlama Eşiğini Değiştir"
```
1. GitHub'da config/scoring.json dosyasını aç
2. Kalem ✏️ tıkla
3. "publish_score": 65 → "publish_score": 55 yap
4. "Commit changes" tıkla
5. BİTTİ ✅ (YZ'ye sormana bile gerek yok!)
```

### Senaryo 2: "Yazım üslubunu değiştir"
```
1. GitHub'da config/prompts.json dosyasını aç
2. Kalem ✏️ tıkla
3. post_writer promptundaki talimatları değiştir
4. "Commit changes" tıkla
5. BİTTİ ✅ (Config değişikliği, YZ'ye sormana gerek yok)
```

### Senaryo 3: "Facebook Hikaye (Story) Paylaşımı Ekle"
```
1. YZ'ye sor → şu dosyaları ister: facebook_poster.py, main.py
2. O dosyaları GitHub'dan kopyala → YZ'ye yapıştır
3. YZ düzeltilmiş hallerini verir
4. GitHub'da her dosyayı aç → eski içeriği sil → yenisini yapıştır
5. BİTTİ ✅
```

### Senaryo 4: "Instagram Paylaşımı da Ekle"
```
1. YZ'ye sor → şu dosyaları ister: main.py, requirements.txt
2. YZ ayrıca YENİ dosya verir: src/instagram_poster.py
3. GitHub'da "Add file" → "Create new file" → dosyayı oluştur
4. Mevcut dosyaları güncelle (kopyala-yapıştır)
5. BİTTİ ✅
```

---

*Versiyon: 2.0 — Büyük Temizlik Operasyonu sonrası güncellenmiştir*
