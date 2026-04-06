"""
agents/agent_image.py — Görsel İşleme Ajanı (v3.1)

otoXtra Facebook Botu için pipeline'dan seçilen haberi alıp
görsel hazırlayan ve pipeline.json'a yazan bağımsız ajan.

Görsel kaynakları (öncelik sırasıyla):
  1. RSS'den gelen image_url
  2. Haber sitesinden og:image scraping
  3. YEDEK GÖRSEL — lacivert arka plan + solid logo

Bu ajan ASLA başarısız olmaz — en kötü ihtimalde
yedek görsel oluşturur ve pipeline'a yazar.

Bağımsız çalıştırma:
    python agents/agent_image.py
    python agents/agent_image.py --test

Diğer modüller bu ajanı şöyle çağırır:
    from agents.agent_image import run
    success = run()
"""

import os
import sys
import tempfile
from typing import Optional

import requests
from PIL import Image, ImageDraw

from core.logger import log
from core.config_loader import load_config, get_project_root
from core.state_manager import get_stage, set_stage, init_pipeline


# ============================================================
# SABİTLER
# ============================================================

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_REQUEST_TIMEOUT = 15
_MIN_IMAGE_WIDTH = 400

# Yedek görsel renk ayarları
_FALLBACK_BG_COLOR = (18, 25, 44)       # #12192c — lacivert
_FALLBACK_STRIPE_COLOR = (24, 35, 60)   # biraz daha açık lacivert


# ============================================================
# TEST MODU
# ============================================================

def _is_test_mode() -> bool:
    """TEST_MODE ortam değişkenini veya --test argümanını kontrol eder."""
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    if "--test" in sys.argv:
        return True
    return False


# ============================================================
# 1. URL'DEN GÖRSEL İNDİRME
# ============================================================

def download_image(image_url: str) -> Optional[str]:
    """Verilen URL'den görseli indirir, geçici dosyaya kaydeder.

    Args:
        image_url: İndirilecek görselin URL'si.

    Returns:
        str: Geçici dosya yolu. Başarısızsa None.
    """
    if not image_url:
        return None

    try:
        log(f"📥 RSS görsel indiriliyor: {image_url[:100]}...")

        response = requests.get(
            image_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
            stream=True,
        )
        response.raise_for_status()

        # Content-Type kontrolü
        content_type = response.headers.get("Content-Type", "")
        if content_type and not content_type.startswith("image/"):
            log(f"ℹ️ İndirilen dosya görsel değil (Content-Type: {content_type})")
            return None

        # Geçici dosyaya kaydet
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False, prefix="otoxtra_rss_"
        )
        temp_path = temp_file.name

        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                temp_file.write(chunk)
        temp_file.close()

        # Boyut kontrolü
        try:
            img = Image.open(temp_path)
            img_width, img_height = img.size
            img.close()

            if img_width < _MIN_IMAGE_WIDTH:
                log(
                    f"ℹ️ RSS görsel çok küçük ({img_width}x{img_height}), "
                    f"minimum {_MIN_IMAGE_WIDTH}px gerekli"
                )
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                return None

        except Exception as img_err:
            log(f"⚠️ Görsel dosyası açılamadı: {img_err}", "WARNING")
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return None

        log(f"✅ RSS görsel indirildi: {img_width}x{img_height}")
        return temp_path

    except requests.exceptions.Timeout:
        log(f"⚠️ RSS görsel indirme zaman aşımı: {image_url[:80]}", "WARNING")
        return None
    except requests.exceptions.RequestException as exc:
        log(f"⚠️ RSS görsel indirme HTTP hatası: {exc}", "WARNING")
        return None
    except Exception as exc:
        log(f"⚠️ RSS görsel indirme beklenmeyen hata: {exc}", "WARNING")
        return None


# ============================================================
# 2. HABER SİTESİNDEN og:image ÇEKME
# ============================================================

def scrape_og_image(url: str) -> Optional[str]:
    """Haber URL'sindeki og:image meta etiketinden görsel indirir.

    Args:
        url: Haber sayfasının URL'si.

    Returns:
        str: Geçici dosya yolu. Başarısızsa None.
    """
    if not url:
        log("⚠️ Görsel çekme: URL boş", "WARNING")
        return None

    try:
        log(f"🔍 og:image aranıyor: {url[:80]}...")

        response = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(response.text, "html.parser")

        og_tag = soup.find("meta", property="og:image")
        if not og_tag:
            og_tag = soup.find("meta", property="og:image:url")

        if not og_tag or not og_tag.get("content"):
            log("ℹ️ og:image etiketi bulunamadı")
            return None

        image_url = og_tag["content"].strip()
        if not image_url.startswith("http"):
            log(f"ℹ️ og:image URL geçersiz: {image_url[:80]}")
            return None

        return download_image(image_url)

    except requests.exceptions.Timeout:
        log(f"⚠️ og:image zaman aşımı: {url}", "WARNING")
        return None
    except requests.exceptions.RequestException as exc:
        log(f"⚠️ og:image sayfa çekme hatası: {exc}", "WARNING")
        return None
    except Exception as exc:
        log(f"⚠️ og:image beklenmeyen hata: {exc}", "WARNING")
        return None


# ============================================================
# 3. YEDEK GÖRSEL OLUŞTURMA (v3.1 — Lacivert + Solid Logo)
# ============================================================

def _create_fallback_image(width: int, height: int) -> str:
    """Lacivert arka plan üzerine solid logo ile yedek görsel oluşturur.

    Tasarım:
      - #12192c lacivert arka plan
      - İnce dekoratif şeritler (üst ve alt)
      - Ortada solid logo (%25 genişlik)

    Args:
        width:  Görsel genişliği.
        height: Görsel yüksekliği.

    Returns:
        str: Oluşturulan görselin geçici dosya yolu.
             Hata durumunda bile bir yol döner (son çare).
    """
    # Logo dosyasını bul
    project_root = get_project_root()
    logo_candidates = [
        os.path.join(project_root, "assets", "logo_solid.png"),
        os.path.join(project_root, "assets", "logo_solid.jpg"),
        os.path.join(project_root, "assets", "logo.png"),
    ]
    logo_path = None
    for candidate in logo_candidates:
        if os.path.exists(candidate):
            logo_path = candidate
            break

    try:
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False, prefix="otoxtra_fallback_"
        )
        temp_path = temp_file.name
        temp_file.close()

        # Lacivert arka plan
        img = Image.new("RGB", (width, height), _FALLBACK_BG_COLOR)
        draw = ImageDraw.Draw(img)

        # İnce dekoratif şeritler
        stripe_thickness = 2
        stripe_margin = int(height * 0.22)
        stripe_padding_x = int(width * 0.15)

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

        # Logo ekle
        if logo_path:
            try:
                logo_img = Image.open(logo_path)
                if logo_img.mode != "RGBA":
                    logo_img = logo_img.convert("RGBA")

                # Logo boyutu: görselin %25 genişliği
                logo_max_width = int(width * 0.25)
                logo_orig_w, logo_orig_h = logo_img.size
                aspect = logo_orig_h / logo_orig_w
                logo_new_width = logo_max_width
                logo_new_height = int(logo_new_width * aspect)

                # Yükseklik sınırı: görselin %30'u
                max_logo_height = int(height * 0.30)
                if logo_new_height > max_logo_height:
                    logo_new_height = max_logo_height
                    logo_new_width = int(logo_new_height / aspect)

                logo_img = logo_img.resize(
                    (logo_new_width, logo_new_height), Image.LANCZOS
                )

                # Tam ortaya yerleştir
                paste_x = (width - logo_new_width) // 2
                paste_y = (height - logo_new_height) // 2

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
                    f"({logo_new_width}x{logo_new_height})"
                )

            except Exception as logo_err:
                log(f"⚠️ Yedek görsele logo eklenemedi: {logo_err}", "WARNING")
        else:
            log("⚠️ Logo dosyası bulunamadı — sadece lacivert arka plan", "WARNING")

        img.save(temp_path, format="JPEG", quality=95)
        img.close()

        log(f"🔄 Yedek görsel hazır: {width}x{height} → {temp_path}")
        return temp_path

    except Exception as exc:
        log(f"❌ Yedek görsel oluşturma hatası: {exc}", "ERROR")

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
        except Exception as exc2:
            log(f"❌ Son çare görsel de oluşturulamadı: {exc2}", "ERROR")
            temp_file = tempfile.NamedTemporaryFile(
                suffix=".jpg", delete=False, prefix="otoxtra_empty_"
            )
            temp_path = temp_file.name
            temp_file.close()
            img = Image.new("RGB", (width, height), (50, 50, 50))
            img.save(temp_path, format="JPEG", quality=85)
            img.close()
            return temp_path


# ============================================================
# 4. BOYUTLANDIRMA VE KIRPMA
# ============================================================

def resize_and_crop(
    image_path: str,
    target_width: int,
    target_height: int,
) -> str:
    """Görseli hedef boyuta getirir (resize + center crop).

    Args:
        image_path:    İşlenecek görselin dosya yolu.
        target_width:  Hedef genişlik (piksel).
        target_height: Hedef yükseklik (piksel).

    Returns:
        str: İşlenmiş görselin dosya yolu.
    """
    log(f"📐 Boyutlandırılıyor: {target_width}x{target_height}")

    try:
        img = Image.open(image_path)
        img_width, img_height = img.size
        log(f"📐 Orijinal boyut: {img_width}x{img_height}")

        target_ratio = target_width / target_height
        current_ratio = img_width / img_height

        if current_ratio > target_ratio:
            # Genişlik fazla → yüksekliğe göre ölçekle, yatay kırp
            new_height = target_height
            new_width = int(img_width * (target_height / img_height))
            img = img.resize((new_width, new_height), Image.LANCZOS)
            left = (new_width - target_width) // 2
            img = img.crop((left, 0, left + target_width, new_height))

        elif current_ratio < target_ratio:
            # Yükseklik fazla → genişliğe göre ölçekle, dikey kırp
            new_width = target_width
            new_height = int(img_height * (target_width / img_width))
            img = img.resize((new_width, new_height), Image.LANCZOS)
            top = (new_height - target_height) // 2
            img = img.crop((0, top, new_width, top + target_height))

        else:
            img = img.resize((target_width, target_height), Image.LANCZOS)

        # Boyut garantisi
        if img.size != (target_width, target_height):
            img = img.resize((target_width, target_height), Image.LANCZOS)

        # RGB'ye çevir
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.save(image_path, format="JPEG", quality=90)
        img.close()

        log(f"✅ Boyutlandırıldı: {target_width}x{target_height}")
        return image_path

    except Exception as exc:
        log(f"⚠️ Boyutlandırma hatası: {exc}", "WARNING")
        return image_path


# ============================================================
# 5. LOGO / WATERMARK EKLEME
# ============================================================

def add_logo(image_path: str) -> str:
    """Ana görsele otoXtra logosunu (transparan watermark) ekler.

    SADECE haber görselleri için çalışır.
    Yedek görselde logo zaten var, bu fonksiyon çağrılmaz.

    Args:
        image_path: Logo eklenecek görselin dosya yolu.

    Returns:
        str: Logo eklenmiş görselin dosya yolu.
    """
    settings_config = load_config("settings")
    images_settings = settings_config.get("images", {})

    logo_position = images_settings.get("logo_position", "bottom_right")
    logo_opacity = images_settings.get("logo_opacity", 0.7)
    logo_size_percent = images_settings.get("logo_size_percent", 15)
    padding = 20

    logo_path = os.path.join(get_project_root(), "assets", "logo.png")

    if not os.path.exists(logo_path):
        log(f"⚠️ Logo dosyası bulunamadı: {logo_path}", "WARNING")
        return image_path

    try:
        base_img = Image.open(image_path)
        base_width, base_height = base_img.size

        if base_img.mode != "RGBA":
            base_img = base_img.convert("RGBA")

        logo_img = Image.open(logo_path)
        if logo_img.mode != "RGBA":
            logo_img = logo_img.convert("RGBA")

        # Logo boyutu hesapla
        logo_target_width = int(base_width * logo_size_percent / 100)
        logo_orig_w, logo_orig_h = logo_img.size
        aspect = logo_orig_h / logo_orig_w
        logo_target_height = int(logo_target_width * aspect)

        logo_img = logo_img.resize(
            (logo_target_width, logo_target_height), Image.LANCZOS
        )
        logo_w, logo_h = logo_img.size

        # Opaklık uygula
        r, g, b, alpha = logo_img.split()
        alpha = alpha.point(lambda p: int(p * logo_opacity))
        logo_img = Image.merge("RGBA", (r, g, b, alpha))

        # Pozisyon hesapla
        position_map = {
            "bottom_right": (base_width - logo_w - padding, base_height - logo_h - padding),
            "bottom_left":  (padding, base_height - logo_h - padding),
            "top_right":    (base_width - logo_w - padding, padding),
            "top_left":     (padding, padding),
        }
        pos_x, pos_y = position_map.get(logo_position, position_map["bottom_right"])
        pos_x = max(0, pos_x)
        pos_y = max(0, pos_y)

        # Birleştir
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
            f"opaklık={logo_opacity}, boyut=%{logo_size_percent}"
        )
        return image_path

    except Exception as exc:
        log(f"⚠️ Logo ekleme hatası: {exc}", "WARNING")
        return image_path


# ============================================================
# 6. ANA GÖRSEL HAZIRLAMA FONKSİYONU
# ============================================================

def prepare_image(article: dict) -> str:
    """Haber için görsel hazırlar. ASLA None dönmez.

    Adımlar:
      1. RSS'den gelen image_url'yi indir
      2. Başarısızsa haber sitesinden og:image çek
      3. İkisi de yoksa lacivert + solid logo yedek görsel oluştur
      4. Görseli Facebook boyutuna getir
      5. Logo/watermark ekle (SADECE haber görseli için)

    Article dict'ine "image_source" alanı eklenir:
      "rss_image" | "og:image" | "fallback"

    Args:
        article: Haber dict'i.

    Returns:
        str: İşlenmiş görselin dosya yolu. Her zaman geçerli.
    """
    log("─" * 40)
    log(f"🖼️ Görsel hazırlama: {article.get('title', '')[:80]}")

    # Ayarları oku
    settings_config = load_config("settings")
    images_settings = settings_config.get("images", {})

    should_add_logo = images_settings.get("add_logo", True)
    feed_image_width = images_settings.get("feed_image_width", 1200)
    feed_image_height = images_settings.get("feed_image_height", 630)

    image_path = None
    image_source = None

    # ADIM 1: RSS'den gelen görsel
    rss_image_url = article.get("image_url", "")
    if rss_image_url:
        log("📸 ADIM 1: RSS görseli indiriliyor...")
        image_path = download_image(rss_image_url)
        if image_path:
            image_source = "rss_image"
            log("✅ Görsel RSS'den alındı")
        else:
            log("ℹ️ RSS görseli indirilemedi → og:image denenecek")
    else:
        log("ℹ️ RSS'de görsel yok → og:image denenecek")

    # ADIM 2: og:image scraping
    if image_path is None:
        can_scrape = article.get("can_scrape_image", True)
        article_link = article.get("link", "")
        if can_scrape and article_link:
            log("📸 ADIM 2: og:image çekiliyor...")
            image_path = scrape_og_image(article_link)
            if image_path:
                image_source = "og:image"
                log("✅ Görsel og:image'den alındı")
            else:
                log("ℹ️ og:image'den de görsel çekilemedi")
        elif not can_scrape:
            log("ℹ️ Bu kaynak için görsel çekme devre dışı")
        else:
            log("ℹ️ Haber URL'si yok → og:image atlanıyor")

    # ADIM 3: Yedek görsel
    if image_path is None:
        log("🔄 ADIM 3: Yedek görsel oluşturuluyor (lacivert + logo)...", "WARNING")
        image_path = _create_fallback_image(feed_image_width, feed_image_height)
        image_source = "fallback"
        log("✅ Yedek görsel hazır")

    # ADIM 4: Boyutlandır
    if image_source != "fallback":
        log(f"📐 ADIM 4: Boyutlandırma ({feed_image_width}x{feed_image_height})")
        image_path = resize_and_crop(image_path, feed_image_width, feed_image_height)
    else:
        log("📐 ADIM 4: Yedek görsel zaten doğru boyutta — atlanıyor")

    # ADIM 5: Watermark
    if image_source == "fallback":
        log("🏷️ ADIM 5: Yedek görselde logo var — watermark atlanıyor")
    elif should_add_logo:
        log("🏷️ ADIM 5: Logo/watermark ekleniyor...")
        image_path = add_logo(image_path)
    else:
        log("ℹ️ Logo ekleme ayarlarda kapalı — atlanıyor")

    article["image_source"] = image_source
    log(f"✅ Görsel hazır: kaynak={image_source} → {image_path}")
    log("─" * 40)

    return image_path


# ============================================================
# 7. AJAN GİRİŞ NOKTASI
# ============================================================

def run() -> bool:
    """Ajanı çalıştırır. orchestrator.py tarafından çağrılır.

    Returns:
        bool: Başarılıysa True, hata varsa False.
    """
    log("─" * 55)
    log("agent_image başlıyor")
    log("─" * 55)

    # Write aşaması bitti mi?
    write_stage = get_stage("write")
    if write_stage.get("status") != "done":
        log("write aşaması tamamlanmamış — image çalıştırılamaz", "ERROR")
        set_stage("image", "error", error="write aşaması tamamlanmamış")
        return False

    # Write çıktısından haberi ve metni al
    write_output = write_stage.get("output", {})
    article = write_output.get("article", {})
    post_text = write_output.get("post_text", "")

    if not article:
        log("Write çıktısında haber yok", "WARNING")
        set_stage("image", "error", error="Write çıktısında haber yok")
        return False

    log(f"İşlenecek haber: {article.get('title', '')[:60]}")

    # Aşamayı çalışıyor işaretle
    set_stage("image", "running")

    try:
        # Görseli hazırla (ASLA None dönmez)
        image_path = prepare_image(article)

        # Pipeline'a yaz
        output = {
            "article": article,
            "post_text": post_text,
            "image_path": image_path,
            "image_source": article.get("image_source", "unknown"),
        }
        set_stage("image", "done", output=output)

        log(
            f"agent_image tamamlandı → "
            f"görsel={article.get('image_source', '?')} "
            f"→ {image_path}"
        )
        return True

    except Exception as exc:
        log(f"agent_image kritik hata: {exc}", "ERROR")
        set_stage("image", "error", error=str(exc))
        return False


# ============================================================
# MODÜL TESTİ (doğrudan çalıştırılırsa)
# ============================================================

if __name__ == "__main__":
    log("=== agent_image.py modül testi başlıyor ===")

    # Test için pipeline başlat
    init_pipeline("test-image")

    # Sahte write verisi oluştur
    fake_article = {
        "title": "Test: Yeni Elektrikli SUV Türkiye'de Satışa Çıktı",
        "link": "https://www.ntv.com.tr/",
        "summary": "Test özet metni.",
        "image_url": "",
        "source_name": "Test Kaynak",
        "source_priority": "high",
        "can_scrape_image": True,
        "score": 78,
    }
    fake_post_text = (
        "🚗 Yeni elektrikli SUV Türkiye'de!\n\n"
        "Test post metni burada yer alıyor.\n\n"
        "#elektrikli #SUV #otomotiv"
    )

    set_stage("fetch", "done", output={"articles": [fake_article], "count": 1})
    set_stage("score", "done", output={
        "selected_article": fake_article,
        "score": 78,
        "title": fake_article["title"],
    })
    set_stage("write", "done", output={
        "article": fake_article,
        "post_text": fake_post_text,
        "post_text_length": len(fake_post_text),
    })

    # Ajanı çalıştır
    log("\nagent_image çalıştırılıyor...")
    success = run()

    if success:
        image_stage = get_stage("image")
        output = image_stage.get("output", {})

        log(f"\n{'─' * 50}")
        log("SONUÇ:")
        log(f"  Haber      : {output.get('article', {}).get('title', 'YOK')[:60]}")
        log(f"  Görsel     : {output.get('image_path', 'YOK')}")
        log(f"  Kaynak     : {output.get('image_source', 'YOK')}")
        log(f"  Post metni : {len(output.get('post_text', ''))} karakter")

        # Görsel var mı kontrol et
        image_path = output.get("image_path", "")
        if image_path and os.path.exists(image_path):
            size_kb = os.path.getsize(image_path) // 1024
            log(f"  Dosya boyutu: {size_kb} KB")
            log(f"  ✅ Görsel dosyası mevcut")
        else:
            log(f"  ❌ Görsel dosyası bulunamadı: {image_path}", "WARNING")

        log(f"{'─' * 50}")
    else:
        log("Ajan başarısız oldu", "WARNING")

    log("=== agent_image.py modül testi tamamlandı ===")
