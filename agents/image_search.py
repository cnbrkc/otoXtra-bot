"""
agents/image_search.py - DuckDuckGo ve AI Görsel Arama (v2.0 - Akıllı Yedek Görsel)

v2.0 UPDATE:
  - build_image_search_queries(): Haber başlığının ilk kelimelerini çöp gibi
    aratmak yerine, AI'dan marka+model odaklı HASSAS arama sorguları üretilir.
    AI yoksa marka/model sözlüğü ile deterministik çıkarım yapılır.
  - verify_image_relevance(): İndirilen aday görsel, Gemini VISION ile
    doğrulanır. Alakasız marka, stock fotoğraf veya konuyla ilgisiz görseller
    REDDEDİLİR. Vision kullanılamıyorsa fail-open (eski davranış).

Yedek görsel bulma yöntemleri (DDGS ve AI prompt) burada.
"""
import re
from typing import List, Optional

from ddgs import DDGS

from core.logger import log

_TR_ASCII_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")

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


# ── AKILLI SORGU ÜRETİMİ (v2.0) ───────────────────────────────────────────────

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


def _deterministic_search_queries(title: str) -> List[str]:
    """AI kullanılamadığında başlıktan marka+model çıkarımı yapar."""
    queries: List[str] = []
    lowered = _ascii_turkish(title).lower()

    brand_found = ""
    for brand in _KNOWN_BRANDS:
        pattern = r"(?<![a-z0-9])" + re.escape(_ascii_turkish(brand).lower()) + r"(?![a-z0-9])"
        if re.search(pattern, lowered):
            brand_found = brand
            break

    if brand_found:
        # Markadan sonra gelen model kelimelerini topla (en fazla 2)
        start_idx = lowered.find(_ascii_turkish(brand_found).lower()) + len(brand_found)
        rest = title[start_idx:]
        tokens = re.findall(r"[A-Za-zĞÜŞİÖÇğüşıöç0-9][A-Za-zĞÜŞİÖÇğüşıöç0-9\-+.]*", rest)
        model_tokens: List[str] = []
        for token in tokens[:6]:
            normalized = _ascii_turkish(token).lower().rstrip("'")
            if normalized in _QUERY_STOP_WORDS:
                break
            model_tokens.append(token)
            if len(model_tokens) == 2:
                break

        if model_tokens:
            queries.append(" ".join([brand_found] + model_tokens))
        queries.append(brand_found)

    # Genel içerik sorgusu: başlığın dolgu kelimeleri atılmış hali
    words = re.findall(r"[A-Za-zĞÜŞİÖÇğüşıöç0-9][A-Za-zĞÜŞİÖÇğüşıöç0-9\-]*", title)
    content_words = [w for w in words if _ascii_turkish(w).lower() not in _QUERY_STOP_WORDS]
    if content_words:
        queries.append(" ".join(content_words[:4]))

    return _dedupe_keep_order(queries)[:3]


def build_image_search_queries(article: dict) -> List[str]:
    """Haber için hassas görsel arama sorguları üretir.

    Önce AI'dan marka+model odaklı sorgular istenir (en özelden genele).
    AI başarısız olursa deterministik marka/model çıkarımı devreye girer.

    Returns:
        List[str]: Öncelik sırasına göre en fazla 3 sorgu. Boş dönmez;
        hiçbir şey çıkarılamazsa başlığın kısaltılmış hali kullanılır.
    """
    title = (article.get("title", "") or "").strip()
    summary = (article.get("summary", "") or "").strip()

    if not title:
        return []

    try:
        from core.ai_client import ask_ai, parse_ai_json
    except ImportError:
        return _deterministic_search_queries(title) or [title[:80]]

    prompt = (
        "Sen bir otomotiv haber sitesi için görsel arama uzmanısın.\n"
        "Aşağıdaki haber için DuckDuckGo görsel aramasında kullanılacak, "
        "haberle GERÇEKTEN eşleşen fotoğrafları bulma olasılığı en yüksek "
        "arama sorgularını üret.\n\n"
        "KURALLAR:\n"
        "- En fazla 3 sorgu yaz; EN ÖZELDEN EN GENİŞE sırala.\n"
        "- Haber belirli bir marka/model hakkında ise ilk sorgu MUTLAKA "
        "'marka model' formatında olmalı (ör. 'BMW iX3 2026').\n"
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
            result = _dedupe_keep_order(clean_queries)[:3]
            log(f"AI arama sorguları hazır: {result}", "INFO")
            return result

        log("AI sorgu üretimi boş döndü, deterministik çıkarıma geçiliyor", "WARNING")
    except Exception as exc:
        log(f"AI sorgu üretimi hatası: {exc}", "WARNING")

    fallback = _deterministic_search_queries(title)
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
