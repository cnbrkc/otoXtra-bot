"""
image_handler.py — Görsel İşleme Modülü (v3 — Yedek Görsel Desteği)

Bu modül haber paylaşımı için görsel temin eder ve işler.

Görsel kaynakları (öncelik sırasıyla):
  1. RSS'den gelen image_url (news_fetcher tarafından çekilir)
  2. Haber sitesinden og:image scraping
  3. YEDEK GÖRSEL (fallback) — assets/fallback_image.jpg  ← YENİ v3

v3 Değişiklikler:
•   Yedek görsel desteği eklendi: Hiçbir kaynaktan görsel elde
    edilemezse assets/fallback_image.jpg kullanılır
•   prepare_image() ASLA None dönmez — her zaman bir görsel yolu döner
•   _create_fallback_copy() fonksiyonu eklendi
•   "Görselsiz paylaşma" durumu ortadan kalktı

Elde edilen görsel:
•   Facebook için uygun boyuta getirilir (1200×630 varsayılan)
•   otoXtra logosu/watermark eklenir
•   Geçici dosya olarak kaydedilir

Akış:
  prepare_image(article)
    ├─ download_image()      → RSS'den gelen URL'yi indir
    ├─ scrape_og_image()     → RSS'de yoksa siteden og:image çek
    ├─ _create_fallback_copy() → ikisi de yoksa yedek görseli kopyala  ← YENİ
    ├─ resize_and_crop()     → Facebook boyutuna getir
    └─ add_logo()            → logo/watermark ekle

Kullandığı modüller:
•   utils.py → load_config(), log(), get_project_root()

Kullandığı dosyalar:
•   config/settings.json    → images ayarları
•   assets/logo.png         → watermark logosu
•   assets/fallback_image.jpg → yedek görsel (v3)
"""

import os
import shutil
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
# 1) URL'den Görsel İndirme
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
                f"ℹ️ İndirilen dosya görsel değil "
                f"(Content-Type: {content_type})",
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
            log(
                f"⚠️ RSS görsel dosyası açılamadı: {img_err}",
                "WARNING",
            )
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return None

        log(
            f"✅ RSS görsel indirildi: {img_width}x{img_height} → "
            f"{temp_path}",
            "INFO",
        )
        return temp_path

    except requests.exceptions.Timeout:
        log(
            f"⚠️ RSS görsel indirme zaman aşımı: {image_url[:80]}",
            "WARNING",
        )
        return None
    except requests.exceptions.RequestException as req_err:
        log(
            f"⚠️ RSS görsel indirme HTTP hatası: {req_err}",
            "WARNING",
        )
        return None
    except Exception as e:
        log(
            f"⚠️ RSS görsel indirme beklenmeyen hata: {e}",
            "WARNING",
        )
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
        response = requests.get(
            url, headers=headers, timeout=_REQUEST_TIMEOUT
        )
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
        log(
            f"⚠️ og:image sayfa çekme zaman aşımı: {url}",
            "WARNING",
        )
        return None
    except requests.exceptions.RequestException as req_err:
        log(
            f"⚠️ og:image sayfa çekme hatası: {req_err}",
            "WARNING",
        )
        return None
    except Exception as e:
        log(f"⚠️ og:image beklenmeyen hata: {e}", "WARNING")
        return None


# ──────────────────────────────────────────────
# 3) Yedek Görsel Kopyalama (v3 — YENİ)
# ──────────────────────────────────────────────

def _create_fallback_copy() -> Optional[str]:
    """
    Yedek görseli (fallback_image.jpg) geçici dosyaya kopyalar.

    assets/fallback_image.jpg dosyasını okur ve temp dizinine
    kopyasını oluşturur. Böylece resize_and_crop() ve add_logo()
    orijinal dosyayı değiştirmez.

    Returns:
        Kopyalanan geçici dosya yolu. Dosya yoksa None.
    """
    fallback_path: str = os.path.join(
        get_project_root(), "assets", "fallback_image.jpg"
    )

    if not os.path.exists(fallback_path):
        log(
            f"❌ Yedek görsel dosyası bulunamadı: {fallback_path}",
            "ERROR",
        )
        log(
            "💡 Lütfen 1200x630 boyutunda bir JPEG dosyasını "
            "assets/fallback_image.jpg olarak kaydedin",
            "ERROR",
        )
        return None

    try:
        # Geçici dosyaya kopyala (orijinali korumak için)
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False, prefix="otoxtra_fallback_"
        )
        temp_path: str = temp_file.name
        temp_file.close()

        shutil.copy2(fallback_path, temp_path)

        # Dosya bütünlüğünü kontrol et
        img = Image.open(temp_path)
        img_width, img_height = img.size
        img.close()

        log(
            f"🔄 Yedek görsel kopyalandı: {img_width}x{img_height} → "
            f"{temp_path}",
            "INFO",
        )
        return temp_path

    except Exception as e:
        log(f"❌ Yedek görsel kopyalama hatası: {e}", "ERROR")
        try:
            os.unlink(temp_path)
        except (OSError, UnboundLocalError):
            pass
        return None


# ──────────────────────────────────────────────
# 4) Boyutlandırma ve Kırpma
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
    log(
        f"📐 Görsel boyutlandırılıyor: {target_width}x{target_height}",
        "INFO",
    )

    try:
        img = Image.open(image_path)
        img_width, img_height = img.size

        log(f"📐 Orijinal boyut: {img_width}x{img_height}", "INFO")

        target_ratio: float = target_width / target_height
        current_ratio: float = img_width / img_height

        if current_ratio > target_ratio:
            new_height: int = target_height
            new_width: int = int(
                img_width * (target_height / img_height)
            )
            img = img.resize((new_width, new_height), Image.LANCZOS)

            left: int = (new_width - target_width) // 2
            right: int = left + target_width
            img = img.crop((left, 0, right, new_height))

        elif current_ratio < target_ratio:
            new_width = target_width
            new_height = int(
                img_height * (target_width / img_width)
            )
            img = img.resize((new_width, new_height), Image.LANCZOS)

            top: int = (new_height - target_height) // 2
            bottom: int = top + target_height
            img = img.crop((0, top, new_width, bottom))

        else:
            img = img.resize(
                (target_width, target_height), Image.LANCZOS
            )

        if img.size != (target_width, target_height):
            img = img.resize(
                (target_width, target_height), Image.LANCZOS
            )

        # RGB moduna çevir
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.save(image_path, format="JPEG", quality=90)
        img.close()

        log(
            f"✅ Görsel boyutlandırıldı: {target_width}x{target_height}",
            "INFO",
        )
        return image_path

    except Exception as e:
        log(f"⚠️ Görsel boyutlandırma hatası: {e}", "WARNING")
        return image_path


# ──────────────────────────────────────────────
# 5) Logo / Watermark Ekleme
# ──────────────────────────────────────────────

def add_logo(image_path: str) -> str:
    """
    Ana görsele otoXtra logosunu (watermark) ekler.

    Logo ayarları settings.json'dan okunur:
      - logo_position:     konum (bottom_right, bottom_left, vb.)
      - logo_opacity:      saydamlık (0.0 - 1.0)
      - logo_size_percent: ana görselin genişliğinin yüzdesi

    Args:
        image_path: Logo eklenecek görselin dosya yolu.

    Returns:
        Logo eklenmiş görselin dosya yolu (aynı dosya üzerine yazılır).
    """
    settings_config: dict = load_config("settings")
    images_settings: dict = settings_config.get("images", {})

    logo_position: str = images_settings.get(
        "logo_position", "bottom_right"
    )
    logo_opacity: float = images_settings.get("logo_opacity", 0.7)
    logo_size_percent: int = images_settings.get("logo_size_percent", 15)
    padding: int = 20

    logo_path: str = os.path.join(
        get_project_root(), "assets", "logo.png"
    )

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

        logo_target_width: int = int(
            base_width * logo_size_percent / 100
        )

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
# 6) Ana Fonksiyon — Görsel Hazırlama (v3)
# ──────────────────────────────────────────────

def prepare_image(article: dict) -> str:
    """
    ANA FONKSİYON — Haber için görsel hazırlar (v3 — Yedek Görsel).

    Tüm adımları sırayla çalıştırır:
      1. RSS'den gelen image_url'yi indir (ÖNCELİKLİ)
      2. Başarısızsa haber sitesinden og:image çek
      3. İkisi de yoksa YEDEK GÖRSELİ kullan (v3)
      4. Görseli Facebook boyutuna getir (resize + crop)
      5. Logo/watermark ekle

    v3 DEĞİŞİKLİK: Bu fonksiyon ASLA None dönmez.
    Her zaman bir görsel yolu döner (en kötü ihtimalle yedek görsel).

    Article dict'ine "image_source" alanı eklenir:
      - "rss_image"      → RSS feed'den gelen görsel URL'si
      - "og:image"       → haber sitesinden çekildi
      - "fallback"       → yedek görsel kullanıldı (v3)

    Args:
        article: Haber dict'i (en az "link", "title", "image_url").

    Returns:
        str: İşlenmiş görselin dosya yolu. Her zaman geçerli bir yol döner.
    """
    separator: str = "-" * 40

    log(separator, "INFO")
    log(
        f"🖼️ Görsel hazırlama başlıyor: "
        f"{article.get('title', 'Başlık yok')[:80]}",
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
            log(
                "ℹ️ RSS görseli indirilemedi, og:image denenecek",
                "INFO",
            )
    else:
        log(
            "ℹ️ RSS'de görsel URL'si yok, og:image denenecek",
            "INFO",
        )

    # ── ADIM 2: Haber sitesinden og:image çek ──
    if image_path is None:
        can_scrape: bool = article.get("can_scrape_image", True)
        if can_scrape:
            article_link: str = article.get("link", "")
            if article_link:
                log(
                    "📸 ADIM 2: Haber sitesinden og:image çekiliyor...",
                    "INFO",
                )
                image_path = scrape_og_image(article_link)
                if image_path:
                    image_source = "og:image"
                    log("✅ Görsel og:image'den çekildi", "INFO")
                else:
                    log(
                        "ℹ️ og:image'den de görsel çekilemedi",
                        "INFO",
                    )
            else:
                log(
                    "ℹ️ Haber URL'si yok, og:image atlanıyor",
                    "INFO",
                )
        else:
            log(
                "ℹ️ Bu kaynak için görsel çekme devre dışı",
                "INFO",
            )

    # ── ADIM 3: YEDEK GÖRSEL (v3 — YENİ) ──
    if image_path is None:
        log(
            "🔄 ADIM 3: RSS ve og:image başarısız — "
            "YEDEK GÖRSEL kullanılıyor...",
            "WARNING",
        )
        image_path = _create_fallback_copy()
        if image_path:
            image_source = "fallback"
            log("✅ Yedek görsel yüklendi", "INFO")
        else:
            # Yedek görsel de yoksa — son çare
            # Bu durumda basit bir görsel oluştur
            log(
                "❌ Yedek görsel de bulunamadı — "
                "boş görsel oluşturuluyor...",
                "ERROR",
            )
            image_path = _create_emergency_image(
                feed_image_width, feed_image_height
            )
            image_source = "emergency"
            log("⚠️ Acil durum görseli oluşturuldu", "WARNING")

    # ── ADIM 4: Boyutlandır ve kırp ──
    log(
        f"📐 ADIM 4: Boyutlandırma "
        f"({feed_image_width}x{feed_image_height})",
        "INFO",
    )
    image_path = resize_and_crop(
        image_path, feed_image_width, feed_image_height
    )

    # ── ADIM 5: Logo ekle ──
    if should_add_logo:
        log("🏷️ ADIM 5: Logo/watermark ekleniyor...", "INFO")
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


# ──────────────────────────────────────────────
# 7) Acil Durum Görseli (v3 — Son Çare)
# ──────────────────────────────────────────────

def _create_emergency_image(width: int, height: int) -> str:
    """
    Yedek görsel dosyası bile yoksa son çare olarak
    basit düz renkli bir görsel oluşturur.

    Bu sadece assets/fallback_image.jpg dosyası olmadığında çalışır.
    Koyu gri arka plan üzerine beyaz bir çizgi koyar.

    Args:
        width:  Görsel genişliği.
        height: Görsel yüksekliği.

    Returns:
        Oluşturulan görselin geçici dosya yolu.
    """
    try:
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False, prefix="otoxtra_emergency_"
        )
        temp_path: str = temp_file.name
        temp_file.close()

        # Koyu gri arka plan
        img = Image.new("RGB", (width, height), (30, 30, 30))

        # Ortada ince beyaz şerit
        from PIL import ImageDraw

        draw = ImageDraw.Draw(img)
        stripe_y = height // 2
        stripe_height = 2
        draw.rectangle(
            [0, stripe_y, width, stripe_y + stripe_height],
            fill=(200, 200, 200),
        )

        img.save(temp_path, format="JPEG", quality=90)
        img.close()

        log(
            f"🆘 Acil durum görseli oluşturuldu: "
            f"{width}x{height} → {temp_path}",
            "INFO",
        )
        return temp_path

    except Exception as e:
        log(f"❌ Acil durum görseli oluşturulamadı: {e}", "ERROR")
        # Gerçekten hiçbir şey olmadıysa boş dosya oluştur
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False, prefix="otoxtra_empty_"
        )
        temp_path = temp_file.name
        temp_file.close()

        img = Image.new("RGB", (width, height), (50, 50, 50))
        img.save(temp_path, format="JPEG", quality=85)
        img.close()
        return temp_path
