import os
import textwrap
from PIL import Image, ImageDraw, ImageFont
from core.config_loader import get_project_root
from core.logger import log

# Kart boyutları (Instagram/Facebook optimal dikey boyut)
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1350

# Renkler
BG_COLOR = (18, 25, 36)           # Koyu lacivert arka plan
TOP_BAR_COLOR = (24, 35, 50)      # Üst bar rengi
TEXT_AREA_COLOR = (18, 25, 36)    # Yazı alanı rengi
TITLE_COLOR = (255, 255, 255)     # Başlık beyaz
BODY_COLOR = (210, 215, 220)      # Haber metni açık gri
ACCENT_COLOR = (0, 255, 150)      # Çizgi vurgu rengi (Neon yeşil/mavi istersen değiştir)

# Fontlar (assets/ klasöründe olmalı)
FONT_BOLD_PATH = os.path.join(get_project_root(), "assets", "font_bold.ttf")
FONT_REG_PATH = os.path.join(get_project_root(), "assets", "font_regular.ttf")

def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD_PATH if bold else FONT_REG_PATH
    if not os.path.exists(path):
        log(f"Font bulunamadı: {path}. Varsayılan kullanılıyor.", "WARNING")
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)

def _draw_wrapped_text(draw, text, font, max_width, position, fill, line_spacing=10):
    """Metni belirli bir genişliğe göre satırlara böler ve çizer."""
    y = position[1]
    # Kelimeleri bölerek satır sığdırma
    lines = []
    words = text.split()
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

    # Satırları çiz
    for line in lines:
        draw.text((position[0], y), line, font=font, fill=fill)
        bbox = draw.textbbox((0, 0), line, font=font)
        line_height = bbox[3] - bbox[1]
        y += line_height + line_spacing
        
    return y # Bitiş Y koordinatını döndür

def create_social_card(title: str, summary: str, image_path: str, output_path: str) -> str:
    """
    Verilen başlık, özet ve görsel ile profesyonel bir sosyal medya kartı oluşturur.
    """
    try:
        # 1. Canvas oluştur
        canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BG_COLOR)
        draw = ImageDraw.Draw(canvas)

        # 2. Üst Bar (Logo ve Site Adı)
        top_bar_height = 120
        draw.rectangle([0, 0, CANVAS_WIDTH, top_bar_height], fill=TOP_BAR_COLOR)
        
        # Logo (Eğer assets/logo.png varsa)
        logo_path = os.path.join(get_project_root(), "assets", "logo.png")
        if os.path.exists(logo_path):
            logo = Image.open(logo_path).convert("RGBA")
            logo_size = 80
            logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
            canvas.paste(logo, (40, 20), logo)
            
        # Marka Adı
        font_brand = _get_font(40, bold=True)
        draw.text((140, 35), "otoXtra", font=font_brand, fill=TITLE_COLOR)

        # 3. Başlık (Üst barın altı)
        font_title = _get_font(48, bold=True)
        title_y = 160
        title_end_y = _draw_wrapped_text(
            draw, title.upper(), font_title, 
            max_width=CANVAS_WIDTH - 80, 
            position=(40, title_y), 
            fill=TITLE_COLOR,
            line_spacing=15
        )

        # Başlık altı vurgu çizgisi
        draw.rectangle([40, title_end_y + 10, 150, title_end_y + 14], fill=ACCENT_COLOR)
        title_end_y += 40

        # 4. Haber Görseli (Ortada)
        img_area_height = 550
        img_area_top = title_end_y
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

        # 5. Haber Metni (Alt Kısım)
        font_body = _get_font(32, bold=False)
        body_y = img_area_bottom + 40
        
        _draw_wrapped_text(
            draw, summary, font_body, 
            max_width=CANVAS_WIDTH - 80, 
            position=(40, body_y), 
            fill=BODY_COLOR,
            line_spacing=12
        )

        # 6. Alt Bar (Kaynak vb. istersen eklenebilir)
        draw.rectangle([0, CANVAS_HEIGHT - 10, CANVAS_WIDTH, CANVAS_HEIGHT], fill=ACCENT_COLOR)

        # Kaydet
        canvas.save(output_path, format="JPEG", quality=95)
        log(f"Sosyal medya kartı oluşturuldu: {output_path}")
        return output_path

    except Exception as e:
        log(f"Kart oluşturma hatası: {e}", "ERROR")
        return image_path # Hata olursa orijinal görseli dön
