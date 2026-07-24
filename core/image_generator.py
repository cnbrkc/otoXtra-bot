import os
import re
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from core.config_loader import get_project_root
from core.logger import log

# Story boyutu (IG/Facebook Story standard)
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

BG_COLOR_RGBA = (18, 25, 36, 255)
TEXT_COLOR = (255, 255, 255)

FONT_BOLD_PATH = os.path.join(get_project_root(), "assets", "Roboto-Bold.ttf")
FONT_REG_PATH = os.path.join(get_project_root(), "assets", "Roboto-Regular.ttf")


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD_PATH if bold else FONT_REG_PATH
    if not os.path.exists(path):
        log(f"Font bulunamadi: {path}. Varsayilan kullaniliyor.", "WARNING")
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current_line = ""

    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    return lines


def _fit_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    return ImageOps.fit(img, (target_w, target_h), method=Image.LANCZOS, centering=(0.5, 0.5))


def _fit_contain(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    ratio = min(max_w / img.width, max_h / img.height)
    n_w = max(1, int(img.width * ratio))
    n_h = max(1, int(img.height * ratio))
    return img.resize((n_w, n_h), Image.LANCZOS)


def _prepare_text(post_text: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in (post_text or "").split("\n") if ln.strip()]
    title = lines[0] if lines else "OTOXTRA HABER"
    title = re.sub(r"[^\w\s]", "", title).strip().upper()
    body = "\n".join(lines[1:]) if len(lines) > 1 else ""
    return title, body


def create_social_card(post_text: str, image_path: str, output_path: str) -> str:
    """
    Story kart üretimi:
    - 1080x1920 canvas
    - Blur arka plan
    - Ortada net ana görsel (büyütülmüş)
    - Üstte başlık (yukarı kaydırılmış), altta açıklama (aşağı kaydırılmış)
    - Yazılara ince siyah kontur (stroke) eklenerek beyaz zeminde okunabilirlik artırıldı
    - PNG/JPEG uzantısına göre uygun export
    """
    try:
        title, body = _prepare_text(post_text)

        font_title = _get_font(62, bold=True)
        font_body = _get_font(40, bold=False)

        dummy_img = Image.new("RGB", (1, 1))
        dummy_draw = ImageDraw.Draw(dummy_img)

        max_text_width = CANVAS_WIDTH - 120
        title_lines = _wrap_text(dummy_draw, title, font_title, max_text_width)
        body_lines = _wrap_text(dummy_draw, body, font_body, max_text_width)

        title_h = 0
        for ln in title_lines:
            b = dummy_draw.textbbox((0, 0), ln, font=font_title)
            title_h += (b[3] - b[1]) + 10
        title_h = max(0, title_h - 10)

        body_h = 0
        for ln in body_lines:
            b = dummy_draw.textbbox((0, 0), ln, font=font_body)
            body_h += (b[3] - b[1]) + 8
        body_h = max(0, body_h - 8)

        logo_size = 120
        image_box_h = 940      # 760 -> 940 (foto daha büyük)
        gap = 48
        body_push = 80         # body'i aşağı kaydırmak için ekstra boşluk
        top_pull_up = 80       # logo+başlık'ı yukarı çekmek için offset

        total_h = (
            logo_size + gap
            + title_h + gap
            + image_box_h + gap
            + body_push
            + body_h
        )

        # Logo+başlık yukarı, gövde aşağı yerleşsin diye başlangıcı yukarı çekiyoruz
        y_cursor = max(80, (CANVAS_HEIGHT - total_h) // 2 - top_pull_up)

        canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), BG_COLOR_RGBA)

        if image_path and os.path.exists(image_path):
            try:
                src = Image.open(image_path).convert("RGB")
                bg = _fit_cover(src, CANVAS_WIDTH, CANVAS_HEIGHT)
                bg = bg.filter(ImageFilter.GaussianBlur(30))
                canvas.paste(bg, (0, 0))
            except Exception as e:
                log(f"Blur arka plan hazirlanamadi: {e}", "WARNING")

        overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (18, 25, 36, 120))
        canvas = Image.alpha_composite(canvas, overlay)
        draw = ImageDraw.Draw(canvas)

        # --- Logo ---
        logo_path = os.path.join(get_project_root(), "assets", "logo.png")
        if os.path.exists(logo_path):
            try:
                logo = Image.open(logo_path).convert("RGBA")
                logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
                logo_x = (CANVAS_WIDTH - logo_size) // 2
                canvas.paste(logo, (logo_x, y_cursor), logo)
            except Exception as e:
                log(f"Logo islenemedi: {e}", "WARNING")
        y_cursor += logo_size + gap

        # --- Başlık (ince siyah kontur ile) ---
        for ln in title_lines:
            b = draw.textbbox((0, 0), ln, font=font_title)
            lw = b[2] - b[0]
            lh = b[3] - b[1]
            x = (CANVAS_WIDTH - lw) // 2
            # İnce siyah stroke -> beyaz zeminde kaybolmaması için
            draw.text(
                (x, y_cursor), ln,
                font=font_title,
                fill=TEXT_COLOR,
                stroke_width=2,
                stroke_fill=(0, 0, 0, 220),
            )
            y_cursor += lh + 10

        y_cursor += gap

        # --- Ana görsel (büyütülmüş) ---
        img_top = y_cursor
        if image_path and os.path.exists(image_path):
            try:
                src = Image.open(image_path).convert("RGB")
                main_img = _fit_contain(src, CANVAS_WIDTH - 80, image_box_h)

                img_w, img_h = main_img.size
                img_x = (CANVAS_WIDTH - img_w) // 2
                img_y = img_top + (image_box_h - img_h) // 2

                mask = Image.new("L", (img_w, img_h), 0)
                mdraw = ImageDraw.Draw(mask)
                mdraw.rounded_rectangle((0, 0, img_w, img_h), radius=20, fill=255)

                shadow = Image.new("RGBA", (img_w + 12, img_h + 12), (0, 0, 0, 0))
                sdraw = ImageDraw.Draw(shadow)
                sdraw.rounded_rectangle((6, 6, img_w + 6, img_h + 6), radius=24, fill=(0, 0, 0, 90))
                canvas.alpha_composite(shadow, dest=(img_x - 6, img_y - 6))

                main_rgba = main_img.convert("RGBA")
                canvas.paste(main_rgba, (img_x, img_y), mask)
            except Exception as e:
                log(f"Ana gorsel islenemedi: {e}", "WARNING")

        # Body'yi aşağı kaydırmak için ekstra boşluk
        y_cursor += image_box_h + gap + body_push

        # --- Body (ince siyah kontur ile) ---
        for ln in body_lines:
            b = draw.textbbox((0, 0), ln, font=font_body)
            lw = b[2] - b[0]
            lh = b[3] - b[1]
            x = (CANVAS_WIDTH - lw) // 2
            draw.text(
                (x, y_cursor), ln,
                font=font_body,
                fill=TEXT_COLOR,
                stroke_width=1,
                stroke_fill=(0, 0, 0, 200),
            )
            y_cursor += lh + 8

        final_img = canvas.convert("RGB")
        lower = (output_path or "").lower()

        if lower.endswith(".png"):
            final_img.save(output_path, format="PNG", optimize=True, compress_level=4)
        else:
            final_img.save(
                output_path,
                format="JPEG",
                quality=95,
                optimize=True,
                subsampling=0
            )

        log(f"Sosyal medya karti olusturuldu: {output_path}")
        return output_path

    except Exception as e:
        log(f"Kart olusturma hatasi: {e}", "ERROR")
        return image_path
