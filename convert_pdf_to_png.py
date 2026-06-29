import os
import json
import fitz  # Бібліотека PyMuPDF
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# ID вашої папки на Google Диску
FOLDER_ID = '1NW5iKh6fkzXhvrHUmVVjJL5YJTmHGA7E'

# Назва секрету в GitHub, де лежить ваш JSON ключ сервісного акаунта
CREDENTIALS_JSON = os.environ.get('GDRIVE_SERVICE_ACCOUNT_KEY')

# Тимчасова папка на віртуальній машині GitHub для обробки
TEMP_DIR = "temp_processing"

def get_drive_service():
    """Авторизація через сервісний акаунт з правами на запис/редагування."""
    creds_dict = json.loads(CREDENTIALS_JSON)
    # Використовуємо повний scope 'drive' для можливості завантаження файлів
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

def main():
    if not CREDENTIALS_JSON:
        print("Помилка: Не знайдено змінну оточення GDRIVE_SERVICE_ACCOUNT_KEY у Secrets!")
        return

    service = get_drive_service()
    os.makedirs(TEMP_DIR, exist_ok=True)

    # 1. Отримуємо список УСІХ файлів у папці
    print("Аналіз вмісту папки на Google Диску...")
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and trashed = false",
        fields="files(id, name, mimeType, modifiedTime)",
        pageSize=1000  # <--- ВИПРАВЛЕНО ТУТ (було maxResults)
    ).execute()
    all_files = results.get('files', [])

    # Створюємо множину імен файлів, які вже є в папці
    existing_names = {f['name'] for f in all_files}
    
    # Відфільтровуємо тільки PDF-файли
    pdf_files = [f for f in all_files if f['mimeType'] == 'application/pdf']

    if not pdf_files:
        print("У папці не знайдено жодного PDF-файлу для обробки.")
        return

    print(f"Знайдено PDF-файлів на Диску: {len(pdf_files)}")

    for f in pdf_files:
        pdf_name = f['name']
        file_id = f['id']
        mod_time_str = f['modifiedTime']  # Зберігаємо точний час у форматі ISO (напр. 2026-06-29T19:50:00.000Z)
        base_name = os.path.splitext(pdf_name)[0]

        # ОПТИМІЗАЦІЯ: Якщо перша сторінка вже є на Диску, пропускаємо цей PDF
        expected_first_page = f"{base_name}_1.png"
        if expected_first_page in existing_names:
            print(f"Пропущено (вже конвертовано раніше): {pdf_name}")
            continue

        print(f"Обробка файлу: {pdf_name}...")
        temp_pdf_path = os.path.join(TEMP_DIR, pdf_name)
        
        # 2. Завантажуємо PDF на віртуальну машину GitHub для обробки
        request = service.files().get_media(fileId=file_id)
        with open(temp_pdf_path, 'wb') as pdf_file:
            downloader = MediaIoBaseDownload(pdf_file, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        # 3. Конвертуємо посторінково
        doc = fitz.open(temp_pdf_path)
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)  # Якість зображення
            
            # Формат назви за вашим запитом: оригінальна назва_1.png
            img_name = f"{base_name}_{page_num + 1}.png"
            img_path = os.path.join(TEMP_DIR, img_name)
            pix.save(img_path)
            
            # 4. Завантажуємо зображення назад у ту саму папку Google Диска
            file_metadata = {
                'name': img_name,
                'parents': [FOLDER_ID],
                'modifiedTime': mod_time_str  # Передаємо ту саму дату зміни, що була у PDF
            }
            media = MediaFileUpload(img_path, mimetype='image/png')
            
            # Створюємо файл на Диску. Google Drive API автоматично застосує вказану modifiedTime
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            print(f"  -> Завантажено сторінку: {img_name} з оригінальною датою")
            
            # Видаляємо локальний PNG після завантаження
            os.remove(img_path)

        doc.close()
        # Видаляємо локальний тимчасовий PDF
        os.remove(temp_pdf_path)

    print("Процес завершено успішно!")

if __name__ == '__main__':
    main()
