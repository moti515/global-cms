import os
import json
import subprocess
from datetime import datetime
from PIL import Image
from pillow_heif import register_heif_opener
import config

# Реєстрація підтримки HEIF/HEIC
register_heif_opener()

def optimize_media_geometry(local_path, filename, mime_type):
    """Оптимізує пропорції зображень та обов'язково конвертує HEIC/PNG/WEBP у JPEG для Meta."""
    if not os.path.exists(local_path):
        return local_path

    lower_name = filename.lower()
    is_image = mime_type.startswith("image/") or lower_name.endswith(('.heic', '.heif', '.webp'))

    if is_image:
        try:
            with Image.open(local_path) as img:
                img = img.convert('RGB')
                w, h = img.size
                ratio = w / h
                
                is_heic_or_webp = lower_name.endswith(('.heic', '.heif', '.webp'))
                needs_padding = ratio < 0.8 or ratio > 1.91
                
                if needs_padding or is_heic_or_webp:
                    new_filename = filename.rsplit('.', 1)[0] + '.jpg'
                    os.makedirs('temp_mebli', exist_ok=True)
                    padded_post_path = os.path.join('temp_mebli', 'post_ready_' + new_filename)
                    
                    if needs_padding:
                        print(f"📐 Оптимізація геометрії ({ratio:.2f}) та конвертація для: {filename}")
                        if ratio < 0.8:
                            new_w = int(h * 0.8)
                            new_h = h
                        else:
                            new_w = w
                            new_h = int(w / 1.91)
                            
                        canvas = Image.new('RGB', (new_w, new_h), (255, 255, 255))
                        paste_x = (new_w - w) // 2
                        paste_y = (new_h - h) // 2
                        
                        canvas.paste(img, (paste_x, paste_y))
                        canvas.save(padded_post_path, 'JPEG', quality=95)
                    else:
                        print(f"🔄 Конвертація {filename} у JPEG для сумісності з Meta API...")
                        img.save(padded_post_path, 'JPEG', quality=95)
                        
                    return padded_post_path
        except Exception as e:
            print(f"⚠️ Помилка калібрування геометрії поста: {e}")

    return local_path

def extract_video_frame(video_path, output_frame_path):
    """Витягує 1-шу секунду відео за допомогою FFmpeg для аналізу за допомогою AI."""
    print(f"🎬 Витягуємо тестовий кадр з відео: {os.path.basename(video_path)}")
    cmd = ['ffmpeg', '-y', '-i', video_path, '-ss', '00:00:01', '-vframes', '1', output_frame_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_frame_path

def get_manufacturer_header(category, date_str, lang_idx, mode, target_loc=None):
    """Генерує естетичний заголовок відповідно до категорії, обраної мови та локації."""
    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else str(datetime.now().year)
    cat_lower = category.lower()
    
    pref = config.LANG_CONFIG.get(lang_idx, config.LANG_CONFIG[0])
    header_lines = []

    resolved_loc = ""
    if target_loc:
        try:
            loc_json = json.loads(target_loc)
            if isinstance(loc_json, dict):
                resolved_loc = loc_json.get(str(lang_idx), loc_json.get("0", ""))
            else:
                resolved_loc = str(target_loc)
        except (json.JSONDecodeError, TypeError):
            resolved_loc = str(target_loc)

    invalid_markers = ["невідоме місце", "невідомо", "unknown", "unbekannt", "-", "none", "null", "невідоме місто"]
    has_valid_loc = resolved_loc and not any(marker in resolved_loc.lower() for marker in invalid_markers)

    if "montage various" in cat_lower:
        header_lines.append(f"📅 {pref['year']}: {year}")
        if has_valid_loc: header_lines.append(f"📍 {pref['loc']}: {resolved_loc}")
        header_lines.append(f"🛠️ {pref['assembly']}")
        return "\n".join(header_lines) + "\n\n"
        
    if "various" in cat_lower:
        header_lines.append(f"📅 {pref['year']}: {year}")
        if has_valid_loc: header_lines.append(f"📍 {pref['loc']}: {resolved_loc}")
        header_lines.append(f"💡 {pref['concept']}")
        return "\n".join(header_lines) + "\n\n"
        
    if "instruktion" in cat_lower:
        header_lines.append(f"📐 {pref['ergonomics']}")
        if has_valid_loc: header_lines.append(f"📍 {pref['loc']}: {resolved_loc}")
        return "\n".join(header_lines) + "\n\n"

    for key, info in config.COMPANIES_DB.items():
        if key in cat_lower:
            correct_name = info["names"].get(lang_idx, info["names"][0])
            header_lines.append(f"📅 {pref['year']}: {year}")
            if has_valid_loc: header_lines.append(f"📍 {pref['loc']}: {resolved_loc}")
            header_lines.append(f"🛠️ {pref['brand']}: {correct_name}")
            
            if "ig_" in mode:
                if info.get("ig_handle"):
                    if isinstance(info['ig_handle'], list):
                        for handle in info['ig_handle']:
                            header_lines.append(f"📸 Instagram: {handle}")
                    else:
                        header_lines.append(f"📸 Instagram: {info['ig_handle']}")
                header_lines.append(pref["link_in_bio"])
            else:
                if info.get("links"): header_lines.extend(info["links"])
                    
            return "\n".join(header_lines) + "\n\n"
            
    header_lines.append(f"📅 {pref['year']}: {year}")
    if has_valid_loc: header_lines.append(f"📍 {pref['loc']}: {resolved_loc}")
    return "\n".join(header_lines) + "\n\n"

# =====================================================================
# 🔍 ЗАГОТОВКИ ДЛЯ РОЗШИРЕННЯ (Екзиф, Локації, Вотермарки)
# =====================================================================
def extract_exif_metadata(file_path):
    """Заготовка для читання метаданих EXIF зображення (дата зйомки, GPS координати)."""
    # Буде розширено за потреби за допомогою PIL.ExifTags
    return {}

def overlay_text_on_image(image_path, text, position="bottom"):
    """Заготовка для брендування або нанесення технічного тексту безпосередньо на картинку."""
    # Буде розширено за допомогою ImageDraw
    return image_path
