# 🗺️ otoXtra BOT — PROJE ŞEMASI (Proje Haritası v1.0)

> **Bu dosya projenin HARİTASIDIR.**
> Hangi dosya ne iş yapar, nerede durur, ne zaman değiştirilir — hepsi burada.
>
> 🔑 **Ne zaman kullanırsın?**
> - Bir şeyi değiştirmek istediğinde → önce buraya bak
> - YZ'ye (ChatGPT/Claude) soru soracaksan → bu dosyayı yapıştır
> - "Hangi dosyayı düzenlemem lazım?" diye merak ediyorsan → buraya bak

---

## 📂 DOSYA YAPISI (Tam Liste)

otoXtra-bot/
├── 📄 README.md ← Kurulum rehberi (şu an okuyorsun)
├── 📄 SCHEMA.md ← PROJE HARİTASI (bu dosya)
├── 📁 config/ ← SENİN AYARLARIN
│ ├── sources.json
│ ├── settings.json
│ ├── keywords.json
│ ├── scoring.json
│ └── prompts.json
├── 📁 data/ ← BOT KENDİSİ GÜNCELLER
│ └── posted_news.json
├── 📁 assets/ ← LOGON
│ └── logo.png
├── 📁 src/ ← KOD DOSYALARI
│ ├── main.py
│ ├── news_fetcher.py
│ ├── content_filter.py
│ ├── ai_processor.py
│ ├── image_handler.py
│ ├── facebook_poster.py
│ └── utils.py
├── 📁 .github/workflows/ ← ZAMANLAYICI
│ └── bot.yml
└── 📄 requirements.txt ← BAĞIMLILIKLAR

---

## 📁 config/ — SENİN AYARLARIN (İstediğin zaman değiştir)

Bu klasördeki dosyalar botun AYARLARIDIR. Kod bilmene gerek yok,
sadece sayıları ve kelimeleri değiştirirsin.

| Dosya | Ne İşe Yarar | Örnek Değişiklik |
|-------|-------------|-----------------|
| `sources.json` | Haber kaynakları listesi (RSS linkleri) | Yeni haber sitesi ekle/çıkar |
| `settings.json` | Genel ayarlar (limit, zamanlama, görsel) | Günlük post sayısını değiştir |
| `keywords.json` | Dahil/hariç kelimeler | "Tesla" haberlerini hariç tut |
| `scoring.json` | Puanlama kriterleri ve eşikler | Minimum puanı 65'ten 50'ye düşür |
| `prompts.json` | YZ'ye gönderilen talimatlar | Yazım üslubunu değiştir |

### 📄 config/sources.json — Haber Kaynakları

Haber sitelerinin RSS adreslerini içerir.
Her kaynak için şu bilgiler var:

    name: Kaynak adı (log'larda görünür)
    url: RSS feed adresi
    can_scrape_image: true/false
    → true = Bu siteden haber görseli çekilebilir
    → false = Bu siteden görsel çekme, YZ ile üret
    (Bazı siteler görsel çekmeyi engelliyor, onlar için false yap)


### 📄 config/settings.json — Genel Ayarlar

Botun davranışını kontrol eden ana ayar dosyası:

    max_daily_posts: Günlük maksimum post sayısı (varsayılan: 7)
    random_delay_max_minutes: Rastgele bekleme süresi (varsayılan: 8)
    ⚠️ DİKKAT: Bu değeri artırırsan GitHub Actions dakikası daha çok harcanır!
    min_post_interval_hours: İki post arası minimum süre (varsayılan: 2)
    skip_probability_percent: Rastgele atlama olasılığı (varsayılan: 10)
    Görsel ayarları: logo pozisyonu, opaklık, boyut


### 📄 config/keywords.json — Anahtar Kelimeler

İki liste içerir:

    include_keywords: Bu kelimeler geçen haberler ÖNCELİKLİ
    (örnek: "elektrikli", "SUV", "lansman")
    exclude_keywords: Bu kelimeler geçen haberler ATILIR
    (örnek: "kaza", "ölüm", "mahkeme")


### 📄 config/scoring.json — Puanlama

Haberlerin puanlanma kriterleri:

    publish_score: Normal günlerde minimum puan (varsayılan: 65)
    slow_day_score: Sakin günlerde minimum puan (varsayılan: 50)
    (Bugün 2'den az post yapılmışsa bu eşik kullanılır)


### 📄 config/prompts.json — YZ Talimatları

Yapay zekaya gönderilen komutlar:

    quality_gate: Kalite kontrolü promptu
    viral_scorer: Puanlama promptu
    post_writer: Facebook metni yazma promptu
    image_prompt_generator: Görsel üretim promptu

✍️ Üslup değiştirmek istersen post_writer promptunu düzenle!


---

## 📁 data/ — BOT KENDİSİ GÜNCELLER (DOKUNMA!)

| Dosya | Ne İşe Yarar | Dikkat |
|-------|-------------|--------|
| `posted_news.json` | Paylaşılmış haberlerin kaydı | ❌ Elle düzenleme! Bot günceller |

Bu dosya şunları tutar:

    posts: Paylaşılan her haberin başlığı, linki, puanı, zamanı
    daily_counts: Her gün kaç post yapıldığı

Bot her paylaşımda bu dosyayı günceller ve GitHub'a kaydeder.
500+ kayıt birikince eski kayıtlar otomatik temizlenir.

⚠️ Bu dosyayı SİLME veya BOŞALTMA. Bot bozulur.
İçeriği şu olmalı (minimum): {"posts": [], "daily_counts": {}}


---

## 📁 assets/ — LOGON

| Dosya | Ne İşe Yarar | Format |
|-------|-------------|--------|
| `logo.png` | Görsellere eklenen watermark | Şeffaf PNG, tercihen 500x500px |

Logo her görselin sağ alt köşesine yarı saydam olarak eklenir.
Değiştirmek için: Yeni logo.png dosyasını aynı isimle yükle.


---

## 📁 src/ — KOD DOSYALARI (YZ'ye sorup değiştirirsin)

> ⚠️ **Bu dosyaları KENDİN değiştirme.**
> Değişiklik istiyorsan YZ'ye (ChatGPT/Claude) sor.
> YZ sana düzeltilmiş dosyanın TAMAMINI verir, sen kopyala-yapıştır yaparsın.

| # | Dosya | Emoji | Ne Yapar | Ne Zaman Değişir |
|---|-------|-------|----------|-----------------|
| 1 | `utils.py` | 🔧 | Yardımcı fonksiyonlar (log, config okuma, tarih) | Nadiren |
| 2 | `news_fetcher.py` | 📰 | RSS/Google News'ten haber çeker, filtreler | Yeni kaynak türü eklenirse |
| 3 | `content_filter.py` | 🔍 | YZ ile kalite kontrolü + viral puanlama | Puanlama mantığı değişirse |
| 4 | `ai_processor.py` | 🤖 | YZ ile metin üretimi (Gemini/Groq/HF) | Yeni YZ servisi eklenirse |
| 5 | `image_handler.py` | 🖼️ | Görsel çekme/üretme/logo ekleme | Görsel işleme değişirse |
| 6 | `facebook_poster.py` | 📣 | Facebook'a paylaşım + kayıt tutma | Facebook API değişirse |
| 7 | `main.py` | 🧠 | Ana kontrol (tüm adımları sırayla çalıştırır) | Yeni özellik eklenirse |

### ⚠️ ÖNEMLİ NOT: `src/__init__.py` dosyası YOK ve OLUŞTURMA!

Python "python src/main.py" komutuyla çalıştığında src/ klasörünü
otomatik olarak tanır. init.py dosyasına gerek yok.
Oluşturursan SORUN ÇIKABİLİR.


---

## 📄 Diğer Dosyalar

| Dosya | Ne İşe Yarar | Ne Zaman Değişir |
|-------|-------------|-----------------|
| `requirements.txt` | Python kütüphaneleri listesi | Yeni kütüphane eklenirse |
| `.github/workflows/bot.yml` | Zamanlayıcı (ne zaman çalışsın) | Çalışma saatlerini değiştirmek istersen |
| `README.md` | Kurulum rehberi | Yeni bölüm eklenirse |
| `SCHEMA.md` | Bu dosya (proje haritası) | Yeni dosya eklenirse |

---

## 🏆 ALTIN KURAL

╔═══════════════════════════════════════════════════════════╗
║ ║
║ Config değişikliği → KENDİN YAP (JSON dosyasını düzenle)║
║ Kod değişikliği → YZ'YE SOR (ChatGPT / Claude) ║
║ ║
╚═══════════════════════════════════════════════════════════╝


---

## 🤖 YZ'YE SORARKEN ADIMLAR

Bir şeyi değiştirmek veya yeni özellik eklemek istiyorsan:

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


> **📌 NOT:** YZ ASLA "şu satırı bul, değiştir" DEMEZ.
> HER ZAMAN dosyanın TAMAMINI verir. Sen de TAMAMEN değiştirirsin.
> Bu sayede yarım yamalak değişiklik riski olmaz.

---

## 💡 ÖZELLİK EKLEME ÖRNEKLERİ

### Senaryo 1: "Facebook Hikaye (Story) Paylaşımı Ekle"

    YZ'ye sor → şu dosyaları ister: facebook_poster.py, main.py
    O dosyaları GitHub'dan kopyala → YZ'ye yapıştır
    YZ düzeltilmiş hallerini verir
    GitHub'da her dosyayı aç → eski içeriği sil → yenisini yapıştır
    config/settings.json → "enable_stories": true ekle
    BİTTİ ✅ (3 dosyada kopyala-yapıştır)


### Senaryo 2: "Instagram Paylaşımı da Ekle"

    YZ'ye sor → şu dosyaları ister: main.py, requirements.txt
    YZ ayrıca YENİ dosya verir: src/instagram_poster.py
    GitHub'da "Add file" → "Create new file" → src/instagram_poster.py oluştur
    Mevcut dosyaları güncelle (kopyala-yapıştır)
    config/settings.json → instagram ayarı ekle
    BİTTİ ✅


### Senaryo 3: "Puanlama Eşiğini Değiştir"

    GitHub'da config/scoring.json dosyasını aç
    Kalem ✏️ tıkla
    "publish_score": 65 → "publish_score": 55 yap
    "Commit changes" tıkla
    BİTTİ ✅ (YZ'ye sormana bile gerek yok!)


---

## 🔄 VERİ AKIŞI (Büyük Resim)

Her 2 saatte bir GitHub Actions tetiklenir:
│
▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ 📰 Haberleri │───▶│ 🔍 Kaliteyi │───▶│ 🤖 Metni │
│ Tara │ │ Filtrele │ │ Yaz │
│ │ │ Puanla │ │ │
└──────────────┘ └──────────────┘ └──────┬───────┘
│
┌────────────────────────────────────────────┘
▼
┌──────────────┐ ┌──────────────┐
│ 🖼️ Görseli │───▶│ 📣 Facebook'a│
│ Hazırla │ │ Paylaş │
│ Logo Ekle │ │ Kaydet │
└──────────────┘ └──────────────┘


---

*Son güncelleme: Proje oluşturulma tarihi*
*Versiyon: 1.0*
