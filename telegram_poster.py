import os
import sys
import json
import io
import time
import subprocess
import re
import math
from datetime import datetime
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image, ImageOps
from PIL.ExifTags import TAGS, GPSTAGS
from pillow_heif import register_heif_opener

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
        return build('drive', 'v3', credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"❌ ПОМИЛКА ініціалізації Google Drive: {e}")
        sys.exit(1)

# =====================================================================
# 🧠 АНАЛІЗ МЕТАДАНИХ ТА ГЕОЛОКАЦІЇ
# =====================================================================

def extract_date_from_filename(filename):
    current_year = datetime.now().year
    min_year = 2000  
    name_part = filename.rsplit('.', 1)[0]

    # Формат РРРР-ММ-ДД або РРРРММДД
    match_yyyy = re.search(r'\b(\d{4})[-._]?(0[1-9]|1[0-2])[-._]?([0-2]\d|3[01])', name_part)
    if match_yyyy:
        year, month, day = match_yyyy.groups()
        try: 
            dt = datetime(int(year), int(month), int(day))
            if min_year <= dt.year <= current_year:
                return dt
        except ValueError: 
            pass

    # Формат ДД-ММ-РРРР або ДДММРРРР
    match_dd = re.search(r'\b(0[1-9]|[12]\d|3[01])[-._]?(0[1-9]|1[0-2])[-._]?(\d{4})', name_part)
    if match_dd:
        day, month, year = match_dd.groups()
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
                geotagging = {GPSTAGS.get(t, t): value for t, value in gps_ifd.items()}
                if 'GPSLatitude' in geotagging and 'GPSLongitude' in geotagging:
                    def _to_deg(v):
                        return float(v[0]) + (float(v[1]) / 60.0) + (float(v[2]) / 3600.0)
                    lat = _to_deg(geotagging['GPSLatitude'])
                    lon = _to_deg(geotagging['GPSLongitude'])
                    if geotagging.get('GPSLatitudeRef') == 'S': lat = -lat
                    if geotagging.get('GPSLongitudeRef') == 'W': lon = -lon
    except Exception:
        pass
    return date_str, lat, lon

def get_video_metadata(video_path):
    date_str, lat, lon = None, None, None
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', video_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            tags = json.loads(result.stdout).get('format', {}).get('tags', {})
            creation_time = tags.get('creation_time')
            if creation_time:
                try:
                    dt = datetime.strptime(creation_time[:19], '%Y-%m-%dT%H:%M:%S')
                    date_str = dt.strftime('%Y:%m:%d %H:%M:%S')
                except Exception: 
                    pass
            
            loc_str = tags.get('location') or tags.get('location-eng')
            if loc_str:
                match = re.match(r'([+-]\d+\.\d+)([+-]\d+\.\d+)', loc_str)
                if match:
                    lat, lon = float(match.group(1)), float(match.group(2))
    except Exception:
        pass
    return date_str, lat, lon

def get_location_data(lat, lon, mode='family'):
    if lat is None or lon is None: 
        return "", ""
    
    url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=13&accept-language=uk"
    headers = {'User-Agent': 'FurnitureArchive_TelegramBot_2026'}
    
    for attempt in range(3):
        try:
            res = requests.get(url, headers=headers, timeout=15).json()
            address = res.get('address', {})
            city_town = (
                address.get('city') or address.get('town') or 
                address.get('village') or address.get('municipality') or 
                address.get('county')
            )
            country = address.get('country') or ""
            
            if city_town and country:
                clean_location = f"{city_town}, {country}"
            elif country:
                clean_location = country
            else:
                clean_location = city_town or ""
                
            display_location = clean_location
            if mode == 'exchange' and display_location:
                google_maps_url = f"https://www.google.com/maps/?q={lat},{lon}"
                display_location = f'<a href="{google_maps_url}">{clean_location}</a>'
                
            return display_location, clean_location
        except Exception:
            time.sleep(2)
            
    return "", ""

def get_intellectual_date(local_path, filename, gdrive_file, now_time=None):
    if now_time is None:
        now_time = datetime.now()
    min_year = 2000  

    fn_date = extract_date_from_filename(filename)
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
    except Exception:
        pass

    return now_time, lat, lon

# =====================================================================
# ⚙️ ОБРОБКА МЕДІА ЯК У КЛІЄНТІ TELEGRAM ("СКРІПКА")
# =====================================================================

def download_file_with_retry(service, file_id, local_path, file_name, retries=3):
    for attempt in range(retries):
        try:
            current_service = service if attempt == 0 else get_gdrive_service()
            request = current_service.files().get_media(fileId=file_id)
            with io.FileIO(local_path, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request, chunksize=10 * 1024 * 1024)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            return True
        except Exception as e:
            if attempt == retries - 1:
                print(f"   ❌ Помилка завантаження {file_name}: {e}")
            if os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except Exception:
                    pass
            time.sleep(2 * (attempt + 1))
    return False

def compress_photo_like_telegram(input_path, max_dim=2560, max_size_bytes=9.5 * 1024 * 1024):
    """Імітує стандартне стиснення фото додатком Telegram (до 2560px по довшій стороні, чистий sRGB JPEG)."""
    output_path = os.path.join('downloaded', 'tg_' + os.path.basename(input_path).rsplit('.', 1)[0] + '.jpg')
    try:
        with Image.open(input_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != 'RGB':
                img = img.convert('RGB')
                
            w, h = img.size
            if w > max_dim or h > max_dim:
                if w > h:
                    new_w = max_dim
                    new_h = max(1, int(h * (max_dim / w)))
                else:
                    new_h = max_dim
                    new_w = max(1, int(w * (max_dim / h)))
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            # Чисте полотно без EXIF / ICC-профілів для 100% сумісності
            clean_img = Image.new('RGB', img.size)
            clean_img.paste(img)
            
            quality = 82
            clean_img.save(output_path, 'JPEG', quality=quality, optimize=True)
            
            while os.path.getsize(output_path) > max_size_bytes and quality > 30:
                quality -= 15
                clean_img.save(output_path, 'JPEG', quality=quality, optimize=True)
                
            return output_path
    except Exception as e:
        print(f"   ⚠️ Помилка оптимізації фото {os.path.basename(input_path)}: {e}")
        return input_path

def compress_video_like_telegram(input_path, output_path):
    """Імітує стандартне стиснення відео додатком Telegram (H.264, AAC, 720p max, faststart)."""
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vcodec', 'libx264', '-crf', '26',
        '-preset', 'faster', '-acodec', 'aac',
        '-b:a', '128k', '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        '-vf', "scale='min(1280,iw)':-2",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def gif_to_mp4(input_path, output_path):
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-movflags', '+faststart', '-pix_fmt', 'yuv420p',
        '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def get_video_duration(video_path):
    cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0

def split_video_if_needed(input_path, max_size_bytes=49 * 1024 * 1024):
    current_size = os.path.getsize(input_path)
    if current_size <= max_size_bytes:
        return [input_path]
        
    duration = get_video_duration(input_path)
    if duration == 0:
        return [input_path]
        
    num_parts = math.ceil(current_size / max_size_bytes)
    part_duration = duration / num_parts
    parts = []
    base_name = input_path.rsplit('.', 1)[0]
    
    for i in range(num_parts):
        start_time = i * part_duration
        output_part = f"{base_name}_part{i+1}.mp4"
        cmd = [
            'ffmpeg', '-y', '-ss', str(start_time), '-i', input_path,
            '-t', str(part_duration), '-c', 'copy', '-movflags', '+faststart', output_part
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(output_part) and os.path.getsize(output_part) > 0:
            parts.append(output_part)
            
    return parts if parts else [input_path]

# =====================================================================
# 📡 ВІДПРАВКА В TELEGRAM ТА АРХІВАЦІЯ
# =====================================================================

def send_media_group(media_batch, caption, chat_id):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token or not chat_id:
        print("❌ ПОМИЛКА: Відсутній TELEGRAM_BOT_TOKEN або CHAT_ID!")
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
        res = requests.post(url, data=payload, files=files, timeout=180).json()
        for f in files.values(): f.close()
            
        if not res.get('ok'):
            print(f"❌ Помилка Telegram API: {res.get('description')}")
            for idx_err, it in enumerate(media_batch, 1):
                sz = os.path.getsize(it['local_path']) / (1024 * 1024)
                print(f"   Деталі файлу #{idx_err}: {it['original_name']} ({it['mime']}, {sz:.2f} MB)")
            return False
        return True
    except Exception as e:
        print(f"❌ Мережева помилка відправки в Telegram: {e}")
        for f in files.values(): f.close()
        return False

def move_to_gdrive_trash(service, file_id, file_name, current_folder_id):
    for attempt in range(3):
        try:
            current_service = service if attempt == 0 else get_gdrive_service()
            current_service.files().update(
                fileId=file_id,
                addParents=TRASH_FOLDER_ID,
                removeParents=current_folder_id,
                fields='id, parents'
            ).execute()
            return True
        except Exception:
            time.sleep(2)
    print(f"   ⚠️ Не вдалося перемістити в кошик: {file_name}")
    return False

def clean_local_files(files_list):
    for f in (files_list or []):
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

# =====================================================================
# 🚀 ГОЛОВНА ЛОГІКА СКРИПТА
# =====================================================================

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'family'
    
    if mode == 'exchange':
        FOLDER_ID = '1U6QKj7RkEI17gw3V0nMb2RsK6gyDf5no'
        CHAT_ID = '-1003606633217'
        print("🌟 РЕЖИМ: ФОТООБМІННИК")
    else:
        FOLDER_ID = '1MFTlnTVwOuPysxtdS-FzQSZAhFnbzTwz'
        CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
        print("👪 РЕЖИМ: СІМЕЙНИЙ АРХІВ")

    if not CHAT_ID:
        print("❌ ПОМИЛКА: Не вказано ID цільового чату!")
        sys.exit(1)
        
    service = get_gdrive_service()
    
    try:
        results = service.files().list(
            q=f"'{FOLDER_ID}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size)",
            orderBy="createdTime",
            pageSize=50
        ).execute()
    except Exception as e:
        print(f"❌ ПОМИЛКА Google Диску: {e}")
        sys.exit(1)
    
    gdrive_files = [f for f in results.get('files', []) if f['id'] != TRASH_FOLDER_ID]
    total_files = len(gdrive_files)
    
    if not total_files:
        print("ℹ️ Папка порожня.")
        return

    print(f"🚀 Знайдено файлів: {total_files}\n")
    processed_items = []
    local_files_to_clean = []
    os.makedirs('downloaded', exist_ok=True)
    
    for idx, f in enumerate(gdrive_files, 1):
        mime_type = f['mimeType']
        lower_name = f['name'].lower()
        
        if not (mime_type.startswith(('image/', 'video/')) or lower_name.endswith(VALID_EXTENSIONS)):
            print(f"💥 Непідтримуваний файл [{idx}/{total_files}]: {f['name']} ({mime_type})")
            clean_local_files(local_files_to_clean)
            sys.exit(1)
                    
        local_path = os.path.join('downloaded', f['name'])
        
        if not download_file_with_retry(service, f['id'], local_path, f['name']):
            print(f"💥 АВАРІЙНЕ ЗАВЕРШЕННЯ на файлі [{idx}/{total_files}]: {f['name']}")
            clean_local_files(local_files_to_clean)
            sys.exit(1)

        local_files_to_clean.append(local_path)

        final_dt, lat, lon = get_intellectual_date(local_path, f['name'], f)
        file_date = final_dt.strftime('%d.%m.%Y')
        
        display_loc, group_loc = get_location_data(lat, lon, mode=mode)
        log_loc = group_loc if group_loc else "без GPS"
        if not group_loc:
            group_loc = "Невідоме місце"

        # Компактний лог в один рядок
        print(f"📥 [{idx}/{total_files}] {f['name']} — 📅 {file_date} — 📍 {log_loc}")

        # 1. GIF -> MP4
        if mime_type == 'image/gif' or lower_name.endswith('.gif'):
            mp4_path = os.path.join('downloaded', f['name'].rsplit('.', 1)[0] + '_gif.mp4')
            gif_to_mp4(local_path, mp4_path)
            if os.path.exists(mp4_path):
                local_files_to_clean.append(mp4_path)
                local_path = mp4_path
                mime_type = 'video/mp4'
                
        # 2. Будь-яке фото (включно з HEIC, PNG, WEBP, JPG) -> стандартний JPEG до 2560px
        if mime_type.startswith('image/') or lower_name.endswith(('.heic', '.heif')):
            prepared_path = compress_photo_like_telegram(local_path)
            if prepared_path != local_path:
                local_files_to_clean.append(prepared_path)
                local_path = prepared_path
                mime_type = 'image/jpeg'

            processed_items.append({
                'id': f['id'],
                'original_name': f['name'],
                'mime': mime_type,
                'local_path': local_path,
                'date': file_date,
                'group_location': group_loc,
                'display_location': display_loc
            })

        # 3. Будь-яке відео -> стискаємо як у Telegram (720p H.264)
        elif mime_type.startswith('video/') or lower_name.endswith(('.mov', '.avi', '.mkv', '.3gp', '.mpeg', '.mpg', '.swf')):
            compressed_path = os.path.join('downloaded', 'tg_' + f['name'].rsplit('.', 1)[0] + '.mp4')
            compress_video_like_telegram(local_path, compressed_path)
            if os.path.exists(compressed_path) and os.path.getsize(compressed_path) > 0:
                local_files_to_clean.append(compressed_path)
                local_path = compressed_path
                mime_type = 'video/mp4'

            video_parts = split_video_if_needed(local_path)
            for part_path in video_parts:
                if part_path != local_path:
                    local_files_to_clean.append(part_path)
                processed_items.append({
                    'id': f['id'],
                    'original_name': f['name'],
                    'mime': mime_type,
                    'local_path': part_path,
                    'date': file_date,
                    'group_location': group_loc,
                    'display_location': display_loc
                })

    # Групування за [Дата + Місто]
    groups = {}
    for item in processed_items:
        key = (item['date'], item['group_location'])
        groups.setdefault(key, []).append(item)
        
    if groups:
        first_key = list(groups.keys())[0]
        items = groups[first_key]
        date, group_loc = first_key
        
        print(f"\n💎 Публікуємо групу: 📅 {date} | 📍 {group_loc} (файлів: {len(items)})")
        
        sample_display_loc = items[0]['display_location']
        base_caption = f"📅 {date}"
        if sample_display_loc:
            base_caption += f" 📍 {sample_display_loc}"
            
        sub_batches = []
        current_batch = []
        current_batch_size = 0
        
        for item in items:
            f_size = os.path.getsize(item['local_path'])
            if len(current_batch) >= 10 or (current_batch_size + f_size) > 45 * 1024 * 1024:
                if current_batch:
                    sub_batches.append(current_batch)
                current_batch = []
                current_batch_size = 0
            current_batch.append(item)
            current_batch_size += f_size
        if current_batch:
            sub_batches.append(current_batch)
            
        success_ids = set()
        for s_idx, sb in enumerate(sub_batches):
            caption = base_caption
            if len(sub_batches) > 1:
                caption += f" (Частина {s_idx + 1}/{len(sub_batches)})"
                
            print(f"📡 Відправка альбому {s_idx + 1}/{len(sub_batches)} ({len(sb)} медіа)...")
            if send_media_group(sb, caption, CHAT_ID):
                moved_count = 0
                for uploaded_item in sb:
                    if uploaded_item['id'] not in success_ids:
                        if move_to_gdrive_trash(service, uploaded_item['id'], uploaded_item['original_name'], FOLDER_ID):
                            moved_count += 1
                        success_ids.add(uploaded_item['id'])
                print(f"📦 Успішно надіслано та переміщено в кошик: {moved_count} файл(ів)")
            else:
                print(f"💥 АВАРІЙНЕ ЗАВЕРШЕННЯ під час відправки альбому!")
                clean_local_files(local_files_to_clean)
                sys.exit(1)

    clean_local_files(local_files_to_clean)
    print("🏁 Синхронізацію завершено.")
    sys.exit(0)

if __name__ == '__main__':
    main()
