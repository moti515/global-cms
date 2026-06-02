import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

# Ваша головна карта: "ID Кореневої папки на Диску": "Назва аркуша в Таблиці"
ROOT_FOLDERS_MAPPING = {
    '0B2maH6ay7dwhNDRBZnFJbnR2VDA': "П'ятниця",
    # 'ID_НОВОЇ_КОРЕНЕВОЇ_ПАПКИ': "Меблевий гумор"  <- Коли створите нову, просто розкоментуйте і впишіть сюди
}

def get_services():
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
    drive = build('drive', 'v3', credentials=creds)
    sheets = build('sheets', 'v4', credentials=creds)
    return drive, sheets

def ensure_sheet_exists(sheets_service, title, headers):
    """Перевіряє наявність аркуша (створює, якщо немає) та прописує заголовки"""
    meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = [s['properties']['title'] for s in meta.get('sheets', [])]
    
    if title not in sheets:
        print(f"🔹 Аркуша '{title}' не знайдено в книзі. Автоматично створюємо...")
        body = {'requests': [{'addSheet': {'properties': {'title': title}}}]}
        sheets_service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=body).execute()
        
    check_headers = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"'{title}'!A1:1"
    ).execute()
    
    if not check_headers.get('values'):
        print(f"📝 Прописуємо структуру заголовків для аркуша '{title}'...")
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID, range=f"'{title}'!A1",
            valueInputOption='RAW', body={'values': [headers]}
        ).execute()

def get_sheet_id(sheets_service, title):
    meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for s in meta.get('sheets', []):
        if s['properties']['title'] == title:
            return s['properties']['sheetId']
    return 0

def main():
    print("🔄 Запуск абсолютної синхронізації реєстру контенту за вашою логікою...")
    drive, sheets = get_services()
    
    # 1. Створюємо службовий аркуш налаштувань, якщо його немає
    service_headers = ["ID Підпапки", "Назва підпапки", "Цільовий Аркуш", "Активно для мене (ТАК/НІ)", "Поточний статус / Правило"]
    ensure_sheet_exists(sheets, "⚙️ Налаштування Папок", service_headers)
    
    # Зчитуємо поточний вміст службового аркуша для аналізу
    res_service = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2:E"
    ).execute()
    service_rows = res_service.get('values', [])
    
    # Перетворюємо в мапу {folder_id: [дані рядка]} для миттєвого пошуку і модифікації
    service_map = {}
    for row in service_rows:
        if row and len(row) > 0:
            while len(row) < 5:  # Гарантуємо, що в масиві завжди є 5 елементів
                row.append("")
            service_map[row[0]] = row

    # 2. Проходимо циклом по ваших кореневих папках
    for root_id, tab_name in ROOT_FOLDERS_MAPPING.items():
        print(f"\n📁 Обробка кореневої папки: {tab_name} (ID: {root_id})")
        
        # Автоматично створюємо аркуш для цієї кореневої папки, якщо ви його щойно додали в скрипт
        content_headers = ["ID Файлу (Google Drive)", "Назва файлу", "Категорія (Папка)", "Публікацій у Пост", "Публікацій у Сторіс"]
        ensure_sheet_exists(sheets, tab_name, content_headers)
        
        # Скануємо Диск: знаходимо всі підпапки всередині цієї кореневої папки
        current_drive_subfolders = {}
        page_token = None
        while True:
            q = f"'{root_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            res_drive = drive.files().list(
                q=q, fields="nextPageToken, files(id, name)", pageSize=100, pageToken=page_token
            ).execute()
            for folder in res_drive.get('files', []):
                current_drive_subfolders[folder['id']] = folder['name']
            page_token = res_drive.get('nextPageToken')
            if not page_token:
                break
                
        # --- ОНОВЛЕННЯ СЛУЖБОВОГО АРКУША В ПАМ'ЯТІ ---
        # а) Додаємо нові підпапки, яких ви раніше не бачили
        for sub_id, sub_name in current_drive_subfolders.items():
            if sub_id not in service_map:
                service_map[sub_id] = [sub_id, sub_name, tab_name, "НІ", "✨ Нова папка! Очікує вашого правила"]
            else:
                # Якщо папка вже була, оновлюємо її ім'я (якщо ви змінили його на Диску)
                service_map[sub_id][1] = sub_name
                # Якщо вона раніше вважалася видаленою, але ви її повернули — скидаємо варнінг
                if "❌ ВИДАЛЕНО" in str(service_map[sub_id][4]):
                    service_map[sub_id][4] = "Знову знайдено на Диску"

        # б) Зворотна перевірка: якщо папка закріплена за цим аркушем у таблиці, але на Диску зникла
        for sub_id, row_data in service_map.items():
            if row_data[2] == tab_name:  # Перевіряємо тільки записи цього аркуша
                if sub_id not in current_drive_subfolders:
                    row_data[4] = "❌ ВИДАЛЕНО НА ДИСКУ (Перевірте чи діє правило!)"

        # --- БЕЗУМОВНА СИНХРОНІЗАЦІЯ ФАЙЛІВ НА ТЕМАТИЧНИЙ АРКУШ ---
        # Ми забираємо файли з УСІХ існуючих підпаок цієї кореневої папки
        print(f"🔍 Збір файлів з усіх підпапок для аркуша '{tab_name}'...")
        drive_files = {}
        
        for sub_id, sub_name in current_drive_subfolders.items():
            page_token_file = None
            while True:
                q_file = f"'{sub_id}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
                res_files = drive.files().list(
                    q=q_file, fields="nextPageToken, files(id, name)", pageSize=1000, pageToken=page_token_file
                ).execute()
                
                for f in res_files.get('files', []):
                    drive_files[f['id']] = {"name": f['name'], "category": sub_name}
                    
                page_token_file = res_files.get('nextPageToken')
                if not page_token_file:
                    break

        # Зчитуємо поточний стан аркуша контенту
        raw_sheet = sheets.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A2:E"
        ).execute()
        sheet_rows = raw_sheet.get('values', [])
        sheet_map = {row[0]: {"idx": i + 2, "data": row} for i, row in enumerate(sheet_rows) if row and len(row) > 0}
        
        rows_to_append = []
        ids_to_delete = []
        
        # Обчислюємо нові файли, а також оновлюємо змінені назви/категорії
        for f_id, f_info in drive_files.items():
            if f_id not in sheet_map:
                rows_to_append.append([f_id, f_info['name'], f_info['category'], 0, 0])
            else:
                # Файл вже є в таблиці, перевіряємо чи не змінилася його назва або папка (категорія)
                existing_item = sheet_map[f_id]
                existing_name = existing_item["data"][1]
                existing_category = existing_item["data"][2]
                
                if existing_name != f_info['name'] or existing_category != f_info['category']:
                    row_idx = existing_item["idx"]
                    print(f"🔄 Виявлено зміни для файлу ID {f_id} (Рядок {row_idx}). Оновлюємо назву/категорію на: [{f_info['category']}] -> {f_info['name']}")
                    
                    # Оновлюємо колонки B (Назва) та C (Категорія) для цього рядка
                    sheets.spreadsheets().values().update(
                        spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!B{row_idx}:C{row_idx}",
                        valueInputOption='RAW', body={'values': [[f_info['name'], f_info['category']]]}
                    ).execute()
                
        # Обчислюємо файли, які зникли з Диску
        for f_id, sheet_info in sheet_map.items():
            if f_id not in drive_files:
                ids_to_delete.append(sheet_info['idx'])
                
        # Записуємо дельту додавання
        if rows_to_append:
            print(f"➕ Додаємо {len(rows_to_append)} нових файлів на аркуш '{tab_name}'")
            sheets.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A2",
                valueInputOption='RAW', body={'values': rows_to_append}
            ).execute()
            
        # Записуємо дельту видалення застарілих файлів
        if ids_to_delete:
            print(f"❌ Видаляємо {len(ids_to_delete)} застарілих рядків з аркуша '{tab_name}'")
            requests = []
            for row_idx in sorted(ids_to_delete, reverse=True):
                requests.append({
                    "deleteDimension": {
                        "range": {
                            "sheetId": get_sheet_id(sheets, tab_name),
                            "dimension": "ROWS",
                            "startIndex": row_idx - 1,
                            "endIndex": row_idx
                        }
                    }
                })
            sheets.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": requests}).execute()

    # 3. Наприкінці повністю перезаписуємо оновлений службовий аркуш налаштувань
    print("\n💾 Збереження оновленого службового списку підпапок...")
    updated_service_values = list(service_map.values())
    
    # Очищаємо старі рядки, щоб не залишалося сміття внизу
    sheets.spreadsheets().values().clear(spreadsheetId=SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2:E").execute()
    
    if updated_service_values:
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2",
            valueInputOption='RAW', body={'values': updated_service_values}
        ).execute()
        
    print("✨ Синхронізацію завершено. Все працює точно за вашим алгоритмом!")

if __name__ == '__main__':
    main()
