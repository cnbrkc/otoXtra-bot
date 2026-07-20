# Privacy Policy

otoXtra Bot, Facebook/Threads/Instagram sayfalarına otomatik içerik paylaşımı için çalışır.

## Collected Data
- Facebook Page ID & Access Token (60 günlük)
- Threads User ID & Access Token
- AI API keys (Gemini, Groq, OpenRouter, HuggingFace)
- Telegram Bot Token & Chat ID (bildirimler için)
- RSS kaynaklarından gelen herkese açık haber verileri
- Twitter/X tweet URL'leri (FxTwitter API üzerinden görsel çekimi için)

## Usage
Bu veriler yalnızca botun çalışması, içerik üretmesi ve paylaşım yapması için kullanılır:
- RSS feed'lerden haber çekme
- Yapay zeka ile viral puanlama ve Türkçe post metni oluşturma
- Görsel bulma, indirme, logo watermark ekleme
- Facebook/Threads/Instagram Story'e paylaşım
- Telegram'a bildirim gönderme

## Storage
- Bot çalışma durumu ve paylaşılan içerik geçmişi repository içindeki JSON dosyalarında tutulur (`data/posted_news.json`, `queue/pipeline.json`)
- 30 günden eski kayıtlar otomatik temizlenir
- Kişisel kullanıcı verisi toplanmaz/satılmaz
- Tüm API anahtarları GitHub Secrets'ta şifreli saklanır

## Third-Party Services
Bot aşağıdaki servisleri kullanır (her biri kendi gizlilik politikasına tabidir):
- **Google Gemini**: AI içerik üretimi
- **Groq/OpenRouter/HuggingFace**: Yedek AI servisleri
- **Facebook Graph API v25.0**: Facebook/Threads/Instagram paylaşımı
- **FxTwitter API**: Twitter/X tweet görselleri çekimi
- **DuckDuckGo Image Search**: Görsel fallback arama
- **Catbox.moe / 0x0.st / Telegraph / ImgBB**: Görsel upload (Threads fallback zinciri)
- **Telegram Bot API**: Bildirim mesajları

## Data Retention
- Paylaşım geçmişi: 30 gün (otomatik temizlik)
- Pipeline durumu: Her çalışmada güncellenir
- Haftalık istatistikler: Sürekli saklanır (actions/shares/errors/skips)

## Contact
İletişim için repo sahibi ile GitHub üzerinden iletişime geçebilirsiniz.
