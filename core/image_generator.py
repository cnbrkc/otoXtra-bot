import os
import re
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from core.config_loader import get_project_root
from core.logger import log

# Kart boyutları (Instagram/Facebook STORY boyutu - 9:16)
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

# Renkler
BG_COLOR = (18, 25, 36)           # Koyu lacivert arka plan
TEXT_COLOR = (255, 255, 255)      # Saf Parlak Beyaz

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

        # 2. Yükseklikleri Hesapla (Tam Ortalama İçin)
        dummy_img = Image.new("RGB", (1, 1))
        dummy_draw = ImageDraw.Draw(dummy_img)

        font_title = _get_font(55, bold=True)
        title_lines = _wrap_text(dummy_draw, title, font_title, CANVAS_WIDTH - 120)
        title_h = sum([(dummy_draw.textbbox((0,0), line, font=font_title)[3]) for line in title_lines]) + (len(title_lines)-1)*15

        font_body = _get_font(35, bold=False)
        body_lines = _wrap_text(dummy_draw, body, font_body, CANVAS_WIDTH - 120)
        body_h = sum([(dummy_draw.textbbox((0,0), line, font=font_body)[3]) for line in body_lines]) + (len(body_lines)-1)*12

        # Görsel için ayrılan alan (Kırpılmadan sığdırılacağı için sadece max yükseklik lazım)
        img_max_h = 800 
        gap = 40

        total_h = title_h + gap + img_max_h + gap + body_h
        y_cursor = (CANVAS_HEIGHT - total_h) // 2  # DİKEY TAM ORTALAMA

        # 3. Arka Planı Oluştur (Blur Sihri)
        canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BG_COLOR)
        
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
                
                # B. Yoğun Blur uygula (Pürüzsüz renkler için)
                blur_img = blur_img.filter(ImageFilter.GaussianBlur(80))
                
                # C. Ekrana ortala ve yapıştır
                left = (b_w - CANVAS_WIDTH) // 2
                top = (b_h - CANVAS_HEIGHT) // 2
                blur_img = blur_img.crop((left, top, left + CANVAS_WIDTH, top + CANVAS_HEIGHT))
                canvas.paste(blur_img, (0, 0))
            except Exception as e:
                log(f"Blur arka plan hazırlanamadı: {e}", "WARNING")

        # D. Koyu Lacivert Gradient (En üstten ve en alttan gelip fotoğrafa yaklaştıkça şeffaflaşan)
        # Bunun için RGBA modunda bir overlay oluşturup alphayı değiştiriyoruz
        overlay = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0,0,0,0))
        
        # Gradient için yükseklik sınırları
        top_fade_limit = y_cursor - 60 # Başlığın biraz üstü
        bottom_fade_limit = y_cursor + title_h + gap + img_max_h + gap + 60 # Metnin biraz altı
        
        # Çizgi çizgi alpha değerini değiştirerek gradient oluştur
        for y in range(CANVAS_HEIGHT):
            if y < top_fade_limit:
                # En tepede full lacivert (alpha 255), aşağı indikçe şeffaflaşıyor (alpha 0)
                alpha = int(255 * (1 - (y / top_fade_limit)))
                # Alpha 0'ın altına düşmesin
                if alpha < 0: alpha = 0
            elif y > bottom_fade_limit:
                # En altta full lacivert (alpha 255), yukarı çıktıkça şeffaflaşıyor (alpha 0)
                dist_from_bottom = CANVAS_HEIGHT - y
                dist_to_fade = CANVAS_HEIGHT - bottom_fade_limit
                alpha = int(255 * (dist_from_bottom / dist_to_fade))
                if alpha < 0: alpha = 0
                if alpha > 255: alpha = 255
            else:
                # Fotoğrafın ve metnin olduğu orta kısımda hiç lacivert olmasın
                alpha = 0
                
            # Bu satırı lacivert renkle çiz
            ImageDraw.Draw(overlay).line([(0, y), (CANVAS_WIDTH, y)], fill=(18, 25, 36, alpha))
            
        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
        draw = ImageDraw.Draw(canvas)

        # 4. Başlık Çiz (Yatay Ortalı)
        for line in title_lines:
            bbox = draw.textbbox((0,0), line, font=font_title)
            line_w = bbox[2] - bbox[0]
            x = (CANVAS_WIDTH - line_w) // 2
            draw.text((x, y_cursor), line, font=font_title, fill=TEXT_COLOR)
            y_cursor += bbox[3] + 15
        y_cursor += gap - 15

        # 5. Ana Görseli Çiz (Kırpılmadan - Contain Mantığı ile Ortalanmış)
        img_y_start = y_cursor
        if image_path and os.path.exists(image_path):
            try:
                img = Image.open(image_path).convert("RGB")
                img_ratio = img.width / img.height
                target_ratio = 1000 / img_max_h  # 1000 max width, 800 max height
                
                if img_ratio > target_ratio: 
                    # Yatay görsel, genişliğe sığdır
                    n_w = 1000
                    n_h = int(1000 / img_ratio)
                else: 
                    # Dikey görsel, yüksekliğe sığdır
                    n_h = img_max_h
                    n_w = int(img_max_h * img_ratio)
                    
                img = img.resize((n_w, n_h), Image.LANCZOS)
                img_x = (CANVAS_WIDTH - n_w) // 2
                img_y = img_y_start + (img_max_h - n_h) // 2
                canvas.paste(img, (img_x, img_y))
            except Exception as e:
                log(f"Ana görsel işlenirken hata: {e}", "WARNING")
        y_cursor += img_max_h + gap

        # 6. Haber Metnini Çiz (Yatay Ortalı)
        for line in body_lines:
            bbox = draw.textbbox((0,0), line, font=font_body)
            line_w = bbox[2] - bbox[0]
            x = (CANVAS_WIDTH - line_w) // 2
            draw.text((x, y_cursor), line, font=font_body, fill=TEXT_COLOR)
            y_cursor += bbox[3] + 12

        # Kaydet
        canvas.convert("RGB").save(output_path, format="JPEG", quality=95)
        log(f"Sosyal medya kartı oluşturuldu: {output_path}")
        return output_path

    except Exception as e:
        log(f"Kart oluşturma hatası: {e}", "ERROR")
        return image_path
