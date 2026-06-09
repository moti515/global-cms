import os
import sys
import io
from googleapiclient.http import MediaIoBaseDownload
from config import FOLDER_INPUT_ID, FOLDER_TRASH_ID, VALID_EXTENSIONS

def count_total_files(service):
    total = 0
    page_token = None
    while True:
        try:
            results = service.files().list(
                q=f"'{FOLDER_INPUT_ID}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageSize=1000,
                pageToken=page_token
            ).execute()
            files = results.get('files', [])
            for f in files:
                if f['id'] == FOLDER_TRASH_ID:
                    continue
                mime_type = f.get('mimeType', '')
                lower_name = f.get('name', '').lower()
                if mime_type.startswith(('image/', 'video/')) or lower_name.endswith(VALID_EXTENSIONS):
                    total += 1
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Помилка підрахунку файлів на Google Диску: {e}")
    return total

def download_file(service, file_id, file_name, local_path):
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.FileIO(local_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.close()
    except Exception as e:
        sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося завантажити файл '{file_name}' з Google Диску. Причина: {e}")

def move_files_to_trash(service, file_list):
    for f in file_list:
        try:
            service.files().update(
                fileId=f['id'],
                addParents=FOLDER_TRASH_ID,
                removeParents=FOLDER_INPUT_ID,
                fields='id, parents'
            ).execute()
            print(f"📦 Переміщено в архів: {f['name']}")
        except Exception as e:
            sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося перемістити файл {f['name']} в архів на Диску: {e}")
