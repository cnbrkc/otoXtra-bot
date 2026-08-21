"""
agents/image_search.py - DuckDuckGo, Yapısal Kaynaklar ve AI Görsel Arama (v3.0)

v3.0 UPDATE (Güvenli Yedek Görsel):
  - YAPISAL KAYNAKLAR: Yedek aramada artık önce Wikipedia (pageimages),
    Wikimedia Commons (file search + categorymembers) ve CarAPI (marka+model
    -> Wikimedia fotoğrafı, ücretsiz/anahtarsız) taranır. Bu kaynaklarda
    görseller etiketli ve marka/model ile eşleşiktir; DDG gürültüsüne göre
    çok daha güvenilirdir.
  - SORGU KALİTESİ: Arama sorgularına haberden çıkarılan MODEL YILI eklenir
    ('marka model yıl' formatı); AI promptuna da yıl kuralı eklendi.
  - URL SİNYALİ: DDG adayları indirilmeden önce URL/filename içinde
    marka+model tokeni barındıranlar öne alınır (alakasız sonuç azalır).
  - FAIL-CLOSED KAPI: vision_gate_passed() ile yedek görsel kabulü artık
    varsayılan olarak SADECE Gemini VISION onayıyla olur (verdict True).
    Vision kullanılamıyorsa görsel reddedilir -> text-only paylaşım.
    (fallback_require_vision=false ile eski fail-open davranışa dönülebilir.)

v2.0 UPDATE:
  - build_image_search_queries(): Haber başlığının ilk kelimelerini çöp gibi
    aratmak yerine, AI'dan marka+model odaklı HASSAS arama sorguları üretilir.
    AI yoksa marka/model sözlüğü ile deterministik çıkarım yapılır.
  - verify_image_relevance(): İndirilen aday görsel, Gemini VISION ile
    doğrulanır. Alakasız marka, stock fotoğraf veya konuyla ilgisiz görseller
    REDDEDİLİR. Vision kullanılamıyorsa fail-open (eski davranış).

Yedek görsel bulma yöntemleri (DDG, Wikipedia, Commons, CarAPI ve AI) burada.
"""
import re
from typing import List, Optional, Tuple

import requests
from ddgs import DDGS

from core.logger import log

_TR_ASCII_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")

_WIKI_TIMEOUT = 12
_WIKI_USER_AGENT = (
    "otoXtra-bot/1.0 (https://github.com/cnbrkc/otoXtra-bot; "
    "news image fallback, automated bot)"
)
_WIKI_LANGS = ("tr", "en")
_CARAPI_URL = "https://carapi.trustcar.info/getImage"
_CARAPI_TIMEOUT = 10

# Yapısal kaynaklarda taranacak sorgu sayısı üst sınırı
_MAX_STRUCTURED_QUERIES = 2

# Sorgularda kullanılmayacak haber dolgu kelimeleri
_QUERY_STOP_WORDS = {
    "son", "dakika", "haber", "haberler", "turkiye", "turkiye'de", "resmen",
    "aciklandi", "duyuruldu", "tanitildi", "geldi", "cikti", "satisa", "girdi",
    "ortaya", "beklenen", "yeni", "iste", "bu", "bir", "ve", "ile", "icin",
    "hakkında", "hakkinda", "gelisti", "gelisme", "iddea", "iddia", "hamle",
    "kullanici", "kullanıcılara", "detay", "detaylar", "video", "foto",
    "fotoğraf", "fotograf", "kare", "goruntu", "görüntü", "an", "ani",
    "pazarda", "pazarina", "sektorde", "sektör", "dunya", "dünya", "avrupa",
    # Ek bağlaç/eki kelimeler (URL'de parçalanınca 'de','da' gibi ayrılır)
    "de", "da", "ki", "mi", "mu", "nin", "nun", "in", "un", "lar", "ler",
    "deki", "daki", "oldu", "olacak", "fiyat", "fiyati", "liste", "listesi",
    "belli", "nasıl", "nasil", "ne", "nerede", "hangi",
    # v3.0: Sık geçen haber fiilleri/yer adları (model çıkarımını kirletir)
    "yenilendi", "yenilenen", "guncellendi", "guncellenen", "guncellendi",
    "makyajlandi", "makyajlanan", "tanitti", "tanitan", "duyurdu", "sunuldu",
    "sundu", "almanya", "abd", "cini", "girdi", "giriyor", "girecek",
    "satista", "satis", "i", "ı",
}

# Deterministik sorgu çıkarımı için bilinen otomobil markaları.
# Resmi yazımla tutulur; eşleşme küçük harf üzerinden yapılır.
# Sıralama ÖNEMLİ: çok kelimeli/uzun markalar önce denenmelidir.
_KNOWN_BRANDS = [
    "Aston Martin", "Alfa Romeo", "Land Rover", "Range Rover",
    "Mercedes-Benz", "Mercedes", "Volkswagen", "Toyota", "BMW", "Audi", "Ford",
    "Renault", "Fiat", "Hyundai", "Kia", "Honda", "Tesla", "BYD", "Togg",
    "Volvo", "Peugeot", "Citroen", "Opel", "Skoda", "Mazda", "Nissan",
    "Mitsubishi", "Porsche", "Ferrari", "Lamborghini", "Cupra", "Dacia",
    "Chery", "Geely", "Jetour", "Omoda", "Jaecoo", "Leapmotor", "Mini",
    "Seat", "Lexus", "Subaru", "Suzuki", "Jeep", "Jaguar", "Chevrolet",
    "Dodge", "Cadillac", "Genesis", "Polestar", "Rivian", "Lucid",
    "Skywell", "VW", "MG",
]


def get_duckduckgo_image_candidates(article_title: str, max_results: int = 10) -> List[str]:
    """
    DuckDuckGo görsel arama motorunu kullanarak verilen sorgu için görsel URL'leri bulur.

    Args:
        article_title (str): Arama yapılacak sorgu (haber başlığı veya özel sorgu).
        max_results (int, optional): Maksimum dönecek görsel URL'si sayısı. Varsayılan: 10.

    Returns:
        List[str]: Bulunan görsel URL'lerinin listesi. Hata olursa boş liste döner.
    """
    try:
        clean_title = re.sub(r'http\S+', '', article_title).strip()
        clean_title = clean_title.lower()
        clean_title = re.sub(r'[^\w\s]', '', clean_title)

        tr_map = str.maketrans("çğıöşü", "cgiosu")
        clean_title = clean_title.translate(tr_map)

        words = clean_title.split()
        clean_title = " ".join(words[:8])

        if not clean_title:
            return []

        log(f"DDG Görsel Aranıyor: {clean_title}")

        with DDGS() as ddgs:
            results = list(ddgs.images(query=clean_title, max_results=max_results))

        if not results:
            log("DuckDuckGo görsel sonuç döndürmedi.", "WARNING")
            return []

        image_urls = [r.get("image") for r in results if r.get("image")]
        log(f"DuckDuckGo {len(image_urls)} adet görsel adayı buldu.")
        return image_urls

    except Exception as e:
        log(f"DuckDuckGo görsel arama hatası: {e}", "ERROR")
        return []


# ── AKILLI SORGU ÜRETİMİ (v3.0) ───────────────────────────────────────────────

def _ascii_turkish(text: str) -> str:
    return (text or "").translate(_TR_ASCII_MAP)


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        key = _ascii_turkish(item).lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _extract_model_year(title: str, summary: str = "") -> str:
    """Başlık/özetten model yılı çıkarır (ör. 2025, 2026).

    Yalnızca otomobil haberlerinde anlamlı olan 1990-2030 aralığı kabul edilir.
    Önce başlığa, bulunamazsa özete bakılır.
    """
    for text in (title or "", summary or ""):
        match = re.search(r"\b(20[0-2]\d|19[89]\d)\b", text)
        if match:
            year = int(match.group(1))
            if 1990 <= year <= 2030:
                return str(year)
    return ""


def _extract_brand_model(title: str) -> Tuple[str, str]:
    """Başlıktan marka + model çıkarır (deterministik, sözlük tabanlı).

    Returns:
        (marka, model) — model bulunamazsa model boş string olur.
        Marka bulunamazsa ("", "").
    """
    if not title:
        return "", ""
    lowered = _ascii_turkish(title).lower()
    for brand in _KNOWN_BRANDS:
        pattern = r"(?<![a-z0-9])" + re.escape(_ascii_turkish(brand).lower()) + r"(?![a-z0-9])"
        match = re.search(pattern, lowered)
        if not match:
            continue
        # _ascii_turkish birebir (1 karakter -> 1 karakter) eşleme yaptığı
        # için index'ler orijinal başlıkta da geçerlidir.
        rest = title[match.end():]
        tokens = re.findall(
            r"[A-Za-z0-9ÇĞİÖŞÜçğışöü][A-Za-z0-9ÇĞİÖŞÜçğışöü\-+.]*",
            rest,
        )
        model_parts = []
        for token in tokens[:6]:
            if re.fullmatch(r"\d{4}", token):
                continue  # yıl tokeni, model değil
            normalized = _ascii_turkish(token).lower().rstrip("'")
            if normalized in _QUERY_STOP_WORDS:
                break
            model_parts.append(token)
            if len(model_parts) == 2:
                break
        return brand, " ".join(model_parts)
    return "", ""


def _deterministic_search_queries(title: str, summary: str = "") -> List[str]:
    """AI kullanılamadığında başlıktan marka+model+yıl çıkarımı yapar."""
    queries: List[str] = []

    brand, model = _extract_brand_model(title)
    year = _extract_model_year(title, summary)

    if brand:
        base = f"{brand} {model}".strip() if model else brand
        queries.append(f"{base} {year}".strip() if year else base)
        queries.append(brand)

    # Genel içerik sorgusu: başlığın dolgu kelimeleri atılmış hali
    words = re.findall(r"[A-Za-zĞÜŞİÖÇğüşıöç0-9][A-Za-zĞÜŞİÖÇğüşıöç0-9\-]*", title or "")
    content_words = [w for w in words if _ascii_turkish(w).lower() not in _QUERY_STOP_WORDS]
    if content_words:
        content_query = " ".join(content_words[:4])
        if year and year not in content_query:
            content_query = f"{content_query} {year}"
        queries.append(content_query)

    return _dedupe_keep_order(queries)[:3]


def build_image_search_queries(article: dict) -> List[str]:
    """Haber için hassas görsel arama sorguları üretir.

    Önce AI'dan marka+model odaklı sorgular istenir (en özelden genele);
    haberde model yılı varsa ilk sorguya eklenir ('marka model yıl').
    AI başarısız olursa deterministik marka/model/yıl çıkarımı devreye girer.

    Returns:
        List[str]: Öncelik sırasına göre en fazla 3 sorgu. Boş dönmez;
        hiçbir şey çıkarılamazsa başlığın kısaltılmış hali kullanılır.
    """
    title = (article.get("title", "") or "").strip()
    summary = (article.get("summary", "") or "").strip()

    if not title:
        return []

    year = _extract_model_year(title, summary)

    try:
        from core.ai_client import ask_ai, parse_ai_json
    except ImportError:
        return _deterministic_search_queries(title, summary) or [title[:80]]

    prompt = (
        "Sen bir otomotiv haber sitesi için görsel arama uzmanısın.\n"
        "Aşağıdaki haber için DuckDuckGo görsel aramasında kullanılacak, "
        "haberle GERÇEKTEN eşleşen fotoğrafları bulma olasılığı en yüksek "
        "arama sorgularını üret.\n\n"
        "KURALLAR:\n"
        "- En fazla 3 sorgu yaz; EN ÖZELDEN EN GENİŞE sırala.\n"
        "- Haber belirli bir marka/model hakkında ise ilk sorgu MUTLAKA "
        "'marka model yıl' formatında olmalı (ör. 'BMW iX3 2025').\n"
        "- Haberde model yılı geçiyorsa (2024, 2025, 2026 gibi) ilk sorguya "
        "mutlaka ekle; yıl yoksa 'marka model' yaz.\n"
        "- İkinci sorgu modelin kısa hali (ör. 'BMW iX3'), üçüncüsü gerekirse "
        "biraz daha geniş bir alternatif (ör. 'BMW yeni SUV') olabilir.\n"
        "- Haber belirli bir araç hakkında değilse, konuyu en iyi anlatan "
        "2-4 kelimelik somut sorgular yaz.\n"
        "- Haber fiilleri ve dolgu kelimeleri YASAK: 'son dakika', 'haber', "
        "'açıklandı', 'Türkiye', 'resmen', 'duyuruldu' gibi kelimeler kullanma.\n"
        "- Her sorgu 2-6 kelime olsun.\n\n"
        "ÇIKTI: SADECE aşağıdaki JSON'u döndür, başka hiçbir şey yazma:\n"
        "{\"arama_sorgulari\": [\"...\", \"...\", \"...\"]}\n\n"
        f"HABER BAŞLIĞI: {title}\n"
        f"HABER ÖZETİ: {summary[:250]}\n"
    )

    try:
        response = ask_ai(prompt, stage="image_query", max_tokens=300)
        parsed = parse_ai_json(response) if response else None

        raw_queries: List[str] = []
        if isinstance(parsed, dict):
            candidate = parsed.get("arama_sorgulari") or parsed.get("sorgular") or []
            if isinstance(candidate, list):
                raw_queries = [str(q).strip() for q in candidate if str(q).strip()]
        elif isinstance(parsed, list):
            raw_queries = [str(q).strip() for q in parsed if str(q).strip()]

        clean_queries = []
        for query in raw_queries:
            query = re.sub(r"\s+", " ", query).strip("\"' ")
            if 2 <= len(query) <= 80:
                clean_queries.append(query)

        if clean_queries:
            # v3.0: Haberde yıl varsa ilk sorguya ekle (AI atlamışsa).
            if year and clean_queries and year not in clean_queries[0]:
                clean_queries[0] = f"{clean_queries[0]} {year}"
            result = _dedupe_keep_order(clean_queries)[:3]
            log(f"AI arama sorguları hazır: {result}", "INFO")
            return result

        log("AI sorgu üretimi boş döndü, deterministik çıkarıma geçiliyor", "WARNING")
    except Exception as exc:
        log(f"AI sorgu üretimi hatası: {exc}", "WARNING")

    fallback = _deterministic_search_queries(title, summary)
    if fallback:
        log(f"Deterministik arama sorguları: {fallback}", "INFO")
        return fallback

    # Son çare: başlığın ilk kelimeleri (eski davranış)
    return [" ".join(title.split()[:6])]


# ── VISION DOĞRULAMA KAPISI (v2.0) ────────────────────────────────────────────

def verify_image_relevance(image_path: str, article: dict) -> Optional[bool]:
    """İndirilen aday görselin haberle gerçekten eşleşip eşleşmediğini denetler.

    Gemini VISION ile görsel incelenir. Haber belirli bir marka/model
    hakkında ise farklı marka araçlar ve konuyla ilgisiz stock fotoğraflar
    REDDEDİLİR.

    Returns:
        True  -> görsel haberle uyumlu
        False -> görsel alakasız, KULLANMA
        None  -> doğrulama yapılamadı (AI/Gemini yok); çağıran fail-open karar verir
    """
    if not image_path:
        return None

    try:
        from core.ai_client import ask_ai_with_image, parse_ai_json
    except ImportError:
        return None

    title = (article.get("title", "") or "").strip()
    summary = (article.get("summary", "") or "").strip()
    if not title:
        return None

    prompt = (
        "Sen bir otomotiv haber sitesinin görsel denetleyicisisin.\n"
        "Ekteki görsel, aşağıdaki haber için bir görsel arama motorundan bulundu. "
        "Görevin: bu görselin haberle GERÇEKTEN eşleşip eşleşmediğine karar vermek.\n\n"
        f"HABER BAŞLIĞI: {title}\n"
        f"HABER ÖZETİ: {summary[:250]}\n\n"
        "KARAR KURALLARI:\n"
        "- Haber belirli bir marka/modelden bahsediyorsa görsel o marka/modeli "
        "veya doğrudan onunla ilgili bir sahneyi göstermeli. FARKLI markanın "
        "aracını gösteren görseller ALAKASIZDIR.\n"
        "- Haber belirli bir marka/modelden bahsetmiyorsa görselin haberin "
        "konusuyla doğrudan ilgili olması yeterli (ör. şarj haberi için şarj "
        "istasyonu, satış raporu haberi için otomobil pazarı sahnesi).\n"
        "- Haberle bağı olmayan stock fotoğraflar ALAKASIZDIR: boş yol, "
        "direksiyon close-up, yakıt pompası, anahtar, trafik, insan portresi.\n"
        "- Otomotivle ilgisiz görseller, ekran görüntüleri, logolar, meme'ler "
        "ALAKASIZDIR.\n"
        "- Kararsız kalırsan görselin gerçekten haberdeki konuyu gösterip "
        "göstermediğine bak; şüphe ALAKASIZ lehine kullanılsın.\n\n"
        "ÇIKTI: SADECE aşağıdaki JSON'u döndür, başka hiçbir şey yazma:\n"
        "{\"uyumlu\": true, \"gorulen\": \"görselde ne var, 5-10 kelime\", "
        "\"gerekce\": \"kısa karar gerekçesi\"}\n"
    )

    try:
        response = ask_ai_with_image(prompt, image_path)
        if not response:
            log("Vision doğrulama yapılamadı (yanıt yok) -> fail-open", "WARNING")
            return None

        parsed = parse_ai_json(response)
        if not isinstance(parsed, dict):
            log("Vision doğrulama JSON'u parse edilemedi -> fail-open", "WARNING")
            return None

        verdict = parsed.get("uyumlu")
        seen = str(parsed.get("gorulen", "") or "")[:80]
        if isinstance(verdict, bool):
            log(f"Vision doğrulama: uyumlu={verdict} (görülen: {seen})", "INFO")
            return verdict

        log("Vision doğrulama 'uyumlu' alanı bozuk -> fail-open", "WARNING")
        return None
    except Exception as exc:
        log(f"Vision doğrulama hatası: {exc} -> fail-open", "WARNING")
        return None


def _ai_search_image_url(article: dict) -> Optional[str]:
    """
    Yapay zeka (Gemini/Groq vb.) kullanarak haber başlığına uygun bir görsel URL'si bulur.
    AI'ya direkt URL döndürmesi için prompt gönderir.

    NOT (v2.0): Bu yöntem SON ÇARE olarak tutulur; bulunan URL ayrıca
    verify_image_relevance ile doğrulanır.

    Args:
        article (dict): Haber verisini içeren sözlük. 'title' anahtarı zorunludur.

    Returns:
        Optional[str]: Bulunan görsel URL'si. Bulunamazsa veya hata olursa None döner.
    """
    try:
        from core.ai_client import ask_ai
    except ImportError:
        log("AI gorsel arama: ai_client import edilemedi", "WARNING")
        return None

    title = (article.get("title", "") or "").strip()
    if not title:
        log("AI gorsel arama: Baslik bos, atlanıyor", "WARNING")
        return None

    prompt = (
        f"Find a publicly accessible image URL for this news headline. "
        f"Return ONLY the direct image URL (ending in .jpg, .jpeg, or .png), nothing else. "
        f"If you cannot find a suitable image, return the word NONE.\n\n"
        f"Headline: {title}"
    )

    try:
        log(f"AI gorsel arama baslatiliyor: {title[:60]}...")
        response = ask_ai(prompt, stage="image_search")

        if not response or not isinstance(response, str):
            log("AI gorsel arama: Bos/gecersiz yanit", "WARNING")
            return None

        response = response.strip()

        if response.upper() == "NONE" or not response:
            log("AI gorsel arama: AI gorsel bulamadi", "INFO")
            return None

        if response.startswith("http") and any(ext in response.lower() for ext in (".jpg", ".jpeg", ".png", ".webp")):
            log(f"AI gorsel arama: URL bulundu! {response[:80]}...")
            return response

        _url_pattern = re.compile(r'https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp)', re.IGNORECASE)
        url_match = _url_pattern.search(response)

        if url_match:
            found_url = url_match.group(0)
            log(f"AI gorsel arama: URL cikarildi! {found_url[:80]}...")
            return found_url

        log(f"AI gorsel arama: Yanit gecersiz format: {response[:100]}", "WARNING")
        return None

    except Exception as exc:
        log(f"AI gorsel arama hata: {exc}", "WARNING")
        return None


# ── YAPISAL KAYNAKLAR (v3.0) ──────────────────────────────────────────────────
# Wikipedia (pageimages), Wikimedia Commons (file search + category) ve CarAPI
# (marka+model -> Wikimedia fotoğrafı). Üçü de ücretsizdir, API anahtarı
# gerektirmez ve otomobil modellerinde etiketli/doğru görseller döndürür.
# Tüm çağrılar kısa timeout + try/except ile korunur; hata olursa boş döner
# ve akış DDG'ye düşer.


def _fetch_json(url: str, params: dict, timeout: int = _WIKI_TIMEOUT) -> Optional[dict]:
    try:
        resp = requests.get(
            url, params=params,
            headers={"User-Agent": _WIKI_USER_AGENT},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log(f"Yapısal görsel API hatası ({url}): {exc}", "WARNING")
        return None


def _wikipedia_api_url(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def _wikipedia_search_params(query: str, limit: int = 3) -> dict:
    return {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json",
    }


def _pageimages_params(titles: str, width: int = 1600) -> dict:
    return {
        "action": "query",
        "titles": titles,
        "prop": "pageimages",
        "piprop": "thumbnail|original",
        "pithumbsize": width,
        "redirects": "1",
        "format": "json",
    }


def get_wikipedia_image_candidates(queries: List[str], max_candidates: int = 4) -> List[dict]:
    """Wikipedia madde arama + lead image (pageimages) ile aday toplar.

    Returns:
        [{"url": str, "source_type": "wikipedia", "title": str}, ...]
    """
    if not queries:
        return []
    results: List[dict] = []
    seen: set = set()

    for query in queries[:_MAX_STRUCTURED_QUERIES]:
        if len(results) >= max_candidates:
            break
        for lang in _WIKI_LANGS:
            if len(results) >= max_candidates:
                break
            data = _fetch_json(_wikipedia_api_url(lang), _wikipedia_search_params(query))
            if not data:
                continue
            titles = [p.get("title", "") for p in data.get("query", {}).get("search", []) if p.get("title")]
            if not titles:
                continue
            img_data = _fetch_json(
                _wikipedia_api_url(lang),
                _pageimages_params("|".join(titles[:2])),
            )
            if not img_data:
                continue
            for page in img_data.get("query", {}).get("pages", {}).values():
                img = page.get("thumbnail") or page.get("original") or {}
                url = img.get("source", "")
                if url and url not in seen:
                    seen.add(url)
                    results.append({
                        "url": url,
                        "source_type": "wikipedia",
                        "title": page.get("title", ""),
                    })

    log(f"Wikipedia adayları: {len(results[:max_candidates])} görsel", "INFO")
    return results[:max_candidates]


def _commons_api_url() -> str:
    return "https://commons.wikimedia.org/w/api.php"


def _commons_search_params(query: str, limit: int = 8) -> dict:
    return {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": "6",
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|size",
        "iiurlwidth": 1600,
        "format": "json",
    }


def _commons_category_params(category: str, limit: int = 8) -> dict:
    return {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmtype": "file",
        "cmlimit": limit,
        "format": "json",
    }


def _append_commons_results(data: Optional[dict], results: List[dict], seen: set, source_type: str) -> None:
    if not data:
        return
    for page in data.get("query", {}).get("pages", {}).values():
        imageinfo = page.get("imageinfo") or [{}]
        info = imageinfo[0] if isinstance(imageinfo, list) else {}
        url = info.get("thumburl") or info.get("url") or ""
        if url and url not in seen:
            seen.add(url)
            results.append({
                "url": url,
                "source_type": source_type,
                "title": page.get("title", ""),
            })


def get_commons_image_candidates(queries: List[str], max_candidates: int = 6) -> List[dict]:
    """Wikimedia Commons dosya araması + kategori üyeleri ile aday toplar.

    Kategori denemesi yalnızca ilk sorgu için yapılır ve yalnızca dosya
    araması az sonuç verdiyse çalıştırılır (gereksiz çağrıyı önler).

    Returns:
        [{"url": str, "source_type": "commons", "title": str}, ...]
    """
    if not queries:
        return []
    results: List[dict] = []
    seen: set = set()

    for query in queries[:_MAX_STRUCTURED_QUERIES]:
        if len(results) >= max_candidates:
            break
        data = _fetch_json(_commons_api_url(), _commons_search_params(query))
        _append_commons_results(data, results, seen, "commons")

        if len(results) < 3 and query == queries[0]:
            # Ör. "BMW iX3" -> "Category:BMW iX3" altındaki dosyalar
            category = " ".join(w.capitalize() for w in query.split()[:3])
            cat_data = _fetch_json(_commons_api_url(), _commons_category_params(category))
            _append_commons_results(cat_data, results, seen, "commons_category")

    log(f"Commons adayları: {len(results[:max_candidates])} görsel", "INFO")
    return results[:max_candidates]


def get_carapi_candidate(title: str) -> Optional[dict]:
    """CarAPI (ücretsiz, anahtarsız): marka+model -> gerçek araç fotoğrafı.

    Kaynak Wikimedia Commons olduğu için görsel etiketli ve modelle eşleşiktir.
    Hata/sonuç yoksa None döner (akış bozulmaz).
    """
    brand, model = _extract_brand_model(title or "")
    if not brand or not model:
        return None
    data = _fetch_json(_CARAPI_URL, {"make": brand, "model": model, "format": "json"}, timeout=_CARAPI_TIMEOUT)
    if not data or not data.get("found"):
        return None
    image_url = data.get("image_url") or data.get("image") or ""
    if not image_url:
        return None
    log(f"CarAPI adayı: {brand} {model} -> {image_url[:80]}", "INFO")
    return {"url": image_url, "source_type": "carapi", "title": f"{brand} {model}"}


# ── URL SİNYALİ (v3.0) ────────────────────────────────────────────────────────
# DDG sonuçları indirilmeden önce URL/filename içinde marka+model tokeni
# arayan ucuz bir ön-filtre: alakasız sonuçlar sıralamada arkaya düşer.


def _url_signal_score(url: str, queries: List[str]) -> int:
    """URL/filename'de arama sorgusundaki marka+model tokenlerini sayar."""
    if not url or not queries:
        return 0
    low_url = _ascii_turkish(url).lower()
    tokens: set = set()
    for query in queries:
        for token in re.findall(r"[a-z0-9]+", _ascii_turkish(query).lower()):
            if len(token) >= 3 and token not in _QUERY_STOP_WORDS:
                tokens.add(token)
    score = 0
    for token in tokens:
        if token in low_url:
            score += 1
    return score


# ── VISION KAPISI (v3.0) ──────────────────────────────────────────────────────
# Yedek görsel kabulü. require_vision=True ise YALNIZCA Gemini VISION onayıyla
# kabul edilir (fail-closed); Vision kullanılamıyorsa görsel reddedilir ve
# bot text-only paylaşım yapar. require_vision=False eski fail-open davranıştır.


def vision_gate_passed(verdict: Optional[bool], require_vision: bool) -> bool:
    """Yedek görselin kabul edilip edilmeyeceğine karar verir.

    Args:
        verdict: verify_image_relevance sonucu (True uyumlu, False alakasız,
                 None doğrulama yapılamadı).
        require_vision: True -> fail-closed (yalnızca True kabul);
                        False -> fail-open (yalnızca False red).
    """
    if require_vision:
        return verdict is True
    return verdict is not False
