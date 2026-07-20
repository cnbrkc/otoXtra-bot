"""
agents/story_generator.py - Instagram Story Gorsel Modulu (v1.0)

NE YAPAR?
  - Secilen haberin gorselini + basligini alir.
  - 1080x1920 (9:16) DIKEY bir Instagram Story gorseli uretir:
      * Arka plan: haberin fotografi (yoksa marka renginde gradient)
      * Ustte kirmizi "OTOXTRA" rozeti
      * Alt kisimda BUYUK baslik yazisi (beyaz + golge, otomatik sigdirma)
      * Kaynak etiketi + sag-altta logo watermark
  - Uretilen gorseli Telegram'a gonderir (sen manuel olarak Instagram story'ye yuklersin).

ONEMLI:
  - Ana pipeline'a (Facebook/Threads paylasimi) HICBIR SEKILDE KARISMAZ.
  - Ayri cagrilir, tamamen try/except icindedir. Hata verse bile bot calismaya devam eder.
  - Varsayilan olarak KAPALIDIR. settings.json icine:
        "instagram": { "enabled": true, "send_to_telegram": true }
    ekleyince acilir.
"""

import os
import tempfile

import requests
from PIL import Image, ImageDraw, ImageFont

from core.config_loader import get_project_root
from core.logger import log


# ─────────────────────────── AYARLAR ───────────────────────────
_STORY_WIDTH = 1080
_STORY_HEIGHT = 1920

_BRAND_NAME = "OTOXTRA"
_ACCENT_COLOR = (220, 38, 38)     # kirmizi aksan / rozet
_BG_TOP = (15, 23, 42)            # fallback gradient ust (koyu lacivert)
_BG_BOTTOM = (30, 41, 59)         # fallback gradient alt (slate)
_TEXT_COLOR = (255, 255, 255)
_SUBTEXT_COLOR = (225, 228, 235)
_SHADOW_COLOR = (0, 0, 0)


# ─────────────────────────── YARDIMCILAR ───────────────────────────
def _load_font(size: int, bold: bool = True):
    """assets/ icinden font yükler; yoksa Pillow varsayilanina duser."""
    root = get_project_root()
    if bold:
        candidates = [
            os.path.join(root, "assets", "Roboto-Black.ttf"),
            os.path.join(root, "assets", "Montserrat-VariableFont_wght.ttf"),
            os.path.join(root, "assets", "Roboto-Bold.ttf"),
            os.path.join(root, "assets", "Roboto-Regular.ttf"),
        ]
    else:
        candidates = [
            os.path.join(root, "assets", "Roboto-Medium.ttf"),
            os.path.join(root, "assets", "Roboto-Regular.ttf"),
        ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    log("Story: TTF font bulunamadi, varsayilan font kullaniliyor.", "WARNING")
    return ImageFont.load_default()


def _cover_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Gorseli hedef orana 'cover' seklinde keser (ortadan)."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = max(target_w, int(round(src_w * scale)))
    new_h = max(target_h, int(round(src_h * scale)))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _vertical_alpha_gradient(width: int, height: int, top_alpha: int, bottom_alpha: int) -> Image.Image:
    """Alt kisim okunurlugu icin yukaridan asagiya artan saydamlik katmani."""
    grad = Image.new("L", (1, height))
    for y in range(height):
        t = y / max(1, height - 1)
        grad.putpixel((0, y), int(top_alpha + (bottom_alpha - top_alpha) * t))
    grad = grad.resize((width, height))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overlay.putalpha(grad)
    return overlay


def _branded_gradient(width: int, height: int) -> Image.Image:
    """Fotograf yoksa uretilen marka gradient arka plan."""
    grad = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(1, height - 1)
        r = int(_BG_TOP[0] + (_BG_BOTTOM[0] - _BG_TOP[0]) * t)
        g = int(_BG_TOP[1] + (_BG_BOTTOM[1] - _BG_TOP[1]) * t)
        b = int(_BG_TOP[2] + (_BG_BOTTOM[2] - _BG_TOP[2]) * t)
        grad.putpixel((0, y), (r, g, b))
    return grad.resize((width, height))


def _wrap_text(text: str, font, draw: ImageDraw.ImageDraw, max_width: int) -> list:
    """Metni max_width icine sigacak sekilde satirlara boler."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_text_shadow(draw, xy, text, font, fill=_TEXT_COLOR, shadow=_SHADOW_COLOR):
    """Okunurluk icin etrafinda golgeli beyaz yazi."""
    x, y = xy
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
        draw.text((x + dx, y + dy), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


# ─────────────────────────── ANA URETIM ───────────────────────────
def create_instagram_story_image(article: dict, base_image_path: str = "", post_text: str = "") -> str:
    """
    Instagram Story gorseli uretir ve gecici dosya yolunu dondurur.
    Hata durumunda bile gecerli bir (gradient) gorsel dondurur; asla exception firlatmaz.
    """
    width, height = _STORY_WIDTH, _STORY_HEIGHT
    margin = 90

    title = (article.get("title", "") or "").strip()
    if not title:
        title = (post_text or "").strip().split("\n")[0][:140]
    source = (article.get("source_name", "") or "").strip()

    # 1) ARKA PLAN — once parametre, sonra article.image_paths, sonra gradient
    bg_path = base_image_path
    if (not bg_path or not os.path.exists(bg_path)):
        for p in (article.get("image_paths") or []):
            if p and os.path.exists(p):
                bg_path = p
                break

    background_kind = "gradient"
    try:
        if bg_path and os.path.exists(bg_path):
            base = Image.open(bg_path).convert("RGB")
            base = _cover_crop(base, width, height).convert("RGBA")
            base = Image.alpha_composite(base, _vertical_alpha_gradient(width, height, 70, 215))
            background_kind = "photo"
        else:
            base = _branded_gradient(width, height).convert("RGBA")
    except Exception as exc:
        log(f"Story arka plan hatasi, gradient kullaniliyor: {exc}", "WARNING")
        base = _branded_gradient(width, height).convert("RGBA")
        background_kind = "gradient"

    draw = ImageDraw.Draw(base)

    # 2) UST ROZET (kirmizi, marka adi)
    brand_font = _load_font(46, bold=True)
    pill_w = int(draw.textlength(_BRAND_NAME, font=brand_font)) + 80
    pill_h = 78
    pill_x, pill_y = margin, 120
    draw.rounded_rectangle([pill_x, pill_y, pill_x + pill_w, pill_y + pill_h], radius=40, fill=_ACCENT_COLOR)
    draw.text((pill_x + 40, pill_y + 12), _BRAND_NAME, font=brand_font, fill=_TEXT_COLOR)

    # 3) BASLIK — font boyutunu otomatik sigdir
    max_text_width = width - margin * 2
    text_band_bottom = height - 230
    max_total_height = 460
    font_size = 110
    title_lines, chosen_font, line_h = [], None, 0
    while font_size >= 46:
        font = _load_font(font_size, bold=True)
        lines = _wrap_text(title, font, draw, max_text_width)
        total_h = len(lines) * (font_size + 26)
        if total_h <= max_total_height:
            title_lines, chosen_font, line_h = lines, font, font_size + 26
            break
        font_size -= 8
    if chosen_font is None:
        chosen_font = _load_font(46, bold=True)
        title_lines = _wrap_text(title, chosen_font, draw, max_text_width)
        line_h = 46 + 26

    total_h = len(title_lines) * line_h
    cur_y = text_band_bottom - total_h
    for line in title_lines:
        _draw_text_shadow(draw, (margin, cur_y), line, chosen_font)
        cur_y += line_h

    # 4) KAYNAK ETIKETI
    if source:
        src_font = _load_font(40, bold=False)
        _draw_text_shadow(draw, (margin, height - 195), source.upper(), src_font, fill=_SUBTEXT_COLOR)

    # 5) LOGO WATERMARK (sag-alt)
    try:
        logo_path = os.path.join(get_project_root(), "assets", "logo.png")
        if os.path.exists(logo_path):
            logo = Image.open(logo_path).convert("RGBA")
            lw = int(width * 0.16)
            lo_w, lo_h = logo.size
            lh = int(lw * lo_h / max(1, lo_w))
            logo = logo.resize((lw, lh), Image.LANCZOS)
            base.paste(logo, (width - lw - margin, height - lh - margin), logo)
    except Exception as exc:
        log(f"Story logo eklenemedi (devam ediliyor): {exc}", "WARNING")

    # 6) KAYDET
    final = base.convert("RGB")
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, prefix="otoxtra_story_")
    tmp.close()
    final.save(tmp.name, format="JPEG", quality=92)
    final.close()
    log(f"Instagram Story gorseli hazir: {width}x{height} (arka plan={background_kind})")
    return tmp.name


# ─────────────────────────── TELEGRAM GONDERIMI ───────────────────────────
def send_story_to_telegram(image_path: str, article: dict) -> bool:
    """Story gorselini Telegram'a photo olarak gonderir."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        log("Story: TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID eksik, gonderilmedi.", "WARNING")
        return False
    if not image_path or not os.path.exists(image_path):
        log("Story: Gonderilecek gorsel yok.", "WARNING")
        return False

    title = (article.get("title", "") or "")[:200]
    caption = (
        f"📱 INSTAGRAM STORY GÖRSELİ\n\n"
        f"Başlık: {title}\n\n"
        f"Bu görsel 1080x1920 (9:16) Story formatında hazır.\n"
        f"Instagram'a manuel olarak story yükleyebilirsin."
    )
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        with open(image_path, "rb") as photo:
            files = {"photo": photo}
            data = {"chat_id": chat_id, "caption": caption}
            resp = requests.post(url, files=files, data=data, timeout=30)
            if resp.status_code == 200:
                log("Story gorseli Telegram'a gonderildi.")
                return True
            log(f"Story Telegram hatasi: {resp.text}", "ERROR")
    except Exception as exc:
        log(f"Story Telegram gonderim hatasi: {exc}", "ERROR")
    return False


# ─────────────────────────── TEK GIRIS NOKTASI ───────────────────────────
def generate_and_deliver_story(article: dict, image_paths: list, post_text: str, settings: dict) -> str:
    """
    Publisher'in cagirdigi ana fonksiyon.
      - settings: settings.json -> "instagram" bolumu
    Donus: uretilen story gorselinin yolu (bossa ""). Hata firlatMAZ.
    """
    try:
        if not settings.get("enabled", False):
            log("Instagram Story modulu kapali (instagram.enabled=false)")
            return ""

        base_image = image_paths[0] if image_paths else ""
        story_path = create_instagram_story_image(article, base_image, post_text)

        if settings.get("send_to_telegram", True):
            send_story_to_telegram(story_path, article)

        return story_path
    except Exception as exc:
        log(f"Instagram Story modulu beklenmedik hata (ana akis etkilenmez): {exc}", "ERROR")
        return ""
