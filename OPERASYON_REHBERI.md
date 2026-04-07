# otoXtra Bot - Operasyon Rehberi (Kod Bilmeyenler Icin)

Bu rehberin amaci:
- Bot post atmadiysa nedenini bulmak
- Hata oldugunda panik yapmadan adim adim cozumlemek
- Temel kontrolu teknik bilgi olmadan yapabilmek

## 1) Bot calisiyor mu? (Ilk bakis)
1. GitHub repo icinde **Actions** sekmesine gir.
2. Son calisan workflow adina bak:
   - `otoXtra Bot` (asil calisma)
   - `Validate` (kalite kontrol)
3. Sonuc:
   - Yesil: teknik olarak calismis
   - Kirmizi: hata var, asagidaki adimlara gec

## 2) En sik 5 sorun ve hizli cozum

### Sorun 1: Hic post atilmamis
Kontrol:
1. Actions > son `otoXtra Bot` run > loglara gir
2. `fetch` asamasinda `No article found` var mi bak

Cozum:
1. `config/keywords.json` cok dar olabilir, biraz gevset
2. `config/settings.json` icinde `max_article_age_hours` cok dusuk olabilir, yukselt
3. Feed kaynaklari kapali olabilir (`enabled: false`), ac

### Sorun 2: Facebook hatasi (token/page)
Kontrol:
1. Logda `FB_PAGE_ID` veya `FB_ACCESS_TOKEN` eksik yaziyor mu?
2. Logda Facebook API error code gorunuyor mu?

Cozum:
1. GitHub > Settings > Secrets and variables > Actions
2. Su secretlar dolu mu kontrol et:
   - `FB_PAGE_ID`
   - `FB_ACCESS_TOKEN`
3. Access token suresi dolduysa yenile

### Sorun 3: AI uretimi bos donuyor
Kontrol:
1. Loglarda `ai_client.ask_ai` retry/hata satirlari var mi?
2. `OPENROUTER_API_KEY` veya diger AI keyleri eksik mi?

Cozum:
1. Secretlari tekrar kontrol et:
   - `OPENROUTER_API_KEY`
   - Kullanilan baska AI keyleri
2. Gecici servis sorunu olabilir, bir sonraki schedule'i bekle

### Sorun 4: Validate kirmizi (test fail)
Kontrol:
1. Actions > `Validate` > kirmizi adimi ac
2. Hangi testin patladigini satir satir oku

Cozum:
1. Son yaptigin config degisikligini geri al
2. Tekrar commit at, Validate yesil olana kadar ilerleme

### Sorun 5: Kaynaklar var ama yine az haber geliyor
Kontrol:
1. `source_health` loglarinda timeout/no_entries var mi?
2. Feed URL'leri acik mi (tarayicidan acmayi dene)

Cozum:
1. Sorunlu feed URL'sini guncelle
2. Kaynagi gecici kapat (`enabled: false`)
3. Diger kaynaklarla devam et

## 3) Her degisiklikten sonra zorunlu kontrol listesi
1. Commit attiktan sonra `Validate` yesil mi?
2. Sonraki `otoXtra Bot` run yesil mi?
3. En az bir post atildi mi / neden atilmadigi logda net mi?
4. README ile gercek davranis halen uyumlu mu?

## 4) Acil durum (her sey bozulduysa)
1. Son calisan yesil commit'i bul
2. Ona geri don (revert)
3. Validate yesil olana kadar yeni degisiklik yapma
4. Sonra degisiklikleri tek tek, kucuk parcalar halinde tekrar uygula

## 5) Secret listesi (minimum)
- `FB_PAGE_ID`
- `FB_ACCESS_TOKEN`
- `OPENROUTER_API_KEY`

## 6) Not
- Botun "hic post atmamasi" her zaman bug degildir.
- Bazen filtreler geregi uygun haber cikmaz.
- Onemli olan: loglarin nedenini acik yazmasi.
