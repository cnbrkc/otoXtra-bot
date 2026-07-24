"""
agents/image_search.py - DuckDuckGo ve AI Görsel Arama
Yedek görsel bulma yöntemleri (DDGS ve AI prompt) burada.
"""
import re
from typing import List, Optional
from ddgs import DDGS
from core.logger import log

def get_duckduckgo_image_candidates(article_title: str, max_results: int = 10) -> List[str]:
    """
    DuckDuckGo görsel arama motorunu kullanarak verilen başlık için görsel URL'leri bulur.
    
    Args:
        article_title (str): Arama yapılacak haber başlığı.
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

def _ai_search_image_url(article: dict) -> Optional[str]:
    """
    Yapay zeka (Gemini/Groq vb.) kullanarak haber başlığına uygun bir görsel URL'si bulur.
    AI'ya direkt URL döndürmesi için prompt gönderir.
    
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
