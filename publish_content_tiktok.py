import os
import sys
import json
import io
import time
import subprocess
import re
import requests
import random  # <-- ВИПРАВЛЕНО: додано для random.choice в AI метаданих
from datetime import datetime
from zoneinfo import ZoneInfo  # Нативно в Python 3.9+ для точного часу в Німеччині
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Імпортуємо Pillow
from PIL import Image, ImageOps  # <-- ВИПРАВЛЕНО: додано ImageOps для Smart Crop
from PIL.ExifTags import TAGS, GPSTAGS
from pillow_heif import register_heif_opener

# Трюк для сумісності старого MoviePy з новим Pillow в Python 3.12
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

from moviepy.editor import VideoFileClip, ImageClip, concatenate_videoclips, AudioFileClip, TextClip, CompositeVideoClip
from moviepy.audio.AudioClip import AudioArrayClip
import numpy as np

# Реєстрація підтримки HEIC форматів для Pillow (iPhone фото)
register_heif_opener()

# --- НАЛАШТУВАННЯ ---
CLIENT_KEY = os.environ.get('CLIENT_KEY_TIKTOK')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET_TIKTOK')
TOKENS_FILE = 'tiktok_tokens.json'

FOLDER_INPUT_ID = '19wPAbTuyGGqMI4twWXfU5gfs-vk2Ru_G'
FOLDER_TRASH_ID = '1L3veD90e7Fr1acwlK7PmhSs_JrofyT6N'

TARGET_DURATION = 8  
TEST_FPS = 30        

MUSIC_FALLBACK_PATH = 'assets/trending_travel_music.mp3'

# Розширений список форматів, включаючи iPhone (.mov, .heic) та Android (.mp4, .webp, .jpg)
VALID_EXTENSIONS = (
    '.3gp', '.avi', '.gif', '.heic', '.heif', '.jpeg', '.jpg', 
    '.mkv', '.mov', '.mp4', '.mpeg', '.mpg', '.tif', '.tiff', '.webp', '.png'
)

# --- АВТОРИЗАЦІЯ GOOGLE DRIVE ---
def get_gdrive_service():
    try:
        key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
        creds = service_account.Credentials.from_service_account_info(key_dict, scopes=['https://www.googleapis.com/auth/drive'])
        return build('drive', 'v3', credentials=creds)
    except KeyError:
        sys.exit("❌ ПОМИЛКА: Секрет GDRIVE_SERVICE_ACCOUNT_KEY не знайдено в змінних оточення!")
    except json.JSONDecodeError:
        sys.exit("❌ ПОМИЛКА: Вміст GDRIVE_SERVICE_ACCOUNT_KEY не є коректним JSON!")
    except Exception as e:
        sys.exit(f"❌ ПОМИЛКА ініціалізації Google Drive: {e}")

# --- АВТОРИЗАЦІЯ TIKTOK ---
def get_valid_tiktok_token():
    try:
        with open(TOKENS_FILE, 'r') as f:
            tokens = json.load(f)
    except FileNotFoundError:
        sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Файл токенів tiktok_tokens.json не знайдено!")

    url = "https://open.tiktokapis.com/v2/oauth/token/"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": tokens['refresh_token']
    }
    
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        new_tokens = response.json()
        tokens.update(new_tokens) 
        with open(TOKENS_FILE, 'w') as f:
            json.dump(tokens, f, indent=4)
        return tokens['access_token']
    else:
        print(f"❌ Помилка оновлення токена TikTok: {response.text}")
        return tokens.get('access_token')

# --- ПІДРАХУНОК ВСІХ ФАЙЛІВ ДЛЯ КРОНУ ---
def count_total_files(service):
    total = 0
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{FOLDER_INPUT_ID}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageSize=1000,
                pageToken=page_token
            ).execute()
            files = results.get('files', [])
            for f in files:
                if f['id'] == FOLDER_TRASH_ID:
                    continue
                mime_type = f.get('mimeType', '')
                lower_name = f.get('name', '').lower()
                if mime_type.startswith(('image/', 'video/')) or lower_name.endswith(VALID_EXTENSIONS):
                    total += 1
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Помилка підрахунку файлів на Google Диску: {e}")
    return total

# --- РОЗУМНЕ ВИПРАВЛЕННЯ ГЕОМЕТРІЇ (БЕЗ СПОТВОРЕННЯ) ---
def fit_video_with_background(clip, target_w=1080, target_h=1920):
    """Пропорційно масштабує відео та додає чорні поля (letterbox/pillarbox) замість спотворення"""
    target_ar = target_w / target_h
    clip_ar = clip.w / clip.h
    
    if clip_ar > target_ar:
        # Альбомне (широке) відео -> підганяємо по ширині
        resized = clip.resize(width=target_w)
    else:
        # Портретне (вузьке/високе) відео -> підганяємо по висоті
        resized = clip.resize(height=target_h)
        
    # Накладаємо по центру на чорний екран заданого розміру
    return CompositeVideoClip([resized.set_position("center")], size=(target_w, target_h))

def prepare_padded_image(local_path, output_path, target_w=1080, target_h=1920):
    """Масштабує фото пропорційно з додаванням чорних полів та урахуванням EXIF-поворотів"""
    with Image.open(local_path) as img:
        # Авто-орієнтація на основі EXIF (захист від перевернутих мобільних фото)
        img = ImageOps.exif_transpose(img)
        
        img.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        background = Image.new('RGB', (target_w, target_h), (0, 0, 0))
        offset = ((target_w - img.width) // 2, (target_h - img.height) // 2)
        background.paste(img, offset)
        background.save(output_path, 'JPEG', quality=95)

# --- ПРІОРИТЕТ №1: ВИЗНАЧЕННЯ ДАТИ З НАЗВИ ФАЙЛУ ---
def extract_date_from_filename(filename):
    """Шукає дату в назві файлу (типові кодування камер та месенджерів)"""
    match = re.search(r'\b(20[0-2]\d)[-._]?(0[1-9]|1[0-2])[-._]?([0-2]\d|3[01])\b', filename)
    if match:
        year, month, day = match.groups()
        try:
            return datetime(int(year), int(month), int(day))
        except ValueError:
            pass
            
    match_ts = re.search(r'\b(1[4-7]\d{8,11})\b', filename)
    if match_ts:
        ts = int(match_ts.group(1))
        if len(match_ts.group(1)) > 10:  
            ts = ts / 1000
        try:
            return datetime.fromtimestamp(ts)
        except:
            pass
            
    return None

# --- ОБРОБКА МЕТАДАНИХ ТА ГЕОЛОКАЦІЇ ---
def get_exif_data(image_path):
    date_str, lat, lon = None, None, None
    try:
        with Image.open(image_path) as img:
            exif = img._getexif()
            if not exif:
                return date_str, lat, lon
            geotagging = {}
            for tag, value in exif.items():
                decoded = TAGS.get(tag, tag)
                if decoded == 'DateTimeOriginal':
                    date_str = value
                if decoded == 'GPSInfo':
                    for t in value:
                        sub_decoded = GPSTAGS.get(t, t)
                        geotagging[sub_decoded] = value[t]
            
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

def get_location_name(lat, lon):
    if lat is None or lon is None: return None
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10&accept-language=uk"
        headers = {'User-Agent': 'TikTokAutomation_Bot_2026'}
        res = requests.get(url, headers=headers, timeout=10).json()
        address = res.get('address', {})
        city = address.get('city') or address.get('town') or address.get('village') or address.get('county')
        country = address.get('country')
        return f"{city}, {country}" if city and country else country
    except Exception as e:
        print(f"⚠️ Помилка геокодування OSM: {e}")
    return None

# --- CONVERSION ---
def gif_to_mp4(input_path, output_path):
    print(f"🎞️ Конвертуємо GIF {input_path} в MP4 відео...")
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-movflags', 'faststart', '-pix_fmt', 'yuv420p',
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
        output_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg Error: {res.stderr}")

# --- ДИНАМІЧНА ГЕНЕРАЦІЯ ТЕКСТІВ ---
def generate_ai_metadata(date_str, location_geo):
    year = date_str.split('.')[-1] if '.' in date_str else "2026"
    
    generic_phrases = [
        "Магія природи, яка заліковує душу ✨",
        "Там, де час зупиняється... 🌿",
        "Краса в простих деталях ⛰️",
        "Естетика цього світу зашкалює 😍",
        "Моменти, які залишаються назавжди"
    ]
    
    location_phrases = [
        f"Місце, куди хочеться повертатися: {location_geo} ✨",
        f"Атмосфера цього дня: {location_geo} 🌍",
        f"Відкриваючи неймовірні куточки: {location_geo} 🌿",
        f"Подорож, що надихає | {location_geo} 🧭",
        f"Спогади з серця: {location_geo}"
    ]
    
    if location_geo and location_geo != "Невідоме місце":
        trending_text = random.choice(location_phrases)
        location = location_geo
    else:
        trending_text = random.choice(generic_phrases)
        location = "Магія природи"
        
    return trending_text, year, location

# --- КОМПІЛЯЦІЯ ФІНАЛЬНОГО ВІДЕО ---
def compile_final_video(clips, text_info):
    trending_text, year, location = text_info
    try:
        final_video = concatenate_videoclips(clips, method="compose")
        
        if final_video.audio is None:
            if os.path.exists(MUSIC_FALLBACK_PATH):
                bg_music = AudioFileClip(MUSIC_FALLBACK_PATH).set_duration(final_video.duration)
                final_video = final_video.set_audio(bg_music)
            else:
                silence_array = np.zeros((int(44100 * final_video.duration), 2))
                silent_audio = AudioArrayClip(silence_array, fps=44100).set_duration(final_video.duration)
                final_video = final_video.set_audio(silent_audio)
            
        main_txt = TextClip(trending_text, fontsize=48, color='white', font='Arial-Bold', method='caption', size=(950, None)).set_position(('center', 400)).set_duration(final_video.duration)
        meta_txt = TextClip(f"{location} | {year}", fontsize=38, color='yellow', font='Arial').set_position(('center', 1500)).set_duration(final_video.duration)
        
        result_video = CompositeVideoClip([final_video, main_txt, meta_txt])
        output_name = f"ready_tiktok_{int(time.time())}.mp4"
        
        result_video.write_videofile(
            output_name, 
            fps=TEST_FPS, 
            codec="libx264", 
            audio_codec="aac",
            bitrate="2500k",
            ffmpeg_params=["-pix_fmt", "yuv420p"]
        )
        
        result_video.close()
        final_video.close()
        return output_name
    except Exception as e:
        sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Помилка рендерингу відео: {e}")

# --- ПОВНИЙ ІНТЕЛЕКТУАЛЬНИЙ БЛОК ВИЗНАЧЕННЯ ДАТИ ---
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

# --- ПУБЛІКАЦІЯ У TIKTOK ---
def upload_to_tiktok(video_path, description):
    access_token = get_valid_tiktok_token()
    if not access_token:
        print("Публікація скасована через відсутність дійсного токена.")
        return False

    video_size = os.path.getsize(video_path)
    MAX_SINGLE_SIZE = 64 * 1024 * 1024       
    DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024    

    if video_size <= MAX_SINGLE_SIZE:
        chunk_size = video_size
        total_chunk_count = 1
    else:
        chunk_size = DEFAULT_CHUNK_SIZE
        total_chunk_count = video_size // chunk_size

    init_url = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8"
    }
    
    body = {
        "post_info": {
            "title": description if description else "Мій тест #travel #vlog",
            "privacy_level": "SELF_ONLY",  
            "disable_duet": True,
            "disable_comment": True,
            "disable_stitch": True,
            "video_cover_timestamp_ms": 1000
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunk_count
        }
    }
    
    print(f"Надсилання запиту на ініціалізацію в TikTok (Розмір файлу: {video_size} байт)...")
    init_res = requests.post(init_url, headers=headers, json=body)
    
    if init_res.status_code != 200:
        print(f"❌ Помилка ініціалізації чернетки: {init_res.status_code} - {init_res.text}")
        return False
        
    res_data = init_res.json()
    if 'data' not in res_data or 'upload_url' not in res_data['data']:
        print(f"❌ Помилка API TikTok: {res_data.get('error')}")
        return False

    publish_id = res_data['data'].get('publish_id')
    upload_url = res_data['data']['upload_url']
    
    print(f"✅ Успішна ініціалізація TikTok! ID: {publish_id}")
    print(f"Починаємо передачу файлу частинами (Всього чанків: {total_chunk_count})...")

    with open(video_path, 'rb') as video_file:
        for i in range(total_chunk_count):
            first_byte = i * chunk_size
            if i == total_chunk_count - 1:
                last_byte = video_size - 1
            else:
                last_byte = (i + 1) * chunk_size - 1
            
            byte_size_of_this_chunk = last_byte - first_byte + 1
            video_file.seek(first_byte)
            chunk_data = video_file.read(byte_size_of_this_chunk)
            
            put_headers = {
                "Content-Type": "video/mp4",
                "Content-Length": str(byte_size_of_this_chunk),
                "Content-Range": f"bytes {first_byte}-{last_byte}/{video_size}"
            }
            
            print(f"📤 Надсилання чанку {i+1}/{total_chunk_count} (байти {first_byte}-{last_byte})...")
            upload_res = requests.put(upload_url, headers=put_headers, data=chunk_data)
            
            expected_status = 201 if i == total_chunk_count - 1 else 206
            if upload_res.status_code != expected_status:
                print(f"❌ Помилка завантаження чанку {i+1}: Отримано статус {upload_res.status_code}, очікувався {expected_status}.")
                return False

    print("🚀 Відео успішно передано на сервери TikTok!")
    return True

# --- ОЧИЩЕННЯ ТА АРХІВАЦІЯ GOOGLE DRIVE ---
def move_files_to_trash(service, file_list):
    for f in file_list:
        try:
            service.files().update(
                fileId=f['id'],
                addParents=FOLDER_TRASH_ID,
                removeParents=FOLDER_INPUT_ID,
                fields='id, parents'
            ).execute()
            print(f"📦 Переміщено в архів: {f['name']}")
        except Exception as e:
            sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося перемістити файл {f['name']} в архів на Диску: {e}")

# --- ГОЛОВНА ЛОГІКА ---
def main():
    run_mode = os.environ.get('RUN_MODE', 'manual')
    print(f"⚙️ Запуск у режимі: {run_mode.upper()}")
    
    service = get_gdrive_service()
    
    if run_mode == 'cron':
        print("🔍 Підраховуємо загальну кількість файлів у папці...")
        total_files = count_total_files(service)
        
        berlin_hour = datetime.now(ZoneInfo("Europe/Berlin")).hour
        print(f"📊 На Диску знайдено файлів: {total_files} | Поточна година в DE: {berlin_hour}")
        
        allowed_hours = []
        if total_files <= 1000:
            allowed_hours = [11]
        elif total_files <= 2000:
            allowed_hours = [11, 17]
        elif total_files <= 3000:
            allowed_hours = [5, 11, 17]
        else:
            allowed_hours = [5, 11, 17, 23]
            
        if berlin_hour not in allowed_hours:
            print(f"☕ [ШТАТНИЙ ПРОПУСК] Для {total_files} файлів година {berlin_hour} не передбачена графіком.")
            sys.exit(0)
            
        print("✅ Успішно! Умови графіку виконано. Переходимо до відбору та обробки медіа.")

    try:
        results = service.files().list(
            q=f"'{FOLDER_INPUT_ID}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size)",
            orderBy="createdTime",
            pageSize=50
        ).execute()
    except Exception as e:
        sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Помилка отримання файлів з Google Диску: {e}")
        
    gdrive_files = results.get('files', [])
    gdrive_files = [f for f in gdrive_files if f['id'] != FOLDER_TRASH_ID]
    
    if not gdrive_files:
        print("☕ [ШТАТНИЙ ПРОПУСК] Папка вхідних медіа порожня.")
        sys.exit(0)

    print(f"Знайдено файлів для поточної збірки: {len(gdrive_files)}")
    processed_items = []
    os.makedirs('downloaded', exist_ok=True)
    
    for f in gdrive_files:
        mime_type = f.get('mimeType', '')
        lower_name = f['name'].lower()
        
        is_valid_media = mime_type.startswith(('image/', 'video/')) or lower_name.endswith(VALID_EXTENSIONS)
        if not is_valid_media:
            continue
            
        local_path = os.path.join('downloaded', f['name'])
        print(f"Завантаження {f['name']}...")
        
        try:
            request = service.files().get_media(fileId=f['id'])
            fh = io.FileIO(local_path, 'wb')
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            fh.close()
        except Exception as e:
            sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося завантажити файл '{f['name']}' з Google Диску. Причина: {e}")

        now = datetime.now()
        final_dt, lat, lon = get_intellectual_date(local_path, f['name'], f, now)
        file_date = final_dt.strftime('%d.%m.%Y')
        
        location = "Невідоме місце"
        if lat and lon:
            time.sleep(1)  
            location = get_location_name(lat, lon) or "Невідоме місце"
            
        # Конвертація GIF
        if mime_type == 'image/gif' or lower_name.endswith('.gif'):
            mp4_path = os.path.join('downloaded', f['name'].rsplit('.', 1)[0] + '_gif.mp4')
            try:
                gif_to_mp4(local_path, mp4_path)
            except Exception as e:
                sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося конвертувати GIF у MP4. Деталі: {e}")
            
            if os.path.exists(local_path): os.remove(local_path)
            local_path = mp4_path
            mime_type = 'video/mp4'
                
        # Конвертація HEIC / HEIF (Apple)
        elif mime_type in ['image/heic', 'image/heif'] or lower_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join('downloaded', f['name'].rsplit('.', 1)[0] + '.jpg')
            try:
                with Image.open(local_path) as img:
                    img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
            except Exception as e:
                sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося розкодувати iPhone-формат HEIC/HEIF. Деталі: {e}")
            
            if os.path.exists(local_path): os.remove(local_path)
            local_path = jpg_path
            mime_type = 'image/jpeg'

        processed_items.append({
            'id': f['id'],
            'name': f['name'],
            'mime': mime_type,
            'local_path': local_path,
            'date': file_date,
            'location': location
        })

    # --- ГРУПУВАННЯ ТА МОНТАЖ (КОРЕКЦІЯ ТАЙМІНГІВ ТА ГЕОМЕТРІЇ) ---
    groups = {}
    for item in processed_items:
        key = (item['date'], item['location'])
        if key not in groups:
            groups[key] = []
        groups[key].append(item)
        
    MIN_DURATION = 20
    MAX_DURATION = 40
    PHOTO_DURATION = 3.0  # Оптимально для перегляду одного фото в TikTok

    for (date, loc), items in groups.items():
        print(f"🎬 Знайдено групу для монтажу: Дата {date} | Локація: {loc}. Всього файлів у групі: {len(items)}")
        
        # Визначаємо тривалість оригінальних файлів у групі
        valid_items = []
        for item in items:
            mime = item['mime']
            local_path = item['local_path']
            if 'video' in mime:
                try:
                    with VideoFileClip(local_path) as clip:
                        item['duration'] = clip.duration
                    valid_items.append(item)
                except Exception as e:
                    print(f"⚠️ Відео '{item['name']}' пошкоджене: {e}. Пропускаємо.")
            elif 'image' in mime:
                item['duration'] = PHOTO_DURATION
                valid_items.append(item)

        if not valid_items:
            print("☕ Немає валідних медіафайлів у цій групі.")
            continue

        # --- КЕЙС 1: ОДИН ДОВГИЙ ФАЙЛ (> 40 секунд) -> Серійна нарізка ---
        if len(valid_items) == 1 and valid_items[0]['duration'] > MAX_DURATION:
            single_item = valid_items[0]
            total_dur = single_item['duration']
            print(f"✂️ Виявлено один довгий файл ({total_dur:.1f} сек). Ріжемо на частини та публікуємо послідовно...")
            
            start = 0
            part_num = 1
            all_parts_success = True
            generated_files = []
            chunk_length = 35.0  # Зручний розмір кроку, щоб красиво вкладатися в ліміти
            
            while start < total_dur:
                end = min(start + chunk_length, total_dur)
                part_duration = end - start
                print(f"📦 Обробка частини {part_num} ({start:.1f}s - {end:.1f}s, тривалість: {part_duration:.1f}s)")
                
                try:
                    with VideoFileClip(single_item['local_path']) as full_video:
                        trimmed = full_video.subclip(start, end)
                        
                        # Якщо фінальний залишковий шматочок менший за 20 секунд — зациклюємо його
                        if part_duration < MIN_DURATION:
                            print(f"🔄 Фінальна частина закоротка ({part_duration:.1f}s). Зациклюємо для виконання ліміту...")
                            loops = int(np.ceil(MIN_DURATION / part_duration))
                            trimmed = concatenate_videoclips([trimmed] * loops)
                        
                        smart_video = fit_video_with_background(trimmed, 1080, 1920)
                        
                        text_info = generate_ai_metadata(date, loc)
                        trending_text, year, location = text_info
                        hash_tag = location.split(',')[0].strip().replace(" ", "")
                        tiktok_description = f"{trending_text} (Частина {part_num}) 🌍 #travel #{hash_tag}"
                        
                        final_file = compile_final_video([smart_video], text_info)
                        generated_files.append(final_file)
                        
                        upload_success = upload_to_tiktok(final_file, tiktok_description)
                        if not upload_success:
                            print(f"❌ Помилка завантаження частини {part_num}.")
                            all_parts_success = False
                            break
                except Exception as e:
                    print(f"❌ Помилка обробки довгого відео на частині {part_num}: {e}")
                    all_parts_success = False
                    break
                
                start = end
                part_num += 1
                
            if all_parts_success:
                move_files_to_trash(service, [single_item])
                for gf in generated_files:
                    if os.path.exists(gf): os.remove(gf)
                if os.path.exists(single_item['local_path']): os.remove(single_item['local_path'])
                print("🏁 Послідовну публікацію великого файлу завершено успішно.")
            else:
                sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Публікація однієї з частин довгого ролика зазнала невдачі.")
            
            return  # Скрипт обробляє лише одну групу за один запуск крону

        # --- КЕЙС 2 ТА 3: ЗВИЧАЙНА ГРУПА (ОБМЕЖЕННЯ ДО 40 СЕК / ЗАЦИКЛЕННЯ МЕНШЕ 20 СЕК) ---
        selected_items = []
        accumulated_duration = 0
        
        for item in valid_items:
            if accumulated_duration + item['duration'] <= MAX_DURATION:
                selected_items.append(item)
                accumulated_duration += item['duration']
            else:
                if accumulated_duration >= MIN_DURATION:
                    break
                else:
                    # Якщо ліміт в 20 сек ще не набрано, але поточний файл перестрибує ліміт 40 сек, обрізаємо частину відео
                    remaining_space = MAX_DURATION - accumulated_duration
                    if remaining_space >= 4.0 and 'video' in item['mime']:
                        item['crop_to_duration'] = remaining_space
                        item['duration'] = remaining_space
                        selected_items.append(item)
                        accumulated_duration += remaining_space
                    break

        print(f"📐 Розумний відбір: {len(selected_items)} файлів. Чиста тривалість збірки: {accumulated_duration:.1f} сек.")

        # Якщо сумарний час відібраних оригіналів менший за 20 секунд — циклічно розмножуємо сам перелік файлів
        final_items_to_render = list(selected_items)
        if accumulated_duration < MIN_DURATION:
            print(f"🔄 Загальний час {accumulated_duration:.1f}s менший за {MIN_DURATION}s. Зациклюємо файли...")
            while accumulated_duration < MIN_DURATION:
                for item in selected_items:
                    final_items_to_render.append(item)
                    accumulated_duration += item['duration']
                    if accumulated_duration >= MIN_DURATION:
                        break

        # Створення медіакліпів з правильними пропорціями та полями
        clips = []
        temp_images_to_clean = []
        
        for item in final_items_to_render:
            local_path = item['local_path']
            mime = item['mime']
            
            if 'video' in mime:
                try:
                    full_video = VideoFileClip(local_path)
                    dur = item.get('crop_to_duration', full_video.duration)
                    start_time = max(0, full_video.duration / 2 - dur / 2)
                    end_time = min(full_video.duration, start_time + dur)
                    
                    trimmed = full_video.subclip(start_time, end_time)
                    smart_video = fit_video_with_background(trimmed, 1080, 1920)
                    clips.append(smart_video)
                except Exception as e:
                    print(f"⚠️ Пропуск кліпу відео через помилку рендеру: {e}")
            elif 'image' in mime:
                try:
                    # Генеруємо унікальний тимчасовий файл для зображення з чорними полями
                    temp_img_path = os.path.join('downloaded', f"padded_{int(time.time())}_{os.path.basename(local_path)}")
                    prepare_padded_image(local_path, temp_img_path, 1080, 1920)
                    temp_images_to_clean.append(temp_img_path)
                    
                    img_clip = ImageClip(temp_img_path).set_duration(PHOTO_DURATION)
                    clips.append(img_clip)
                except Exception as e:
                    print(f"⚠️ Не вдалося пропорційно обробити фото {local_path}: {e}")

        if not clips:
            sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося зібрати жодного кліпу для фінального монтажу.")

        # Генерація метаданих, компіляція та вивантаження
        text_info = generate_ai_metadata(date, loc)
        trending_text, year, location = text_info
        hash_tag = location.split(',')[0].strip().replace(" ", "")
        tiktok_description = f"{trending_text} 🌍 #travel #{hash_tag}"
        
        final_file = compile_final_video(clips, text_info)
        print(f"🎉 Фінальне відео зібрано: {final_file}")
        
        upload_success = upload_to_tiktok(final_file, tiktok_description)
        
        if upload_success:
            # Переміщуємо в архів ТІЛЬКИ ті файли, які реально увійшли в цю збірку (selected_items). 
            # Залишок залишиться для наступних кронів.
            move_files_to_trash(service, selected_items)
            
            # Повне очищення локального кешу
            if os.path.exists(final_file): os.remove(final_file)
            for img_p in temp_images_to_clean:
                if os.path.exists(img_p): os.remove(img_p)
            for item in selected_items:
                if os.path.exists(item['local_path']): os.remove(item['local_path'])
            print("🏁 Публікацію поточної групи успішно завершено.")
        else:
            for img_p in temp_images_to_clean:
                if os.path.exists(img_p): os.remove(img_p)
            sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Публікація в TikTok зазнала невдачі. Файли збережено на Диску для повтору.")
            
        return  # Обробили першу групу, виходимо

if __name__ == '__main__':
    main()
