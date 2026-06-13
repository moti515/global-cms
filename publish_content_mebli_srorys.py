import os
import sys
import json
import time
import base64
import requests
import subprocess
import re
import textwrap
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image, ImageDraw, ImageOps, ImageFont
from PIL.ExifTags import TAGS, GPSTAGS
from pillow_heif import register_heif_opener

# Реєстрація підтримки HEIF/HEIC
register_heif_opener()

# ⚙️ НАЛАШТУВАННЯ (Беруться напряму з системних змінних GitHub Actions)
IG_USER_ID = os.environ.get("IG_USER_ID")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")

SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
TAB_NAME = "Меблі"

HOT_FOLDER_ID = '1BlPC3ua00pHnqdwpy2EA3EzOA-tCmt2N'
TRASH_FOLDER_ID = '1L3veD90e7Fr1acwlK7PmhSs_JrofyT6N'

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

VALID_MEDIA_EXTENSIONS = ('.gif', '.heic', '.heif', '.jpeg', '.jpg', '.mp4', '.png', '.webp', '.mov', '.avi')
DOCUMENT_EXTENSIONS = ('.pdf', '.doc', '.docx', '.djvu', '.txt', '.rtf', '.fb2', '.epub')

# 🏢 СИНХРОНІЗОВАНА ГЛОБАЛЬНА БАЗА ДАНИХ КОМПАНЙ ТА КАТЕГОРІЙ
COMPANIES_DB = {
    "goncharenko": {
        "names": {0: "Олександр Гончаренко", 1: "Oleksandr Goncharenko", 2: "Oleksandr Goncharenko"},
        "links": ["📸 Instagram: instagr.am/goncharenko8721"],
        "ig_handle": "@goncharenko8721"
    },
    "gurov": {
        "names": {0: "Андрій Гуров", 1: "Andrii Gurov", 2: "Andrii Gurov"},
        "links": ["🌐 Facebook: fb.com/andrej.gurov.755581"]
    },
    "solovey": {
        "names": {0: "Студія меблів «Соловей»", 1: "Solovey Furniture Studio", 2: "Möbelstudio Solovey"},
        "links": ["📸 Instagram: instagr.am/mebelsolovei"],
        "ig_handle": "@mebelsolovei"
    },
    "furniture park": {
        "names": {0: "Меблевий парк", 1: "Furniture Park", 2: "Furniture Park"},
        "links": [
            "📸 Instagram: instagr.am/meblevyi_park",
            "📸 Instagram: instagr.am/meblovo_ukraine",
            "📢 Telegram: t.me/Meblevyi_park",
            "📸 Instagram: instagr.am/renovaelite"
        ],
        "ig_handle": [
            "@meblevyi_park",
            "@meblovo_ukraine",
            "@renovaelite"
        ]
    }
}

def get_services():
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds), build('sheets', 'v4', credentials=creds)

def log_unsupported_to_service(sheets_service, folder_name, file_name, reason="непідтримуваний формат"):
    try:
        res = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2:E"
        ).execute()
        rows = res.get('values', [])
        
        for idx, row in enumerate(rows):
            if len(row) > 1 and row[1] == folder_name:
                range_to_update = f"'⚙️ Налаштування Папок'!E{idx + 2}"
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID, range=range_to_update,
                    valueInputOption='RAW', body={'values': [[f"⚠️ {reason}: {file_name}"]]}
                ).execute()
                print(f"📝 Зафіксовано системне попередження для [{folder_name}] на службовому аркуші.")
                break
    except Exception as e:
        print(f"❌ Не вдалося записати помилку на службовий аркуш: {e}")

# 📐 ОПТИМІЗАЦІЯ ФОТО ПІД СТОРІЗ (1080x1920) З УРАХУВАННЯМ ОРІЄНТАЦІЇ КАНАЛУ
def optimize_image_story(final_upload_path, orig_name):
    print("📐 Режим Сторіс (Фото): вписуємо зображення у формат 1080x1920...")
    story_path = os.path.join('temp_mebli', 'story_padded_' + orig_name.rsplit('.', 1)[0] + '.jpg')
    try:
        with Image.open(final_upload_path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert('RGB')
            orig_w, orig_h = img.size
            
            target_w, target_h = 1080, 1920
            canvas = Image.new('RGB', (target_w, target_h), (20, 20, 20)) # Темно-сірий преміальний фон
            
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
        print(f"⚠️ Не вдалося відформатувати фото под сторіз: {e}")
        return final_upload_path

def get_video_duration(video_path):
    cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nocues=1', video_path]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0 and res.stdout:
            return float(res.stdout.strip())
    except Exception as e:
        print(f"⚠️ Не вдалося визначити тривалість відео: {e}")
    return 0.0

# 📐 ОПТИМІЗАЦІЯ ВІДЕО ПІД СТОРІЗ (1080x1920) + НАКЛАДАННЯ ТЕКСТУ ЧЕРЕЗ FFMPEG
def optimize_video_story(local_path, f_name, text, year=None, location=None):
    print("🎬 Оптимізація Відео: підганяємо під ліміти Instagram (1080x1920, стиснення, новий макет)...")
    processed_files = []
    
    duration = get_video_duration(local_path)
    if duration == 0:
        print("⚠️ Тривалість 0 або помилка аналізу. Спробуємо обробити як один файл.")
        duration = 59.0
        
    base_name = f_name.rsplit('.', 1)[0]
    vf_filters = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black"
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.exists(font_path):
        if text:
            # Валідація кирилиці для FFmpeg (захист від кракозябр)
            text = "".join(c for c in text if ord(c) < 128 or (0x0400 <= ord(c) <= 0x04FF) or c in "—–«»’'\".,!?-() ")
            clean_text = text.replace("'", "").replace(":", "\\:").replace(",", "\\,")
            lines = textwrap.wrap(clean_text, width=30)
            
            start_y = 200
            line_height = 55
            for i, line in enumerate(lines):
                current_y = start_y + (i * line_height)
                vf_filters += (
                    f",drawtext=fontfile={font_path}:text='{line}':"
                    f"x=(w-text_w)/2:y={current_y}:fontsize=42:fontcolor=white:"
                    f"borderw=5:bordercolor=black:fix_bounds=1"
                )
        
        meta_parts = []
        if location and location != "Невідоме місце":
            meta_parts.append(location)
        if year:
            meta_parts.append(str(year))
            
        if meta_parts:
            clean_meta = " | ".join(meta_parts).replace("'", "").replace(":", "\\:").replace(",", "\\,")
            vf_filters += (
                f",drawtext=fontfile={font_path}:text='{clean_meta}':"
                f"x=70:y=1650:fontsize=34:fontcolor=0xFFE664:"  # Жовтий колір у форматі FFmpeg (Hex)
                f"borderw=4:bordercolor=black:fix_bounds=1"
            )

    output_template = os.path.join('temp_mebli', f'story_padded_{base_name}_part_%03d.mp4')
    
    cmd = [
        'ffmpeg', '-y', '-i', local_path,
        '-vf', vf_filters,
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

# ✍️ ГАРМОНІЙНЕ НАКЛАДАННЯ ТЕКСТУ НА ЗОБРАЖЕННЯ (PILLOW) — НОВИЙ МАКЕТ
def overlay_text_on_image(image_path, text, year=None, location=None):
    try:
        text = "".join(c for c in text if ord(c) < 128 or (0x0400 <= ord(c) <= 0x04FF) or c in "—–«»’'\".,!?-() ")
        
        with Image.open(image_path) as img:
            img = img.convert('RGBA')
            draw = ImageDraw.Draw(img)
            
            font_size_main = 40
            font_size_meta = 34
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            
            try:
                font_main = ImageFont.truetype(font_path, font_size_main)
                font_meta = ImageFont.truetype(font_path, font_size_meta)
            except IOError:
                font_main = ImageFont.load_default()
                font_meta = ImageFont.load_default()
            
            if text:
                words = text.split()
                lines = []
                current_line = []
                for word in words:
                    current_line.append(word)
                    if draw.textlength(" ".join(current_line), font=font_main) > 920:
                        current_line.pop()
                        lines.append(" ".join(current_line))
                        current_line = [word]
                if current_line:
                    lines.append(" ".join(current_line))
                
                start_y = 200      
                line_height = 55   
                
                for i, line in enumerate(lines):
                    bbox = draw.textbbox((0, 0), line, font=font_main)
                    text_w = bbox[2] - bbox[0]
                    current_x = (1080 - text_w) // 2
                    current_y = start_y + (i * line_height)
                    
                    for dx, dy in [(-2,-2), (-2,2), (2,-2), (2,2), (-1,0), (1,0), (0,-1), (0,1)]:
                        draw.text((current_x + dx, current_y + dy), line, font=font_main, fill=(0, 0, 0, 240))
                    draw.text((current_x, current_y), line, font=font_main, fill=(255, 255, 255))
            
            meta_parts = []
            if location and location != "Невідоме місце":
                meta_parts.append(location)
            if year:
                meta_parts.append(str(year))
                
            if meta_parts:
                meta_text = " | ".join(meta_parts)
                meta_x = 70
                meta_y = 1650  
                
                for dx, dy in [(-2,-2), (-2,2), (2,-2), (2,2), (-1,0), (1,0), (0,-1), (0,1)]:
                    draw.text((meta_x + dx, meta_y + dy), meta_text, font=font_meta, fill=(0, 0, 0, 240))
                draw.text((meta_x, meta_y), meta_text, font=font_meta, fill=(255, 240, 100))
            
            final_img = img.convert('RGB')
            final_img.save(image_path, 'JPEG', quality=95)
            print("🎨 Текст та метадані успішно нанесено на фото сторіс за новим макетом.")
    except Exception as e:
        print(f"⚠️ Помилка графічного накладання тексту на фото: {e}")

# 🌍 ЦЕНТРАЛІЗОВАНИЙ КАСКАДНИЙ ЗАВАНТАЖУВАЧ З ПІДТРИМКОЮ IMGBB
def get_google_drive_direct_url(file_id, local_file_path=None):
    if local_file_path and os.path.exists(local_file_path):
        filename = os.path.basename(local_file_path)
        lower_name = filename.lower()
        mime_type = "video/mp4" if lower_name.endswith(('.mp4', '.mov', '.avi')) else "image/jpeg"
        browser_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
        }
        
        # 1️⃣ Catbox.moe
        print(f"☁️ Завантажуємо сторіс-файл {filename} на Catbox.moe...")
        try:
            with open(local_file_path, 'rb') as f:
                file_bytes = f.read()
            if file_bytes:
                res = requests.post(
                    'https://catbox.moe/user/api.php',
                    data={'reqtype': 'fileupload'},
                    files={'fileToUpload': (filename, file_bytes, mime_type)},
                    headers=browser_headers, timeout=(7, 25)
                )
                if res.status_code == 200 and res.text.startswith('http'):
                    return res.text.strip(), None
        except: pass

        # 2️⃣ ImageKit.io
        imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
        if imagekit_key:
            print(f"☁️ Завантажуємо сторіс-файл {filename} на ImageKit.io...")
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://upload.imagekit.io/api/v1/files/upload',
                        auth=(imagekit_key, ''),
                        files={'file': (filename, f, mime_type)},
                        data={'fileName': filename, 'useUniqueFileName': 'true'}, timeout=60
                    )
                    if res.status_code in [200, 201]:
                        res_data = res.json()
                        return res_data.get('url'), res_data.get('fileId')
            except: pass

        # 3️⃣ ImgBB API (Синхронізовано з основним модулем)
        imgbb_key = os.environ.get("IMGBB_API_KEY")
        if imgbb_key and mime_type == "image/jpeg":
            print(f"☁️ Завантажуємо фото сторіс {filename} на ImgBB API...")
            try:
                with open(local_file_path, 'rb') as f:
                    img_bytes = f.read()
                if img_bytes:
                    res = requests.post(
                        'https://api.imgbb.com/1/upload',
                        data={'key': imgbb_key, 'expiration': 86400},
                        files={'image': (filename, img_bytes, mime_type)},
                        timeout=30
                    ).json()
                    if res.get('success'):
                        return res['data']['url'], None
            except: pass

    print(f"🚨 Аварійний режим завантаження для Google Drive ID: {file_id}")
    return f"https://docs.google.com/uc?export=download&id={file_id}", None

def delete_from_imagekit(file_id: str):
    if not file_id: return
    imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
    if not imagekit_key: return
    try: requests.delete(f"https://api.imagekit.io/v1/files/{file_id}", auth=(imagekit_key, ''), timeout=15)
    except: pass

# =====================================================================
# 🧠 ІНТЕЛЕКТУАЛЬНИЙ БЛОК АНАЛІЗУ МЕТАДАНИХ ТА ГЕОЛОКАЦІЇ (TIKTOK ENGINE)
# =====================================================================

def extract_date_from_filename(filename):
    """
    Каскадний пошук дати в імені файлу.
    Підтримує: YYYY-MM-DD, YYYYMMDD, DD-MM-YYYY, DDMMYYYY з будь-якими роздільниками.
    """
    current_year = datetime.now().year
    min_year = 2000  # Нижній ліміт для архіву меблів
    
    name_part = filename.rsplit('.', 1)[0]

    # 1️⃣ Формат РРРР-ММ-ДД або РРРРММДД (наприклад: "20090515", "2026_01_12")
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

    # 2️⃣ Формат ДД-ММ-РРРР або ДДММРРРР (наприклад: "14092011083", "15_05_2009")
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
            
    # Блок Unix Timestamp видалено за недоцільністю та для уникнення хибних спрацьовувань.
    return None

def get_exif_data(image_path):
    """Витягує дату зйомки та GPS координати з фотографій (включаючи суб-блоки IFD)."""
    date_str, lat, lon = None, None, None
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return date_str, lat, lon
            
            # Шукаємо DateTimeOriginal у правильному суб-блоці EXIF (ID: 34665)
            exif_ifd = exif.get_ifd(34665)
            if exif_ifd:
                for tag, value in exif_ifd.items():
                    if TAGS.get(tag) == 'DateTimeOriginal':
                        date_str = value
                        break
            
            # Резервний пошук в основних тегах
            if not date_str:
                for tag, value in exif.items():
                    if TAGS.get(tag) == 'DateTimeOriginal':
                        date_str = value
                        break

            # GPS інформація (ID: 34853)
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
    """Зчитує метадані відеофайлу за допомогою ffprobe."""
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
    Повертає локалізацію українською мовою.
    Результат: кортеж (КрасиваНазваДляВідео, НазваМістаДляГрупування)
    """
    if lat is None or lon is None: 
        return "", ""
    try:
        # Додано параметр accept-language=uk для збереження українських назв у сторіз
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=15&accept-language=uk"
        headers = {'User-Agent': 'FurnitureStories_MetadataBot_2026'}
        res = requests.get(url, headers=headers, timeout=10).json()
        address = res.get('address', {})
        
        # 1. Точне та цікаве місце (виробництво, шоурум, парк, локальний об'єкт)
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
        
        # 2. Стабільне місто/регіон для можливої кластеризації або сортування
        group_place = address.get('city') or address.get('town') or address.get('village') or address.get('county')
        group_location = f"{group_place}, {country}" if group_place and country else country
        
        return display_location, group_location
    except Exception as e:
        print(f"⚠️ Помилка геокодування OSM: {e}")
    return "", ""

def get_intellectual_date(local_path, filename, gdrive_file, now_time=None):
    """
    Каскадний пошук реальної дати створення медіафайлу.
    Пріоритет: Назва файлу -> EXIF/FFmpeg -> Дані Google Drive -> Поточний час.
    """
    if now_time is None:
        now_time = datetime.now()

    min_year = 2000  # Базовий рік початку цифрового архіву

    # 1️⃣ Спроба розпізнати дату з імені файлу
    fn_date = extract_date_from_filename(filename)
    if fn_date:
        print(f"🎯 Дату успішно розпізнано з назви файлу '{filename}': {fn_date.strftime('%d.%m.%Y')}")
    
    meta_date, lat, lon = None, None, None
    mime_type = gdrive_file.get('mimeType', '')
    lower_name = filename.lower()
    
    # 2️⃣ Збір метаданих залежно від типу контенту (EXIF)
    if mime_type.startswith('image/') or lower_name.endswith(('.heic', '.heif', '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff')):
        meta_date, lat, lon = get_exif_data(local_path)
    elif mime_type.startswith('video/') or lower_name.endswith(('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.mpeg', '.mpg')):
        meta_date, lat, lon = get_video_metadata(local_path)

    # Якщо дату взяли з імені файлу, повертаємо її (координати додаються, якщо знайшли в EXIF)
    if fn_date:
        return fn_date, lat, lon

    # 3️⃣ Якщо в імені дати не було, валідуємо дату з EXIF метаданих самого файлу
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
        print(f"⚠️ Метадані файлу містявть нелогічну дату: {meta_date}. Шукаємо заміну в системі Google Drive.")

    # 4️⃣ Фолбек: Дані про створення/модифікацію об'єкта в хмарі Google Drive
    try:
        dt_created = datetime.strptime(gdrive_file['createdTime'][:19], '%Y-%m-%dT%H:%M:%S')
        dt_modified = datetime.strptime(gdrive_file['modifiedTime'][:19], '%Y-%m-%dT%H:%M:%S')
        
        # Беремо найранішу дату. Для старих фото modifiedTime на Диску часто зберігає 
        # оригінальну дату зміни файлу на комп'ютері ще до епохи хмар (як ваші "15 бер. 2002 р.")
        earliest_gdrive = min(dt_created, dt_modified)
        
        # Змінено ліміт з 2010 на min_year (2000), щоб коректно пропускати архіви 2002 року
        if min_year <= earliest_gdrive.year <= now_time.year:
            return earliest_gdrive, lat, lon
    except Exception as e:
        print(f"⚠️ Помилка зчитування системних дат Google Drive: {e}")

    # 5️⃣ Крайній випадок: якщо взагалі нічого не знайшли, повертаємо поточний час
    return now_time, lat, lon

# 🧠 ШІ ГЕНЕРАЦІЯ ЛАКОНІЧНОГО ОПИСУ ДЛЯ КОНКРЕТНОЇ СТОРІС
def generate_story_caption(image_paths, category, date_str, lang_idx, target_loc):
    gemini_key = os.environ.get("GEMINI_API_KEY")
    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else str(datetime.now().year)
    
    # Використовуємо глобальний конфіг для фолбеків
    pref = LANG_CONFIG.get(lang_idx, LANG_CONFIG[0])
    
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
    if any(marker in resolved_loc.lower() for marker in invalid_markers):
        resolved_loc = ""
    # -----------------------------------------------

    # --- ВИЗНАЧЕННЯ БРЕНДУ ТА СПЕЦ-КАТЕГОРІЙ ---
    cat_lower = category.lower()
    real_manufacturer = category
    
    if "montage various" in cat_lower:
        real_manufacturer = "Професійний монтаж меблів" if lang_idx == 0 else "Professional furniture installation"
    elif "various" in cat_lower:
        real_manufacturer = "Сучасні меблеві тренди" if lang_idx == 0 else "Modern furniture concepts"
    elif "instruktion" in cat_lower:
        real_manufacturer = "Конструкторські стандарти" if lang_idx == 0 else "Furniture design standards"
    else:
        for key, info in COMPANIES_DB.items():
            if key in cat_lower:
                real_manufacturer = info["names"].get(lang_idx, info["names"][0])
                break

    if not gemini_key:
        return pref.get("no_gemini_caption", "Професійна якість та увага до деталей!")

    models_to_try = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]
    lang_instructions = {
        0: "Напиши текст виключно УКРАЇНСЬКОЮ мовою. КРИТИЧНО: НЕ використовуй жодних емодзі, смайлів чи спеціальних символів.",
        1: "Write the text exclusively in ENGLISH. CRITICAL: Do NOT use any emojis or special symbols.",
        2: "Schreibe den Text ausschließlich auf DEUTSCH. KRITISCH: Nutze absolute KEINE Emojis oder Sonderzeichen."
    }
    
    prompt = (
        f"Ти професійний копірайтер та меблевий конструктор. Подивись на це зображення (або кадр з відео).\n"
        f"Напиши ОДНУ коротку, мотиваційну або інформативну фразу (максимум 1-2 речення) для Instagram Stories.\n"
        f"Врахуй контекст: на фото може бути як готовий меблевий шедевр, так і брудний процес виробництва, технічна документація, "
        f"заміри приміщення, скріншоти програм, робочі моменти команди або навіть виправлення браку.\n"
        f"Зроби опис живим, експертним, без банальних закликів. Текст буде нанесено прямо на медіафайл.\n"
        f"Бренд/Концепт: '{real_manufacturer}'. Рік: {year}. Локація: {resolved_loc if resolved_loc else 'Робочий процес'}.\n"
        f"{lang_instructions.get(lang_idx, lang_instructions[0])}\n"
        f"КРИТИЧНО: Видай ЛІШЕ фінальний текст підпису без лапок, вступів та хештегів."
    )

    try:
        parts = [{"text": prompt}]
        for img_path in image_paths:
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    image_bytes = f.read()
                base64_image = base64.b64encode(image_bytes).decode('utf-8')
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64_image}})
        
        payload = {"contents": [{"parts": parts}]}
        for model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
            try:
                res = requests.post(url, json=payload, timeout=20).json()
                if 'candidates' in res and res['candidates']:
                    return res['candidates'][0]['content']['parts'][0]['text'].strip()
            except: 
                continue
    except Exception as e:
        print(f"⚠️ Помилка генерації текста ШІ: {e}")
        
    return pref.get("fallback_caption", "Створюємо меблі з точним розрахунком!")

def wait_for_meta_container(container_id, access_token):
    check_url = f"https://graph.facebook.com/v19.0/{container_id}"
    params = {"fields": "status_code,status", "access_token": access_token}
    for _ in range(30):
        try:
            r = requests.get(check_url, params=params).json()
            status = r.get("status_code", "").upper()
            if status == "FINISHED": return True
            elif status == "ERROR": return False
            print(f"⏳ Очікування обробки медіафайлу в Meta... Статус: {status}")
        except: pass
        time.sleep(5)
    return False

def main():
    if len(sys.argv) < 3:
        print("💡 Запуск: python script.py ig_story <tab_name>")
        sys.exit(1)  # Неправильний запуск — це теж помилка воркфлоу

    mode = sys.argv[1].lower()
    forced_tab = sys.argv[2]
    current_tab = forced_tab if forced_tab else TAB_NAME
    
    if mode != "ig_story":
        print(f"❌ Цей скрипт сконструйовано виключно под 'ig_story'. Передано: {mode}")
        sys.exit(1)

    drive, sheets = get_services()
    os.makedirs('temp_mebli', exist_ok=True)
    
    selected_queue = []
    has_global_failures = False  # 🚩 Головний індикатор помилок для GitHub Actions
    
    # 1️⃣ ЕТАП ПРІОРИТЕТУ: Перевірка наявності файлів у гарячій папці
    print(f"🔍 Перевірка наявності файлів у гарячій папці [{HOT_FOLDER_ID}]...")
    try:
        hot_query = f"'{HOT_FOLDER_ID}' in parents and trashed = false"
        hot_res = drive.files().list(
            q=hot_query,
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size)",
            orderBy="createdTime",
            pageSize=50
        ).execute()
        hot_files = hot_res.get('files', [])
    except Exception as e:
        print(f"❌ ПОМИЛКА під час отримання списку файлів з Google Диску: {e}")
        hot_files = []
        has_global_failures = True

    if hot_files:
        print(f"🔥 У гарячій папці виявлено {len(hot_files)} файлів. Активуємо пріоритетну чергу!")
        hot_group_items = []
        
        for f in hot_files:
            f_id, f_name = f['id'], f['name']
            lower_name = f_name.lower()
            
            if not lower_name.endswith(VALID_MEDIA_EXTENSIONS):
                print(f"⚠️ Файл [{f_name}] має непідтримуваний формат для Сторіс. Переносимо далі.")
                continue
            
            local_path = os.path.join('temp_mebli', f_name)
            print(f"📥 Попереднє завантаження для аналізу метаданих: {f_name}...")
            try:
                request = drive.files().get_media(fileId=f_id)
                with open(local_path, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done: _, done = downloader.next_chunk()
            except Exception as e:
                print(f"❌ Не вдалося завантажити {f_name} для аналізу: {e}")
                has_global_failures = True
                continue
            
            try:
                # Виклик оновленої функції (з лімітом від 2000 року і без Timestamp)
                final_date, lat, lon = get_intellectual_date(local_path, f_name, f)
                if hasattr(final_date, 'strftime'):
                    date_str = final_date.strftime('%d.%m.%Y')
                else:
                    date_str = str(final_date)

                display_location, group_location = get_location_data(lat, lon)
            except Exception as e:
                print(f"⚠️ Помилка автоматичного визначення дати/локації для {f_name}: {e}")
                date_str = "01.01.2026"  
                display_location, group_location = "", ""

            detected_company = "Загальне"
            for key in COMPANIES_DB.keys():
                if key in lower_name:
                    detected_company = key
                    break
            
            hot_group_items.append({
                "id": f_id,
                "name": f_name,
                "local_path": local_path,
                "category": detected_company,
                "date": date_str,
                "location": display_location,      
                "group_location": group_location,  
                "mode": "hot_folder",
                "counter_cell": None
            })

        if hot_group_items:
            groups = {}
            for item in hot_group_items:
                g_key = (item["date"], item["group_location"])
                groups.setdefault(g_key, []).append(item)
            
            first_key = list(groups.keys())[0]
            selected_queue = groups[first_key][:4]
            print(f"📂 [Гаряча Папка] Сформовано чергу: Дата={first_key[0]}, Локація для групування={first_key[1]}. Елементів: {len(selected_queue)}")
            
            selected_ids = {x["id"] for x in selected_queue}
            for item in hot_group_items:
                if item["id"] not in selected_ids and os.path.exists(item["local_path"]):
                    os.remove(item["local_path"])

    # 2️⃣ ЕТАП ФОЛБЕКУ: Якщо гаряча папка порожня, беремо дані з таблиці (Карусель)
    if not selected_queue:
        print(f"📊 [Режим: РЕЄСТР ТАБЛИЦІ] Гаряча папка порожня. Аналізуємо реєстр '{current_tab}'...")
        try:
            res = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{current_tab}'!A2:H").execute()
            rows = res.get('values', [])
        except Exception as e:
            print(f"❌ Помилка доступу до Google Sheets: {e}")
            sys.exit(1)

        if not rows:
            print("ℹ️ Реєстр порожній. Публікувати нічого.")
            return

        col_idx = 4
        col_letter = "E"

        valid_rows = []
        for i, r in enumerate(rows):
            if len(r) >= 3:  
                if r[2].lower() == "temporary": continue
                try:
                    val = r[col_idx] if len(r) > col_idx and r[col_idx] else "0"
                    counter = int(val)
                    valid_rows.append({"row_idx": i + 2, "data": r, "counter": counter})
                except ValueError: continue

        if not valid_rows:
            print("ℹ️ Немає доступних рядків для публікації.")
            return

        min_counter = min(item["counter"] for item in valid_rows)
        min_pool = [item for item in valid_rows if item["counter"] == min_counter]

        groups = {}
        for item in min_pool:
            data = item["data"]
            group_key = (data[2], data[6] if len(data) > 6 else "", data[7] if len(data) > 7 else "")
            groups.setdefault(group_key, []).append(item)

        first_key = list(groups.keys())[0]
        selected_group_items = groups[first_key][:4]
        category_name, target_date, target_loc = first_key
        print(f"📂 Обрано групу з Таблиці: {category_name}. Елементів: {len(selected_group_items)}")
        
        for item in selected_group_items:
            selected_queue.append({
                "id": item["data"][0],
                "name": item["data"][1],
                "local_path": None,
                "category": category_name,
                "date": target_date,
                "location": target_loc,
                "mode": "sheet",
                "counter_cell": f"'{current_tab}'!{col_letter}{item['row_idx']}",
                "counter_val": item["counter"]
            })

    if not selected_queue:
        print("ℹ️ Черга публікації порожня.")
        return

    # Налаштування мови
    target_lang_cell = "'⚙️ Налаштування Папок'!H2"
    lang_value = "UK"
    try:
        lang_res = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=target_lang_cell).execute()
        lang_values = lang_res.get('values', [])
        if lang_values and lang_values[0]:
            lang_value = lang_values[0][0].strip().upper()
    except Exception as e:
        print(f"⚠️ Не вдалося зчитати мову з комірки H2: {e}")

    if any(x in lang_value for x in ["EN", "ENG", "АНГЛ", "ENGLISH"]):
        lang_idx = 1
        next_lang_value = "DE"
    elif any(x in lang_value for x in ["DE", "GER", "НІМ", "DEUTSCH"]):
        lang_idx = 2
        next_lang_value = "UK"
    else:
        lang_idx = 0
        next_lang_value = "EN"
        
    print(f"🌐 Поточна мова Сторіс: {lang_value} (Індекс: {lang_idx}). Наступна буде: {next_lang_value}")
    
    local_files_to_clean = []
    success_published_any = False

    # 3️⃣ ЗАГАЛЬНИЙ ЦИКЛ ПУБЛІКАЦІЇ
    for idx_item, item in enumerate(selected_queue):
        f_id, f_name = item["id"], item["name"]
        lower_name = f_name.lower()
        
        if item["mode"] == "sheet":
            if not lower_name.endswith(VALID_MEDIA_EXTENSIONS):
                log_unsupported_to_service(sheets, item["category"], f_name, reason="непідтримуваний формат для сторіз")
                continue

            local_path = os.path.join('temp_mebli', f_name)
            print(f"\n📥 [{idx_item + 1}/{len(selected_queue)}] Завантаження з Drive: {f_name}...")
            try:
                request = drive.files().get_media(fileId=f_id)
                with open(local_path, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done: _, done = downloader.next_chunk()
            except Exception as e:
                print(f"❌ Не вдалося завантажити {f_name}: {e}")
                has_global_failures = True
                continue
        else:
            local_path = item["local_path"]
            print(f"\n🎬 [{idx_item + 1}/{len(selected_queue)}] Обробка вже завантаженого файлу з гарячої папки: {f_name}...")

        final_path = local_path
        is_video = lower_name.endswith(('.mp4', '.mov', '.avi'))
        
        if lower_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join('temp_mebli', f_name.rsplit('.', 1)[0] + '.jpg')
            try:
                with Image.open(local_path) as img:
                    img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
                final_path = jpg_path
                local_files_to_clean.append(jpg_path)
            except Exception as e:
                print(f"❌ Помилка конвертації HEIC для {f_name}: {e}")
                has_global_failures = True
                continue

        local_files_to_clean.append(local_path)

        # Створення прев'ю для AI, якщо це відео
        ai_media_snapshot = final_path
        if is_video:
            frame_path = os.path.join('temp_mebli', f"frame_{f_id}.jpg")
            subprocess.run(['ffmpeg', '-y', '-i', final_path, '-ss', '00:00:01', '-vframes', '1', frame_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(frame_path):
                ai_media_snapshot = frame_path
                local_files_to_clean.append(frame_path)

        story_caption_text = generate_story_caption([ai_media_snapshot], item["category"], item["date"], lang_idx, item["location"])
        print(f"💬 Текст для Сторіс: \"{story_caption_text}\"")

        # --- Інтеграція змінних для нанесення метаданих на фото/відео
        try:
            year_variable = item["date"].split(".")[2] if item["date"] and len(item["date"].split(".")) == 3 else str(datetime.now().year)
        except Exception:
            year_variable = str(datetime.now().year)
            
        location_variable = item["location"]

        media_parts_to_upload = []
        try:
            if is_video:
                media_parts_to_upload = optimize_video_story(final_path, f_name, story_caption_text, year=year_variable, location=location_variable)
            else:
                optimized_path = optimize_image_story(final_path, f_name)
                overlay_text_on_image(optimized_path, story_caption_text, year=year_variable, location=location_variable)
                media_parts_to_upload = [optimized_path]
        except Exception as e:
            print(f"❌ Помилка рендерингу/оптимізації медіа файлу {f_name}: {e}")
            has_global_failures = True
            continue

        item_published_successfully = False
        all_parts_successful = True  # 🆕 Прапорець для контролю ВСІХ фрагментів одного файлу

        # Послідовна публікація кожного фрагмента в Meta API
        for sub_idx, active_path in enumerate(media_parts_to_upload):
            if len(media_parts_to_upload) > 1:
                print(f"📦 Обробка фрагмента [{sub_idx + 1}/{len(media_parts_to_upload)}] для файлу {f_name}...")
                
            if active_path != final_path and active_path != local_path:
                local_files_to_clean.append(active_path)

            pub_url, ik_id = get_google_drive_direct_url(f_id, local_file_path=active_path)
            
            if not pub_url:
                print(f"⚠️ Не вдалося отримати публічне посилання для фрагмента {active_path}.")
                has_global_failures = True
                all_parts_successful = False  # 🆕 Фрагмент зафейлився
                continue

            print(f"📡 Надсилання сторіз в Meta API...")
            param_type = "video_url" if is_video else "image_url"
            payload = {
                "media_type": "STORIES",
                param_type: pub_url,
                "access_token": META_ACCESS_TOKEN
            }
            
            try:
                # 1. Створення контейнера
                res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media", data=payload).json()
                
                if res and "id" in res:
                    creation_id = res["id"]
                    # 2. Очікування готовності
                    is_ready = wait_for_meta_container(creation_id, META_ACCESS_TOKEN)
                    
                    if is_ready:
                        # 3. Фінальна публікація
                        publish_res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={
                            "creation_id": creation_id, "access_token": META_ACCESS_TOKEN
                        }).json()
                        
                        if "id" in publish_res:
                            print(f"✅ Фрагмент [{sub_idx + 1}/{len(media_parts_to_upload)}] успішно опубліковано! ID: {publish_res['id']}")
                            success_published_any = True
                        else:
                            print(f"❌ Помилка публікації сторіз в Meta API: {publish_res}")
                            has_global_failures = True
                            all_parts_successful = False  # 🆕 Помилка публікації
                    else:
                        print(f"❌ Контейнер медіафайлу не перейшов у стан готовності.")
                        has_global_failures = True
                        all_parts_successful = False  # 🆕 Таймаут контейнера
                else:
                    print(f"❌ Помилка створення контейнера сторіз: {res}")
                    has_global_failures = True
                    all_parts_successful = False  # 🆕 Помилка створення
            except Exception as e:
                print(f"❌ Критичний збій під час запиту до Meta API: {e}")
                has_global_failures = True
                all_parts_successful = False  # 🆕 Ексепшн мережі/API

            if ik_id: 
                delete_from_imagekit(ik_id)

        # 🆕 Визначаємо успіх файлу ТІЛЬКИ якщо всі його фрагменти завантажилися без збоїв
        if all_parts_successful and media_parts_to_upload:
            item_published_successfully = True
        else:
            print(f"⚠️ Файл [{f_name}] опубліковано не повністю або зі збоями. Він залишається в робочій черзі.")

        # Оновлюємо статус/лічильники лише у разі ПОВНОГО успіху
        if item_published_successfully:
            if item["mode"] == "sheet":
                new_val = item["counter_val"] + 1
                try:
                    sheets.spreadsheets().values().update(
                        spreadsheetId=SPREADSHEET_ID, range=item["counter_cell"],
                        valueInputOption='RAW', body={'values': [[new_val]]}
                    ).execute()
                    print(f"✍️ Лічильник у {item['counter_cell']} оновлено на {new_val}.")
                except Exception as e:
                    print(f"⚠️ Не вдалося зберегти лічильник: {e}")
            elif item["mode"] == "hot_folder":
                try:
                    file_meta = drive.files().get(fileId=f_id, fields='parents').execute()
                    previous_parents = ",".join(file_meta.get('parents', []))
                    drive.files().update(
                        fileId=f_id,
                        addParents=TRASH_FOLDER_ID,
                        removeParents=previous_parents,
                        fields='id, parents'
                    ).execute()
                    print(f"🗑️ Файл [{f_name}] успішно переміщено до кошика на Google Диску.")
                except Exception as e:
                    print(f"⚠️ Не вдалося перемістити файл {f_name} до кошика: {e}")

    if success_published_any:
        try:
            sheets.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID, range=target_lang_cell,
                valueInputOption='RAW', body={'values': [[next_lang_value]]}
            ).execute()
            print(f"\n🔄 Мову для наступного запуску Сторіс (комірка H2) змінено на: {next_lang_value}")
        except Exception as e:
            print(f"⚠️ Не вдалося оновити мову в комірці H2: {e}")

    for f in local_files_to_clean:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
    print("🧹 Тимчасові локальні файли успішно очищені.")

    # 🚨 Фінальний акорд для GitHub Actions
    if has_global_failures:
        print("\n💥 [Система] Скрипт виконав частину роботи, але зафіксовано помилки публікації контенту.")
        sys.exit(1)  # Завершуємо з помилкою — GitHub пофарбує запуск у червоний
    else:
        print("\n🚀 [Система] Зовнішній запуск API успішний! Всі обрані файли опубліковані без помилок.")

if __name__ == "__main__":
    main()
