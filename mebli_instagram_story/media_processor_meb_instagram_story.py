import os
import re
import json
import textwrap
import subprocess
import requests
from datetime import datetime
from PIL import Image, ImageDraw, ImageOps, ImageFont
from PIL.ExifTags import TAGS, GPSTAGS

# 🎨 ГЕНЕРАЦІЯ ЄДИНОГО ПРОЗОРОГО PNG-ОВЕРЛЕЮ З ТЕКСТОМ ТА ЕМОДЗІ
def generate_story_overlay(base_name, text, year=None, location=None):
    """
    Створює прозорий PNG-файл (1080x1920), наносить текст (з підтримкою емодзі та умляутів)
    і повертає шлях до тимчасового файлу.
    """
    overlay = Image.new('RGBA', (1080, 1920), (0, 0, 0, 0))
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font_size_main = 40
    font_size_meta = 34
    
    try:
        font_main = ImageFont.truetype(font_path, font_size_main)
        font_meta = ImageFont.truetype(font_path, font_size_meta)
    except IOError:
        font_main = ImageFont.load_default()
        font_meta = ImageFont.load_default()

    # Спробуємо підключити pilmoji для кольорових емодзі
    try:
        from pilmoji import Pilmoji
        has_pilmoji = True
    except ImportError:
        print("⚠️ Бібліотеку 'pilmoji' не знайдено. Емодзі можуть відображатися некоректно. Виконайте: pip install pilmoji")
        has_pilmoji = False

    # Тимчасовий інструмент для замірів довжини рядків
    draw_measure = ImageDraw.Draw(overlay)

    # 1. Розбиття головного тексту на рядки (Wrap)
    lines = []
    if text:
        words = text.split()
        current_line = []
        for word in words:
            current_line.append(word)
            if draw_measure.textlength(" ".join(current_line), font=font_main) > 920:
                current_line.pop()
                lines.append(" ".join(current_line))
                current_line = [word]
        if current_line:
            lines.append(" ".join(current_line))

    # Внутрішня функція для малювання тексту з обведенням (штрихом) через Pilmoji або Pillow
    def draw_text_dynamic(canvas, position, content, font, fill_color, stroke_color=(0, 0, 0, 240), stroke_width=3):
        if has_pilmoji:
            with Pilmoji(canvas) as pilmoji:
                pilmoji.text(position, content, font=font, fill=fill_color, stroke_width=stroke_width, stroke_fill=stroke_color)
        else:
            d = ImageDraw.Draw(canvas)
            d.text(position, content, font=font, fill=fill_color, stroke_width=stroke_width, stroke_fill=stroke_color)

    # 2. Малювання головного тексту (Центрований, зверху)
    start_y = 200
    line_height = 55
    for i, line in enumerate(lines):
        bbox = draw_measure.textbbox((0, 0), line, font=font_main)
        text_w = bbox[2] - bbox[0]
        current_x = (1080 - text_w) // 2
        current_y = start_y + (i * line_height)
        
        draw_text_dynamic(overlay, (current_x, current_y), line, font_main, (255, 255, 255))

    # 3. Малювання метаданих (Зліва, знизу)
    meta_parts = []
    if location and location != "Невідоме місце":
        meta_parts.append(location)
    if year:
        meta_parts.append(str(year))
        
    if meta_parts:
        meta_text = " | ".join(meta_parts)
        meta_x = 70
        meta_y = 1650
        draw_text_dynamic(overlay, (meta_x, meta_y), meta_text, font_meta, (255, 240, 100))

    overlay_path = os.path.join('temp_mebli', f'overlay_{base_name}.png')
    overlay.save(overlay_path, 'PNG')
    return overlay_path


# 📐 ОПТИМІЗАЦІЯ ФОТО ПІД СТОРІЗ (1080x1920)
def optimize_image_story(final_upload_path, orig_name):
    print("📐 Режим Сторіс (Фото): вписуємо зображення у формат 1080x1920...")
    story_path = os.path.join('temp_mebli', 'story_padded_' + orig_name.rsplit('.', 1)[0] + '.jpg')
    try:
        with Image.open(final_upload_path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert('RGB')
            orig_w, orig_h = img.size
            
            target_w, target_h = 1080, 1920
            canvas = Image.new('RGB', (target_w, target_h), (20, 20, 20)) # Темно-сірий фон
            
            scale = min(target_w / orig_w, target_h / orig_h)
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)
            
            resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            paste_x = (target_w - new_w) // 2
            paste_y = (target_h - new_h) // 2
            canvas.paste(resized_img, (paste_x, paste_y))
            canvas.save(story_path, 'JPEG', quality=95)
            
        return story_path
    except Exception as e:
        print(f"⚠️ Не вдалося відформатувати фото під сторіз: {e}")
        return final_upload_path


# ✍️ НАКЛАДАННЯ ТЕКСТУ НА ЗОБРАЖЕННЯ ЧЕРЕЗ PNG-ОВЕРЛЕЙ
def overlay_text_on_image(image_path, text, year=None, location=None):
    try:
        base_name = os.path.basename(image_path).rsplit('.', 1)[0]
        # ГЕНЕРУЄМО ТЕКСТОВИЙ ШАР
        overlay_png = generate_story_overlay(base_name, text, year, location)
        
        with Image.open(image_path) as img:
            img = img.convert('RGBA')
            with Image.open(overlay_png) as overlay:
                # Накладаємо прозорий шар поверх фото
                final_img = Image.alpha_composite(img, overlay)
            
            final_img = final_img.convert('RGB')
            final_img.save(image_path, 'JPEG', quality=95)
            
        # Очищуємо тимчасовий PNG
        if os.path.exists(overlay_png):
            os.remove(overlay_png)
            
        print("🎨 Текст та емодзі успішно нанесено на фото сторіс через PNG-оверлей.")
    except Exception as e:
        print(f"⚠️ Помилка графічного накладання тексту на фото: {e}")


# 📐 ОПТИМІЗАЦІЯ ВІДЕО ПІД СТОРІЗ ЧЕРЕЗ FFMPEG (МЕТОД PNG-ОВЕРЛЕЮ)
def optimize_video_story(local_path, f_name, text, year=None, location=None):
    print("🎬 Оптимізація Відео: підганяємо під ліміти Instagram (1080x1920) + Мапимо PNG-оверлей...")
    processed_files = []
    
    duration = get_video_duration(local_path)
    if duration == 0:
        print("⚠️ Тривалість 0 або помилка аналізу. Спробуємо обробити як один файл.")
        duration = 59.0
        
    base_name = f_name.rsplit('.', 1)[0]
    
    # 1. СТВОРЮЄМО КРАСИВИЙ ШАР З ТЕКСТОМ ТА ЕМОДЗІ
    overlay_png_path = generate_story_overlay(base_name, text, year, location)
    
    # 2. СКЛАДАЄМО ФІЛЬТР FFMPEG: спочатку ресайз відео, потім накладання PNG оверлею за координатами 0:0
    vf_base = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black"
    filter_complex = f"[0:v]{vf_base}[bg]; [bg][1:v]overlay=0:0"
    
    output_template = os.path.join('temp_mebli', f'story_padded_{base_name}_part_%03d.mp4')
    
    cmd = [
        'ffmpeg', '-y', 
        '-i', local_path,          # Вхід [0:v] - відео
        '-i', overlay_png_path,    # Вхід [1:v] - наш прозорий PNG оверлей
        '-filter_complex', filter_complex,
        '-c:v', 'libx264', 
        '-profile:v', 'main', 
        '-level:v', '4.0', 
        '-pix_fmt', 'yuv420p',
        '-b:v', '3000k',          
        '-maxrate', '4500k', 
        '-bufsize', '9000k', 
        '-c:a', 'aac', 
        '-b:a', '128k'
    ]
    
    if duration > 60.0:
        print(f"✂️ Відео триває {duration:.1f} сек. Нарізаємо на частини по 60 секунд...")
        cmd += [
            '-f', 'segment',
            '-segment_time', '60',
            '-reset_timestamps', '1',
            output_template
        ]
    else:
        single_output = os.path.join('temp_mebli', f'story_padded_{base_name}.mp4')
        cmd.append(single_output)

    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Видаляємо тимчасовий файл оверлею, бо він уже "впечений" у відео
        if os.path.exists(overlay_png_path):
            try: os.remove(overlay_png_path)
            except: pass
            
        if res.returncode == 0:
            if duration > 60.0:
                dir_content = os.listdir('temp_mebli')
                part_files = sorted([
                    os.path.join('temp_mebli', f) for f in dir_content 
                    if f.startswith(f'story_padded_{base_name}_part_') and f.endswith('.mp4')
                ])
                processed_files.extend(part_files)
            else:
                single_output = os.path.join('temp_mebli', f'story_padded_{base_name}.mp4')
                if os.path.exists(single_output):
                    processed_files.append(single_output)
                    
            return processed_files
    except Exception as e:
        print(f"⚠️ Помилка під час рендерингу відео через FFmpeg: {e}")
        
    return [local_path]

def get_video_duration(video_path):
    cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nocues=1', video_path]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0 and res.stdout:
            return float(res.stdout.strip())
    except Exception as e:
        print(f"⚠️ Не вдалося визначити тривалість відео: {e}")
    return 0.0

# =====================================================================
# 🧠 ІНТЕЛЕКТУАЛЬНИЙ БЛОК АНАЛІЗУ МЕТАДАНИХ ТА ГЕОЛОКАЦІЇ
# =====================================================================

def extract_date_from_filename(filename):
    current_year = datetime.now().year
    min_year = 2000  
    name_part = filename.rsplit('.', 1)[0]

    match_yyyy_mm_dd = re.search(r'\b(\d{4})[-._]?(0[1-9]|1[0-2])[-._]?([0-2]\d|3[01])', name_part)
    if match_yyyy_mm_dd:
        year, month, day = match_yyyy_mm_dd.groups()
        try: 
            dt = datetime(int(year), int(month), int(day))
            if min_year <= dt.year <= current_year:
                print(f"🎯 Дату успішно розпізнано за шаблоном [РРРР-ММ-ДД]: {dt.strftime('%d.%m.%Y')}")
                return dt
        except ValueError: 
            pass

    match_dd_mm_yyyy = re.search(r'\b(0[1-9]|[12]\d|3[01])[-._]?(0[1-9]|1[0-2])[-._]?(\d{4})', name_part)
    if match_dd_mm_yyyy:
        day, month, year = match_dd_mm_yyyy.groups()
        try: 
            dt = datetime(int(year), int(month), int(day))
            if min_year <= dt.year <= current_year:
                print(f"🎯 Дату успішно розпізнано за шаблоном [ДД-ММ-РРРР]: {dt.strftime('%d.%m.%Y')}")
                return dt
        except ValueError: 
            pass
            
    return None

def get_exif_data(image_path):
    date_str, lat, lon = None, None, None
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return date_str, lat, lon
            
            exif_ifd = exif.get_ifd(34665)
            if exif_ifd:
                for tag, value in exif_ifd.items():
                    if TAGS.get(tag) == 'DateTimeOriginal':
                        date_str = value
                        break
            
            if not date_str:
                for tag, value in exif.items():
                    if TAGS.get(tag) == 'DateTimeOriginal':
                        date_str = value
                        break

            gps_ifd = exif.get_ifd(34853)
            if gps_ifd:
                geotagging = {}
                for t, value in gps_ifd.items():
                    sub_decoded = GPSTAGS.get(t, t)
                    geotagging[sub_decoded] = value
                
                if 'GPSLatitude' in geotagging and 'GPSLongitude' in geotagging:
                    def _to_degrees(value):
                        return float(value[0]) + (float(value[1]) / 60.0) + (float(value[2]) / 3600.0)
                    
                    lat = _to_degrees(geotagging['GPSLatitude'])
                    lon = _to_degrees(geotagging['GPSLongitude'])
                    if geotagging.get('GPSLatitudeRef') == 'S': lat = -lat
                    if geotagging.get('GPSLongitudeRef') == 'W': lon = -lon
    except Exception as e:
        print(f"⚠️ Попередження EXIF для {image_path}: {e}")
    return date_str, lat, lon

def get_video_metadata(video_path):
    date_str, lat, lon = None, None, None
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', video_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            tags = data.get('format', {}).get('tags', {})
            creation_time = tags.get('creation_time')
            if creation_time:
                try:
                    dt = datetime.strptime(creation_time[:19], '%Y-%m-%dT%H:%M:%S')
                    date_str = dt.strftime('%Y:%m:%d %H:%M:%S')
                except: 
                    pass
            
            loc_str = tags.get('location') or tags.get('location-eng')
            if loc_str:
                match = re.match(r'([+-]\d+\.\d+)([+-]\d+\.\d+)', loc_str)
                if match:
                    lat, lon = float(match.group(1)), float(match.group(2))
    except Exception as e:
        print(f"⚠️ Попередження відео-метаданих для {video_path}: {e}")
    return date_str, lat, lon

def get_location_data(lat, lon):
    """
    Повертає локалізацію оригінальною мовою місцевості (без примусового accept-language=uk).
    Результат: кортеж (КрасиваНазваДляВідео, НазваМістаДляГрупування)
    """
    if lat is None or lon is None: 
        return "", ""
    try:
        # 🌟 КЛЮЧОВА ЗМІНА: Вилучено параметр accept-language=uk. 
        # Тепер сервіс повертає назви мовою локації зйомки.
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=15"
        headers = {'User-Agent': 'FurnitureStories_MetadataBot_2026'}
        res = requests.get(url, headers=headers, timeout=10).json()
        address = res.get('address', {})
        
        # 1. Точне місце (виробництво, пам'ятка, локальний об'єкт чи назва району/міста)
        exact_place = (
            address.get('tourism') or 
            address.get('amenity') or 
            address.get('historic') or
            address.get('suburb') or 
            address.get('city') or 
            address.get('town') or 
            address.get('village')
        )
        country = address.get('country')
        display_location = f"{exact_place}, {country}" if exact_place and country else country
        
        # 2. Стабільне місто/регіон для кластеризації
        group_place = address.get('city') or address.get('town') or address.get('village') or address.get('county')
        group_location = f"{group_place}, {country}" if group_place and country else country
        
        return display_location, group_location
    except Exception as e:
        print(f"⚠️ Помилка геокодування OSM: {e}")
    return "", ""

def get_intellectual_date(local_path, filename, gdrive_file, now_time=None):
    if now_time is None:
        now_time = datetime.now()
    min_year = 2000  

    fn_date = extract_date_from_filename(filename)
    if fn_date:
        print(f"🎯 Дату успішно розпізнано з назви файлу '{filename}': {fn_date.strftime('%d.%m.%Y')}")
    
    meta_date, lat, lon = None, None, None
    mime_type = gdrive_file.get('mimeType', '')
    lower_name = filename.lower()
    
    if mime_type.startswith('image/') or lower_name.endswith(('.heic', '.heif', '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff')):
        meta_date, lat, lon = get_exif_data(local_path)
    elif mime_type.startswith('video/') or lower_name.endswith(('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.mpeg', '.mpg')):
        meta_date, lat, lon = get_video_metadata(local_path)

    if fn_date:
        return fn_date, lat, lon

    if meta_date:
        for date_format in ('%Y:%m:%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
            try:
                clean_meta = str(meta_date).strip()[:19].replace('T', ' ')
                clean_fmt = date_format.replace('T', ' ')
                dt_parsed = datetime.strptime(clean_meta, clean_fmt)
                if min_year <= dt_parsed.year <= now_time.year:
                    return dt_parsed, lat, lon
            except ValueError:
                continue

    try:
        dt_created = datetime.strptime(gdrive_file['createdTime'][:19], '%Y-%m-%dT%H:%M:%S')
        dt_modified = datetime.strptime(gdrive_file['modifiedTime'][:19], '%Y-%m-%dT%H:%M:%S')
        earliest_gdrive = min(dt_created, dt_modified)
        if min_year <= earliest_gdrive.year <= now_time.year:
            return earliest_gdrive, lat, lon
    except Exception as e:
        print(f"⚠️ Помилка зчитування системних дат Google Drive: {e}")

    return now_time, lat, lon
