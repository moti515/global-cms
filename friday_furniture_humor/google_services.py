import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from config import SCOPES, SPREADSHEET_ID


def get_services():
    """Створює та повертає екземпляри клієнтів Google Drive та Google Sheets API."""
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds), build('sheets', 'v4', credentials=creds)


def log_unsupported_to_service(sheets_service, folder_name, file_name, reason="непідтримуваний формат"):
    """Записує системне попередження на службовий аркуш у випадку невідомого або текстового формату."""
    try:
        res = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2:E"
        ).execute()
        rows = res.get('values', [])
        
        for idx, row in enumerate(rows):
            if len(row) > 1 and row[1] == folder_name:
                range_to_update = f"'⚙️ Налаштування Папок'!E{idx + 2}"
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID, range=range_to_update,
                    valueInputOption='RAW', body={'values': [[f"⚠️ {reason}: {file_name}"]]}
                ).execute()
                print(f"📝 Зафіксовано системне попередження для [{folder_name}] на службовому аркуші.")
                break
    except Exception as e:
        print(f"❌ Не вдалося записати помилку на службовий аркуш: {e}")


def get_valid_rows_from_sheet(sheets_service, tab_name, counter_col_idx):
    """Зчитує рядки з аркуша Google Sheets та форматує лічильники публікацій."""
    res = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, 
        range=f"'{tab_name}'!A2:E"
    ).execute()
    
    rows = res.get('values', [])
    if not rows:
        return []

    valid_rows = []
    for i, r in enumerate(rows):
        if len(r) >= 3:
            while len(r) < 5: 
                r.append("0")
            try:
                r[3], r[4] = (int(r[3]) if r[3] else 0), (int(r[4]) if r[4] else 0)
                valid_rows.append({"row_idx": i + 2, "data": r})
            except ValueError:
                continue
    return valid_rows


def update_sheet_counter(sheets_service, tab_name, row_line, mode, current_count):
    """Оновлює значення лічильника публікацій у відповідному стовпчику таблиці."""
    new_counter = current_count + 1
    col_letter = "D" if mode == 'post' else "E"
    sheets_service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, 
        range=f"'{tab_name}'!{col_letter}{row_line}",
        valueInputOption='RAW', 
        body={'values': [[new_counter]]}
    ).execute()
    print(f"📊 Лічильник оновлено на +1 для аркуша '{tab_name}' (Рядок {row_line}). Нове значення: {new_counter}")


def download_file_from_drive(drive_service, file_id, local_path):
    """Завантажує файл з Google Drive порціями на локальний диск."""
    print(f"📥 Завантажуємо медіа з Google Диску: {os.path.basename(local_path)}...")
    request = drive_service.files().get_media(fileId=file_id)
    with open(local_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
