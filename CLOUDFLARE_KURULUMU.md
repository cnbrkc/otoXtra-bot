# Cloudflare ile zamanlayıcı kurulumu

Bu bot artık saatini GitHub'dan değil Cloudflare Worker'dan alabilir. Cloudflare her saat Worker'ı uyandırır; Worker Türkiye saatini kontrol eder ve yalnızca eski GitHub cron'undaki saatlerde GitHub Actions'ı başlatır.

Hedef saatler: **06, 07, 08, 09, 11, 13, 15, 17, 19, 20 (Türkiye saati)**.

## 1. GitHub token oluştur

1. GitHub'da **Settings → Developer settings → Fine-grained personal access tokens → Generate new token** seç.
2. Repository access bölümünde yalnızca `cnbrkc/otoXtra-bot` seç.
3. Repository permissions altında **Actions: Read and write** ver.
4. Tokenı oluştur ve bir yere geçici olarak kopyala. Bu token şifre gibidir; kimseyle paylaşma.

## 2. Worker'ı kur

Bilgisayarında Node.js kurulu olmalı. Terminalde:

```bash
cd cloudflare
npm install
npx wrangler login
```

Worker'ın adresinden çağrı yapılmasını engellemek için güçlü bir tetikleme şifresi üret:

```bash
openssl rand -hex 32
```

Çıkan değeri ve GitHub tokenını Cloudflare'a gizli olarak kaydet:

```bash
npx wrangler secret put GITHUB_TOKEN
# ekranda sorunca GitHub tokenını yapıştır

npx wrangler secret put TRIGGER_SECRET
# ekranda sorunca openssl çıktısını yapıştır
```

Sonra yayınla:

```bash
npx wrangler deploy
```

## 3. Kontrol et

Deploy sonunda `https://otoxtra-github-scheduler.<hesabın>.workers.dev` benzeri bir adres görünür. Worker her saat çalışır. Hedef saatte GitHub Actions içindeki **otoXtra Bot** workflow'u kendiliğinden başlar.

Elle test etmek için:

```bash
curl -i -X POST \
  -H "Authorization: Bearer BURAYA_TR_TRIGGER_SECRET" \
  https://WORKER_ADRESIN
```

Bu komut yalnızca Türkiye saati hedef saatlerinden birindeyse Action başlatır. Diğer saatlerde `dispatched: false` döner; bu normaldir.

## 4. GitHub tarafında kontrol

Repository → **Settings → Actions → General** bölümünde workflow'ların çalışmasına izin verildiğinden emin ol. `GITHUB_TOKEN` yerine oluşturduğun fine-grained token kullanıldığı için ayrıca repository secret eklemek gerekmez.

GitHub Actions sayfasında `otoXtra Bot` workflow'unun **workflow_dispatch** tetikleyicisi açık kalmalıdır. Worker bu düğmeye basıyormuş gibi davranır.

## Saat mantığı

Cloudflare cron UTC ile `0 * * * *` olarak her saatin başında çalışır. Worker saati `Europe/Istanbul` zaman dilimine çevirir; yaz/kış saati değişiklikleriyle uğraşman gerekmez. Botun içindeki paylaşım aralığı ve state kontrolleri aynen çalışmaya devam eder.

## Güvenlik ve sorun giderme

- `GITHUB_TOKEN` ve `TRIGGER_SECRET` hiçbir dosyaya yazılmaz; yalnızca Cloudflare secret olarak tutulur.
- `401 Unauthorized`: İstek başlığındaki secret yanlış veya eksik.
- `502 Dispatch error`: Token süresi dolmuş olabilir ya da Actions yazma izni yoktur.
- Action hiç başlamıyorsa Worker logs'ta hata, GitHub'da workflow dosya adını ve branch'i kontrol et.
- Cloudflare Dashboard → Worker → **Logs** ekranı, hangi saatte dispatch yapıldığını gösterir.
