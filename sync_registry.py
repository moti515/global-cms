import os
import json
import sys
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

# Базові папки для стартової ініціалізації службового аркуша
INITIAL_FOLDERS = [
    {"id": "1k6Fe4nWpkQixHHoOqKyMusFowcnE7frX", "name": "П'ятниця", "tab": "П'ятниця"},
    {"id": "1sIIKtq2bVK4qpkw2rlXIBtQDrgHinqV2", "name": "Чорна п'ятниця", "tab": "П'ятниця"},
    {"id": "1g-lplYIH5DoDd8gm309oz5cweSLCIVIG", "name": "1 квітня", "tab": "П'ятниця"},
    {"id": "1MNER7syW6ZjkwVGZX6F7qxUqJ5qBTZVK", "name": "23 лютого", "tab": "П'ятниця"},
    {"id": "1RWDqywey13WPn0otnJdBd1BCrM-rAQp-", "name": "8 Березня", "tab": "П'ятниця"},
    {"id": "1MpBIUHqo-6T8PVU_OlduQn_zLiz2fmEL", "name": "3 вересня", "tab": "П'ятниця"},
    {"id": "12OnhHe1_2rNrxCrCIYug_LkgSmnIwLQo", "name": "31 травня", "tab": "П'ятниця"},
    {"id": "1gclpu7LEqCaQZqmtdpVLzFeQiRgerQQx", "name": "Новий рік", "tab": "П'ятниця"},
    {"id": "1HOy6tugF53KQeoxgtAIdstHIE54UpzJp", "name": "Неділя", "tab": "П'ятниця"},
    {"id": "1zL9q6Lcsz9PAROtWAXe5Ihmtwmx879T4", "name": "Субота", "tab": "П'ятниця"},
    {"id": "1q9tO2_tmlacquT6149bFmF05NHMItbUh", "name": "Четвер", "tab": "П'ятниця"},
    {"id": "1lRzoFfv3wIrAKE5pZatqeukpmQXJym9-", "name": "Середа", "tab": "П'ytниця"},
    {"id": "1f7EIhQZFhMo83vmjfwtw98_QMWaLTYnE", "name": "Вівторок", "tab": "П'ятниця"},
    {"id": "10xYhETrClFEm91oWOtY_NgfMt6ZsL9fq", "name": "Різне", "tab": "П'ятниця"}
]

def get_services():
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
    drive = build('drive', 'v3', credentials=creds)
    sheets = build('sheets', 'v4', credentials=creds)
    return drive, sheets

def ensure_sheet_exists(sheets_service, title, headers):
    """Перевіряє наявність аркуша (створює якщо немає) та НАЯВНІСТЬ ЗАГОЛОВКІВ (додає якщо порожньо)"""
    meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = [s['properties']['title'] for s in meta.get('sheets', [])]
    
    # Крок 1: Якщо аркуша взагалі немає в книзі — створюємо його
    if title not in sheets:
        print(f"🔹 Аркуша '{title}' не знайдено. Створюємо...")
        body = {'requests': [{'addSheet': {'properties': {'title': title}}}]}
        sheets_service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
        
    # Крок 2: Перевіряємо, чи є заголовки у першому рядку (A1:1)
    check_headers = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{title}'!A1:1"
    ).execute()
    
    # Якщо рядок заголовків порожній — записуємо їх туди
    if not check_headers.get('values'):
        print(f"📝 Заголовки відсутні або аркуш порожній. Прописуємо структуру для '{title}'...")
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{title}'!A1",
            valueInputOption='RAW',
            body={'values': [headers]}
        ).execute()

def init_settings_sheet(sheets_service):
    """Ініціалізує службовий аркуш конфігурації папок"""
    headers = ["ID Підпапки", "Назва підпапки", "Цільовий Аркуш", "Активно (ТАК/НІ)", "Правило обробки"]
    ensure_sheet_exists(sheets_service, "⚙️ Налаштування Папок", headers)
    
    # Перевіряємо наявність даних (починаючи з рядка 2)
    res = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2:A"
    ).execute()
    
    if not res.get('values'):
        print("🌱 Заповнення службового аркуша базовими папками...")
        rows = [[f['id'], f['name'], f['tab'], "ТАК", "Автоматична обробка"] for f in INITIAL_FOLDERS]
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2",
            valueInputOption='RAW', body={'values': rows}
        ).execute()

def load_active_folders(sheets_service):
    """Завантажує список папок, які користувач позначив як Активно = ТАК"""
    res = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2:E"
    ).execute()
    
    folders = []
    for row in res.get('values', []):
        if len(row) >= 4 and row[3] == "ТАК":
            folders.append({"id": row[0], "name": row[1], "tab": row[2]})
    return folders

def sync_tab_with_drive(drive_service, sheets_service, tab_name, active_folders):
    """Синхронізує конкретний аркуш із файлами з відповідних папок Google Drive"""
    headers = ["ID Файлу (Google Drive)", "Назва файлу", "Категорія (Папка)", "Публікацій у Пост", "Публікацій у Сторіс"]
    ensure_sheet_exists(sheets_service, tab_name, headers)
    
    # 1. Зчитуємо поточний стан аркуша (дані починаються з рядка 2)
    raw_sheet = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A2:E"
    ).execute()
    
    sheet_rows = raw_sheet.get('values', [])
    sheet_map = {row[0]: {"idx": i + 2, "data": row} for i, row in enumerate(sheet_rows) if row and len(row) > 0}
    
    # 2. Збираємо ВСІ актуальні файли з Google Drive для цього аркуша
    drive_files = {}
    for folder in active_folders:
        if folder['tab'] != tab_name:
            continue
            
        page_token = None
        while True:
            q = f"'{folder['id']}' in parents and trashed = false"
            res = drive_service.files().list(
                q=q, fields="nextPageToken, files(id, name)", pageSize=1000, pageToken=page_token
            ).execute()
            
            for f in res.get('files', []):
                drive_files[f['id']] = {"name": f['name'], "category": folder['name']}
                
            page_token = res.get('nextPageToken')
            if not page_token:
                break

    # 3. Обчислюємо дельту (диференціал)
    rows_to_append = []
    ids_to_delete = []
    
    for f_id, f_info in drive_files.items():
        if f_id not in sheet_map:
            # Новий файл заносимо з нульовими лічильниками
            rows_to_append.append([f_id, f_info['name'], f_info['category'], 0, 0])
            
    for f_id, sheet_info in sheet_map.items():
        if f_id not in drive_files:
            # Файлу більше немає на Диску — маркуємо рядок на видалення
            ids_to_delete.append(sheet_info['idx'])

    # 4. Пакетне виконання операцій
    if rows_to_append:
        print(f"➕ {tab_name}: Додаємо {len(rows_to_append)} нових файлів.")
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A2",
            valueInputOption='RAW', body={'values': rows_to_append}
        ).execute()

    if ids_to_delete:
        print(f"❌ {tab_name}: Видаляємо {len(ids_to_delete)} застарілих рядків.")
        requests = []
        # Сортуємо індекси з кінця, щоб видалення не зсувало номери попередніх рядків
        for row_idx in sorted(ids_to_delete, reverse=True):
            requests.append({
                "deleteDimension": {
                    "range": {
                        "sheetId": get_sheet_id(sheets_service, tab_name),
                        "dimension": "ROWS",
                        "startIndex": row_idx - 1,
                        "endIndex": row_idx
                    }
                }
            })
        sheets_service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": requests}).execute()

def get_sheet_id(sheets_service, title):
    meta =
