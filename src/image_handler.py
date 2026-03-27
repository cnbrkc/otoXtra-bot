"""
image_handler.py — Görsel İşleme Modülü (v2 — Sadece Haber Görseli)

Bu modül haber paylaşımı için görsel temin eder ve işler.
YZ görsel üretimi KALDIRILDI — sadece haber kaynağından görsel çekilir.

Görsel kaynakları (öncelik sırasıyla):
  1. RSS'den gelen image_url (news_fetcher tarafından çekilir)
  2. Haber sitesinden og:image scraping

Elde edilen görsel:
  - Facebook için uygun boyuta getirilir (1200×630 varsayılan)
  - otoXtra logosu/watermark eklenir
  - Geçici dosya olarak kaydedilir

Akış:
  prepare_image(article)
    ├─ download_image()      → RSS'den gelen URL'yi indir
    ├─ scrape_og_image()     → RSS'de yoksa siteden og:image çek
    ├─ resize_and_crop()     → Facebook boyutuna getir
    └─ add_logo()            → logo/watermark ekle

Kullandığı modüller:
  - utils.py → load_config(), log(), get_project_root()

Kullandığı dosyalar:
  - config/settings.json → images ayarları
  - assets/logo.png      → watermark logosu

NOT: ai_processor.py artık import EDİLMİYOR (YZ görsel üretimi kaldırıldı)
"""

import os
import tempfile
from typing import Optional

import requests
from PIL import Image

from utils import load_config, log, get_project_root


# ──────────────────────────────────────────────
# Sabitler
# ──────────────────────────────────────────────

_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_REQUEST_TIMEOUT: int = 15
_MIN_IMAGE_WIDTH: int = 400  # Minimum genişlik (v2: 600→400 düşürüldü)


# ──────────────────────────────────────────────
# 1) URL'den Görsel İndirme (YENİ — v2)
# ──────────────────────────────────────────────

def download_image(image_url: str) -> Optional[str]:
    """
    Verilen URL'den görseli indirir ve geçici dosyaya kaydeder.

    RSS feed'den gelen image_url için kullanılır.
    og:image scraping'den farklı olarak direkt URL'ye gider,
    HTML parse etmez.

    Args:
        image_url: İndirilecek görselin URL'si.

    Returns:
        İndirilen görselin geçici dosya yolu. Başarısızsa None.
    """
    if not image_url:
        return None

    try:
        log(f"📥 RSS görsel indiriliyor: {image_url[:100]}...", "INFO")

        headers: dict = {"User-Agent": _USER_AGENT}
        response = requests.get(
            image_url,
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
            stream=True,
        )
        response.raise_for_status()

        # Content-Type kontrolü
        content_type: str = response.headers.get("Content-Type", "")
        if content_type and not content_type.startswith("image/"):
            log(
                f"ℹ️ İndirilen dosya görsel değil (Content-Type: {content_type})",
                "INFO",
            )
            return None

        # Geçici dosyaya kaydet
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False, prefix="otoxtra_rss_"
        )
        temp_path: str = temp_file.name

        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                temp_file.write(chunk)
        temp_file.close()

        # Boyut kontrolü (Pillow ile)
        try:
            img = Image.open(temp_path)
            img_width, img_height = img.size
            img.close()

            if img_width < _MIN_IMAGE_WIDTH:
                log(
                    f"ℹ️ RSS görsel çok küçük ({img_width}x{img_height}), "
                    f"minimum {_MIN_IMAGE_WIDTH}px gerekli",
                    "INFO",
                )
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                return None

        except Exception as img_err:
            log(f"⚠️ RSS görsel dosyası açılamadı: {img_err}", "WARNING")
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return None

        log(f"✅ RSS görsel indirildi: {img_width}x{img_height} → {temp_path}", "INFO")
        return temp_path

    except requests.exceptions.Timeout:
        log(f"⚠️ RSS görsel indirme zaman aşımı: {image_url[:80]}", "WARNING")
        return None
    except requests.exceptions.RequestException as req_err:
        log(f"⚠️ RSS görsel indirme HTTP hatası: {req_err}", "WARNING")
        return None
    except Exception as e:
        log(f"⚠️ RSS görsel indirme beklenmeyen hata: {e}", "WARNING")
        return None


# ──────────────────────────────────────────────
# 2) Haber Sitesinden og:image Çekme
# ──────────────────────────────────────────────

def scrape_og_image(url: str) -> Optional[str]:
    """
    Haber URL'sindeki og:image meta etiketinden görsel indirir.

    İşleyiş:
      1. Haber sayfasının HTML'ini indir
      2. <meta property="og:image" content="..."> etiketini bul
      3. Görseli indir, boyut ve tip kontrolü yap
      4. Geçici dosyaya kaydet

    Args:
        url: Haber sayfasının URL'si.

    Returns:
        İndirilen görselin geçici dosya yolu. Başarısızsa None.
    """
    if not url:
        log("⚠️ Görsel çekme: URL boş", "WARNING")
        return None

    try:
        log(f"🔍 og:image aranıyor: {url[:80]}...", "INFO")

        headers: dict = {"User-Agent": _USER_AGENT}
        response = requests.get(url, headers=headers, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()

        # HTML'i parse et
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(response.text, "html.parser")

        # og:image meta tag'ini bul
        og_tag = soup.find("meta", property="og:image")

        if not og_tag:
            og_tag = soup.find("meta", property="og:image:url")

        if not og_tag or not og_tag.get("content"):
            log("ℹ️ og:image etiketi bulunamadı", "INFO")
            return None

        image_url: str = og_tag["content"].strip()

        if not image_url.startswith("http"):
            log(f"ℹ️ og:image URL geçersiz: {image_url[:80]}", "INFO")
            return None

        # Görseli indir (download_image fonksiyonunu kullan)
        return download_image(image_url)

    except requests.exceptions.Timeout:
        log(f"⚠️ og:image sayfa çekme zaman aşımı: {url}", "WARNING")
        return None
    except requests.exceptions.RequestException as req_err:
        log(f"⚠️ og:image sayfa çekme hatası: {req_err}", "WARNING")
        return None
    except Exception as e:
        log(f"⚠️ og:image beklenmeyen hata: {e}", "WARNING")
        return None


# ──────────────────────────────────────────────
# 3) Boyutlandırma ve Kırpma
# ──────────────────────────────────────────────

def resize_and_crop(
    image_path: str,
    target_width: int,
    target_height: int,
) -> str:
    """
    Görseli hedef boyuta getirir (resize + center crop).

    İşleyiş:
      1. En-boy oranlarını karşılaştır
      2. Kısa kenarı hedef boyuta getir (oranı koruyarak resize)
      3. Uzun kenardan ortadan kırp (center crop)
      4. Tam hedef boyuta getir
      5. RGB moduna çevir (JPEG uyumluluğu)

    Args:
        image_path:    İşlenecek görselin dosya yolu.
        target_width:  Hedef genişlik (piksel).
        target_height: Hedef yükseklik (piksel).

    Returns:
        İşlenmiş görselin dosya yolu (aynı dosya üzerine yazılır).
    """
    log(f"📐 Görsel boyutlandırılıyor: {target_width}x{target_height}", "INFO")

    try:
        img = Image.open(image_path)
        img_width, img_height = img.size

        log(f"📐 Orijinal boyut: {img_width}x{img_height}", "INFO")

        target_ratio: float = target_width / target_height
        current_ratio: float = img_width / img_height

        if current_ratio > target_ratio:
            new_height: int = target_height
            new_width: int = int(img_width * (target_height / img_height))
            img = img.resize((new_width, new_height), Image.LANCZOS)

            left: int = (new_width - target_width) // 2
            right: int = left + target_width
            img = img.crop((left, 0, right, new_height))

        elif current_ratio < target_ratio:
            new_width = target_width
            new_height = int(img_height * (target_width / img_width))
            img = img.resize((new_width, new_height), Image.LANCZOS)

            top: int = (new_height - target_height) // 2
            bottom: int = top + target_height
            img = img.crop((0, top, new_width, bottom))

        else:
            img = img.resize((target_width, target_height), Image.LANCZOS)

        if img.size != (target_width, target_height):
            img = img.resize((target_width, target_height), Image.LANCZOS)

        # RGB moduna çevir
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.save(image_path, format="JPEG", quality=90)
        img.close()

        log(f"✅ Görsel boyutlandırıldı: {target_width}x{target_height}", "INFO")
        return image_path

    except Exception as e:
        log(f"⚠️ Görsel boyutlandırma hatası: {e}", "WARNING")
        return image_path


# ──────────────────────────────────────────────
# 4) Logo / Watermark Ekleme
# ──────────────────────────────────────────────

def add_logo(image_path: str) -> str:
    """
    Ana görsele otoXtra logosunu (watermark) ekler.

    Logo ayarları settings.json'dan okunur:
      - logo_position:     konum (bottom_right, bottom_left, top_right, top_left)
      - logo_opacity:      saydamlık (0.0 - 1.0)
      - logo_size_percent: ana görselin genişliğinin yüzdesi olarak logo boyutu

    Args:
        image_path: Logo eklenecek görselin dosya yolu.

    Returns:
        Logo eklenmiş görselin dosya yolu (aynı dosya üzerine yazılır).
    """
    settings_config: dict = load_config("settings")
    images_settings: dict = settings_config.get("images", {})

    logo_position: str = images_settings.get("logo_position", "bottom_right")
    logo_opacity: float = images_settings.get("logo_opacity", 0.7)
    logo_size_percent: int = images_settings.get("logo_size_percent", 15)
    padding: int = 20

    logo_path: str = os.path.join(get_project_root(), "assets", "logo.png")

    if not os.path.exists(logo_path):
        log(
            f"⚠️ Logo dosyası bulunamadı: {logo_path}. "
            "Logo eklenmeden devam ediliyor.",
            "WARNING",
        )
        return image_path

    try:
        base_img = Image.open(image_path)
        base_width, base_height = base_img.size

        if base_img.mode != "RGBA":
            base_img = base_img.convert("RGBA")

        logo_img = Image.open(logo_path)

        if logo_img.mode != "RGBA":
            logo_img = logo_img.convert("RGBA")

        logo_target_width: int = int(base_width * logo_size_percent / 100)

        logo_orig_width, logo_orig_height = logo_img.size
        aspect_ratio: float = logo_orig_height / logo_orig_width
        logo_target_height: int = int(logo_target_width * aspect_ratio)

        logo_img = logo_img.resize(
            (logo_target_width, logo_target_height),
            Image.LANCZOS,
        )

        logo_width, logo_height = logo_img.size

        r, g, b, alpha = logo_img.split()
        alpha = alpha.point(lambda p: int(p * logo_opacity))
        logo_img = Image.merge("RGBA", (r, g, b, alpha))

        position_map: dict = {
            "bottom_right": (
                base_width - logo_width - padding,
                base_height - logo_height - padding,
            ),
            "bottom_left": (
                padding,
                base_height - logo_height - padding,
            ),
            "top_right": (
                base_width - logo_width - padding,
                padding,
            ),
            "top_left": (
                padding,
                padding,
            ),
        }

        pos_x, pos_y = position_map.get(
            logo_position,
            position_map["bottom_right"],
        )

        pos_x = max(0, pos_x)
        pos_y = max(0, pos_y)

        overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
        overlay.paste(logo_img, (pos_x, pos_y))

        base_img = Image.alpha_composite(base_img, overlay)

        final_img = base_img.convert("RGB")
        final_img.save(image_path, format="JPEG", quality=90)

        base_img.close()
        logo_img.close()
        overlay.close()
        final_img.close()

        log(
            f"✅ Logo eklendi: pozisyon={logo_position}, "
            f"opaklık={logo_opacity}, boyut=%{logo_size_percent}",
            "INFO",
        )
        return image_path

    except Exception as e:
        log(f"⚠️ Logo ekleme hatası: {e}", "WARNING")
        return image_path


# ──────────────────────────────────────────────
# 5) Ana Fonksiyon — Görsel Hazırlama (v2)
# ──────────────────────────────────────────────

def prepare_image(article: dict) -> Optional[str]:
    """
    ANA FONKSİYON — Haber için görsel hazırlar (v2 — YZ üretimi yok).

    Tüm adımları sırayla çalıştırır:
      1. RSS'den gelen image_url'yi indir (ÖNCELİKLİ — v2)
      2. Başarısızsa haber sitesinden og:image çek
      3. Görseli Facebook boyutuna getir (resize + crop)
      4. Logo/watermark ekle

    YZ görsel üretimi KALDIRILDI. Görsel bulunamazsa None döner.

    Article dict'ine "image_source" alanı eklenir:
      - "rss_image"      → RSS feed'den gelen görsel URL'si (v2)
      - "og:image"       → haber sitesinden çekildi
      - None             → görsel elde edilemedi

    Args:
        article: Haber dict'i (en az "link", "title", "image_url" alanları).

    Returns:
        İşlenmiş görselin dosya yolu. Hiç görsel elde edilemediyse None.
    """
    separator: str = "-" * 40

    log(separator, "INFO")
    log(
        f"🖼️ Görsel hazırlama başlıyor: {article.get('title', 'Başlık yok')[:80]}",
        "INFO",
    )

    # ── Ayarları oku ──
    settings_config: dict = load_config("settings")
    images_settings: dict = settings_config.get("images", {})

    should_add_logo: bool = images_settings.get("add_logo", True)
    feed_image_width: int = images_settings.get("feed_image_width", 1200)
    feed_image_height: int = images_settings.get("feed_image_height", 630)

    image_path: Optional[str] = None
    image_source: Optional[str] = None

    # ── ADIM 1: RSS'den gelen görsel URL'sini indir (ÖNCELİKLİ) ──
    rss_image_url: str = article.get("image_url", "")
    if rss_image_url:
        log("📸 ADIM 1: RSS'den gelen görsel indiriliyor...", "INFO")
        image_path = download_image(rss_image_url)
        if image_path:
            image_source = "rss_image"
            log("✅ Görsel RSS'den indirildi", "INFO")
        else:
            log("ℹ️ RSS görseli indirilemedi, og:image denenecek", "INFO")
    else:
        log("ℹ️ RSS'de görsel URL'si yok, og:image denenecek", "INFO")

    # ── ADIM 2: Haber sitesinden og:image çek ──
    if image_path is None:
        can_scrape: bool = article.get("can_scrape_image", True)
        if can_scrape:
            article_link: str = article.get("link", "")
            if article_link:
                log("📸 ADIM 2: Haber sitesinden og:image çekiliyor...", "INFO")
                image_path = scrape_og_image(article_link)
                if image_path:
                    image_source = "og:image"
                    log("✅ Görsel og:image'den çekildi", "INFO")
                else:
                    log("ℹ️ og:image'den de görsel çekilemedi", "INFO")
            else:
                log("ℹ️ Haber URL'si yok, og:image atlanıyor", "INFO")
        else:
            log("ℹ️ Bu kaynak için görsel çekme devre dışı", "INFO")

    # ── Görsel elde edilemediyse ──
    if image_path is None:
        log("❌ Hiçbir kaynaktan görsel elde edilemedi — görselsiz devam", "WARNING")
        article["image_source"] = None
        return None

    # ── ADIM 3: Boyutlandır ve kırp ──
    log(
        f"📐 ADIM 3: Boyutlandırma ({feed_image_width}x{feed_image_height})",
        "INFO",
    )
    image_path = resize_and_crop(image_path, feed_image_width, feed_image_height)

    # ── ADIM 4: Logo ekle ──
    if should_add_logo:
        log("🏷️ ADIM 4: Logo/watermark ekleniyor...", "INFO")
        image_path = add_logo(image_path)
    else:
        log("ℹ️ Logo ekleme devre dışı (ayarlarda kapalı)", "INFO")

    # ── Article'a kaynak bilgisi ekle ──
    article["image_source"] = image_source

    log(
        f"✅ Görsel hazır: kaynak={image_source}, dosya={image_path}",
        "INFO",
    )
    log(separator, "INFO")

    return image_path
