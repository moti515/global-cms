"""
================================================================================
🎨 МОДУЛЬ ОБРОБКИ МЕДІА ТА ФОРМАТУВАННЯ (Geom, Padding, FFmpeg, Headers)
================================================================================

ОПИС РОБОТИ МОДУЛЯ:
 1. Оптимізація та нормалізація зображень: Відкриває зображення будь-якого формату 
    (HEIC, PNG, WEBP, JPEG), автоповертає пікселі за EXIF Orientation,
    примусово конвертує у чистий Baseline RGB JPEG.
 2. Калібрування пропорцій: Якщо співвідношення сторін виходить за межі [0.8; 1.91], 
    додає підкладку ЧОРНОГО кольору (0, 0, 0) для відповідності стандартам Meta API.
 3. Аналіз відео: Використовує системну утиліту FFmpeg (із захисною перевіркою shutil.which) 
    для витягування першого кадру, який передається в ШІ Gemini.
 4. Шаблонізатор заголовків: Формує естетичний блок із інформацією про бренд, 
    рік, локацію та соціальні посилання відповідно до вибраної мови (UK/EN/DE).

================================================================================
"""

import os
import json
import shutil
import subprocess
from datetime import datetime
from PIL import Image, ImageOps  # 👈 Додано ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()


def optimize_media_geometry(local_path: str, filename: str, mime_type: str) -> str:
    """Нормалізує зображення у RGB JPEG, повертає за EXIF та додає ЧОРНІ поля (0, 0, 0) для неправильних пропорцій."""
    if not os.path.exists(local_path):
        return local_path

    if filename.lower().endswith(('.mp4', '.mov', '.avi')):
        return local_path

    try:
        with Image.open(local_path) as img:
            # 🌟 КРИТИЧНЕ ВИПРАВЛЕННЯ: Фізично повертаємо пікселі згідно з EXIF Orientation
            img = ImageOps.exif_transpose(img)
            img = img.convert('RGB')
            
            w, h = img.size
            ratio = w / h
            needs_padding = ratio < 0.8 or ratio > 1.91

            base_name = filename.rsplit('.', 1)[0]
            new_filename = f"post_ready_{base_name}.jpg" if not base_name.startswith('post_ready_') else f"{base_name}.jpg"
            
            os.makedirs('temp_mebli', exist_ok=True)
            optimized_path = os.path.join('temp_mebli', new_filename)

            if needs_padding:
                print(f"📐 Оптимізація геометрії ({ratio:.2f}) та нормалізація для: {filename}")
                new_w = int(h * 0.8) if ratio < 0.8 else w
                new_h = h if ratio < 0.8 else int(w / 1.91)

                # Чорне полотно (0, 0, 0)
                canvas = Image.new('RGB', (new_w, new_h), (0, 0, 0))
                paste_x = (new_w - w) // 2
                paste_y = (new_h - h) // 2
                canvas.paste(img, (paste_x, paste_y))
                canvas.save(optimized_path, 'JPEG', quality=95, progressive=False)
            else:
                print(f"🔄 Обов'язкова нормалізація {filename} у стандартний RGB JPEG...")
                img.save(optimized_path, 'JPEG', quality=95, progressive=False)

            return optimized_path

    except Exception as e:
        print(f"⚠️ Помилка обробки медіа геометрії: {e}")

    return local_path


def extract_video_frame(video_path: str, output_frame_path: str):
    """Витягує тестовий кадр з відео через FFmpeg для ШІ-аналізу з перевіркою бінарника."""
    print(f"🎬 Витягуємо тестовий кадр з відео: {os.path.basename(video_path)}")
    if not shutil.which('ffmpeg'):
        print("⚠️ Увага: FFmpeg не знайдено у системі. Кадр не витягнуто.")
        return None

    try:
        cmd = ['ffmpeg', '-y', '-i', video_path, '-ss', '00:00:01', '-vframes', '1', output_frame_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(output_frame_path):
            return output_frame_path
    except Exception as e:
        print(f"⚠️ Помилка виконання FFmpeg: {e}")

    return None


def get_manufacturer_header(category: str, date_str: str, lang_idx: int, mode: str, target_loc: str = None) -> str:
    """Генерує заголовок поста відповідно до бренду, мови та локації."""
    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else str(datetime.now().year)
    cat_lower = category.lower()
    
    import config_meta_post as config
    pref = config.LANG_CONFIG.get(lang_idx, config.LANG_CONFIG[0])
    header_lines = []

    resolved_loc = ""
    if target_loc:
        try:
            loc_json = json.loads(target_loc)
            resolved_loc = loc_json.get(str(lang_idx), loc_json.get("0", "")) if isinstance(loc_json, dict) else str(target_loc)
        except (json.JSONDecodeError, TypeError):
            resolved_loc = str(target_loc)

    invalid_markers = ["невідоме місце", "невідомо", "unknown", "unbekannt", "-", "none", "null"]
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
                    handles = info['ig_handle'] if isinstance(info['ig_handle'], list) else [info['ig_handle']]
                    for handle in handles:
                        header_lines.append(f"📸 Instagram: {handle}")
                header_lines.append(pref["link_in_bio"])
            else:
                if info.get("links"):
                    header_lines.extend(info["links"])

            return "\n".join(header_lines) + "\n\n"

    header_lines.append(f"📅 {pref['year']}: {year}")
    if has_valid_loc: header_lines.append(f"📍 {pref['loc']}: {resolved_loc}")
    return "\n".join(header_lines) + "\n\n"
