import os
import re
from PIL import Image, ImageDraw, ImageFont
from core.config_loader import get_project_root
from core.logger import log

# Kart boyutları (Instagram/Facebook STORY boyutu - 9:16)
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

# Renkler
BG_COLOR = (18, 25, 36)           # Koyu lacivert arka plan
TITLE_COLOR = (255, 255, 255)     # Başlık beyaz
BODY_COLOR = (210, 215, 220)      # Haber metni açık gri

# Fontlar
FONT_BOLD_PATH = os.path.join(get_project_root(), "assets", "Roboto-Bold.ttf")
FONT_REG_PATH = os.path.join(get_project_root(), "assets", "Roboto-Regular.ttf")

def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD_PATH if bold else FONT_REG_PATH
    if not os.path.exists(path):
        log(f"Font bulunamadı: {path}. Varsayılan kullanılıyor.", "WARNING")
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)

def _draw_centered_text(draw, text, font, y, max_width, fill, line_spacing=10):
    """Metni ortalanmış şekilde satırlara böler ve çizer."""
    words = text.split()
    lines = []
    current_line = ""
    
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        width = bbox[2] - bbox[0]
        
        if width <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
            
    if current_line:
        lines.append(current_line)

    for line in lines:
        bbox = draw.textbbox((0,0), line, font=font)
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        
        x = (CANVAS_WIDTH - line_width) // 2
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height + line_spacing
            
    return y

def create_social_card(post_text: str, image_path: str, output_path: str) -> str:
    """
    Verilen YZ post metnini (ilk satır başlık, gerisi metin) ve ham görseli kullanarak 
    ortalanmış profesyonel bir sosyal medya kartı (Story boyutunda) oluşturur.
    """
    try:
        canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BG_COLOR)
        draw = ImageDraw.Draw(canvas)

        # 1. YZ Metnini Başlık ve Gövde olarak ayır
        lines = [ln.strip() for ln in post_text.split("\n") if ln.strip()]
        
        title = lines[0] if lines else "OTOXTRA HABER"
        title = re.sub(r'[^\w\s]', '', title).strip().upper()
        
        body = "\n".join(lines[1:]) if len(lines) > 1 else ""

        # 2. Logo (Üst Orta - 1.5x büyütüldü)
        logo_y = 80
        logo_path = os.path.join(get_project_root(), "assets", "logo.png")
        if os.path.exists(logo_path):
            logo = Image.open(logo_path).convert("RGBA")
            logo_size = 120  # Eskiden 80'di
            logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
            logo_x = (CANVAS_WIDTH - logo_size) // 2
            canvas.paste(logo, (logo_x, logo_y), logo)
            logo_y += logo_size + 30
        else:
            logo_y = 100

        # 3. Başlık (Ortalanmış, Kalın, Büyük - x1.5 büyütüldü)
        font_title = _get_font(65, bold=True)  # Eskiden 45'ti
        title_y = _draw_centered_text(draw, title, font_title, logo_y, CANVAS_WIDTH - 80, TITLE_COLOR, line_spacing=15)
        title_y += 50 # Boşluk

        # 4. Haber Görseli (Ortada - Story boyutuna göre ayarlandı)
        img_area_height = 750
        img_area_top = title_y
        img_area_bottom = img_area_top + img_area_height

        if image_path and os.path.exists(image_path):
            try:
                img = Image.open(image_path).convert("RGB")
                
                # Görseli alana sığdır (Cover mantığı)
                img_ratio = img.width / img.height
                area_ratio = CANVAS_WIDTH / img_area_height
                
                if img_ratio > area_ratio:
                    new_height = img_area_height
                    new_width = int(img_area_height * img_ratio)
                else:
                    new_width = CANVAS_WIDTH
                    new_height = int(CANVAS_WIDTH / img_ratio)
                    
                img = img.resize((new_width, new_height), Image.LANCZOS)
                
                # Ortala ve kırp
                left = (new_width - CANVAS_WIDTH) // 2
                top = (new_height - img_area_height) // 2
                img = img.crop((left, top, left + CANVAS_WIDTH, top + img_area_height))
                
                canvas.paste(img, (0, img_area_top))
            except Exception as e:
                log(f"Görsel işlenirken hata: {e}", "WARNING")
                draw.rectangle([0, img_area_top, CANVAS_WIDTH, img_area_bottom], fill=(50, 50, 50))

        # 5. Haber Metni (Alt Kısım, Ortalanmış - x1.5 büyütüldü)
        body_y = img_area_bottom + 60
        
        # Story boyutu uzun olduğu için metin limitini 600 karaktere çıkardık
        max_body_chars = 600
        if len(body) > max_body_chars:
            body = body[:max_body_chars].rsplit(' ', 1)[0] + "..."

        font_body = _get_font(38, bold=False)  # Eskiden 32'ydi
        _draw_centered_text(draw, body, font_body, body_y, CANVAS_WIDTH - 80, BODY_COLOR, line_spacing=15)

        canvas.save(output_path, format="JPEG", quality=95)
        log(f"Sosyal medya kartı oluşturuldu: {output_path}")
        return output_path

    except Exception as e:
        log(f"Kart oluşturma hatası: {e}", "ERROR")
        return image_path
