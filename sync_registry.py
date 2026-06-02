import os
import json
import sys
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

# Базові папки, які ви надали (для стартової ініціалізації службового аркуша)
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
    """Перевіряє наявність аркуша, створює його та додає заголовки, якщо його немає"""
    meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = [s['properties']['title'] for s in meta.get('sheets', [])]
    
    if title not in sheets:
        print(f"🔹 Створення нового аркуша: {title}")
        body = {'requests': [{'addSheet': {'properties': {'title': title}}}]}
        sheets_service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
        
        # Додаємо хедери
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
    
    # Перевіряємо, чи там порожньо (крім заголовків)
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
    
    # 1. Зчитуємо поточний стан аркуша
    raw_sheet = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A2:E"
    ).execute()
    
    sheet_rows = raw_sheet.get('values', [])
    # Створюємо мапу {file_id: {row_index, data}} для миттєвого пошуку O(1)
    sheet_map = {row[0]: {"idx": i + 2, "data": row} for i, row in enumerate(sheet_rows) if row}
    
    # 2. Збираємо ВСІ актуальні файли з усіх активних папок, що належать цьому аркушу
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
    
    # На заміну (оновлення назви, якщо змінилася, або категорія)
    for f_id, f_info in drive_files.items():
        if f_id not in sheet_map:
            # Новий файл -> ставимо лічильники на 0
            rows_to_append.append([f_id, f_info['name'], f_info['category'], 0, 0])
            
    for f_id, sheet_info in sheet_map.items():
        if f_id not in drive_files:
            # Файл видалено з Драйву -> треба видалити рядок
            ids_to_delete.append(sheet_info['idx'])

    # 4. Виконуємо операції запису/видалення
    # Додавання нових файлів пакетом (Batch Append)
    if rows_to_append:
        print(f"➕ {tab_name}: Додаємо {len(rows_to_append)} нових файлів.")
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A2",
            valueInputOption='RAW', body={'values': rows_to_append}
        ).execute()

    # Очищення видалених файлів (сортуємо індекси з кінця до початку, щоб не зсунути номери рядків!)
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
    
    # Конфігуруємо службовий аркуш
    init_settings_sheet(sheets)
    
    # Отримуємо папки в роботі
    active_folders = load_active_folders(sheets)
    print(f"📂 Знайдено активних папок для сканування: {len(active_folders)}")
    
    # Визначаємо унікальні назви аркушів, які треба оновити
    unique_tabs = list(set([f['tab'] for f in active_folders]))
    
    for tab in unique_tabs:
        print(f"🗂️ Синхронізація аркуша '{tab}'...")
        sync_tab_with_drive(drive, sheets, tab, active_folders)
        
    print(f"✨ Синхронізацію успішно завершено.")

if __name__ == '__main__':
    main()
