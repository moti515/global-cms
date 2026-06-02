import os
import json
import sys
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

# Чітка відповідність: "ID Кореневої папки": "Назва аркуша в Таблиці"
ROOT_FOLDERS_MAPPING = {
    '0B2maH6ay7dwhNDRBZnFJbnR2VDA': "П'ятниця",
    # 'ID_НОВОЇ_КОРЕНЕВОЇ_ПАПКИ': "Меблевий гумор" # Сюди додаватимете нові гілки
}

# Базові папки для першого (стартового) заповнення таблиці, якщо вона абсолютно порожня
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
    {"id": "1lRzoFfv3wIrAKE5pZatqeukpmQXJym9-", "name": "Середа", "tab": "П'ятниця"},
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
    
    if title not in sheets:
        print(f"🔹 Аркуша '{title}' не знайдено. Створюємо...")
        body = {'requests': [{'addSheet': {'properties': {'title': title}}}]}
        sheets_service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
        
    check_headers = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{title}'!A1:1"
    ).execute()
    
    if not check_headers.get('values'):
        print(f"📝 Прописуємо структуру заголовків для '{title}'...")
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID, range=f"'{title}'!A1",
            valueInputOption='RAW', body={'values': [headers]}
        ).execute()

def init_settings_sheet(sheets_service):
    """Ініціалізує службовий аркуш конфігурації папок"""
    headers = ["ID Підпапки", "Назва підпапки", "Цільовий Аркуш", "Активно (ТАК/НІ)", "Правило обробки"]
    ensure_sheet_exists(sheets_service, "⚙️ Налаштування Папок", headers)
    
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

def discover_new_folders(drive_service, sheets_service):
    """Сканує УСІ кореневі папки й автоматично прив'язує нові підпапки до правильних аркушів"""
    print("🔍 Сканування Диску на наявність нових підпапок...")
    
    res = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2:A"
    ).execute()
    existing_ids = {row[0] for row in res.get('values', []) if row}
    
    new_rows = []
    
    for root_id, target_tab in ROOT_FOLDERS_MAPPING.items():
        page_token = None
        while True:
            q = f"'{root_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            try:
                res_drive = drive_service.files().list(
                    q=q, fields="nextPageToken, files(id, name)", pageSize=100, pageToken=page_token
                ).execute()
                
                for folder in res_drive.get('files', []):
                    if folder['id'] not in existing_ids:
                        new_rows.append([
                            folder['id'], 
                            folder['name'], 
                            target_tab,  # Автоматично підставляє назву аркуша зі словника ROOT_FOLDERS_MAPPING
                            "НІ",        # Вимкнено для вашого контролю
                            "⚠️ НОВА ПАПКА! Перевірте та змініть статус на ТАК"
                        ])
                        
                page_token = res_drive.get('nextPageToken')
                if not page_token:
                    break
            except Exception as e:
                print(f"⚠️ Не вдалося просканувати кореневу папку {root_id}: {e}")
                break
            
    if new_rows:
        print(f"✨ Знайдено {len(new_rows)} нових папок! Додаємо в налаштування...")
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2",
            valueInputOption='RAW', body={'values': new_rows}
        ).execute()
    else:
        print("👍 Нових підпапок на Диску не виявлено.")

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
    
    raw_sheet = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A2:E"
    ).execute()
    
    sheet_rows = raw_sheet.get('values', [])
    sheet_map = {row[0]: {"idx": i + 2, "data": row} for i, row in enumerate(sheet_rows) if row and len(row) > 0}
    
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

    rows_to_append = []
    ids_to_delete = []
    
    for f_id, f_info in drive_files.items():
        if f_id not in sheet_map:
            rows_to_append.append([f_id, f_info['name'], f_info['category'], 0, 0])
            
    for f_id, sheet_info in sheet_map.items():
        if f_id not in drive_files:
            ids_to_delete.append(sheet_info['idx'])

    if rows_to_append:
        print(f"➕ {tab_name}: Додаємо {len(rows_to_append)} нових файлів.")
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A2",
            valueInputOption='RAW', body={'values': rows_to_append}
        ).execute()

    if ids_to_delete:
        print(f"❌ {tab_name}: Видаляємо {len(ids_to_delete)} застарілих рядків.")
        requests = []
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
    meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for s in meta.get('sheets', []):
        if s['properties']['title'] == title:
            return s['properties']['sheetId']
    return 0

def main():
    print("🔄 Запуск інтелектуального синхронізатора реєстру контенту...")
    drive, sheets = get_services()
    
    # 1. Конфігуруємо службовий аркуш налаштувань папок
    init_settings_sheet(sheets)
    
    # 2. ВИКЛИК АВТОВИЯВЛЕННЯ (Це було пропущено!)
    discover_new_folders(drive, sheets)
    
    # 3. Отримуємо папки в роботі (де Активно = ТАК)
    active_folders = load_active_folders(sheets)
    print(f"📂 Знайдено активних папок для сканування: {len(active_folders)}")
    
    unique_tabs = list(set([f['tab'] for f in active_folders]))
    
    for tab in unique_tabs:
        print(f"🗂️ Синхронізація аркуша '{tab}'...")
        sync_tab_with_drive(drive, sheets, tab, active_folders)
        
    print(f"✨ Синхронізацію успішно завершено. Все під контролем!")

if __name__ == '__main__':
    main()
