import os
import re
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from core.config_loader import get_project_root
from core.logger import log

# Kart boyutları (Instagram/Facebook STORY boyutu - 9:16)
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

# Renkler (Şeffaf gradyan için RGBA formatında)
BG_COLOR_RGBA = (18, 25, 36, 255)  # Koyu lacivert arka plan (Opak)
TEXT_COLOR = (255, 255, 255)        # Saf Beyaz

# Fontlar
FONT_BOLD_PATH = os.path.join(get_project_root(), "assets", "Roboto-Bold.ttf")
FONT_REG_PATH = os.path.join(get_project_root(), "assets", "Roboto-Regular.ttf")

def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD_PATH if bold else FONT_REG_PATH
    if not os.path.exists(path):
        log(f"Font bulunamadı: {path}. Varsayılan kullanılıyor.", "WARNING")
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)

def _wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            if current_line: lines.append(current_line)
            current_line = word
    if current_line: lines.append(current_line)
    return lines

def create_social_card(post_text: str, image_path: str, output_path: str) -> str:
    try:
        # 1. Metni Hazırla
        lines = [ln.strip() for ln in post_text.split("\n") if ln.strip()]
        title = lines[0] if lines else "OTOXTRA HABER"
        title = re.sub(r'[^\w\s]', '', title).strip().upper()
        body = "\n".join(lines[1:]) if len(lines) > 1 else ""

        # 2. Tam Ortalamak İçin Yükseklikleri Hesapla (Dummy Draw)
        dummy_img = Image.new("RGB", (1, 1))
        dummy_draw = ImageDraw.Draw(dummy_img)

        # PUNTOLAR BÜYÜTÜLDÜ (55 -> 65)
        font_title = _get_font(65, bold=True)
        title_lines = _wrap_text(dummy_draw, title, font_title, CANVAS_WIDTH - 160)
        title_h = sum([(dummy_draw.textbbox((0,0), line, font=font_title)[3]) for line in title_lines]) + (len(title_lines)-1)*18

        # PUNTOLAR BÜYÜTÜLDÜ (35 -> 40)
        font_body = _get_font(40, bold=False)
        body_lines = _wrap_text(dummy_draw, body, font_body, CANVAS_WIDTH - 160)
        body_h = sum([(dummy_draw.textbbox((0,0), line, font=font_body)[3]) for line in body_lines]) + (len(body_lines)-1)*13

        logo_h = 120
        img_h = 700
        gap = 40

        total_h = logo_h + gap + title_h + gap + img_h + gap + body_h
        y_cursor = (CANVAS_HEIGHT - total_h) // 2  # TAM ORTALAMA

        # 3. Arka Planı Oluştur (Blur ve Gradient Sihri)
        canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), BG_COLOR_RGBA)
        
        if image_path and os.path.exists(image_path):
            try:
                img = Image.open(image_path).convert("RGB")
                
                # A. Görseli tüm ekrana kaplayacak şekilde büyüt (Cover mantığı)
                img_ratio = img.width / img.height
                canvas_ratio = CANVAS_WIDTH / CANVAS_HEIGHT
                
                if img_ratio > canvas_ratio:
                    b_h = CANVAS_HEIGHT
                    b_w = int(b_h * img_ratio)
                else:
                    b_w = CANVAS_WIDTH
                    b_h = int(b_w / img_ratio)
                    
                blur_img = img.resize((b_w, b_h), Image.LANCZOS)
                
                # B. Yoğun Blur uygula
                blur_img = blur_img.filter(ImageFilter.GaussianBlur(70))
                
                # C. Ekrana ortala ve yapıştır
                left = (b_w - CANVAS_WIDTH) // 2
                top = (b_h - CANVAS_HEIGHT) // 2
                blur_img = blur_img.crop((left, top, left + CANVAS_WIDTH, top + CANVAS_HEIGHT))
                canvas.paste(blur_img, (0, 0))
            except Exception as e:
                log(f"Blur arka plan hazırlanamadı: {e}", "WARNING")

        # D. Koyu Lacivert Gradient (Alt ve Üst Kenarlarda Solarak Yok Olan Efekt)
        overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0,0,0,0))
        draw_overlay = ImageDraw.Draw(overlay)
        
        fade_dist = 600 # Geniş bir alanda yumuşakça kaybolması için
        
        for y in range(CANVAS_HEIGHT):
            if y < fade_dist:
                # Yukarıdan aşağıya şeffaflaşan lacivert
                alpha = int(255 * (1 - y / fade_dist))
            elif y > CANVAS_HEIGHT - fade_dist:
                # Aşağıdan yukarıya şeffaflaşan lacivert
                alpha = int(255 * ((y - (CANVAS_HEIGHT - fade_dist)) / fade_dist))
            else:
                # Orta kısımda hiç lacivert olmasın (sadece blur kalsın)
                alpha = 0
            draw_overlay.line([(0, y), (CANVAS_WIDTH, y)], fill=(18, 25, 36, alpha))
            
        canvas = Image.alpha_composite(canvas, overlay)
        draw = ImageDraw.Draw(canvas)

        # 4. Elementleri Tam Ortalanmış Olarak Çiz

        # Logo
        logo_path = os.path.join(get_project_root(), "assets", "logo.png")
        if os.path.exists(logo_path):
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize((logo_h, logo_h), Image.LANCZOS)
            logo_x = (CANVAS_WIDTH - logo_h) // 2
            canvas.paste(logo, (logo_x, y_cursor), logo)
        y_cursor += logo_h + gap

        # Başlık
        for line in title_lines:
            bbox = draw.textbbox((0,0), line, font=font_title)
            line_w = bbox[2] - bbox[0]
            x = (CANVAS_WIDTH - line_w) // 2
            draw.text((x, y_cursor), line, font=font_title, fill=TEXT_COLOR)
            y_cursor += bbox[3] + 18  # Satır aralığı biraz açıldı
        y_cursor += gap - 20

        # Ana Görsel (Kırpılmadan, sığdırılarak - Contain)
        img_y = y_cursor
        if image_path and os.path.exists(image_path):
            try:
                img = Image.open(image_path).convert("RGB")
                img_ratio = img.width / img.height
                target_ratio = 1000 / img_h  # 1000 max width, 700 max height
                
                if img_ratio > target_ratio: 
                    n_w = 1000
                    n_h = int(1000 / img_ratio)
                else: 
                    n_h = img_h
                    n_w = int(img_h * img_ratio)
                    
                img = img.resize((n_w, n_h), Image.LANCZOS)
                img_x = (CANVAS_WIDTH - n_w) // 2
                # Görseli ayrılmış alanda dikey olarak da ortala
                canvas.paste(img, (img_x, img_y + (img_h - n_h)//2))
            except Exception as e:
                log(f"Ana görsel işlenirken hata: {e}", "WARNING")
        y_cursor += img_h + gap

        # Haber Metni (Alt Kısım)
        for line in body_lines:
            bbox = draw.textbbox((0,0), line, font=font_body)
            line_w = bbox[2] - bbox[0]
            x = (CANVAS_WIDTH - line_w) // 2
            draw.text((x, y_cursor), line, font=font_body, fill=TEXT_COLOR)
            y_cursor += bbox[3] + 13  # Satır aralığı biraz açıldı

        # Kaydet
        canvas.convert("RGB").save(output_path, format="JPEG", quality=95)
        log(f"Sosyal medya kartı oluşturuldu: {output_path}")
        return output_path

    except Exception as e:
        log(f"Kart oluşturma hatası: {e}", "ERROR")
        return image_path
