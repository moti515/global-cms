import os
import sys
import json
import io
import time
import subprocess
import re
import requests
from datetime import datetime
from zoneinfo import ZoneInfo  # Нативно в Python 3.9+ для точного часу в Німеччині
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Імпортуємо Pillow
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from pillow_heif import register_heif_opener

# Трюк (Monkey Patch) для сумісності старого MoviePy з новим Pillow в Python 3.12
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

# --- РОБОТА З МЕТАДАНИМИ ТА ГЕОКОДУВАННЯМ ---
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
                    d = float(value[0])
                    m = float(value[1])
                    s = float(value[2])
                    return d + (m / 60.0) + (s / 3600.0)
                
                lat = _to_degrees(geotagging['GPSLatitude'])
                lon = _to_degrees(geotagging['GPSLongitude'])
                if geotagging.get('GPSLatitudeRef') == 'S': lat = -lat
                if geotagging.get('GPSLongitudeRef') == 'W': lon = -lon
    except Exception as e:
        print(f"⚠️ Попередження EXIF (не критично): Не вдалося прочитати метадані для {image_path}: {e}")
    return date_str, lat, lon

def get_video_metadata(video_path):
    date_str, lat, lon = None, None, None
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', video_path
        ]
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
                    lat = float(match.group(1))
                    lon = float(match.group(2))
    except Exception as e:
        print(f"⚠️ Попередження відео-метаданих (не критично) для {video_path}: {e}")
    return date_str, lat, lon

def get_location_name(lat, lon):
    if lat is None or lon is None:
        return None
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10&accept-language=uk"
        headers = {'User-Agent': 'TikTokAutomation_Bot_2026'}
        res = requests.get(url, headers=headers, timeout=10).json()
        address = res.get('address', {})
        city = address.get('city') or address.get('town') or address.get('village') or address.get('county')
        country = address.get('country')
        if city and country:
            return f"{city}, {country}"
        elif country:
            return country
    except Exception as e:
        print(f"⚠️ Попередження геокодування OSM: {e}")
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

# --- ОБРОБКА ТА МОНТАЖ (СТРОГИЙ КОНТРОЛЬ ПОМИЛОК) ---
def process_media_group(file_list):
    clips = []
    clip_duration = max(4.0, TARGET_DURATION / len(file_list)) 
    
    for item in file_list:
        local_path = item['local_path']
        mime = item['mime']
        file_name = item['name']
        
        if 'video' in mime:
            try:
                full_video = VideoFileClip(local_path)
                start_time = max(0, full_video.duration / 2 - clip_duration / 2)
                end_time = min(full_video.duration, start_time + clip_duration)
                
                trimmed = full_video.subclip(start_time, end_time).resize(newsize=(1080, 1920))
                clips.append(trimmed)
            except Exception as e:
                # 🚨 АВАРІЯ: Відеофайл пошкоджений або має биті кодеки
                sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Файл відео '{file_name}' пошкоджений, має непідтримувану структуру або не може бути відкритий MoviePy. Деталі: {e}")
                
        elif 'image' in mime:
            try:
                img_clip = ImageClip(local_path).set_duration(clip_duration).resize(newsize=(1080, 1920))
                clips.append(img_clip)
            except Exception as e:
                # 🚨 АВАРІЯ: Зображення бите чи не розпізнається Pillow
                sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Зображення '{file_name}' пошкоджене або має непідтримуваний формат для рендерингу. Деталі: {e}")
                
    return clips

def generate_ai_metadata(date_str, location_geo):
    year = date_str.split('.')[-1] if '.' in date_str else "2026"
    location = location_geo if location_geo != "Невідоме місце" else "Магія природи"
    trending_text = "Місце, куди хочеться повертатися ✨"
    return trending_text, year, location

def compile_final_video(clips, text_info):
    trending_text, year, location = text_info
    
    try:
        final_video = concatenate_videoclips(clips, method="compose")
        
        if final_video.audio is None:
            if os.path.exists(MUSIC_FALLBACK_PATH):
                print("🎵 Додаємо фонову музику...")
                bg_music = AudioFileClip(MUSIC_FALLBACK_PATH).set_duration(final_video.duration)
                final_video = final_video.set_audio(bg_music)
            else:
                print("⚠️ Музику не знайдено. Генеруємо обов'язковий трек тиші для TikTok...")
                silence_array = np.zeros((int(44100 * final_video.duration), 2))
                silent_audio = AudioArrayClip(silence_array, fps=44100).set_duration(final_video.duration)
                final_video = final_video.set_audio(silent_audio)
            
        main_txt = TextClip(trending_text, fontsize=50, color='white', font='Arial-Bold', method='caption', size=(900, None)).set_position(('center', 400)).set_duration(final_video.duration)
        meta_txt = TextClip(f"{location} | {year}", fontsize=40, color='yellow', font='Arial').set_position(('center', 1500)).set_duration(final_video.duration)
        
        result_video = CompositeVideoClip([final_video, main_txt, meta_txt])
        output_name = f"ready_tiktok_{year}.mp4"
        
        result_video.write_videofile(
            output_name, 
            fps=TEST_FPS, 
            codec="libx264", 
            audio_codec="aac",
            bitrate="2000k",
            ffmpeg_params=["-pix_fmt", "yuv420p", "-profile:v", "baseline", "-level", "3.0"]
        )
        
        result_video.close()
        final_video.close()
        for c in clips:
            c.close()
            
        return output_name
    except Exception as e:
        sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Помилка генерації або рендерингу фінального відео файлу. Деталі: {e}")

# --- ПУБЛІКАЦІЯ У TIKTOK ---
def upload_to_tiktok(video_path, description):
    access_token = get_valid_tiktok_token()
    if not access_token:
        print("Публікація скасована через відсутність дійсного токена.")
        return False

    video_size = os.path.getsize(video_path)
    chunk_size = video_size  
    total_chunk_count = 1
    
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
    
    print("Надсилання запиту на ініціалізацію в TikTok (Inbox/Draft)...")
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
    print("Починаємо бінарне завантаження файлу...")

    with open(video_path, 'rb') as video_file:
        put_headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(video_size),
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}"
        }
        upload_res = requests.put(upload_url, headers=put_headers, data=video_file)

    if upload_res.status_code in [200, 201, 204]:
        print("🚀 Відео успішно передано на сервери TikTok!")
        print("Очікуємо обробку файлу сервером (15 секунд)...")
        time.sleep(15)
        
        status_url = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
        status_res = requests.post(status_url, headers=headers, json={"publish_id": publish_id})
        
        if status_res.status_code == 200:
            status_data = status_res.json()
            current_status = status_data.get('data', {}).get('status', 'UNKNOWN')
            fail_reason = status_data.get('data', {}).get('fail_reason', '')
            print(f"📊 Поточний статус відео в TikTok: {current_status}")
            if current_status == "FAILED":
                print(f"❌ Помилка обробки сервером: {fail_reason}")
                return False
        return True
    else:
        print(f"❌ Помилка завантаження файлу: {upload_res.status_code} - {upload_res.text}")
        return False

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
            print(f"☕ [ШТАТНИЙ ПРОПУСК] Для {total_files} файлів година {berlin_hour} не передбачена графіком. Дозволені години: {allowed_hours}.")
            print("🏁 Завершуємо роботу у штатному режимі (Успішно).")
            sys.exit(0)
            
        print("✅ Успішно! Умови графіку виконано. Переходимо до відбору та обробки медіа.")

    # Отримуємо перші 50 файлів для поточної ітерації монтажу
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
        print("☕ [ШТАТНИЙ ПРОПУСК] Папка вхідних медіа порожня. Немає контенту для монтажу.")
        print("🏁 Завершуємо роботу у штатному режимі (Успішно).")
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
            # 🚨 АВАРІЯ: Помилка завантаження з Диску (файл заблоковано, мережевий збій тощо)
            sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося завантажити файл '{f['name']}' з Google Диску. Причина: {e}")

        meta_date, lat, lon = None, None, None
        if mime_type.startswith('image/') or lower_name.endswith(('.heic', '.heif', '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff')):
            meta_date, lat, lon = get_exif_data(local_path)
        elif mime_type.startswith('video/') or lower_name.endswith(('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.mpeg', '.mpg')):
            meta_date, lat, lon = get_video_metadata(local_path)

        final_dt = None
        now = datetime.now()
        if meta_date:
            try:
                dt_parsed = datetime.strptime(meta_date, '%Y:%m:%d %H:%M:%S')
                if dt_parsed.year >= 2000 and dt_parsed <= now:
                    final_dt = dt_parsed
            except:
                pass
                
        if not final_dt:
            try:
                dt_created = datetime.strptime(f['createdTime'][:19], '%Y-%m-%dT%H:%M:%S')
                dt_modified = datetime.strptime(f['modifiedTime'][:19], '%Y-%m-%dT%H:%M:%S')
                earliest_gdrive = min(dt_created, dt_modified)
                if earliest_gdrive.year >= 2010 and earliest_gdrive <= now:
                    final_dt = earliest_gdrive
            except:
                pass

        if not final_dt:
            final_dt = now
            
        file_date = final_dt.strftime('%d.%m.%Y')
        
        location = None
        if lat and lon:
            time.sleep(1)  
            location = get_location_name(lat, lon)
            
        # Конвертація GIF
        if mime_type == 'image/gif' or lower_name.endswith('.gif'):
            mp4_path = os.path.join('downloaded', f['name'].rsplit('.', 1)[0] + '_gif.mp4')
            try:
                gif_to_mp4(local_path, mp4_path)
            except Exception as e:
                # 🚨 АВАРІЯ: GIF пошкоджений або FFmpeg впав
                sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося конвертувати GIF файл '{f['name']}' у MP4. Можливо, файл бінарно пошкоджений. Деталі: {e}")
            
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
                # 🚨 АВАРІЯ: HEIC файл битий або не зчитується Pillow
                sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося розкодувати iPhone-формат HEIC/HEIF для файлу '{f['name']}'. Файл пошкоджено. Деталі: {e}")
                
            if os.path.exists(local_path): os.remove(local_path)
            local_path = jpg_path
            mime_type = 'image/jpeg'

        processed_items.append({
            'id': f['id'],
            'name': f['name'],
            'mime': mime_type,
            'local_path': local_path,
            'date': file_date,
            'location': location or "Невідоме місце"
        })

    groups = {}
    for item in processed_items:
        key = (item['date'], item['location'])
        if key not in groups:
            groups[key] = []
        groups[key].append(item)
        
    for (date, loc), items in groups.items():
        print(f"🎬 Знайдено групу для монтажу: Дата {date} | Локація: {loc}. Файлів: {len(items)}")
        
        clips = process_media_group(items)
        if not clips:
            sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося згенерувати кліпи для вибраної групи медіа-файлів.")
            
        text_info = generate_ai_metadata(date, loc)
        trending_text, year, location = text_info
        
        hash_tag = location.split(',')[0].strip().replace(" ", "")
        tiktok_description = f"{trending_text} 🌍 #travel #{hash_tag}"
        
        final_file = compile_final_video(clips, text_info)
        print(f"🎉 Відео успішно змонтовано: {final_file}")
        
        upload_success = upload_to_tiktok(final_file, tiktok_description)
        
        if upload_success:
            move_files_to_trash(service, items)
            if os.path.exists(final_file): os.remove(final_file)
            for item in items:
                if os.path.exists(item['local_path']): os.remove(item['local_path'])
            print("🏁 Обробку поточної групи успішно завершено.")
        else:
            # 🚨 АВАРІЯ: Монтаж пройшов, але TikTok API відхилив запит (як у нашому логу помилки 403)
            sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Публікація в TikTok зазнала невдачі. Вхідні файли збережено на Диску для повторної спроби.")
        
        return

if __name__ == '__main__':
    main()
