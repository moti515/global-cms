import os
import json
import base64
import requests
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

def get_google_drive_direct_url(file_id, local_file_path=None):
    """Каскадний завантажувач медіафайлів на зовнішні хостинги для Meta API."""
    if local_file_path and os.path.exists(local_file_path):
        filename = os.path.basename(local_file_path)
        lower_name = filename.lower()
        mime_type = "video/mp4" if lower_name.endswith(('.mp4', '.mov', '.avi')) else "image/jpeg"
        
        browser_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
        }
        
        # 1️⃣ Catbox.moe
        print(f"☁️ Завантажуємо файл {filename} на Catbox.moe...")
        try:
            with open(local_file_path, 'rb') as f:
                file_bytes = f.read()
            if file_bytes:
                upload_timeout = (10, 90) if mime_type.startswith("video") else (7, 25)
                res = requests.post(
                    'https://catbox.moe/user/api.php',
                    data={'reqtype': 'fileupload'},
                    files={'fileToUpload': (filename, file_bytes, mime_type)},
                    headers=browser_headers, timeout=upload_timeout
                )
                if res.status_code == 200 and res.text.startswith('http'):
                    direct_url = res.text.strip()
                    print(f"🔗 Посилання від Catbox: {direct_url}")
                    return direct_url, None
        except Exception as e:
            print(f"⚠️ Помилка Catbox: {e}. Пробуємо ImageKit...")

        # 2️⃣ ImageKit.io
        if config.IMAGEKIT_PRIVATE_KEY:
            print(f"☁️ Завантажуємо файл {filename} на ImageKit.io...")
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://upload.imagekit.io/api/v1/files/upload',
                        auth=(config.IMAGEKIT_PRIVATE_KEY, ''),
                        files={'file': (filename, f, mime_type)},
                        data={'fileName': filename, 'useUniqueFileName': 'true'},
                        timeout=60
                    )
                    if res.status_code in [200, 201]:
                        res_data = res.json()
                        print(f"🔗 Посилання від ImageKit: {res_data.get('url')}")
                        return res_data.get('url'), res_data.get('fileId')
            except Exception as e:
                print(f"⚠️ Помилка ImageKit: {e}")

        # 3️⃣ ImgBB API
        if config.IMGBB_API_KEY and mime_type == "image/jpeg":
            print(f"☁️ Завантажуємо фото {filename} на ImgBB...")
            try:
                with open(local_file_path, 'rb') as f:
                    img_bytes = f.read()
                if img_bytes:
                    res = requests.post(
                        'https://api.imgbb.com/1/upload',
                        data={'key': config.IMGBB_API_KEY, 'expiration': 86400},
                        files={'image': (filename, img_bytes, mime_type)},
                        timeout=30
                    ).json()
                    if res.get('success'):
                        print(f"🔗 Посилання від ImgBB: {res['data']['url']}")
                        return res['data']['url'], None
            except Exception as e:
                print(f"⚠️ Помилка ImgBB: {e}")

    print(f"🚨 Аварійний режим для Google Drive ID: {file_id}")
    return f"https://docs.google.com/uc?export=download&id={file_id}", None

def delete_from_imagekit(file_id: str):
    """Видаляє тимчасовий медіафайл з ImageKit по закінченню роботи."""
    if not file_id or not config.IMAGEKIT_PRIVATE_KEY: return
    try:
        requests.delete(f"https://api.imagekit.io/v1/files/{file_id}", auth=(config.IMAGEKIT_PRIVATE_KEY, ''), timeout=15)
        print(f"🗑️ Файл {file_id} видалено з ImageKit.")
    except: pass

def generate_multimodal_caption(image_paths, category, date_str, lang_idx):
    """Звертається до нейромережі Gemini для побудови креативного підпису на основі зображень."""
    pref = config.LANG_CONFIG.get(lang_idx, config.LANG_CONFIG[0])
    if not config.GEMINI_API_KEY:
        return pref["no_gemini_caption"]

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

    # Спроба пройтися по актуальних моделях (завжди тримаємо в кінці стабільну версію)
    models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    
    lang_instructions = {
        0: "Напиши text виключно УКРАЇНСЬКОЮ мовою. Використовуй емодзі.",
        1: "Write the text exclusively in ENGLISH. Use emojis.",
        2: "Schreibe den Text ausschließlich auf DEUTSCH. Nutze Emojis."
    }
    
    prompt = (
        f"Ти професійний копірайтер та меблевий експерт. Подивись на ці зображення. "
        f"Напиши один короткий, натхненний пост для соцмереж. "
        f"Виробник/Напрямок меблів: '{real_manufacturer}'. {lang_instructions.get(lang_idx, lang_instructions[0])} "
        f"КРИТИЧНО: Не пиши жодних передмов чи післямов. Тільки text поста."
    )

    try:
        parts = [{"text": prompt}]
        for img_path in image_paths:
            if os.path.exists(img_path):
                try:
                    with open(img_path, "rb") as f:
                        image_bytes = f.read()
                    base64_image = base64.b64encode(image_bytes).decode('utf-8')
                    parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64_image}})
                except Exception as e:
                    print(f"⚠️ Не вдалося обробити файл {img_path}: {e}")
        
        payload = {"contents": [{"parts": parts}]}
        
        for model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.GEMINI_API_KEY}"
            try:
                res = requests.post(url, json=payload, timeout=20).json()
                if 'candidates' in res and res['candidates']:
                    return res['candidates'][0]['content']['parts'][0]['text'].strip()
            except Exception as e:
                print(f"⚠️ Модель {model} недоступна: {e}. Перехід до наступної...")
                continue
                
        print("⚠️ Моделі Gemini не відповіли успішно. Активовано дефолт.")
        return pref["fallback_caption"]
        
    except Exception as e:
        print(f"⚠️ Критична помилка виконання функції ШІ: {e}")
        return pref["fallback_caption"]
