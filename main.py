import os
import json
import io
import subprocess
from datetime import datetime
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

# Налаштування
FOLDER_ID = '1MFTlnTVwOuPysxtdS-FzQSZAhFnbzTwz'
SCOPES = ['https://www.googleapis.com/auth/drive']

def get_gdrive_service():
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

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
        print(f"Помилка читання EXIF для {image_path}: {e}")
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
        print(f"Помилка геокодування: {e}")
    return None

def compress_video(input_path, output_path):
    """Стискає відео через FFmpeg, якщо воно перевищує ліміт Telegram (50MB)"""
    print(f"Стискаємо відео {input_path} під ліміт 50MB...")
    # Налаштування для безпечного стиснення в формат mp4 (H.264 + AAC)
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-vcodec', 'libx264', '-crf', '28',
        '-preset', 'faster', '-acodec', 'aac',
        '-b:a', '128k', output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def send_media_group(media_batch, caption):
    """Відправляє групу медіафайлів (до 10 штук) в Telegram"""
    token = os.environ['TELEGRAM_BOT_TOKEN']
    chat_id = os.environ['TELEGRAM_CHAT_ID']
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
        return res.get('ok', False)
    except Exception as e:
        print(f"Помилка відправки в Telegram: {e}")
        for f in files.values(): f.close()
        return False

def main():
    service = get_gdrive_service()
    
    # Отримуємо список файлів з папки
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and trashed = false",
        fields="nextPageToken, files(id, name, mimeType, createdTime, size)",
        pageSize=50
    ).execute()
    
    gdrive_files = results.get('files', [])
    if not gdrive_files:
        print("Папка порожня. Немає контенту для публікації.")
        return

    processed_items = []
    os.makedirs('downloaded', exist_ok=True)
    
    for f in gdrive_files:
        if not (f['mimeType'].startswith('image/') or f['mimeType'].startswith('video/')):
            continue
            
        local_path = os.path.join('downloaded', f['name'])
        print(f"Завантаження {f['name']}...")
        
        # Завантажуємо файл
        request = service.files().get_media(fileId=f['id'])
        fh = io.FileIO(local_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.close()
        
        # Аналіз метаданих
        file_date = None
        location = None
        
        if f['mimeType'].startswith('image/'):
            exif_date, lat, lon = get_exif_data(local_path)
            if exif_date:
                try:
                    file_date = datetime.strptime(exif_date, '%Y:%m:%d %H:%M:%S').strftime('%d.%m.%Y')
                except: pass
            if lat and lon:
                location = get_location_name(lat, lon)
                
        # Якщо дату не знайдено в EXIF або це відео, беремо дату створення з Google Диску
        if not file_date:
            dt = datetime.strptime(f['createdTime'], '%Y-%m-%dT%H:%M:%S.%fZ')
            file_date = dt.strftime('%d.%m.%Y')
            
        # Обробка великих відео (> 50 MB)
        if f['mimeType'].startswith('video/') and os.path.getsize(local_path) > 49 * 1024 * 1024:
            compressed_path = os.path.join('downloaded', 'cmp_' + f['name'])
            compress_video(local_path, compressed_path)
            if os.path.exists(compressed_path) and os.path.getsize(compressed_path) <= 49 * 1024 * 1024:
                os.remove(local_path)
                local_path = compressed_path
            else:
                print(f"Не вдалося стиснути {f['name']} нижче 50MB, пропускаємо.")
                continue
                
        processed_items.append({
            'id': f['id'],
            'name': f['name'],
            'mime': f['mimeType'],
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
        
    # Відправка груп (ліміт 10 медіа на один альбом)
    for (date, loc), items in groups.items():
        # Ділимо масив на пачки по 10 файлів
        for i in range(0, len(items), 10):
            batch = items[i:i+10]
            
            caption = f"📅 {date}"
            if loc != "Невідоме місце":
                caption += f" 📍 {loc}"
                
            print(f"Надсилання альбому для {caption} (Елементів: {len(batch)})...")
            success = send_media_group(batch, caption)
            
            if success:
                print("Успішно надіслано. Видаляємо файли з Google Диску...")
                for uploaded_item in batch:
                    try:
                        service.files().delete(fileId=uploaded_item['id']).execute()
                    except Exception as e:
                        print(f"Не вдалося видалити {uploaded_item['name']}: {e}")
            else:
                print(f"Помилка публікації альбому для {caption}")

if __name__ == '__main__':
    main()
