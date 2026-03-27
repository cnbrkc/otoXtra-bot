# 🚗 otoXtra Bot — Otomatik Otomobil Haberleri Facebook Botu

> 📌 **PROJE HARİTASI:** Bu projenin hangi dosyası ne iş yapar bilmek için
> **SCHEMA.md** dosyasını açın. Herhangi bir değişiklik yapmadan önce
> SCHEMA.md'yi okuyun veya YZ'ye (ChatGPT/Claude) yapıştırın.

**otoXtra Bot** otomobil haberlerini otomatik olarak bulur, yapay zeka ile
kaliteli olanları seçer, etkileşimci bir Facebook postu yazar, görsel
hazırlar ve Facebook sayfanızda paylaşır. Tüm bunları bilgisayarınız kapalı
olsa bile GitHub Actions üzerinden **tamamen otomatik** yapar.

**Günde 3-7 arası KALİTELİ haber paylaşır.** Kalite > miktar prensibiyle
çalışır. Clickbait paylaşmaz, aynı haberi tekrar paylaşmaz, bot gibi
davranmaz.

---

## 📋 KURULUM REHBERİ (ADIM ADIM)

Bu rehber yazılımdan hiç anlamayan biri için hazırlanmıştır.
Her adım detaylı anlatılmıştır. Hiçbir adımı atlamayın.

**Tahmini süre:** İlk kurulum yaklaşık 45-60 dakika sürer.
Bir kez kurarsınız, sonra otomatik çalışır.

---

## BÖLÜM A: GitHub Hesabı Açma ve Repo Oluşturma

### A1. GitHub Hesabı Açma

1. Tarayıcınızı açın (Chrome, Firefox, Safari — hangisi olursa)
2. Adres çubuğuna `github.com` yazın, Enter'a basın
3. Sağ üst köşede **"Sign up"** butonuna tıklayın
4. **Enter your email:** E-posta adresinizi yazın (Gmail, Hotmail, ne olursa)
5. **Create a password:** Bir şifre belirleyin (en az 8 karakter)
6. **Enter a username:** Kullanıcı adı seçin (örnek: otoxtra-admin)
7. Doğrulama bulmacasını çözün
8. **"Create account"** tıklayın
9. E-postanıza gelen **doğrulama kodunu** girin
10. Birkaç soru sorar (ne için kullanacaksın vs.) — "Skip" tıklayabilirsiniz

✅ **GitHub hesabınız hazır!**

### A2. Repo (Proje Klasörü) Oluşturma

1. GitHub'a giriş yaptıktan sonra sağ üst köşedeki **"+"** işaretine tıklayın
2. Açılan menüden **"New repository"** seçin
3. Şu alanları doldurun:

   - **Repository name:** `otoXtra-bot` yazın
   - **Description:** `Otomatik otomobil haberleri Facebook botu` yazın (opsiyonel)
   - **"Private"** seçeneğini işaretleyin (projeniz gizli kalır)
   - **"Add a README file"** kutusunu ✅ **İŞARETLEYİN**

4. **"Create repository"** yeşil butonuna tıklayın

✅ **Repo'nuz oluşturuldu!** Şimdi dosyaları ekleyeceğiz.

---

## BÖLÜM B: Dosyaları Repo'ya Yükleme

Her dosyayı tek tek oluşturacağız. İşlem basit: dosya adını yaz,
içeriği yapıştır, kaydet. Her dosya için aynı adımlar:

### Dosya Oluşturma Adımları (Her dosya için tekrarla):

1. Repo sayfanızda **"Add file"** butonuna tıklayın (üst tarafta)
2. **"Create new file"** seçin
3. Üstteki **dosya adı kutusuna** dosya yolunu yazın
   - Örnek: `src/main.py` yazınca otomatik olarak `src` klasörü oluşur
   - Örnek: `config/settings.json` yazınca `config` klasörü oluşur
   - `/` (eğik çizgi) yazdığınızda klasör otomatik ayrılır
4. Alt taraftaki **büyük beyaz alana** dosya içeriğini yapıştırın
   - İçeriği nereden alacaksınız? Proje dosyalarından (aşağıdaki sırayla)
5. Sayfanın en altında **"Commit new file"** (veya "Commit changes") yeşil butonuna tıklayın
6. ✅ Dosya oluşturuldu! Bir sonraki dosyaya geçin.

### 📌 Dosya Oluşturma Sırası (BU SIRAYLA YAPIN):

| # | Dosya Yolu | Ne Olduğu |
|---|-----------|----------|
| 1 | `config/sources.json` | Haber kaynakları |
| 2 | `config/settings.json` | Genel ayarlar |
| 3 | `config/keywords.json` | Anahtar kelimeler |
| 4 | `config/scoring.json` | Puanlama kriterleri |
| 5 | `config/prompts.json` | YZ talimatları |
| 6 | `data/posted_news.json` | Paylaşım kaydı (boş başlar) |
| 7 | `src/utils.py` | Yardımcı fonksiyonlar |
| 8 | `src/news_fetcher.py` | Haber çekme |
| 9 | `src/content_filter.py` | Kalite filtresi |
| 10 | `src/ai_processor.py` | YZ metin üretimi |
| 11 | `src/image_handler.py` | Görsel işleme |
| 12 | `src/facebook_poster.py` | Facebook paylaşım |
| 13 | `src/main.py` | Ana program |
| 14 | `requirements.txt` | Python kütüphaneleri |
| 15 | `.github/workflows/bot.yml` | Zamanlayıcı |
| 16 | `SCHEMA.md` | Proje haritası |

> ⚠️ **ÖNEMLİ:** `src/__init__.py` dosyası **OLUŞTURMAYIN**. Gerek yok.
> Oluşturursanız sorun çıkabilir.

### 🖼️ Logo Yükleme (assets/logo.png):

Logo dosyası farklı şekilde yüklenir (yapıştırılamaz):

1. Repo sayfanızda **"Add file"** → **"Create new file"** tıklayın
2. Dosya adı kutusuna `assets/.gitkeep` yazın
3. İçerik boş bırakın
4. **"Commit new file"** tıklayın (bu, assets klasörünü oluşturur)
5. Oluşan `assets` klasörüne tıklayın
6. **"Add file"** → **"Upload files"** tıklayın
7. Logo dosyanızı (logo.png) bilgisayarınızdan **sürükleyip bırakın**
8. **"Commit changes"** tıklayın

> 💡 Logo şeffaf arka planlı PNG olmalı. İdeal boyut: 500x500 piksel.
> Elinizde logo yoksa, Canva.com'da ücretsiz yapabilirsiniz.

---

## BÖLÜM C: API Key'leri Alma (ÜCRETSİZ)

Bot'un çalışması için 5 adet "anahtar" (API key) lazım.
Hepsi ücretsiz. Her birini alıp bir yere not edin (notepad/not defteri).

---

### C1. 🔑 Gemini API Key (Google Yapay Zeka)

Gemini, Google'ın yapay zekasıdır. Bot bunu haber metni yazmak için kullanır.

1. Tarayıcınızda şu adrese gidin: https://aistudio.google.com/app/apikey
2. Google hesabınızla giriş yapın (Gmail hesabınız varsa o ile)
3. Sayfada **"Create API Key"** butonuna tıklayın
4. **"Create API key in new project"** seçeneğini seçin
5. Birkaç saniye bekleyin
6. Ekranda `AIzaSy` ile başlayan uzun bir kod görünecek
7. Bu kodu **kopyalayın** (yanındaki kopyala simgesine tıklayın)
8. Notepad'e yapıştırın ve başına **"Gemini:"** yazın

Örnek not:
Gemini: AIzaSyB1k2L3m4N5o6P7q8R9s0T1u2V3w4X5y6Z

✅ **Gemini API Key hazır!**

---

### C2. 🔑 Groq API Key (Yedek Yapay Zeka)

Groq, yedek yapay zekadır. Gemini çalışmazsa bot bunu kullanır.

1. Tarayıcınızda şu adrese gidin: https://console.groq.com/keys
2. **"Sign Up"** ile hesap açın (Google hesabıyla giriş yapabilirsiniz)
3. Giriş yaptıktan sonra **"Create API Key"** butonuna tıklayın
4. **Display Name:** `otoXtra` yazın
5. **"Submit"** tıklayın
6. `gsk_` ile başlayan uzun bir kod görünecek
7. **Kopyalayın** ve not edin

Örnek not:
Groq: gsk_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6

> ⚠️ Bu kodu bir kez gösterir! Sayfayı kapatmadan kopyalayın.

✅ **Groq API Key hazır!**

---

### C3. 🔑 HuggingFace API Key (Yedek Görsel Üretim)

HuggingFace, yedek görsel üretimi için kullanılır. Ana görsel servisi
(Pollinations.ai) çalışmazsa devreye girer.

1. Tarayıcınızda şu adrese gidin: https://huggingface.co/settings/tokens
2. Hesabınız yoksa **"Sign Up"** tıklayın → e-posta, şifre ile hesap açın
3. Hesabınız varsa giriş yapın
4. **"Create new token"** (veya "New token") butonuna tıklayın
5. **Token name:** `otoXtra` yazın
6. **Type:** **"Read"** seçin (sadece Read yeterli)
7. **"Generate"** tıklayın
8. `hf_` ile başlayan uzun bir kod görünecek
9. **Kopyalayın** ve not edin

Örnek not:
HuggingFace: hf_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8

✅ **HuggingFace API Key hazır!**

---

### C4. 🔑 Facebook Page Access Token (Facebook Sayfa Erişim Tokenı)

Bu en uzun adım ama sabırla yaparsanız sorunsuz olur.
Bot'un Facebook sayfanıza paylaşım yapabilmesi için bu token gerekli.

#### Ön Koşul: Facebook Sayfanız Olmalı
Eğer Facebook sayfanız yoksa önce oluşturun:
- Facebook → Sol menü → "Sayfalar" → "Yeni Sayfa Oluştur"
- Sayfa adı: "otoXtra" (veya istediğiniz isim)
- Kategori: "Otomobil" veya "Medya/Haberler"

#### Adım 1: Facebook Developer Hesabı Açma

1. Tarayıcınızda şu adrese gidin: https://developers.facebook.com
2. Sağ üstte **"Başlayın"** veya **"Get Started"** tıklayın
3. Facebook hesabınızla giriş yapın
4. Geliştirici sözleşmesini kabul edin
5. Telefon numarası doğrulaması isteyebilir — numaranızı girin, gelen kodu yazın

#### Adım 2: Uygulama Oluşturma

1. Üst menüde **"My Apps"** (Uygulamalarım) tıklayın
2. **"Create App"** (Uygulama Oluştur) tıklayın
3. Uygulama türü sorar: **"Business"** (İşletme) seçin → **"Next"** tıklayın
4. **App name:** `otoXtra Bot` yazın
5. **App contact email:** E-postanız otomatik gelir, değiştirmeyin
6. **"Create App"** tıklayın
7. Şifrenizi tekrar girmenizi isteyebilir — girin

#### Adım 3: Facebook Login Ekleme

1. Uygulama kontrol panelinde **"Add Product"** (Ürün Ekle) bölümüne gidin
2. **"Facebook Login"** kartını bulun → **"Set Up"** tıklayın
3. Platform sorarsa **"Web"** seçin
4. URL sorarsa `https://localhost` yazın → **"Save"** tıklayın
5. Geri kalan adımları **"Next"** tıklayarak geçin

#### Adım 4: Kısa Süreli Token Alma

1. Tarayıcınızda şu adrese gidin: https://developers.facebook.com/tools/explorer/
2. Sağ üst köşede **"Facebook App"** açılır menüsünden **"otoXtra Bot"** seçin
3. **"User or Page"** kısmında **"Get Page Access Token"** seçin
4. Bir pencere açılır: Facebook sayfanızı seçin (otoXtra)
5. İzin isteği gelir — **tüm izinleri verin**:
- `pages_manage_posts` ✅
- `pages_read_engagement` ✅
- `pages_show_list` ✅
6. **"Generate Access Token"** tıklayın
7. Uzun bir kod görünür — bu **KISA SÜRELİ** tokendır
8. Bu kodu kopyalayın (birazdan kullanacağız)

#### Adım 5: App ID ve App Secret Bulma

1. **"My Apps"** → **"otoXtra Bot"** tıklayın
2. Sol menüde **"Settings"** → **"Basic"** tıklayın
3. **App ID:** Sayfanın üstünde yazar → kopyalayın, not edin
4. **App Secret:** **"Show"** butonuna tıklayın → şifrenizi girin → kopyalayın, not edin

Örnek not:
App ID: 123456789012345
App Secret: a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
Kısa Token: EAABwzLixnjYBO5k7... (çok uzun bir kod)

#### Adım 6: Uzun Süreli Token Alma

1. Tarayıcınızın adres çubuğuna şu URL'yi **TAMAMEN** yapıştırın: https://graph.facebook.com/v19.0/oauth/access_token?grant_type=fb_exchange_token&client_id=BURAYA_APP_ID_YAZ&client_secret=BURAYA_APP_SECRET_YAZ&fb_exchange_token=BURAYA_KISA_TOKEN_YAZ
2. Bu URL'deki 3 yeri **kendi değerlerinizle** değiştirin:
- `BURAYA_APP_ID_YAZ` → App ID numaranız (örnek: 123456789012345)
- `BURAYA_APP_SECRET_YAZ` → App Secret kodunuz
- `BURAYA_KISA_TOKEN_YAZ` → Az önce kopyaladığınız kısa token

3. Enter'a basın

4. Ekranda JSON formatında bir yanıt görürsünüz:
```json
{"access_token":"EAABwzLixnjYBO...uzun_kod...","token_type":"bearer","expires_in":5184000}

5. access_token değerini (tırnak içindeki uzun kodu) kopyalayın
Not edin — bu 60 gün geçerli uzun süreli tokenınız
Örnek not:
FB Token (60 gün): EAABwzLixnjYBOaZC... (çok uzun bir kod)



    ⚠️ 60 gün sonra bu token geçersiz olur. O zaman bu adımları
    tekrar yapmanız gerekir. Yaklaşık 2 ayda bir token yenileyin.

✅ Facebook Access Token hazır!

C5. 🔑 Facebook Page ID (Sayfa Kimlik Numarası)

    Facebook'ta kendi sayfanıza gidin (otoXtra sayfası)
    Sol menüde "Hakkında" (veya "About") sekmesine tıklayın
    Sayfayı aşağı kaydırın
    "Sayfa şeffaflığı" veya "Page transparency" bölümünde
    "Sayfa Kimliği" (Page ID) numarası yazar
    Bu numarayı kopyalayın

text

Örnek not:
Page ID: 102345678901234

    💡 Alternatif yol: Sayfa URL'niz facebook.com/otoXtra gibi ise,
    tarayıcıya https://graph.facebook.com/otoXtra yazın.
    Açılan sayfada "id":"102345678901234" görürsünüz.

✅ Facebook Page ID hazır!
BÖLÜM D: API Key'leri GitHub Secrets'a Kaydetme

    ⚠️ API key'leri kodun içine YAZMAYIN!
    GitHub Secrets'ta saklanır — şifrelenmiş kasada durur, kimse göremez.

    GitHub'da repo sayfanıza gidin (github.com/KULLANICIADI/otoXtra-bot)

    Üst menüden "Settings" (Ayarlar) sekmesine tıklayın
        ⚠️ Repo'nun Settings'i, profil Settings'i değil! Repo sayfasında olmalısınız.

    Sol menüde "Secrets and variables" altındaki "Actions" tıklayın

    Sağ üstte "New repository secret" yeşil butonuna tıklayın

    İlk secret'ı ekleyin:
    Name (İsim) kutusuna yazın:	Secret (Değer) kutusuna yapıştırın:
    GEMINI_API_KEY	Not ettiğiniz Gemini API Key

    "Add secret" tıklayın

    ✅ İlk secret eklendi!

    Tekrar "New repository secret" tıklayın ve aynı şekilde şunları da ekleyin:
    #	Name (BÜYÜK HARFLE, aynen böyle yazın)	Secret (Değer)
    1	GEMINI_API_KEY	Gemini API kodunuz
    2	GROQ_API_KEY	Groq API kodunuz
    3	HF_API_KEY	HuggingFace API kodunuz
    4	FB_ACCESS_TOKEN	Facebook uzun süreli tokenınız
    5	FB_PAGE_ID	Facebook sayfa ID numaranız

    📌 Her secret için: "New repository secret" tıkla → İsim yaz → Değer yapıştır → "Add secret" tıkla

    ⚠️ Secret isimlerini BÜYÜK HARFLERLE ve aynen yukarıdaki gibi yazın.
    Yanlış yazarsanız bot çalışmaz.

✅ Tüm secret'lar eklendi!
BÖLÜM E: Workflow İzinlerini Ayarlama

Bot'un posted_news.json dosyasını güncelleyip kaydedebilmesi için
yazma izni gerekli. Bu izni şöyle verirsiniz:

    GitHub repo sayfanızda "Settings" sekmesine tıklayın
    Sol menüde "Actions" altındaki "General" tıklayın
    Sayfayı en alta kaydırın
    "Workflow permissions" bölümünü bulun
    "Read and write permissions" seçeneğini işaretleyin (●)
    "Save" butonuna tıklayın

✅ Workflow izinleri ayarlandı!
BÖLÜM F: Bot'u Çalıştırma ve Test Etme

Her şey hazır! Şimdi bot'u test edelim.
İlk Test (Manuel Çalıştırma):

    GitHub repo sayfanızda üst menüden "Actions" sekmesine tıklayın
    Sol tarafta "otoXtra News Bot" workflow'unu göreceksiniz → tıklayın
    Sağ tarafta "Run workflow" butonu var → tıklayın
    Açılan küçük pencerede tekrar "Run workflow" yeşil butonuna tıklayın
    Sayfa yenilenecek ve bir çalışma (run) başlayacak

Durumu Takip Etme:
Simge	Anlam
🟡 Sarı daire (dönen)	Çalışıyor, bekleyin...
✅ Yeşil tik	Başarılı! Facebook sayfanızı kontrol edin
❌ Kırmızı çarpı	Hata var. Tıklayın → log'u okuyun
Hata Varsa Log'u Okuma:

    Kırmızı ❌ olan çalışmaya tıklayın
    "run-bot" adımına tıklayın
    "Run otoXtra bot" satırına tıklayın — detaylı log açılır
    Hata mesajını okuyun
    Hatayı anlamadıysanız → mesajı kopyalayın → ChatGPT/Claude'a yapıştırın
    → "Bu hata ne, nasıl çözerim?" diye sorun

Otomatik Çalışma:

Test başarılıysa bot bundan sonra otomatik çalışır:

    Her gün saat 08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00'de
    Anti-bot stratejisi sayesinde her seferinde paylaşım garanti değildir (bu normal)
    Günde ortalama 3-5 kaliteli haber paylaşır

BÖLÜM G: SCHEMA.md Dosyası Nerede?

SCHEMA.md dosyası repo'nun ana dizinindedir (kök klasör).

    GitHub'da repo sayfanıza gidin
    Dosya listesinde "SCHEMA.md" dosyasını göreceksiniz
    Tıklayın → projenin tüm haritasını görürsünüz

Ne zaman kullanırsınız?

    Bir dosyayı değiştirmeden önce → SCHEMA.md'ye bakın
    YZ'ye soru soracaksanız → SCHEMA.md'yi kopyalayıp yapıştırın
    "Bu dosya ne işe yarıyor?" diye merak ederseniz → SCHEMA.md'de yazar

BÖLÜM H: Bir Şey Değiştirmek İstersen
🟢 Config Değişikliği (KENDİN YAP — Kod Bilgisi Gerekmez)

Ayar değiştirmek çok kolay. Kod bilmenize gerek yok.

    GitHub'da repo sayfanıza gidin
    config/ klasörüne tıklayın
    Değiştirmek istediğiniz dosyaya tıklayın (örnek: settings.json)
    Sağ üst köşedeki kalem simgesine ✏️ tıklayın
    İstediğiniz değeri değiştirin
    Sağ üstteki "Commit changes..." butonuna tıklayın
    Açılan pencerede tekrar "Commit changes" tıklayın
    ✅ BİTTİ! Değişiklik bir sonraki çalışmada geçerli olur

Örnek değişiklikler:
Ne Yapmak İstiyorsun	Hangi Dosya	Ne Değiştir
Günlük post sayısını azalt	settings.json	"max_daily_posts": 7 → 4
Puanlama eşiğini düşür	scoring.json	"publish_score": 65 → 50
Bir haber kaynağı kaldır	sources.json	O kaynağın satırlarını sil
Yazım üslubunu değiştir	prompts.json	"post_writer" içeriğini düzenle
"Tesla" haberlerini engelle	keywords.json	"exclude_keywords" listesine "Tesla" ekle
🔵 Kod Değişikliği (YZ'YE SOR)

Kod dosyalarını (src/ içindekiler) değiştirmek için YZ'ye sorun.
Adım adım:

    SCHEMA.md'yi kopyalayın:
    GitHub'da SCHEMA.md dosyasını açın → tüm içeriği seçin (Ctrl+A) → kopyalayın (Ctrl+C)

    YZ'ye gidin:
    ChatGPT (chat.openai.com) veya Claude (claude.ai) açın

    YZ'ye yapıştırın ve isteğinizi yazın:

    text

    İşte projemin haritası:
    [SCHEMA.md içeriğini buraya yapıştır]

    Şu özelliği istiyorum: [ne istediğinizi yazın]
    Hangi dosyaları değiştirmem lazım?

    YZ size hangi dosyaları istediğini söyler:
    Örnek: "Bana facebook_poster.py ve main.py dosyalarını gönder"

    O dosyaları GitHub'dan kopyalayın:
    GitHub'da dosyaya tıklayın → kalem ✏️ → Ctrl+A → Ctrl+C → YZ'ye yapıştırın

    YZ düzeltilmiş dosyaların TAMAMINI verir:
    YZ size dosyanın tamamen yeni halini verir (sadece değişen satırı değil)

    GitHub'da dosyayı güncelleyin:
        GitHub'da dosyaya gidin → kalem ✏️ tıklayın
        Ctrl+A ile tüm eski içeriği seçin
        Delete/Backspace ile silin
        YZ'nin verdiği yeni kodu yapıştırın (Ctrl+V)
        "Commit changes" tıklayın

    Her dosya için 7. adımı tekrarlayın → BİTTİ ✅

BÖLÜM I: Sık Sorunlar ve Çözümler (FAQ)
❓ "Actions sekmesinde workflow görünmüyor"

Çözüm:

    .github/workflows/bot.yml dosyasının var olduğundan emin olun
    Dosya yolu tam olarak .github/workflows/bot.yml olmalı
    Dosyayı oluştururken .github/workflows/bot.yml yazın (noktayla başlıyor!)

❓ "Actions çalışmıyor / Permission hatası"

Çözüm:

    Settings → Actions → General
    En altta "Workflow permissions"
    "Read and write permissions" seçin
    Save tıklayın

❓ "Facebook'a paylaşmıyor"

Olası sebepler ve çözümler:

    Token süresi dolmuş (en yaygın sebep)
    → Bölüm C4 adımlarını tekrarlayın → yeni token alın
    → Settings → Secrets → FB_ACCESS_TOKEN → güncelle

    Sayfa ID yanlış
    → Facebook sayfanız → Hakkında → Sayfa Kimliği kontrol edin
    → Secrets'taki FB_PAGE_ID ile aynı mı?

    İzinler eksik
    → Graph API Explorer'da pages_manage_posts izni var mı?

❓ "Hiç haber paylaşmıyor (her seferinde 0 paylaşım)"

Olası sebepler:

    Puanlama eşiği çok yüksek
    → config/scoring.json → "publish_score": 65 → 50 yapın

    Haber kaynakları çalışmıyor
    → config/sources.json kontrol edin, RSS linkleri doğru mu?

    Çok fazla kelime engellenmiş
    → config/keywords.json → "exclude_keywords" listesini kontrol edin

    Rastgele atlama denk gelmiş (bu normal)
    → Anti-bot stratejisi gereği %10 olasılıkla atlar
    → Birkaç kez manuel çalıştırın, en az 1 tanesinde paylaşır

❓ "Çok fazla haber paylaşıyor"

Çözüm:
→ config/settings.json → "max_daily_posts": 7 → 4 veya 3 yapın
→ config/scoring.json → "publish_score": 65 → 75 yapın (eşiği yükseltin)
❓ "60 gün sonra Facebook token süresi doldu"

Çözüm:

    Bölüm C4'teki adımları baştan yapın (yeni token alın)
    GitHub → Settings → Secrets → FB_ACCESS_TOKEN
    "Update" tıklayın → yeni tokenı yapıştırın → Save

    💡 Takviminize 55 gün sonrası için hatırlatma koyun:
    "Facebook token yenile"

❓ "Bot çalışıyor ama log'da hata var"

Çözüm:

    Actions → başarısız çalışmaya tıklayın → log'u açın
    Hata mesajını bulun
    Hata mesajını kopyalayın
    ChatGPT veya Claude'a yapıştırın
    "Bu hata ne anlama geliyor ve nasıl çözerim?" diye sorun
    YZ size çözümü adım adım anlatır

❓ "Facebook API versiyon hatası (API deprecated)"

Çözüm:
Facebook yaklaşık 2 yılda bir eski API versiyonlarını kapatır.

    src/facebook_poster.py dosyasını açın (kalem ✏️)
    Dosyada v19.0 yazan yerleri bulun (Ctrl+F ile arayın)
    v19.0 → v20.0 (veya Facebook'un güncel versiyonu) olarak değiştirin
    Commit changes tıklayın

    💡 Facebook'un güncel API versiyonunu öğrenmek için:
    https://developers.facebook.com/docs/graph-api/changelog

❓ "GitHub Actions dakika limiti doldu"

Açıklama: Private repo'da ayda 2000 dakika ücretsiz GitHub Actions süresi var.

Çözümler:

    config/settings.json → "random_delay_max_minutes": 8 → 5 yapın
    (Her çalışmada daha az bekleme = daha az dakika tüketimi)

    Veya repo'yu Public yapın (Settings → Danger Zone → Change visibility)
        Public repo'da Actions dakikası sınırsız
        API key'leriniz Secrets'ta güvende, repo public olsa bile kimse göremez
        Sadece kodunuz görünür olur

🔮 Gelişme Notları (İleride Eklenebilecekler)

Bu özellikler şu an aktif değil ama ileride YZ'ye sorarak ekleyebilirsiniz:
Özellik	Nasıl Eklenir
📱 Facebook Story paylaşımı	YZ'ye sorun → facebook_poster.py güncellenir
📷 Instagram çapraz paylaşım	YZ'ye sorun → yeni instagram_poster.py eklenir
📊 Performans takibi	Hangi puanlı haberler daha çok etkileşim aldı?
🔄 Otomatik token yenileme	Facebook token 60 gün limitini otomatik aşma
🌐 Twitter/X paylaşımı	YZ'ye sorun → yeni twitter_poster.py eklenir
📞 Yardım

Takıldığınız yerde:

    Bu README'yi tekrar okuyun — çoğu sorunun cevabı burada
    SCHEMA.md'ye bakın — hangi dosya ne iş yapar
    Hata log'unu okuyun — Actions → çalışma → log
    YZ'ye sorun — Hata mesajını ChatGPT/Claude'a yapıştırın

Bu proje otoXtra tarafından oluşturulmuştur.
Versiyon: 1.0
