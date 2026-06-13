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

# Реєструємо підтримку HEIC для Pillow (айфонівські фото)
register_heif_opener()

# Константи конфігурації
SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

FURNITURE_ROOT_ID = '1N_UX-dyhHq6fqHmXFxSQ170RWuDWIVa4'
TEMPORARY_FOLDER_ID = '1BlPC3ua00pHnqdwpy2EA3EzOA-tCmt2N'

TAB_NAME = "Меблі"
SETTINGS_TAB_NAME = "⚙️ Налаштування Папок"
VALID_EXTENSIONS = (
    '.3gp', '.avi', '.gif', '.heic', '.heif', '.jpeg', '.jpg', 
    '.mkv', '.mov', '.mp4', '.mpeg', '.mpg', '.tif', '.tiff', '.webp', '.png'
)

def get_services():
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
    drive = build('drive', 'v3', credentials=creds)
    sheets = build('sheets', 'v4', credentials=creds)
    return drive, sheets

def ensure_sheet_exists(sheets_service, title, headers):
    meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = [s['properties']['title'] for s in meta.get('sheets', [])]
    if title not in sheets:
        body = {'requests': [{'addSheet': {'properties': {'title': title}}}]}
        sheets_service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
    check_headers = sheets_service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{title}'!A1:1").execute()
    if not check_headers.get('values'):
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID, range=f"'{title}'!A1",
            valueInputOption='RAW', body={'values': [headers]}
        ).execute()

def get_sheet_id(sheets_service, title):
    meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for s in meta.get('sheets', []):
        if s['properties']['title'] == title: return s['properties']['sheetId']
    return 0

# --- ІНТЕЛЕКТУАЛЬНИЙ БЛОК АНАЛІЗУ МЕТАДАНИХ ---

def extract_date_from_filename(filename):
    """Каскадний пошук дати в імені файлу за шаблонами YYYY-MM-DD чи DD-MM-YYYY."""
    current_year = datetime.now().year
    min_year = 2000
    name_part = filename.rsplit('.', 1)[0]

    # 1. Шаблон YYYY-MM-DD або YYYYMMDD
    match_yyyy_mm_dd = re.search(r'\b(\d{4})[-._]?(0[1-9]|1[0-2])[-._]?([0-2]\d|3[01])', name_part)
    if match_yyyy_mm_dd:
        year, month, day = match_yyyy_mm_dd.groups()
        try:
            dt = datetime(int(year), int(month), int(day))
            if min_year <= dt.year <= current_year: return dt
        except ValueError: pass

    # 2. Шаблон DD-MM-YYYY або DDMMYYYY
    match_dd_mm_yyyy = re.search(r'\b(0[1-9]|[12]\d|3[01])[-._]?(0[1-9]|1[0-2])[-._]?(\d{4})', name_part)
    if match_dd_mm_yyyy:
        day, month, year = match_dd_mm_yyyy.groups()
        try:
            dt = datetime(int(year), int(month), int(day))
            if min_year <= dt.year <= current_year: return dt
        except ValueError: pass
    return None

def get_exif_data(image_path):
    """Витягує дату зйомки та GPS координати з фотографій (включаючи суб-блоки IFD)."""
    date_str, lat, lon = None, None, None
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif: return date_str, lat, lon
            
            # Пошук DateTimeOriginal у суб-блоці EXIF (ID: 34665)
            exif_ifd = exif.get_ifd(34665)
            if exif_ifd:
                for tag, value in exif_ifd.items():
                    if TAGS.get(tag) == 'DateTimeOriginal':
                        date_str = value
                        break
            
            # Резервний пошук в основній структуви EXIF
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
        print(f"⚠️ Помилка читання EXIF для {image_path}: {e}")
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
                except: pass
            
            loc_str = tags.get('location') or tags.get('location-eng')
            if loc_str:
                match = re.match(r'([+-]\d+\.\d+)([+-]\d+\.\d+)', loc_str)
                if match:
                    lat, lon = float(match.group(1)), float(match.group(2))
    except Exception as e:
        print(f"⚠️ Помилка відео-метаданих для {video_path}: {e}")
    return date_str, lat, lon

def get_location_data(lat, lon):
    """Повертає точне місце та місто для кластеризації українською мовою."""
    if lat is None or lon is None: 
        return "Невідоме місце", "Невідоме місто"
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=15&accept-language=uk"
        headers = {'User-Agent': 'FurnitureCMS_Bot_2026'}
        res = requests.get(url, headers=headers, timeout=10).json()
        address = res.get('address', {})
        
        # 1. Точний об'єкт (назва, район або вулиця)
        exact_place = (
            address.get('tourism') or address.get('amenity') or 
            address.get('historic') or address.get('suburb') or 
            address.get('city') or address.get('town') or address.get('village')
        )
        country = address.get('country')
        display_location = f"{exact_place}, {country}" if exact_place and country else (exact_place or country or "Невідоме місце")
        
        # 2. Стабільна назва населеного пункту для групування
        group_place = address.get('city') or address.get('town') or address.get('village') or address.get('county')
        group_location = f"{group_place}, {country}" if group_place and country else (group_place or country or "Невідоме місто")
        
        return display_location, group_location
    except Exception as e:
        print(f"⚠️ Помилка геокодування OSM: {e}")
        return "Невідоме місце", "Невідоме місто"

def get_intellectual_date(f_info, meta_date):
    """Каскадне визначення реальної дати створення об'єкта з валідацією року."""
    now = datetime.now()
    min_year = 2000

    # Крок 1: Спроба дістати дату з імені файлу
    fn_date = extract_date_from_filename(f_info['name'])
    if fn_date:
        return fn_date

    # Крок 2: Парсинг дати з метаданих файлу (EXIF / Video)
    if meta_date:
        for date_format in ('%Y:%m:%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
            try:
                clean_meta = str(meta_date).strip()[:19].replace('T', ' ')
                clean_fmt = date_format.replace('T', ' ')
                dt_parsed = datetime.strptime(clean_meta, clean_fmt)
                if min_year <= dt_parsed.year <= now.year:
                    return dt_parsed
            except: continue

    # Крок 3: Резервний аналіз дат з Google Drive
    try:
        created_raw = f_info.get('createdTime')
        modified_raw = f_info.get('modifiedTime')
        dt_created = datetime.strptime(created_raw[:19], '%Y-%m-%dT%H:%M:%S') if created_raw else None
        dt_modified = datetime.strptime(modified_raw[:19], '%Y-%m-%dT%H:%M:%S') if modified_raw else None
        
        valid_gdrive_dates = [d for d in [dt_created, dt_modified] if d and min_year <= d.year <= now.year]
        if valid_gdrive_dates:
            return min(valid_gdrive_dates)
    except: pass

    # Крок 4: Фолбек на поточну дату
    return now

# --- БЛОК СИНХРОНІЗАЦІЇ СТРУКТУРИ ФАЙЛІВ ---

def scan_folders_structure(drive_service, folder_id, top_category, drive_files_dict):
    """Рекурсивно знаходить файли, зберігаючи назву підпапки 1-го рівня як категорію."""
    if folder_id == TEMPORARY_FOLDER_ID: return
    page_token = None
    while True:
        q = f"'{folder_id}' in parents and trashed = false"
        res = drive_service.files().list(
            q=q, 
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime)", 
            pageSize=1000, 
            pageToken=page_token
        ).execute()
        
        for f in res.get('files', []):
            if f['mimeType'] == 'application/vnd.google-apps.folder':
                next_category = f['name'] if folder_id == FURNITURE_ROOT_ID else top_category
                scan_folders_structure(drive_service, f['id'], next_category, drive_files_dict)
            else:
                lower_name = f['name'].lower()
                if f['mimeType'].startswith(('image/', 'video/')) or lower_name.endswith(VALID_EXTENSIONS):
                    drive_files_dict[f['id']] = {
                        "name": f['name'], 
                        "mime": f['mimeType'], 
                        "category": top_category,
                        "createdTime": f.get('createdTime'),
                        "modifiedTime": f.get('modifiedTime')
                    }
        page_token = res.get('nextPageToken')
        if not page_token: break

def download_and_extract_meta(drive_service, file_id, f_info):
    """Тимчасово завантажує файл для витягування розширених метаданих та локації."""
    file_name = f_info['name']
    mime_type = f_info['mime']
    
    os.makedirs('temp_meta', exist_ok=True)
    local_path = os.path.join('temp_meta', file_name)
    
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.FileIO(local_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done: _, done = downloader.next_chunk()
        fh.close()
    except Exception as e:
        print(f"⚠️ Помилка завантаження файлу {file_name}: {e}")
        return get_intellectual_date(f_info, None).strftime('%d.%m.%Y'), "Невідоме місце", "Невідоме місто"

    meta_date, lat, lon = None, None, None
    lower_name = file_name.lower()
    
    if mime_type.startswith('image/') or lower_name.endswith(('.heic', '.heif', '.jpg', '.jpeg', '.png', '.webp')):
        meta_date, lat, lon = get_exif_data(local_path)
    elif mime_type.startswith('video/') or lower_name.endswith(('.mp4', '.mov', '.avi', '.mkv')):
        meta_date, lat, lon = get_video_metadata(local_path)

    # Каскадне визначення дати
    file_dt = get_intellectual_date(f_info, meta_date)
    file_date_str = file_dt.strftime('%d.%m.%Y')
    
    # Геокодування (Повертає 2 значення: точне місце та місто для групування)
    if lat and lon:
        time.sleep(1)  # Захист лімітів OSM Nominatim
    display_loc, group_loc = get_location_data(lat, lon)

    if os.path.exists(local_path): os.remove(local_path)
    return file_date_str, display_loc, group_loc

def main():
    print("🚀 Старт інтелектуальної синхронізації меблевого реєстру...")
    drive, sheets = get_services()
    
    ensure_sheet_exists(sheets, SETTINGS_TAB_NAME, ["ID Підпапки", "Назва підпапки", "Цільовий Аркуш", "Активно для мене (ТАК/НІ)", "Поточний статус / Правило"])
    
    # Додано 9-ту колонку "Місто (Групування)" для побудови зведених таблиць або фільтрації контенту
    content_headers = [
        "ID Файлу (Google Drive)", "Назва файлу", "Категорія (Папка)", 
        "Публікацій в Інстаграм Пост", "Публікацій в Інстаграм Сторіз", "Публікацій в Фейсбук Пост", 
        "Дата", "Місцеположення", "Місто (Групування)"
    ]
    ensure_sheet_exists(sheets, TAB_NAME, content_headers)
    
    # Зчитуємо налаштування папок
    res_service = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{SETTINGS_TAB_NAME}'!A2:E").execute()
    service_map = {row[0]: row for row in res_service.get('values', []) if row and len(row) > 0}

    # Збір структури папок 1-го рівня
    current_drive_subfolders = {}
    page_token = None
    while True:
        q = f"'{FURNITURE_ROOT_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        res_drive = drive.files().list(q=q, fields="nextPageToken, files(id, name)", pageSize=100, pageToken=page_token).execute()
        for folder in res_drive.get('files', []):
            if folder['id'] == TEMPORARY_FOLDER_ID: continue
            current_drive_subfolders[folder['id']] = folder['name']
        page_token = res_drive.get('nextPageToken')
        if not page_token: break

    # Синхронізація налаштувань папок
    for sub_id, sub_name in current_drive_subfolders.items():
        if sub_id not in service_map:
            service_map[sub_id] = [sub_id, sub_name, TAB_NAME, "НІ", "✨ Нова папка меблів!"]
        else: service_map[sub_id][1] = sub_name

    # Збір всіх файлів на Диску
    drive_files = {}
    scan_folders_structure(drive, FURNITURE_ROOT_ID, "Різне", drive_files)

    # Зчитування поточної таблиці "Меблі" (Збільшено діапазон до стовпця I)
    raw_sheet = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{TAB_NAME}'!A2:I").execute()
    sheet_rows = raw_sheet.get('values', [])
    sheet_map = {row[0]: {"idx": i + 2, "data": row} for i, row in enumerate(sheet_rows) if row and len(row) > 0}
    
    rows_to_append = []
    ids_to_delete = []
    
    # Обробка дельти
    for f_id, f_info in drive_files.items():
        if f_id not in sheet_map:
            print(f"🆕 Знайдено новий файл: {f_info['name']}. Каскадний аналіз дати та локації...")
            f_date, f_loc, f_group = download_and_extract_meta(drive, f_id, f_info)
            
            rows_to_append.append([
                f_id, f_info['name'], f_info['category'], 
                0, 0, 0, 
                f_date, f_loc, f_group
            ])
        else:
            existing = sheet_map[f_id]
            if existing["data"][1] != f_info['name'] or existing["data"][2] != f_info['category']:
                sheets.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID, range=f"'{TAB_NAME}'!B{existing['idx']}:C{existing['idx']}",
                    valueInputOption='RAW', body={'values': [[f_info['name'], f_info['category']]]}
                ).execute()

    for f_id, sheet_info in sheet_map.items():
        if f_id not in drive_files: ids_to_delete.append(sheet_info['idx'])

    # Запис дельти в таблицю
    if rows_to_append:
        print(f"➕ Додаємо {len(rows_to_append)} нових файлів у таблицю.")
        sheets.spreadsheets().values().append(spreadsheetId=SPREADSHEET_ID, range=f"'{TAB_NAME}'!A2", valueInputOption='RAW', body={'values': rows_to_append}).execute()

    if ids_to_delete:
        print(f"❌ Видаляємо {len(ids_to_delete)} рядків застарілих файлів.")
        requests_list = []
        for row_idx in sorted(ids_to_delete, reverse=True):
            requests_list.append({"deleteDimension": {"range": {"sheetId": get_sheet_id(sheets, TAB_NAME), "dimension": "ROWS", "startIndex": row_idx - 1, "endIndex": row_idx}}})
        sheets.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": requests_list}).execute()

    # Збереження налаштувань папок
    updated_service_values = list(service_map.values())
    sheets.spreadsheets().values().clear(spreadsheetId=SPREADSHEET_ID, range=f"'{SETTINGS_TAB_NAME}'!A2:E").execute()
    if updated_service_values:
        sheets.spreadsheets().values().update(spreadsheetId=SPREADSHEET_ID, range=f"'{SETTINGS_TAB_NAME}'!A2", valueInputOption='RAW', body={'values': updated_service_values}).execute()
        
    print("✨ Реєстр меблів успішно синхронізовано та збагачено метаданими!")

if __name__ == '__main__':
    main()
