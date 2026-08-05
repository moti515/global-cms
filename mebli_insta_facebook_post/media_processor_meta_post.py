import os
import json
import subprocess
from datetime import datetime
from PIL import Image
from pillow_heif import register_heif_opener
import config_meta_post as config

# Реєстрація підтримки HEIF/HEIC для Pillow
register_heif_opener()

def optimize_media_geometry(local_path, filename, mime_type):
    """
    Оптимізує пропорції зображень (додає білі поля) та ОБОВ'ЯЗКОВО 
    нормалізує всі зображення у стандартний RGB Baseline JPEG для сумісності з Meta API.
    """
    if not os.path.exists(local_path):
        return local_path

    lower_name = filename.lower()
    is_video = lower_name.endswith(('.mp4', '.mov', '.avi'))

    if is_video:
        return local_path  # Відео не потребує обробки геометрії в цьому модулі

    try:
        with Image.open(local_path) as img:
            # Примусово конвертуємо колірний режим (CMYK, RGBA, P) у чистий RGB
            img = img.convert('RGB')
            w, h = img.size
            ratio = w / h
            
            needs_padding = ratio < 0.8 or ratio > 1.91
            
            # Формуємо ім'я для гарантовано нормалізованого файлу
            base_name = filename.rsplit('.', 1)[0]
            new_filename = f"post_ready_{base_name}.jpg" if not base_name.startswith('post_ready_') else f"{base_name}.jpg"
                
            os.makedirs('temp_mebli', exist_ok=True)
            optimized_path = os.path.join('temp_mebli', new_filename)

            if needs_padding:
                print(f"📐 Оптимізація геометрії ({ratio:.2f}) та нормалізація для: {filename}")
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
                canvas.save(optimized_path, 'JPEG', quality=95, progressive=False)
            else:
                print(f"🔄 Обов'язкова нормалізація {filename} у стандартний RGB JPEG...")
                img.save(optimized_path, 'JPEG', quality=95, progressive=False)
                
            return optimized_path

    except Exception as e:
        print(f"⚠️ Помилка калібрування геометрії або конвертації поста: {e}")

    return local_path

def extract_video_frame(video_path, output_frame_path):
    """Витягує 1-шу секунду відео за допомогою FFmpeg для аналізу через Gemini AI."""
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

    # --- БЛОК ОБРОБКИ БАГАТОМОВНОЇ ЛОКАЦІЇ ---
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

    # 1. Спеціальні службові категорії
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

    # 2. Пошук у глобальній базі брендів/компаній
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
            
    # Дефолтний вивід, якщо бренд не знайдено в базі
    header_lines.append(f"📅 {pref['year']}: {year}")
    if has_valid_loc: header_lines.append(f"📍 {pref['loc']}: {resolved_loc}")
    return "\n".join(header_lines) + "\n\n"

def extract_exif_metadata(file_path):
    """Заготовка для майбутнього читання метаданих EXIF (дата, GPS)."""
    return {}

def overlay_text_on_image(image_path, text, position="bottom"):
    """Заготовка для нанесення вотермарок або технічного брендування."""
    return image_path
