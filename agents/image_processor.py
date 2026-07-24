"""
agents/image_processor.py - Görsel İşleme, Boyutlandırma ve Logo Ekleme
Pillow (PIL) ile boyutlandırma, fallback üretimi ve logo watermark işlemleri burada.
v1.1: Bellek sızıntısı önleme (with blokları ve .load() eklendi).
"""
import os
import tempfile
from PIL import Image, ImageDraw
from core.config_loader import get_project_root, load_config
from core.logger import log
from agents.image_utils import (
    _FALLBACK_BG_COLOR, _FALLBACK_STRIPE_COLOR, _get_platform_resize_limits, 
    _should_resize_for_platform, _safe_unlink
)

_CACHED_LOGO_INFO = None

def _create_fallback_image(width: int, height: int) -> str:
    project_root = get_project_root()
    logo_candidates = [
        os.path.join(project_root, "assets", "logo_solid.png"),
        os.path.join(project_root, "assets", "logo_solid.jpg"),
        os.path.join(project_root, "assets", "logo.png"),
    ]
    logo_path = next((c for c in logo_candidates if os.path.exists(c)), None)

    try:
        temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, prefix="otoxtra_fallback_")
        temp_path = temp_file.name
        temp_file.close()
        
        img = Image.new("RGB", (width, height), _FALLBACK_BG_COLOR)
        draw = ImageDraw.Draw(img)
        stripe_thickness = 2
        stripe_margin = int(height * 0.22)
        stripe_padding_x = int(width * 0.15)
        draw.rectangle([stripe_padding_x, stripe_margin, width - stripe_padding_x, stripe_margin + stripe_thickness], fill=_FALLBACK_STRIPE_COLOR)
        draw.rectangle([stripe_padding_x, height - stripe_margin - stripe_thickness, width - stripe_padding_x, height - stripe_margin], fill=_FALLBACK_STRIPE_COLOR)

        if logo_path:
            try:
                # GÜVENLİ AÇMA: with bloğu ile açıp hemen RAM'e yüklüyoruz (file descriptor sızıntısı önlenir)
                with Image.open(logo_path) as logo_src:
                    logo_img = logo_src.convert("RGBA")
                    logo_img.load()
                
                logo_max_width = int(width * 0.25)
                logo_orig_w, logo_orig_h = logo_img.size
                aspect = logo_orig_h / logo_orig_w
                logo_new_width = logo_max_width
                logo_new_height = int(logo_new_width * aspect)
                max_logo_height = int(height * 0.30)
                if logo_new_height > max_logo_height:
                    logo_new_height = max_logo_height
                    logo_new_width = int(logo_new_height / aspect)
                logo_img = logo_img.resize((logo_new_width, logo_new_height), Image.LANCZOS)
                paste_x = (width - logo_new_width) // 2
                paste_y = (height - logo_new_height) // 2
                img_rgba = img.convert("RGBA")
                overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                overlay.paste(logo_img, (paste_x, paste_y))
                img = Image.alpha_composite(img_rgba, overlay)
                
                img_rgba.close(); overlay.close(); logo_img.close()
            except Exception as logo_err:
                log(f"Yedek gorsele logo eklenemedi: {logo_err}", "WARNING")
        else:
            log("Logo dosyasi bulunamadi, sadece arka plan olusturuldu", "WARNING")
            
        img.save(temp_path, format="JPEG", quality=95)
        img.close()
        log(f"Yedek gorsel hazir: {temp_path}")
        return temp_path
    except Exception as exc:
        log(f"Yedek gorsel olusturma hatasi: {exc}", "ERROR")
        temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, prefix="otoxtra_emergency_")
        temp_path = temp_file.name
        temp_file.close()
        img = Image.new("RGB", (width, height), (50, 50, 50))
        img.save(temp_path, format="JPEG", quality=85)
        img.close()
        return temp_path

def resize_and_crop(image_path: str, target_width: int, target_height: int) -> str:
    try:
        # GÜVENLİ AÇMA: Görseli RAM'e alıp dosyayı kapatıyoruz, böylece üzerine yazabiliriz.
        with Image.open(image_path) as img_src:
            img_src.load()
            img = img_src.copy()
            
        img_width, img_height = img.size
        target_ratio = target_width / target_height
        current_ratio = img_width / img_height
        
        if current_ratio > target_ratio:
            new_height = target_height
            new_width = int(img_width * (target_height / img_height))
            img = img.resize((new_width, new_height), Image.LANCZOS)
            left = (new_width - target_width) // 2
            img = img.crop((left, 0, left + target_width, new_height))
        elif current_ratio < target_ratio:
            new_width = target_width
            new_height = int(img_height * (target_width / img_width))
            img = img.resize((new_width, new_height), Image.LANCZOS)
            top = (new_height - target_height) // 2
            img = img.crop((0, top, new_width, top + target_height))
        else:
            img = img.resize((target_width, target_height), Image.LANCZOS)
            
        if img.size != (target_width, target_height):
            img = img.resize((target_width, target_height), Image.LANCZOS)
            
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img.close()
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
            
        img.save(image_path, format="JPEG", quality=90)
        img.close()
        return image_path
    except Exception as exc:
        log(f"Boyutlandirma hatasi: {exc}", "WARNING")
        return image_path

def _get_cached_logo():
    global _CACHED_LOGO_INFO
    if _CACHED_LOGO_INFO is not None: return _CACHED_LOGO_INFO
    settings_config = load_config("settings")
    images_settings = settings_config.get("images", {})
    logo_opacity = float(images_settings.get("logo_opacity", 0.7))
    logo_path = os.path.join(get_project_root(), "assets", "logo.png")
    
    if not os.path.exists(logo_path):
        log(f"Logo dosyasi bulunamadi: {logo_path}", "WARNING")
        _CACHED_LOGO_INFO = False
        return False
        
    try:
        # GÜVENLİ AÇMA: Cache'leme yaparken file pointer'ı kapatıyoruz.
        with Image.open(logo_path) as f:
            logo_img = f.convert("RGBA")
            logo_img.load()
            
        r, g, b, alpha = logo_img.split()
        alpha = alpha.point(lambda p: int(p * logo_opacity))
        logo_img = Image.merge("RGBA", (r, g, b, alpha))
        _CACHED_LOGO_INFO = logo_img
        return logo_img
    except Exception as exc:
        log(f"Logo cache yukleme hatasi: {exc}", "WARNING")
        _CACHED_LOGO_INFO = False
        return False

def add_logo(image_path: str) -> str:
    settings_config = load_config("settings")
    images_settings = settings_config.get("images", {})
    logo_position = images_settings.get("logo_position", "bottom_right")
    logo_size_percent = float(images_settings.get("logo_size_percent", 12))
    padding = 20
    cached_logo = _get_cached_logo()
    if not cached_logo: return image_path
    
    try:
        # GÜVENLİ AÇMA: Görseli RAM'e alıp dosyayı kapatıyoruz.
        with Image.open(image_path) as base_src:
            base_src.load()
            base_img = base_src.copy()
            
        base_width, base_height = base_img.size
        if base_img.mode != "RGBA": base_img = base_img.convert("RGBA")
        
        logo_target_width = int(base_width * logo_size_percent / 100)
        logo_orig_w, logo_orig_h = cached_logo.size
        aspect = logo_orig_h / logo_orig_w
        logo_target_height = int(logo_target_width * aspect)
        
        # cached_logo.copy() ile cache'i bozmadan yeni bir obje oluşturuyoruz
        logo_img = cached_logo.copy()
        logo_img = logo_img.resize((logo_target_width, logo_target_height), Image.LANCZOS)
        logo_w, logo_h = logo_img.size
        
        position_map = {
            "bottom_right": (base_width - logo_w - padding, base_height - logo_h - padding),
            "bottom_left": (padding, base_height - logo_h - padding),
            "top_right": (base_width - logo_w - padding, padding),
            "top_left": (padding, padding),
        }
        pos_x, pos_y = position_map.get(logo_position, position_map["bottom_right"])
        pos_x = max(0, pos_x); pos_y = max(0, pos_y)
        
        overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
        overlay.paste(logo_img, (pos_x, pos_y))
        base_img = Image.alpha_composite(base_img, overlay)
        
        # Kaydetme işlemi için RGB'ye çevirip dosyaya yazıyoruz
        final_img = base_img.convert("RGB")
        final_img.save(image_path, format="JPEG", quality=90)
        
        base_img.close(); logo_img.close(); overlay.close(); final_img.close()
        return image_path
    except Exception as exc:
        log(f"Logo ekleme hatasi: {exc}", "WARNING")
        return image_path
