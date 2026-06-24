import os
import sys
import json
import io
import time
import subprocess
import re
import glob
import math
from datetime import datetime
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from pillow_heif import register_heif_opener

# Реєструємо підтримку HEIC форматів для Pillow
register_heif_opener()

TRASH_FOLDER_ID = '1L3veD90e7Fr1acwlK7PmhSs_JrofyT6N'
SCOPES = ['https://www.googleapis.com/auth/drive']

VALID_EXTENSIONS = (
    '.3gp', '.avi', '.gif', '.heic', '.heif', '.jpeg', '.jpg', 
    '.mkv', '.mov', '.mp4', '.mpeg', '.mpg', '.tif', '.tiff', '.webp', '.png', '.swf'
)

def get_gdrive_service():
    try:
        key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
        creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
        return build('drive', 'v3', credentials=creds)
    except KeyError:
        print("❌ ПОМИЛКА: Секрет GDRIVE_SERVICE_ACCOUNT_KEY не знайдено в GitHub Secrets!")
        sys.exit(1)
    except json.JSONDecodeError:
        print("❌ ПОМИЛКА: Вміст GDRIVE_SERVICE_ACCOUNT_KEY не є коректним JSON файлом!")
        sys.exit(1)
    except Exception as e:
        print(f"❌ ПОМИЛКА ініціалізації Google Drive сервісу: {e}")
        sys.exit(1)

# =====================================================================
# 🧠 ІНТЕЛЕКТУАЛЬНИЙ БЛОК АНАЛІЗУ МЕТАДАНИХ ТА ГЕОЛОКАЦІЇ
# =====================================================================

def extract_date_from_filename(filename):
    current_year = datetime.now().year
    min_year = 2000  
    name_part = filename.rsplit('.', 1)[0]

    # 1️⃣ Формат РРРР-ММ-ДД або РРРРММДД
    match_yyyy_mm_dd = re.search(r'\b(\d{4})[-._]?(0[1-9]|1[0-2])[-._]?([0-2]\d|3[01])', name_part)
    if match_yyyy_mm_dd:
        year, month, day = match_yyyy_mm_dd.groups()
        try: 
            dt = datetime(int(year), int(month), int(day))
            if min_year <= dt.year <= current_year:
                return dt
        except ValueError: 
            pass

    # 2️⃣ Формат ДД-ММ-РРРР або ДДММРРРР
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

def get_location_data(lat, lon, mode='family'):
    """Повертає назви міст/країн українською мовою. Для режиму exchange генерує посилання."""
    if lat is None or lon is None: 
        return "", ""
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=13&accept-language=uk"
        headers = {'User-Agent': 'FurnitureArchive_TelegramBot_2026'}
        res = requests.get(url, headers=headers, timeout=10).json()
        address = res.get('address', {})
        
        city_town = (
            address.get('city') or 
            address.get('town') or 
            address.get('village') or 
            address.get('municipality') or
            address.get('county')
        )
        country = address.get('country') or ""
        
        if city_town and country:
            clean_location = f"{city_town}, {country}"
        elif country:
            clean_location = country
        else:
            clean_location = city_town or ""
            
        group_location = clean_location
        display_location = clean_location

        if mode == 'exchange' and display_location:
            google_maps_url = f"https://www.google.com/maps/?q={lat},{lon}"
            display_location = f'<a href="{google_maps_url}">{clean_location}</a>'
            
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
        print(f"🎯 Дату розпізнано з назви файлу '{filename}': {fn_date.strftime('%d.%m.%Y')}")
    
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
        print(f"⚠️ Метадані {filename} нелогічні ({meta_date}). Шукаємо в системі Drive.")

    try:
        dt_created = datetime.strptime(gdrive_file['createdTime'][:19], '%Y-%m-%dT%H:%M:%S')
        dt_modified = datetime.strptime(gdrive_file['modifiedTime'][:19], '%Y-%m-%dT%H:%M:%S')
        earliest_gdrive = min(dt_created, dt_modified)
        
        if min_year <= earliest_gdrive.year <= now_time.year:
            return earliest_gdrive, lat, lon
    except Exception as e:
        print(f"⚠️ Помилка зчитування дат Google Drive: {e}")

    return now_time, lat, lon

# =====================================================================
# ⚙️ ФУНКЦІЇ ОБРОБКИ МЕДІА ТА НАДСИЛАННЯ
# =====================================================================

def convert_to_mp4(input_path, output_path):
    print(f"🎬 Оптимізуємо відео {input_path} в стандартний MP4...")
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vcodec', 'libx264', '-crf', '30',  
        '-preset', 'faster', '-acodec', 'aac',
        '-b:a', '96k', '-pix_fmt', 'yuv420p',
        '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def gif_to_mp4(input_path, output_path):
    print(f"🎞️ Конвертуємо анімацію GIF {input_path} в MP4...")
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-movflags', 'faststart', '-pix_fmt', 'yuv420p',
        '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def split_video(video_path, target_max_size_mb=40):
    """Швидко розрізає відео без втрати якості (copy кодек), якщо воно перевищує ліміт."""
    file_size = os.path.getsize(video_path)
    target_size_bytes = target_max_size_mb * 1024 * 1024
    if file_size <= target_size_bytes:
        return [video_path]
        
    print(f"✂️ Відео {video_path} завелике ({file_size / 1024 / 1024:.2f} MB). Нарізаємо на частини...")
    
    cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        print("⚠️ Не вдалося визначити тривалість відео для нарізки.")
        return [video_path]
        
    try:
        total_duration = float(res.stdout.strip())
    except ValueError:
        return [video_path]
        
    num_parts = math.ceil(file_size / target_size_bytes)
    part_duration = total_duration / num_parts
    
    base_dir = os.path.dirname(video_path) or '.'
    filename_only = os.path.basename(video_path)
    name_part = filename_only.rsplit('.', 1)[0]
    ext_part = filename_only.rsplit('.', 1)[1] if '.' in filename_only else 'mp4'
    
    output_template = os.path.join(base_dir, f"split_v_{name_part}_%03d.{ext_part}")
    
    split_cmd = [
        'ffmpeg', '-y', '-i', video_path,
        '-c', 'copy', '-map', '0',
        '-f', 'segment', '-segment_time', str(part_duration),
        '-reset_timestamps', '1',
        output_template
    ]
    
    subprocess.run(split_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    pattern = os.path.join(base_dir, f"split_v_{name_part}_*.*")
    generated_files = sorted(glob.glob(pattern))
    
    if generated_files:
        print(f"🪓 Відео успішно розрізано на {len(generated_files)} частин.")
        return generated_files
    else:
        print("❌ Помилка під час нарізки відео через ffmpeg. Залишаємо оригінал.")
        return [video_path]

def send_media_group(media_batch, caption, chat_id):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token or not chat_id:
        print("❌ ПОМИЛКА: Відсутній TELEGRAM_BOT_TOKEN або цільовий CHAT_ID!")
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMediaGroup"
    media = []
    files = {}
    
    for idx, item in enumerate(media_batch):
        attach_name = f"media_{idx}"
        m_type = "video" if item['mime'].startswith('video/') else "photo"
        
        media_obj = {"type": m_type, "media": f"attach://{attach_name}"}
        if idx == 0:
            media_obj["caption"] = caption
            media_obj["parse_mode"] = "HTML"  
            
        media.append(media_obj)
        files[attach_name] = open(item['local_path'], 'rb')
        
    payload = {'chat_id': chat_id, 'media': json.dumps(media)}
    
    try:
        res = requests.post(url, data=payload, files=files, timeout=120).json()
        for f in files.values(): f.close()
            
        if not res.get('ok'):
            print(f"❌ Помилка Telegram API: {res.get('description')}")
            return False
        return True
    except Exception as e:
        print(f"❌ Помилка відправки в Telegram: {e}")
        for f in files.values(): f.close()
        return False

# =====================================================================
# 🚀 ГОЛОВНА ЛОГІКА СКРИПТА
# =====================================================================

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'family'
    
    if mode == 'exchange':
        FOLDER_ID = '1U6QKj7RkEI17gw3V0nMb2RsK6gyDf5no'
        CHAT_ID = '-1003606633217'
        print("🌟 РЕЖИМ: ФОТООБМІННИК (Канал -1003606633217)")
    else:
        FOLDER_ID = '1MFTlnTVwOuPysxtdS-FzQSZAhFnbzTwz'
        CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
        print("👪 РЕЖИМ: СІМЕЙНИЙ АРХІВ (Основний чат)")

    if not CHAT_ID:
        print("❌ ПОМИЛКА: Не вказано ID цільового чату/каналу!")
        sys.exit(1)
        
    print("🚀 Старт синхронізації media...")
    service = get_gdrive_service()
    
    try:
        results = service.files().list(
            q=f"'{FOLDER_ID}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size)",
            orderBy="createdTime",
            pageSize=50
        ).execute()
    except Exception as e:
        print(f"❌ ПОМИЛКА під час отримання списку файлів з Google Диску: {e}")
        sys.exit(1)
    
    gdrive_files = results.get('files', [])
    gdrive_files = [f for f in gdrive_files if f['id'] != TRASH_FOLDER_ID]
    if not gdrive_files:
        print("ℹ️ Папка порожня. Немає контенту для публікації.")
        return

    print(f"Знайдено файлів у папці: {len(gdrive_files)}")
    processed_items = []
    local_files_to_clean = []
    os.makedirs('downloaded', exist_ok=True)
    
    for f in gdrive_files:
        mime_type = f['mimeType']
        lower_name = f['name'].lower()
        
        is_valid_media = mime_type.startswith(('image/', 'video/')) or lower_name.endswith(VALID_EXTENSIONS)
        if not is_valid_media:
            print(f"⏭️ Пропускаємо непідтримуваний файл: {f['name']} ({mime_type})")
            continue
                    
        local_path = os.path.join('downloaded', f['name'])
        print(f"\n📥 Завантаження {f['name']}...")
        
        try:
            request = service.files().get_media(fileId=f['id'])
            with io.FileIO(local_path, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
        except Exception as e:
            print(f"❌ Не вдалося завантажити файл {f['name']}: {e}")
            continue

        local_files_to_clean.append(local_path)

        # Аналіз інтелектуальної дати та геокоординат
        final_dt, lat, lon = get_intellectual_date(local_path, f['name'], f)
        file_date = final_dt.strftime('%d.%m.%Y')
        
        # Отримання структурованої локації
        display_loc, group_loc = get_location_data(lat, lon, mode=mode)
        if not group_loc:
            group_loc = "Невідоме місце"

        # Оптимізація та конвертація форматів
        if mime_type == 'image/gif' or lower_name.endswith('.gif'):
            mp4_path = os.path.join('downloaded', f['name'].rsplit('.', 1)[0] + '_gif.mp4')
            gif_to_mp4(local_path, mp4_path)
            if os.path.exists(mp4_path):
                local_files_to_clean.append(mp4_path)
                local_path = mp4_path
                mime_type = 'video/mp4'
                
        elif mime_type in ['image/heic', 'image/heif'] or lower_name.endswith(('.heic', '.heif')):
            print(f"📸 Конвертуємо HEIC-фото в JPG для Telegram...")
            jpg_path = os.path.join('downloaded', f['name'].rsplit('.', 1)[0] + '.jpg')
            try:
                with Image.open(local_path) as img:
                    img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
                local_files_to_clean.append(jpg_path)
                local_path = jpg_path
                mime_type = 'image/jpeg'
            except Exception as e:
                print(f"❌ Помилка конвертації HEIC: {e}")
                continue

        elif mime_type.startswith('video/') or lower_name.endswith(('.mov', '.avi', '.mkv', '.3gp', '.mpeg', '.mpg', '.swf')):
            is_large = os.path.getsize(local_path) > 44 * 1024 * 1024
            is_not_mp4 = not lower_name.endswith('.mp4') or mime_type != 'video/mp4' or lower_name.endswith('.swf')
            
            if is_large or is_not_mp4:
                compressed_path = os.path.join('downloaded', 'opt_' + f['name'].rsplit('.', 1)[0] + '.mp4')
                convert_to_mp4(local_path, compressed_path)
                if os.path.exists(compressed_path):
                    local_files_to_clean.append(compressed_path)
                    local_path = compressed_path
                    mime_type = 'video/mp4'
            
            # [ІНТЕЛЕКТУАЛЬНА НАРІЗКА ВЕЛИКИХ ВІДЕО]
            if os.path.getsize(local_path) > 44 * 1024 * 1024:
                video_parts = split_video(local_path, target_max_size_mb=40)
                for idx, part_path in enumerate(video_parts):
                    if part_path != local_path:
                        local_files_to_clean.append(part_path)
                    
                    processed_items.append({
                        'id': f['id'],
                        'name': f['name'],
                        'mime': 'video/mp4',
                        'local_path': part_path,
                        'date': file_date,
                        'group_location': group_loc,
                        'display_location': display_loc
                    })
                continue

        processed_items.append({
            'id': f['id'],
            'name': f['name'],
            'mime': mime_type,
            'local_path': local_path,
            'date': file_date,
            'group_location': group_loc,
            'display_location': display_loc
        })

    if gdrive_files and not processed_items:
        print("💥 ПОМИЛКА: Файли на Диску виявлено, але жоден з них не зміг успішно завантажитися чи обробитися!")
        clean_local_files(local_files_to_clean)
        sys.exit(1)

    # Розумне групування за [Дата + МістоДляГрупування]
    groups = {}
    for item in processed_items:
        key = (item['date'], item['group_location'])
        groups.setdefault(key, []).append(item)
        
    # Публікація суворо ОДНОГО безпечного за розміром контенту за сесію
    for (date, group_loc), items in groups.items():
        # Захист від розриву частин: групуємо елементи за оригінальним Google Drive ID
        from collections import OrderedDict
        files_dict = OrderedDict()
        for item in items:
            files_dict.setdefault(item['id'], []).append(item)
            
        batch = []
        current_batch_size = 0
        
        for gdrive_id, file_parts in files_dict.items():
            file_total_size = sum(os.path.getsize(p['local_path']) for p in file_parts)
            
            # Якщо файл (або всі його частини разом) влазить у поточні ліміти альбому
            if len(batch) + len(file_parts) <= 10 and (current_batch_size + file_total_size) <= 80 * 1024 * 1024:
                batch.extend(file_parts)
                current_batch_size += file_total_size
            else:
                if batch:
                    break  # Зупиняємося, якщо батч вже має інші медіа
                else:
                    # Якщо батч порожній, а один цей файл перевищує ліміти (наприклад, дуже довге відео на багато шматків),
                    # ми забираємо його повністю, а нижче розіб'ємо на послідовні під-альбоми
                    batch = file_parts
                    current_batch_size = file_total_size
                    break
                    
        if not batch:
            continue
            
        sample_display_loc = batch[0]['display_location']
        success = False
        
        # Перевіряємо, чи потрібно розбивати великий батч на послідовні під-альбоми
        if current_batch_size > 80 * 1024 * 1024 or len(batch) > 10:
            print(f"🔄 Великий контент розбито на декілька під-альбомів через ліміти ({current_batch_size / 1024 / 1024:.2f} MB)...")
            sub_batches = []
            sub_b = []
            sub_size = 0
            for item in batch:
                f_size = os.path.getsize(item['local_path'])
                if len(sub_b) >= 5 or (sub_size + f_size) > 75 * 1024 * 1024:
                    sub_batches.append(sub_b)
                    sub_b = []
                    sub_size = 0
                sub_b.append(item)
                sub_size += f_size
            if sub_b:
                sub_batches.append(sub_b)
                
            all_success = True
            for s_idx, sb in enumerate(sub_batches):
                caption = f"📅 {date}"
                if sample_display_loc:
                    caption += f" 📍 {sample_display_loc}"
                caption += f" (Частина {s_idx + 1}/{len(sub_batches)})"
                
                print(f"📡 Надсилання під-альбому {s_idx + 1}/{len(sub_batches)} (Елементів: {len(sb)})...")
                if not send_media_group(sb, caption, CHAT_ID):
                    all_success = False
                    break
            success = all_success
        else:
            # Звичайне надсилання цілісного альбому
            caption = f"📅 {date}"
            if sample_display_loc:
                caption += f" 📍 {sample_display_loc}"
                
            print(f"\n📡 Надсилання безпечного альбому для {date} (Елементів: {len(batch)}, Розмір: {current_batch_size / 1024 / 1024:.2f} MB)...")
            success = send_media_group(batch, caption, CHAT_ID)
            
        if success:
            print(f"✅ Успішно надіслано в Telegram. Переміщаємо файли в кошик Google Диску...")
            uploaded_ids = set(item['id'] for item in batch)
            for g_id in uploaded_ids:
                name_sample = next(item['name'] for item in batch if item['id'] == g_id)
                try:
                    service.files().update(
                        fileId=g_id,
                        addParents=TRASH_FOLDER_ID,
                        removeParents=FOLDER_ID,
                        fields='id, parents'
                    ).execute()
                    print(f"📦 Переміщено на Диску: {name_sample}")
                except Exception as e:
                    print(f"❌ Не вдалося перемістити {name_sample}: {e}")
            
            clean_local_files(local_files_to_clean)
            print(f"\n🏁 Роботу успішно завершено.")
            sys.exit(0)
        else:
            print(f"\n💥 ПОМИЛКА: Не вдалося опублікувати контент для {date}!")
            clean_local_files(local_files_to_clean)
            sys.exit(1)

    # Якщо пройшли цикл, але нічого не відправили
    clean_local_files(local_files_to_clean)

def clean_local_files(files_list):
    if not files_list:
        return
    for f in files_list:
        if os.path.exists(f):
            try:
                os.remove(f)
            except:
                pass
    print("🧹 Тимчасові локальні файли успішно очищені.")

if __name__ == '__main__':
    main()
