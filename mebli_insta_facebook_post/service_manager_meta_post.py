"""
================================================================================
☁️ СЕРВІСНИЙ МОДУЛЬ (Google Drive, Google Sheets, Cloud Storage, Gemini AI)
================================================================================

ОПИС РОБОТИ МОДУЛЯ:
 1. Google API: Авторизується через Service Account для скачування файлів з Drive 
    та швидкого оновлення лічильників публікацій у Sheets.
 2. Каскадний хостинг медіа: Послідовно відправляє файли на Litterbox (з павзою 3s), 
    а при його відмові — у резервний бізнес-акаунт ImageKit чи ImgBB API.
 3. Генерація підписів Gemini AI: Використовує новий офіційний SDK google-genai 
    (`client.models.generate_content`) для створення релевантних текстів трьома мовами.

================================================================================
"""

import os
import json
import requests
import time
from PIL import Image as PILImage
from google import genai
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import config_meta_post as config


def get_services():
    """Авторизація та ініціалізація Google Drive та Google Sheets API."""
    key_dict = json.loads(config.GDRIVE_SERVICE_ACCOUNT_KEY)
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=config.SCOPES)
    return build('drive', 'v3', credentials=creds), build('sheets', 'v4', credentials=creds)


def download_file_from_drive(drive_service, file_id, local_path):
    """Скачує бінарний файл з Google Drive за його унікальним ID."""
    request = drive_service.files().get_media(fileId=file_id)
    with open(local_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return local_path


def read_current_language(sheets_service, lang_cell_range: str) -> str:
    """Зчитує мовний код (UK, EN, DE) з вказаної комірки налаштувань."""
    try:
        lang_res = sheets_service.spreadsheets().values().get(
            spreadsheetId=config.SPREADSHEET_ID, range=lang_cell_range
        ).execute()
        lang_values = lang_res.get('values', [])
        if lang_values and lang_values[0]:
            return lang_values[0][0].strip().upper()
    except Exception as e:
        print(f"⚠️ Не вдалося прочитати мову з комірки {lang_cell_range}: {e}")
    return "UK"


def update_sheets_registry_and_lang(sheets_service, items: list, current_tab: str, col_letter: str, lang_cell: str, next_lang: str):
    """Оновлює лічильники опублікованих файлів у таблиці та записує наступну мову."""
    for item in items:
        new_val = item["counter"] + 1
        range_to_update = f"'{current_tab}'!{col_letter}{item['row_idx']}"
        try:
            sheets_service.spreadsheets().values().update(
                spreadsheetId=config.SPREADSHEET_ID, range=range_to_update,
                valueInputOption='RAW', body={'values': [[new_val]]}
            ).execute()
            print(f"✍️ Лічильник рядка {item['row_idx']} збільшено до {new_val}.")
        except Exception as e:
            print(f"⚠️ Помилка лічильника в Таблиці: {e}")

    try:
        sheets_service.spreadsheets().values().update(
            spreadsheetId=config.SPREADSHEET_ID, range=lang_cell,
            valueInputOption='RAW', body={'values': [[next_lang]]}
        ).execute()
        print(f"🔄 Мову на наступний раз змінено на: {next_lang}")
    except Exception as e:
        print(f"⚠️️ Не вдалося оновити комірку мови: {e}")


def get_google_drive_direct_url(file_id, local_file_path=None):
    """Каскадний завантажувач медіафайлів на зовнішні хостинги для Meta API (Litterbox -> ImageKit -> ImgBB)."""
    if local_file_path and os.path.exists(local_file_path):
        filename = os.path.basename(local_file_path)
        is_video = filename.lower().endswith(('.mp4', '.mov', '.avi'))
        mime_type = "video/mp4" if is_video else "image/jpeg"
        remote_filename = "post.mp4" if is_video else "post.jpg"

        # 1️⃣ Litterbox (1h)
        print(f"☁️ Завантажуємо файл {filename} на Litterbox.moe (1h)...")
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            with open(local_file_path, 'rb') as f:
                res = requests.post(
                    'https://litterbox.catbox.moe/resources/internals/api.php',
                    data={'reqtype': 'fileupload', 'time': '1h'},
                    files={'fileToUpload': (remote_filename, f, mime_type)},
                    headers=headers,
                    timeout=15
                )
                if res.status_code == 200 and res.text.strip().startswith('http'):
                    direct_url = res.text.strip()
                    print(f"🔗 Посилання від Litterbox: {direct_url}")
                    time.sleep(3)  # Павза для реплікації CDN
                    return direct_url, None
                else:
                    print(f"⚠️ Litterbox відмовив (Статус {res.status_code}): {res.text[:100]}")
        except Exception as e:
            print(f"⚠️ Помилка Litterbox: {e}. Пробуємо резервний ImageKit...")

        # 2️⃣ ImageKit.io
        if config.IMAGEKIT_PRIVATE_KEY:
            print(f"☁️ Резерв: завантажуємо файл {filename} на ImageKit.io...")
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://upload.imagekit.io/api/v1/files/upload',
                        auth=(config.IMAGEKIT_PRIVATE_KEY, ''),
                        files={'file': (filename, f, mime_type)},
                        data={'fileName': filename, 'useUniqueFileName': 'true'},
                        timeout=40
                    )
                    if res.status_code in [200, 201]:
                        res_data = res.json()
                        print(f"🔗 Посилання від ImageKit: {res_data.get('url')}")
                        return res_data.get('url'), res_data.get('fileId')
            except Exception as e:
                print(f"⚠️ Помилка ImageKit: {e}")

        # 3️⃣ ImgBB
        if config.IMGBB_API_KEY and mime_type == "image/jpeg":
            print(f"☁️ Резерв: завантажуємо фото {filename} на ImgBB...")
            try:
                with open(local_file_path, 'rb') as f:
                    img_bytes = f.read()
                if img_bytes:
                    res = requests.post(
                        'https://api.imgbb.com/1/upload',
                        data={'key': config.IMGBB_API_KEY, 'expiration': 86400},
                        files={'image': (filename, img_bytes, mime_type)},
                        timeout=25
                    ).json()
                    if res.get('success'):
                        print(f"🔗 Посилання від ImgBB: {res['data']['url']}")
                        return res['data']['url'], None
            except Exception as e:
                print(f"⚠️ Помилка ImgBB: {e}")

    print(f"🚨 Аварійний режим для Google Drive ID: {file_id}")
    return f"https://docs.google.com/uc?export=download&id={file_id}", None


def delete_from_imagekit(file_id: str):
    """Видаляє тимчасовий медіафайл з ImageKit після закінчення публікації."""
    if not file_id or not config.IMAGEKIT_PRIVATE_KEY:
        return
    try:
        requests.delete(f"https://api.imagekit.io/v1/files/{file_id}", auth=(config.IMAGEKIT_PRIVATE_KEY, ''), timeout=15)
        print(f"🗑️ Файл {file_id} видалено з ImageKit.")
    except Exception:
        pass


def generate_multimodal_caption(image_paths, category, date_str, lang_idx):
    """Генерує креативний підпис через новий SDK google-genai (client.models.generate_content)."""
    pref = config.LANG_CONFIG.get(lang_idx, config.LANG_CONFIG[0])
    if not config.GEMINI_API_KEY:
        return pref["no_gemini_caption"]

    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else str(datetime.now().year)
    cat_lower = category.lower()
    real_manufacturer = category

    if "montage various" in cat_lower:
        real_manufacturer = "Професійний монтаж та збирання меблів" if lang_idx == 0 else "Professional furniture installation"
    elif "various" in cat_lower:
        real_manufacturer = "Сучасні меблеві тренди та концепти" if lang_idx == 0 else "Modern furniture trends and concepts"
    elif "instruktion" in cat_lower:
        real_manufacturer = "Конструкторські стандарти та ергономіка меблів" if lang_idx == 0 else "Furniture design standards and ergonomics"
    else:
        for key, info in config.COMPANIES_DB.items():
            if key in cat_lower:
                real_manufacturer = info["names"].get(lang_idx, info["names"][0])
                break

    models_to_try = [
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite"
    ]

    lang_instructions = {
        0: "Напиши текст виключно УКРАЇНСЬКОЮ мовою. Дозволено додати 1-2 доречних емоїз.",
        1: "Write the text exclusively in ENGLISH. You may include 1-2 relevant emojis.",
        2: "Schreibe den Text ausschließlich auf DEUTSCH. Du darfst 1-2 passende Emojis hinzufügen."
    }

    prompt = (
        f"Ти — досвідчений копірайтер із тонким почуттям гумору та експертний меблевий конструктор.\n"
        f"Подивись на ці зображення (або кадр з відео) і придумай один короткий, влучний та чіпляючий пост для соцмереж.\n\n"
        f"🎯 ТОН ТА СТИЛІСТИКА:\n"
        f"- Будь живим та іронічним. Якщо на фото робочий процес, пил, креслення чи інструменти — "
        f"пожартуй про залаштунки, перфекціонізм, каву на тирсі чи складні технічні вузли.\n"
        f"- Якщо на фото готовий виріб — пиши про естетику, меблеву філософію, ергономіку або домашній затишок та ідеальні зазори.\n"
        f"- Уникай банальних штампів: 'найкраща якість', 'купуйте у нас', 'індивідуальний підхід'.\n\n"
        f"📋 КОНТЕКСТ ДЛЯ АНАЛІЗУ:\n"
        f"Виробник/Напрямок меблів: '{real_manufacturer}'. Рік зйомки: {year}.\n\n"
        f"⚠️ СУВОРІ ОБМЕЖЕННЯ:\n"
        f"1. {lang_instructions.get(lang_idx, lang_instructions[0])}\n"
        f"2. Поверни ЛИШЕ фінальний текст підпису. Без лапок, без вступних слів, без хэштегів та пояснень копірайтера.\n"
        f"3. КРИТИЧНО: Не пиши жодних передмов чи післямов. Тільки текст поста."
    )

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        contents = [prompt]

        for img_path in image_paths:
            if os.path.exists(img_path):
                try:
                    with PILImage.open(img_path) as img:
                        if img.mode in ("RGBA", "P"):
                            img = img.convert("RGB")
                        img.thumbnail((1024, 1024))
                        contents.append(img.copy())
                except Exception as img_err:
                    print(f"⚠️ Не вдалося відкрити зображення {img_path}: {img_err}")

        for model in models_to_try:
            print(f"🚀 Спроба генерації підпису через {model}...")
            try:
                response = client.models.generate_content(model=model, contents=contents)
                if response and response.text:
                    return response.text.strip()
                print(f"⚠️ Модель {model} повернула порожню відповідь.")
            except Exception as model_err:
                print(f"⚠️ Помилка моделі {model}: {model_err}. Перехід до наступної...")
                continue

        print("⚠️ Моделі Gemini не відповіли успішно. Активовано дефолт.")
        return pref["fallback_caption"]

    except Exception as e:
        print(f"⚠️ Критична помилка ШІ: {e}")
        return pref["fallback_caption"]
