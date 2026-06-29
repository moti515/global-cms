import os
import datetime
import fitz  # PyMuPDF
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# ID папки на Google Диску
FOLDER_ID = '1NW5iKh6fkzXhvrHUmVVjJL5YJTmHGA7E'

# Тимчасова папка на віртуальній машині GitHub
TEMP_DIR = "temp_processing"

def get_drive_service():
    """Авторизація через особистий OAuth2 Refresh Token."""
    creds = Credentials(
        token=None,  # Буде оновлено автоматично через Request()
        refresh_token=os.environ.get('GDRIVE_REFRESH_TOKEN'),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get('GDRIVE_CLIENT_ID'),
        client_secret=os.environ.get('GDRIVE_CLIENT_SECRET')
    )
    # Оновлюємо токен доступу
    creds.refresh(Request())
    return build('drive', 'v3', credentials=creds)

def main():
    if not os.environ.get('GDRIVE_REFRESH_TOKEN'):
        print("Помилка: Не знайдено змінні оточення для OAuth2 у GitHub Secrets!")
        return

    service = get_drive_service()
    os.makedirs(TEMP_DIR, exist_ok=True)

    print("Аналіз вмісту папки на Google Диску...")
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and trashed = false",
        fields="files(id, name, mimeType, modifiedTime)",
        pageSize=1000
    ).execute()
    all_files = results.get('files', [])

    existing_names = {f['name'] for f in all_files}
    pdf_files = [f for f in all_files if f['mimeType'] == 'application/pdf']

    if not pdf_files:
        print("У папці не знайдено жодного PDF-файлу для обробки.")
        return

    print(f"Знайдено PDF-файлів на Диску: {len(pdf_files)}")

    for f in pdf_files:
        pdf_name = f['name']
        file_id = f['id']
        mod_time_str = f['modifiedTime']
        base_name = os.path.splitext(pdf_name)[0]

        expected_first_page = f"{base_name}_1.png"
        if expected_first_page in existing_names:
            print(f"Пропущено (вже конвертовано): {pdf_name}")
            continue

        print(f"Обробка файлу: {pdf_name}...")
        temp_pdf_path = os.path.join(TEMP_DIR, pdf_name)
        
        # Завантаження PDF
        request = service.files().get_media(fileId=file_id)
        with open(temp_pdf_path, 'wb') as pdf_file:
            downloader = MediaIoBaseDownload(pdf_file, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        # Конвертація
        doc = fitz.open(temp_pdf_path)
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)
            
            img_name = f"{base_name}_{page_num + 1}.png"
            img_path = os.path.join(TEMP_DIR, img_name)
            pix.save(img_path)
            
            # Завантаження назад (тепер під вашим акаунтом і квотою)
            file_metadata = {
                'name': img_name,
                'parents': [FOLDER_ID],
                'modifiedTime': mod_time_str
            }
            media = MediaFileUpload(img_path, mimetype='image/png')
            
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            print(f"  -> Завантажено сторінку: {img_name}")
            
            os.remove(img_path)

        doc.close()
        os.remove(temp_pdf_path)

    print("Процес завершено успішно!")

if __name__ == '__main__':
    main()
