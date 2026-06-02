import os
import sys
import json
import io
import time
import subprocess
import re
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

# Загальний проміжний кошик для обох режимів
TRASH_FOLDER_ID = '1L3veD90e7Fr1acwlK7PmhSs_JrofyT6N'
SCOPES = ['https://www.googleapis.com/auth/drive']

# Розширений список підтримуваних медіа-форматів
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
        raise
    except json.JSONDecodeError:
        print("❌ ПОМИЛКА: Вміст GDRIVE_SERVICE_ACCOUNT_KEY не є коректним JSON файлом!")
        raise
    except Exception as e:
        print(f"❌ ПОМИЛКА ініціалізації Google Drive сервісу: {e}")
        raise

def get_exif_data(image_path):
    """Витягує дату та GPS з фото"""
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
        print(f"⚠️ Попередження: Не вдалося прочитати EXIF для {image_path}: {e}")
    return date_str, lat, lon

def get_video_metadata(video_path):
    """Витягує реальну дату зйомки та GPS з відео за допомогою ffprobe"""
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
            
            # 1. Шукаємо дату оригінальної зйомки/створення
            creation_time = tags.get('creation_time')
            if creation_time:
                # Формат зазвичай: 2018-09-12T11:07:19.000000Z
                try:
                    dt = datetime.strptime(creation_time[:19], '%Y-%m-%dT%H:%M:%S')
                    date_str = dt.strftime('%Y:%m:%d %H:%M:%S')
                except:
                    pass
            
            # 2. Шукаємо GPS (типова мітка для iPhone/Android відео)
            # Формат зазвичай: "+50.4501+030.5234/" або подібний ISO 6709
            loc_str = tags.get('location') or tags.get('location-eng')
            if loc_str:
                match = re.match(r'([+-]\d+\.\d+)([+-]\d+\.\d+)', loc_str)
                if match:
                    lat = float(match.group(1))
                    lon = float(match.group(2))
    except Exception as e:
        print(f"⚠️ Не вдалося прочитати метадані відео {video_path}: {e}")
    return date_str, lat, lon

def get_location_name(lat, lon):
    """Безкоштовне зворотне геокодування через OpenStreetMap"""
    if lat is None or lon is None:
        return None
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10&accept-language=uk"
        headers = {'User-Agent': 'GlobalCMS_Bot_2026'}
        res = requests.get(url, headers=headers, timeout=10).json()
        address = res.get('address', {})
        city = address.get('city') or address.get('town') or address.get('village') or address.get('county')
        country = address.get('country')
        if city and country:
            return f"{city}, {country}"
        elif country:
            return country
    except Exception as e:
        print(f"⚠️ Попередження: Помилка геокодування OSM: {e}")
    return None

def convert_to_mp4(input_path, output_path):
    print(f"🎬 Оптимізуємо/стискаємо відео {input_path} в стандартний MP4...")
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vcodec', 'libx264', '-crf', '28',
        '-preset', 'faster', '-acodec', 'aac',
        '-b:a', '128k', '-pix_fmt', 'yuv420p',
        '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def gif_to_mp4(input_path, output_path):
    print(f"🎞️ Конвертуємо анімацію GIF {input_path} в MP4 відео...")
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-movflags', 'faststart', '-pix_fmt', 'yuv420p',
        '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def send_media_group(media_batch, caption, chat_id):
    """Відправляє групу медіафайлів (до 10 штук) в Telegram"""
    token = os.environ['TELEGRAM_BOT_TOKEN']
    
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
        print(f"Помилка відправки в Telegram: {e}")
        for f in files.values(): f.close()
        return False

def main():
    # Визначаємо режим роботи через аргументи командного рядка
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
        return
        
    print("🚀 Старт синхронізації медіа...")
    service = get_gdrive_service()
    
    # Отримуємо список файлів з папки
    try:
        results = service.files().list(
            q=f"'{FOLDER_ID}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size)",
            pageSize=50
        ).execute()
    except Exception as e:
        print(f"❌ ПОМИЛКА під час отримання списку файлів з Google Диску: {e}")
        return
    
    gdrive_files = results.get('files', [])
    gdrive_files = [f for f in gdrive_files if f['id'] != TRASH_FOLDER_ID]
    if not gdrive_files:
        print("Папка порожня. Немає контенту для публікації.")
        return

    print(f"Знайдено файлів у папці: {len(gdrive_files)}")
    processed_items = []
    os.makedirs('downloaded', exist_ok=True)
    
    for f in gdrive_files:
        mime_type = f['mimeType']
        lower_name = f['name'].lower()
        
        # Перевірка на підтримувані медіа-розширення
        is_valid_media = mime_type.startswith(('image/', 'video/')) or lower_name.endswith(VALID_EXTENSIONS)

        if not is_valid_media:
            print(f"⏭️ Пропускаємо непідтримуваний файл: {f['name']} ({mime_type})")
            continue
                   
        local_path = os.path.join('downloaded', f['name'])
        print(f"Завантаження {f['name']}...")
        
        # Завантажуємо файл
        try:
            request = service.files().get_media(fileId=f['id'])
            fh = io.FileIO(local_path, 'wb')
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            fh.close()
        except Exception as e:
            print(f"❌ Не вдалося завантажити файл {f['name']}: {e}")
            continue

        # Витяг первинних метаданих з файлу
        # Визначення дати та геолокації
        meta_date, lat, lon = None, None, None
        
        # Обробка зображень
        if mime_type.startswith('image/') or lower_name.endswith(('.heic', '.heif', '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff')):
            meta_date, lat, lon = get_exif_data(local_path)
        # Обробка відео через ffprobe
        elif mime_type.startswith('video/') or lower_name.endswith(('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.mpeg', '.mpg')):
            meta_date, lat, lon = get_video_metadata(local_path)

        # --- ІНТЕЛЕКТУАЛЬНИЙ БЛОК ВАЛІДАЦІЇ ДАТИ ---
        final_dt = None
        now = datetime.now()

        # Крок 1: Спроба розпарсити дату з метаданих файлу (EXIF / ffprobe)
        if meta_date:
            try:
                dt_parsed = datetime.strptime(meta_date, '%Y:%m:%d %H:%M:%S')
                # Валідація: дата має бути від 2010 року і не з майбутнього
                if dt_parsed.year >= 2010 and dt_parsed <= now:
                    final_dt = dt_parsed
                else:
                    print(f"⚠️ Метадані файлу {f['name']} містять нелогічну дату: {meta_date}. Шукаємо заміну на Диску.")
            except:
                pass
            
        # Крок 2: Резервний аналіз дат створення/зміни на самому Google Диску
        if not final_dt:
            try:
                # Обрізаємо до секунд [:19], щоб уникнути проблем із мілісекундами
                dt_created = datetime.strptime(f['createdTime'][:19], '%Y-%m-%dT%H:%M:%S')
                dt_modified = datetime.strptime(f['modifiedTime'][:19], '%Y-%m-%dT%H:%M:%S')
                
                # Беремо найстарішу з двох дат
                earliest_gdrive = min(dt_created, dt_modified)
                
                # Валідація дати з Диску
                if earliest_gdrive.year >= 2010 and earliest_gdrive <= now:
                    final_dt = earliest_gdrive
                else:
                    print(f"⚠️ Дати на Диску для {f['name']} за межами логіки (2010-сьогодні).")
            except Exception as e:
                print(f"⚠️ Помилка зчитування дат з Диску для {f['name']}: {e}")

        # Крок 3: Якщо абсолютно всі дати пошкоджені або нелогічні — ставимо сьогоднішню
        if not final_dt:
            print(f"🛑 Не вдалося знайти адекватну дату для {f['name']}. Присвоєно поточну дату.")
            final_dt = now
            
        file_date = final_dt.strftime('%d.%m.%Y')
        # -------------------------------------------
        
        location = None
        if lat and lon:
            time.sleep(1) # Захист від блокування лімітів OSM Nominatim
            location = get_location_name(lat, lon)
            
        # 2. Конвертація форматів для 100% сумісності з Telegram альбомами

        # Випадок А: Це анімація GIF
        if mime_type == 'image/gif' or lower_name.endswith('.gif'):
            mp4_path = os.path.join('downloaded', f['name'].rsplit('.', 1)[0] + '_gif.mp4')
            gif_to_mp4(local_path, mp4_path)
            if os.path.exists(mp4_path):
                os.remove(local_path)
                local_path = mp4_path
                mime_type = 'video/mp4'
                
        # Випадок Б: Це iPhone фото (HEIC)
        elif mime_type in ['image/heic', 'image/heif'] or lower_name.endswith(('.heic', '.heif')):
            print(f"📸 Конвертуємо HEIC-фото {f['name']} в JPG для Telegram...")
            jpg_path = os.path.join('downloaded', f['name'].rsplit('.', 1)[0] + '.jpg')
            try:
                with Image.open(local_path) as img:
                    img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
                os.remove(local_path)
                local_path = jpg_path
                mime_type = 'image/jpeg'
            except Exception as e:
                print(f"❌ Помилка конвертації HEIC: {e}")
                continue

        # Випадок В: Це відео (Не MP4 або більше 49MB)
        elif mime_type.startswith('video/') or lower_name.endswith(('.mov', '.avi', '.mkv', '.3gp', '.mpeg', '.mpg', '.swf')):
            is_large = os.path.getsize(local_path) > 49 * 1024 * 1024
            is_not_mp4 = not lower_name.endswith('.mp4') or mime_type != 'video/mp4' or lower_name.endswith('.swf')
            
            if is_large or is_not_mp4:
                compressed_path = os.path.join('downloaded', 'opt_' + f['name'].rsplit('.', 1)[0] + '.mp4')
                convert_to_mp4(local_path, compressed_path)
                if os.path.exists(compressed_path) and os.path.getsize(compressed_path) <= 49 * 1024 * 1024:
                    os.remove(local_path)
                    local_path = compressed_path
                    mime_type = 'video/mp4'
                elif is_large:
                    print(f"❌ Не вдалося оптимізувати відео {f['name']} під ліміт 50MB, пропускаємо.")
                    continue
                
        processed_items.append({
            'id': f['id'],
            'name': f['name'],
            'mime': mime_type,
            'local_path': local_path,
            'date': file_date,
            'location': location or "Невідоме місце"
        })

    # Розумне групування за [Дата + Місце]
    groups = {}
    for item in processed_items:
        key = (item['date'], item['location'])
        if key not in groups:
            groups[key] = []
        groups[key].append(item)
        
    # Публікація суворо ОДНОГО альбому (макс 10 елементів) за один запуск скрипта
    for (date, loc), items in groups.items():
        batch = items[:10]  # Беремо максимум перші 10 штук з цієї групи
        
        caption = f"📅 {date}"
        if loc != "Невідоме місце":
            caption += f" 📍 {loc}"
                
        print(f"Надсилання альбому для {caption} (Елементів: {len(batch)})...")
        success = send_media_group(batch, caption, CHAT_ID)
            
        if success:
            print(f"✅ Успішно надіслано. Переміщаємо файли в папку Кошик...")
            for uploaded_item in batch:
                try:
                    service.files().update(
                        fileId=uploaded_item['id'],
                        addParents=TRASH_FOLDER_ID,
                        removeParents=FOLDER_ID,
                        fields='id, parents'
                    ).execute()
                    print(f"📦 Переміщено: {uploaded_item['name']}")
                except Exception as e:
                    print(f"❌ Не вдалося перемістити {uploaded_item['name']}: {e}")
        else:
            print(f"Помилка публікації альбому для {caption}")
            
        print(f"🏁 Першу партію медіа ({len(batch)} шт.) успішно оброблено. Завершуємо роботу.")
        return
if __name__ == '__main__':
    main()
