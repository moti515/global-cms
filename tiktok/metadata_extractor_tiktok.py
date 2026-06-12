import re
import json
import subprocess
import requests
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

# =====================================================================
# 🧠 ІНТЕЛЕКТУАЛЬНИЙ БЛОК АНАЛІЗУ МЕТАДАНИХ ТА ГЕОЛОКАЦІЇ (TIKTOK ENGINE)
# =====================================================================

def extract_date_from_filename(filename):
    """
    Каскадний пошук дати в імені файлу.
    Підтримує: YYYY-MM-DD, YYYYMMDD, DD-MM-YYYY, DDMMYYYY з будь-якими роздільниками.
    Вилучено блок Unix Timestamp для уникнення хибних спрацьовувань на ID файлів.
    """
    current_year = datetime.now().year
    min_year = 2000  # Нижній ліміт для цифрового архіву меблів
    
    name_part = filename.rsplit('.', 1)[0]

    # 1️⃣ Формат РРРР-ММ-ДД або РРРРММДД (наприклад: "20020315", "2026_06_12")
    match_yyyy_mm_dd = re.search(r'\b(\d{4})[-._]?(0[1-9]|1[0-2])[-._]?([0-2]\d|3[01])', name_part)
    if match_yyyy_mm_dd:
        year, month, day = match_yyyy_mm_dd.groups()
        try: 
            dt = datetime(int(year), int(month), int(day))
            if min_year <= dt.year <= current_year:
                return dt
        except ValueError: 
            pass

    # 2️⃣ Формат ДД-ММ-РРРР або ДДММРРРР (наприклад: "15_03_2002", "12062026")
    match_dd_mm_yyyy = re.search(r'\b(0[1-9]|[12]\d|3[01])[-._]?(0[1-9]|1[0-2])[-._]?(\d{4})', name_part)
    if match_dd_mm_yyyy:
        day, month, year = match_dd_mm_yyyy.groups()
        try: 
            dt = datetime(int(year), int(month), int(day))
            if min_year <= dt.year <= current_year:
                return dt
        except ValueError: 
            pass
            
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
    Повертає локалізацію строго українською мовою.
    Результат: кортеж (КрасиваНазваДляВідео, НазваМістаДляГрупування)
    """
    if lat is None or lon is None: 
        return "", ""
    try:
        # accept-language=uk гарантує повернення назв українською для генерації опису ШІ
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=15"
        headers = {'User-Agent': 'FurnitureStories_MetadataBot_2026'}
        res = requests.get(url, headers=headers, timeout=10).json()
        address = res.get('address', {})
        
        # 1. Точне місце (виробництво, майстерня, шоурум, локальний об'єкт)
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
    
    # 2️⃣ Збір метаданих залежно від типу контенту (EXIF / FFmpeg)
    if mime_type.startswith('image/') or lower_name.endswith(('.heic', '.heif', '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff')):
        meta_date, lat, lon = get_exif_data(local_path)
    elif mime_type.startswith('video/') or lower_name.endswith(('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.mpeg', '.mpg')):
        meta_date, lat, lon = get_video_metadata(local_path)

    # Якщо дату вже успішно взяли з імені файлу, повертаємо її (координати додаються з EXIF, якщо вони є)
    if fn_date:
        return fn_date, lat, lon

    # 3️⃣ Якщо в імені дати не було, валідуємо дату з EXIF метаданих самого файлу (мультиформатний парсинг)
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
        print(f"⚠️ Метадані файлу містять нелогічну дату: {meta_date}. Шукаємо заміну в системі Google Drive.")

    # 4️⃣ Фолбек: Дані про створення/модифікацію об'єкта в хмарі Google Drive
    try:
        dt_created = datetime.strptime(gdrive_file['createdTime'][:19], '%Y-%m-%dT%H:%M:%S')
        dt_modified = datetime.strptime(gdrive_file['modifiedTime'][:19], '%Y-%m-%dT%H:%M:%S')
        
        # Беремо найранішу дату. Для старих фото modifiedTime на Диску часто зберігає 
        # оригінальну дату зміни файлу на комп'ютері ще до завантаження в хмару (наприклад, 2002 рік)
        earliest_gdrive = min(dt_created, dt_modified)
        
        if min_year <= earliest_gdrive.year <= now_time.year:
            return earliest_gdrive, lat, lon
    except Exception as e:
        print(f"⚠️ Помилка зчитування системних дат Google Drive: {e}")

    # 5️⃣ Крайній випадок: якщо взагалі нічого не знайшли, повертаємо поточний час
    return now_time, lat, lon
