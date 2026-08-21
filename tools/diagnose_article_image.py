"""
tools/diagnose_article_image.py - Makale görseli teşhis aracı

Bir haber URL'si için görsel çekme zincirini adım adım çalıştırır ve
"neden görsel bulunamıyor?" sorusuna cevap verir:

  1. Makale sayfası scrape edilir -> bulunan adaylar (kaynak tipi + öncelik)
  2. Her aday indirilir -> boyut/oran doğrulaması (hangi aday neden elendi)
  3. (opsiyonel) Gemini VISION ile doğrulama -> görsel haberle uyumlu mu?

Kullanım:
  python tools/diagnose_article_image.py "https://www.log.com.tr/...haber..."
  python tools/diagnose_article_image.py "URL" --title "Haber başlığı" --summary "Özet"
  python tools/diagnose_article_image.py "URL" --no-vision   # vision çağrısı yapma
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.image_scraper import scrape_article_image_urls
from agents.image_utils import _get_image_validation_limits, _download_image_with_reason, _safe_unlink


def _limit_str(limits: dict) -> str:
    return (
        f"min {limits['min_width']}x{limits['min_height']} "
        f"(alan>={limits['min_area']}, oran {limits['min_aspect']}-{limits['max_aspect']})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Makale görseli teşhis aracı")
    parser.add_argument("url", help="Haber URL'si")
    parser.add_argument("--title", default="", help="Haber başlığı (vision doğrulama için)")
    parser.add_argument("--summary", default="", help="Haber özeti (vision doğrulama için)")
    parser.add_argument("--no-vision", action="store_true", help="Vision doğrulamasını atla")
    args = parser.parse_args()

    print("=" * 72)
    print(f"URL: {args.url}")
    limits = _get_image_validation_limits()
    print(f"Doğrulama limitleri: {_limit_str(limits)}")
    print("=" * 72)

    # 1) SCRAPE
    print("\n[1/3] Makale sayfası scrape ediliyor...")
    candidates = scrape_article_image_urls(args.url, max_candidates=15)
    if not candidates:
        print("  ❌ Hiç aday bulunamadı.")
        print("     Olası nedenler:")
        print("     - Site isteği engelliyor (403/Cloudflare) -> loga bak")
        print("     - Sayfa görselleri JS ile yükleniyor (SPA)")
        print("     - Sayfada hiç görsel yok")
    else:
        print(f"  ✅ {len(candidates)} aday bulundu:")
        for i, c in enumerate(candidates, 1):
            print(f"    {i:>2}. [prio={c.get('priority', '?'):>2} | {c.get('source_type', '?'):<20}] {c.get('url', '')[:100]}")

    # 2) İNDİR + DOĞRULA
    print(f"\n[2/3] Adaylar indirilip doğrulanıyor ({len(candidates)} aday)...")
    passed = []
    for i, c in enumerate(candidates, 1):
        url = c.get("url", "")
        path, reason = _download_image_with_reason(url, limits)
        if path:
            dims = reason.replace("ok:", "")
            print(f"    {i:>2}. ✅ İNDİRİLDİ {dims}  {url[:90]}")
            passed.append({"path": path, "url": url, "source_type": c.get("source_type", "?")})
        else:
            print(f"    {i:>2}. ❌ ELENDİ: {reason}  {url[:90]}")

    if not passed:
        print("\n  ➜ Hiçbir aday boyut/oran doğrulamasından geçemedi.")
        print("    Kaynakların thumbnail boyutları limitlerin altında olabilir;")
        print("    loglardaki 'too_small' / 'bad_aspect' nedenlerine bak.")
        return 1

    # 3) VISION (opsiyonel)
    print(f"\n[3/3] Vision doğrulaması ({len(passed)} görsel)...")
    if args.no_vision or not os.environ.get("GEMINI_API_KEY"):
        print("  - Vision atlandı (--no-vision veya GEMINI_API_KEY yok)")
        print("  ➜ Geçen ilk görsel: " + passed[0]["url"][:100])
        for p in passed:
            _safe_unlink(p["path"])
        return 0

    from agents.image_search import verify_image_relevance

    article = {"title": args.title or os.path.basename(args.url.rstrip("/")), "summary": args.summary}
    for i, p in enumerate(passed, 1):
        verdict = verify_image_relevance(p["path"], article)
        if verdict is True:
            print(f"    {i:>2}. ✅ VISION ONAYLADI  {p['url'][:90]}")
            print("\n  ➜ KULLANILABİLİR GÖRSEL: " + p["url"])
            for pp in passed:
                _safe_unlink(pp["path"])
            return 0
        if verdict is False:
            print(f"    {i:>2}. ❌ VISION REDDETTİ (alakasız)  {p['url'][:90]}")
        else:
            print(f"    {i:>2}. ⚠️  VISION doğrulayamadı (fail-closed'da reddedilir)  {p['url'][:90]}")

    print("\n  ➜ Hiçbir görsel Vision onayı alamadı (fail-closed'da text-only yayınlanır).")
    for p in passed:
        _safe_unlink(p["path"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
