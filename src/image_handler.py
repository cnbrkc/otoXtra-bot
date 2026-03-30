"""
image_handler.py — Görsel İşleme Modülü (v3.1 — Lacivert Yedek Görsel)

Bu modül haber paylaşımı için görsel temin eder ve işler.

Görsel kaynakları (öncelik sırasıyla):
  1. RSS'den gelen image_url (news_fetcher tarafından çekilir)
  2. Haber sitesinden og:image scraping
  3. YEDEK GÖRSEL — lacivert arka plan + solid logo (v3.1)

v3.1 Değişiklikler:
•   Yedek görsel: #12192c lacivert arka plan üzerine solid logo
•   Logo ince şerit hissiyatında — büyük arka plan, küçük logo
•   prepare_image() ASLA None dönmez — her zaman görsel yolu döner
•   _create_fallback_image() fonksiyonu eklendi
•   Acil durum görseli de aynı lacivert tema ile oluşturulur

Akış:
  prepare_image(article)
    ├─ download_image()          → RSS'den gelen URL'yi indir
    ├─ scrape_og_image()         → RSS'de yoksa siteden og:image çek
    ├─ _create_fallback_image()  → ikisi de yoksa lacivert+logo oluştur
    ├─ resize_and_crop()         → Facebook boyutuna getir
    └─ add_logo()                → logo/watermark ekle (sadece haber görseli)

Kullandığı dosyalar:
•   config/settings.json        → images ayarları
•   assets/logo.png             → watermark logosu (transparan)
•   assets/logo_solid.png       → yedek görsel logosu (transparan olmayan)
"""

import os
import tempfile
from typing import Optional

import requests
from PIL import Image, ImageDraw

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
_MIN_IMAGE_WIDTH: int = 400

# Yedek görsel renk ayarları (v3.1)
_FALLBACK_BG_COLOR: tuple = (18, 25, 44)     # #12192c — lacivert
_FALLBACK_STRIPE_COLOR: tuple = (24, 35, 60)  # biraz daha açık lacivert (ince şerit)


# ──────────────────────────────────────────────
# 1) URL'den Görsel İndirme
# ──────────────────────────────────────────────

def download_image(image_url: str) -> Optional[str]:
    """
    Verilen URL'den görseli indirir ve geçici dosyaya kaydeder.

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

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(response.text, "html.parser")

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
# 3) Yedek Görsel Oluşturma (v3.1 — Lacivert + Solid Logo)
# ──────────────────────────────────────────────

def _create_fallback_image(width: int, height: int) -> str:
    """
    Lacivert arka plan üzerine solid logo ile yedek görsel oluşturur.

    Tasarım:
    ┌──────────────────────────────────────────┐
    │                                          │
    │          #12192c lacivert arka plan       │
    │     ─────────────────────────────────     │  ← ince açık şerit (üst)
    │                                          │
    │              ┌──────────┐                │
    │              │  LOGO    │                │  ← solid logo, ortada
    │              │ (solid)  │                │
    │              └──────────┘                │
    │                                          │
    │     ─────────────────────────────────     │  ← ince açık şerit (alt)
    │          #12192c lacivert arka plan       │
    │                                          │
    └──────────────────────────────────────────┘

    Logo, görselin %25'i kadar genişlikte tutulur (ince şerit hissi).

    Args:
        width:  Görsel genişliği (varsayılan 1200).
        height: Görsel yüksekliği (varsayılan 630).

    Returns:
        Oluşturulan görselin geçici dosya yolu.
    """
    # ── Solid logo dosyasını bul ──
    project_root: str = get_project_root()

    # Önce logo_solid.png dene, yoksa logo_solid.jpg, yoksa logo.png kullan
    logo_candidates = [
        os.path.join(project_root, "assets", "logo_solid.png"),
        os.path.join(project_root, "assets", "logo_solid.jpg"),
        os.path.join(project_root, "assets", "logo.png"),
    ]

    logo_path: Optional[str] = None
    for candidate in logo_candidates:
        if os.path.exists(candidate):
            logo_path = candidate
            break

    try:
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False, prefix="otoxtra_fallback_"
        )
        temp_path: str = temp_file.name
        temp_file.close()

        # ── Lacivert arka plan oluştur ──
        img = Image.new("RGB", (width, height), _FALLBACK_BG_COLOR)
        draw = ImageDraw.Draw(img)

        # ── İnce dekoratif şeritler (üst ve alt) ──
        stripe_thickness: int = 2
        stripe_margin: int = int(height * 0.22)  # üst/alttan %22 içeride
        stripe_padding_x: int = int(width * 0.15)  # soldan/sağdan %15 boşluk

        # Üst şerit
        draw.rectangle(
            [
                stripe_padding_x,
                stripe_margin,
                width - stripe_padding_x,
                stripe_margin + stripe_thickness,
            ],
            fill=_FALLBACK_STRIPE_COLOR,
        )

        # Alt şerit
        draw.rectangle(
            [
                stripe_padding_x,
                height - stripe_margin - stripe_thickness,
                width - stripe_padding_x,
                height - stripe_margin,
            ],
            fill=_FALLBACK_STRIPE_COLOR,
        )

        # ── Logo ekle (varsa) ──
        if logo_path:
            try:
                logo_img = Image.open(logo_path)

                # RGBA'ya çevir (transparan veya değil fark etmez)
                if logo_img.mode != "RGBA":
                    logo_img = logo_img.convert("RGBA")

                # Logo boyutu: görselin %25 genişliğinde (ince şerit hissi)
                logo_max_width: int = int(width * 0.25)
                logo_orig_w, logo_orig_h = logo_img.size
                aspect: float = logo_orig_h / logo_orig_w
                logo_new_width: int = logo_max_width
                logo_new_height: int = int(logo_new_width * aspect)

                # Yükseklik sınırı — görselin %30'unu geçmesin
                max_logo_height: int = int(height * 0.30)
                if logo_new_height > max_logo_height:
                    logo_new_height = max_logo_height
                    logo_new_width = int(logo_new_height / aspect)

                logo_img = logo_img.resize(
                    (logo_new_width, logo_new_height),
                    Image.LANCZOS,
                )

                # Tam ortaya yerleştir
                paste_x: int = (width - logo_new_width) // 2
                paste_y: int = (height - logo_new_height) // 2

                # RGBA arka plan ile birleştir
                img_rgba = img.convert("RGBA")
                overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                overlay.paste(logo_img, (paste_x, paste_y))
                img_rgba = Image.alpha_composite(img_rgba, overlay)
                img = img_rgba.convert("RGB")

                logo_img.close()
                overlay.close()
                img_rgba.close()

                log(
                    f"✅ Yedek görsel oluşturuldu: {width}x{height}, "
                    f"logo={os.path.basename(logo_path)} "
                    f"({logo_new_width}x{logo_new_height})",
                    "INFO",
                )

            except Exception as logo_err:
                log(
                    f"⚠️ Yedek görsele logo eklenemedi: {logo_err}",
                    "WARNING",
                )
                log(
                    "⚠️ Sadece lacivert arka plan kullanılacak",
                    "WARNING",
                )
        else:
            log(
                "⚠️ Logo dosyası bulunamadı — "
                "sadece lacivert arka plan oluşturuldu",
                "WARNING",
            )

        # ── Kaydet ──
        img.save(temp_path, format="JPEG", quality=95)
        img.close()

        log(
            f"🔄 Yedek görsel hazır: {width}x{height} → {temp_path}",
            "INFO",
        )
        return temp_path

    except Exception as e:
        log(f"❌ Yedek görsel oluşturma hatası: {e}", "ERROR")

        # Son çare: düz lacivert
        try:
            temp_file = tempfile.NamedTemporaryFile(
                suffix=".jpg", delete=False, prefix="otoxtra_emergency_"
            )
            temp_path = temp_file.name
            temp_file.close()

            img = Image.new("RGB", (width, height), _FALLBACK_BG_COLOR)
            img.save(temp_path, format="JPEG", quality=90)
            img.close()
            return temp_path
        except Exception as e2:
            log(f"❌ Son çare görsel de oluşturulamadı: {e2}", "ERROR")
            # Boş dosya bile oluştur
            temp_file = tempfile.NamedTemporaryFile(
                suffix=".jpg", delete=False, prefix="otoxtra_empty_"
            )
            temp_path = temp_file.name
            temp_file.close()
            img = Image.new("RGB", (width, height), (50, 50, 50))
            img.save(temp_path, format="JPEG", quality=85)
            img.close()
            return temp_path


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
# 5) Logo / Watermark Ekleme (Sadece Haber Görseli İçin)
# ──────────────────────────────────────────────

def add_logo(image_path: str) -> str:
    """
    Ana görsele otoXtra logosunu (transparan watermark) ekler.

    Bu fonksiyon SADECE haber görselleri için çalışır.
    Yedek görselde zaten solid logo var, üstüne watermark eklenmez
    (prepare_image'da kontrol ediliyor).

    Args:
        image_path: Logo eklenecek görselin dosya yolu.

    Returns:
        Logo eklenmiş görselin dosya yolu.
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
# 6) Ana Fonksiyon — Görsel Hazırlama (v3.1)
# ──────────────────────────────────────────────

def prepare_image(article: dict) -> str:
    """
    ANA FONKSİYON — Haber için görsel hazırlar.

    v3.1: Bu fonksiyon ASLA None dönmez.

    Adımlar:
      1. RSS'den gelen image_url'yi indir
      2. Başarısızsa haber sitesinden og:image çek
      3. İkisi de yoksa lacivert arka plan + solid logo oluştur
      4. Görseli Facebook boyutuna getir
      5. Logo/watermark ekle (SADECE haber görseli için, yedek görsele eklenmez)

    Article dict'ine "image_source" alanı eklenir:
      - "rss_image"  → RSS feed'den gelen görsel
      - "og:image"   → haber sitesinden çekildi
      - "fallback"   → lacivert+logo yedek görsel

    Args:
        article: Haber dict'i.

    Returns:
        str: İşlenmiş görselin dosya yolu. Her zaman geçerli yol döner.
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

    # ── ADIM 1: RSS'den gelen görsel URL'sini indir ──
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

    # ── ADIM 3: YEDEK GÖRSEL — Lacivert + Solid Logo (v3.1) ──
    if image_path is None:
        log(
            "🔄 ADIM 3: RSS ve og:image başarısız — "
            "YEDEK GÖRSEL oluşturuluyor (lacivert + logo)...",
            "WARNING",
        )
        image_path = _create_fallback_image(
            feed_image_width, feed_image_height
        )
        image_source = "fallback"
        log("✅ Yedek görsel (lacivert + logo) oluşturuldu", "INFO")

    # ── ADIM 4: Boyutlandır ve kırp ──
    # Yedek görsel zaten doğru boyutta oluşturuluyor ama
    # RSS/og:image görselleri için gerekli
    if image_source != "fallback":
        log(
            f"📐 ADIM 4: Boyutlandırma "
            f"({feed_image_width}x{feed_image_height})",
            "INFO",
        )
        image_path = resize_and_crop(
            image_path, feed_image_width, feed_image_height
        )
    else:
        log(
            "📐 ADIM 4: Yedek görsel zaten doğru boyutta — "
            "boyutlandırma atlanıyor",
            "INFO",
        )

    # ── ADIM 5: Logo/Watermark ekle ──
    # ⚠️ SADECE haber görseli için! Yedek görselde zaten logo var.
    if image_source == "fallback":
        log(
            "🏷️ ADIM 5: Yedek görselde logo zaten var — "
            "watermark ATLANYOR",
            "INFO",
        )
    elif should_add_logo:
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
