import re
import json
import subprocess
import requests
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

def extract_date_from_filename(filename):
    match = re.search(r'\b(20[0-2]\d)[-._]?(0[1-9]|1[0-2])[-._]?([0-2]\d|3[01])\b', filename)
    if match:
        year, month, day = match.groups()
        try: return datetime(int(year), int(month), int(day))
        except ValueError: pass
            
    match_ts = re.search(r'\b(1[4-7]\d{8,11})\b', filename)
    if match_ts:
        ts = int(match_ts.group(1))
        if len(match_ts.group(1)) > 10: ts = ts / 1000
        try: return datetime.fromtimestamp(ts)
        except: pass
    return None

def get_exif_data(image_path):
    date_str, lat, lon = None, None, None
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return date_str, lat, lon
            
            # РІШЕННЯ ДАТИ: Шукаємо DateTimeOriginal у правильному суб-блоці EXIF (ID: 34665)
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
                except: pass
            
            loc_str = tags.get('location') or tags.get('location-eng')
            if loc_str:
                match = re.match(r'([+-]\d+\.\d+)([+-]\d+\.\d+)', loc_str)
                if match:
                    lat, lon = float(match.group(1)), float(match.group(2))
    except Exception as e:
        print(f"⚠️ Попередження відео-метаданих для {video_path}: {e}")
    return date_str, lat, lon

def get_location_data(lat, lon):
    """Повертає кортеж: (КрасиваНазваДляВідео, НазваМістаДляГрупування)"""
    if lat is None or lon is None: 
        return "Невідоме місце", "Невідоме місце"
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=15"
        headers = {'User-Agent': 'TikTokAutomation_Bot_2026'}
        res = requests.get(url, headers=headers, timeout=10).json()
        address = res.get('address', {})
        
        # 1. Красиве точне місце для написів та ШІ
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
        
        # 2. Стабільне місто/село для об'єднання у групи
        group_place = address.get('city') or address.get('town') or address.get('village') or address.get('county')
        group_location = f"{group_place}, {country}" if group_place and country else country
        
        return display_location, group_location
    except Exception as e:
        print(f"⚠️ Помилка геокодування OSM: {e}")
    return "", ""

def get_intellectual_date(local_path, filename, gdrive_file, now_time):
    fn_date = extract_date_from_filename(filename)
    meta_date, lat, lon = None, None, None
    mime_type = gdrive_file['mimeType']
    lower_name = filename.lower()
    
    if mime_type.startswith('image/') or lower_name.endswith(('.heic', '.heif', '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff')):
        meta_date, lat, lon = get_exif_data(local_path)
    elif mime_type.startswith('video/') or lower_name.endswith(('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.mpeg', '.mpg')):
        meta_date, lat, lon = get_video_metadata(local_path)

    if fn_date:
        print(f"🎯 Дату успішно розпізнано з назви файлу '{filename}': {fn_date.strftime('%d.%m.%Y')}")
        return fn_date, lat, lon

    if meta_date:
        try:
            dt_parsed = datetime.strptime(meta_date, '%Y:%m:%d %H:%M:%S')
            if 2000 <= dt_parsed.year <= now_time.year:
                return dt_parsed, lat, lon
        except:
            pass

    try:
        dt_created = datetime.strptime(gdrive_file['createdTime'][:19], '%Y-%m-%dT%H:%M:%S')
        dt_modified = datetime.strptime(gdrive_file['modifiedTime'][:19], '%Y-%m-%dT%H:%M:%S')
        earliest_gdrive = min(dt_created, dt_modified)
        if 2010 <= earliest_gdrive.year <= now_time.year:
            return earliest_gdrive, lat, lon
    except:
        pass

    return now_time, lat, lon
