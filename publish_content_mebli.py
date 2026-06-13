import os
import sys
import json
import time
import base64
import requests
import subprocess
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image
from pillow_heif import register_heif_opener

# Реєстрація підтримки HEIF/HEIC
register_heif_opener()

# ⚙️ НАЛАШТУВАННЯ (Беруться напряму з системних змінних GitHub Actions)
IG_USER_ID = os.environ.get("IG_USER_ID")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")

SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
TAB_NAME = "Меблі"

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

# Списки форматів (додано .mov та .avi для відео)
VALID_MEDIA_EXTENSIONS = ('.gif', '.heic', '.heif', '.jpeg', '.jpg', '.mp4', '.png', '.webp', '.mov', '.avi')
DOCUMENT_EXTENSIONS = ('.pdf', '.doc', '.docx', '.djvu', '.txt', '.rtf', '.fb2', '.epub')

# 🌍 ЦЕНТРАЛІЗОВАНА ЛОКАЛІЗАЦІЯ ІНТЕРФЕЙСУ ПОСТІВ
LANG_CONFIG = {
    0: {  # 🇺🇦 Українська
        "year": "Рік", 
        "brand": "Виробник", 
        "loc": "Локація", 
        "assembly": "Монтаж: Меблі, у монтажі яких ми брали участь (професійне збирання)", 
        "concept": "Концепт: Цікаві меблеві рішення, тренди та ідеї з усього світу", 
        "ergonomics": "Ергономіка та проектування: Корисні стандарти та розміри, яких варто дотримуватися при проектуванні меблів.",
        "link_in_bio": "🔗 Посилання на портфоліо — у шапці нашого профілю!",
        "fallback_caption": "Чудова робота нашої команди! Як вам результат? 👇😊",
        "no_gemini_caption": "Якісні меблі для вашого затишку! 👇✨ #меблі #інтерєр"
    },
    1: {  # 🇬🇧 Англійська
        "year": "Year", 
        "brand": "Manufacturer", 
        "loc": "Location", 
        "assembly": "Assembly: Furniture we helped assemble (professional installation)", 
        "concept": "Concept: Interesting furniture solutions, trends, and ideas from around the world", 
        "ergonomics": "Ergonomics and Design: Useful standards and dimensions to follow when designing furniture.",
        "link_in_bio": "🔗 Portfolio link is in our bio!",
        "fallback_caption": "Great work by our team! How do you like the result? 👇😊",
        "no_gemini_caption": "Quality furniture for your comfort! 👇✨ #furniture #interiordesign"
    },
    2: {  # 🇩🇪 Німецька
        "year": "Jahr", 
        "brand": "Hersteller", 
        "loc": "Standort", 
        "assembly": "Montage: Möbel, bei deren Montage wir mitgewirkt haben (professioneller Aufbau)", 
        "concept": "Konzept: Interessante Möbellösungen, Trends und Ideen aus aller Welt", 
        "ergonomics": "Ergonomie und Konstruktion: Nützliche Standards und Maße, die bei der Möbelkonstruktion beachtet werden sollten.",
        "link_in_bio": "🔗 Link zum Portfolio finden Sie in unserer Bio!",
        "fallback_caption": "Tolle Arbeit unseres Teams! Wie gefällt Ihnen das Ergebnis? 👇😊",
        "no_gemini_caption": "Qualitätsmöbel für Ihr gemütliches Zuhause! 👇✨ #moebel #interieur"
    }
}

# 🏢 ГЛОБАЛЬНА БАЗА ДАНИХ КОМПАНІЙ ТА КАТЕГОРІЙ
COMPANIES_DB = {
    "goncharenko": {
        "names": {0: "Олександр Гончаренко", 1: "Oleksandr Goncharenko", 2: "Oleksandr Goncharenko"},
        "links": ["📸 Instagram: instagr.am/goncharenko8721"],
        "ig_handle": "@goncharenko8721"
    },
    "gurov": {
        "names": {0: "Андрій Гуров", 1: "Andrii Gurov", 2: "Andrii Gurov"},
        "links": ["🌐 Facebook: fb.com/andrej.gurov.755581"]
    },
    "solovey": {
        "names": {0: "Студія меблів «Соловей»", 1: "Solovey Furniture Studio", 2: "Möbelstudio Solovey"},
        "links": ["📸 Instagram: instagr.am/mebelsolovei"],
        "ig_handle": "@mebelsolovei"
    },
    "furniture park": {
        "names": {0: "Меблевий парк", 1: "Furniture Park", 2: "Furniture Park"},
        "links": [
            "📸 Instagram: instagr.am/meblevyi_park",
            "📸 Instagram: instagr.am/meblovo_ukraine",
            "📢 Telegram: t.me/Meblevyi_park",
            "📸 Instagram: instagr.am/renovaelite"
        ],
        "ig_handle": [
            "@meblevyi_park",
            "@meblovo_ukraine",
            "@renovaelite"
        ]
    }
}

def get_services():
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds), build('sheets', 'v4', credentials=creds)

def log_unsupported_to_service(sheets_service, folder_name, file_name, reason="непідтримуваний формат"):
    """Записує попередження про непідтримуваний формат на службовий аркуш налаштувань."""
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

def optimize_media_geometry(local_path, filename, mime_type):
    """Оптимізує пропорції зображень під вимоги Meta для звичайних постів у стрічку."""
    if not os.path.exists(local_path):
        return local_path

    if mime_type == "image/jpeg":
        try:
            with Image.open(local_path) as img:
                img = img.convert('RGB')
                w, h = img.size
                ratio = w / h
                
                if ratio < 0.8 or ratio > 1.91:
                    print(f"📐 Оптимізація Поста: Пропорції картинки ({ratio:.2f}) неприпустимі. Коригуємо...")
                    padded_post_path = os.path.join('temp_mebli', 'post_padded_' + filename.rsplit('.', 1)[0] + '.jpg')
                    
                    if ratio < 0.8:
                        new_w = int(h * 0.8)
                        new_h = h
                    else:
                        new_w = w
                        new_h = int(w / 1.91)
                        
                    canvas = Image.new('RGB', (new_w, new_h), (255, 255, 255))
                    paste_x = (new_w - w) // 2
                    paste_y = (new_h - h) // 2
                    
                    canvas.paste(img, (paste_x, paste_y))
                    canvas.save(padded_post_path, 'JPEG', quality=95)
                    return padded_post_path
        except Exception as e:
            print(f"⚠️ Помилка калібрування геометрії поста: {e}")

    return local_path

def get_google_drive_direct_url(file_id, local_file_path=None):
    """Каскадний завантажувач медіафайлів на зовнішні хостинги з API."""
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
                res = requests.post(
                    'https://catbox.moe/user/api.php',
                    data={'reqtype': 'fileupload'},
                    files={'fileToUpload': (filename, file_bytes, mime_type)},
                    headers=browser_headers, timeout=(7, 25)
                )
                if res.status_code == 200 and res.text.startswith('http'):
                    direct_url = res.text.strip()
                    print(f"🔗 Отримано стабільне посилання від Catbox: {direct_url}")
                    return direct_url, None
        except Exception as e:
            print(f"⚠️ Помилка з'єднання з Catbox: {e}. Пробуємо наступний хостинг...")

        # 2️⃣ ImageKit.io
        imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
        if imagekit_key:
            print(f"☁️ Завантажуємо файл {filename} на ImageKit.io...")
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://upload.imagekit.io/api/v1/files/upload',
                        auth=(imagekit_key, ''),
                        files={'file': (filename, f, mime_type)},
                        data={'fileName': filename, 'useUniqueFileName': 'true'},
                        timeout=60
                    )
                    if res.status_code in [200, 201]:
                        res_data = res.json()
                        direct_url = res_data.get('url')
                        ik_id = res_data.get('fileId')
                        print(f"🔗 Отримано залізобетонне посилання від ImageKit: {direct_url}")
                        return direct_url, ik_id
            except Exception as e:
                print(f"⚠️ Помилка завантаження на ImageKit: {e}")

        # 3️⃣ ImgBB API
        imgbb_key = os.environ.get("IMGBB_API_KEY")
        if imgbb_key and mime_type == "image/jpeg":
            print(f"☁️ Завантажуємо фото {filename} на ImgBB API...")
            try:
                with open(local_file_path, 'rb') as f:
                    img_bytes = f.read()
                if img_bytes:
                    res = requests.post(
                        'https://api.imgbb.com/1/upload',
                        data={'key': imgbb_key, 'expiration': 86400},
                        files={'image': (filename, img_bytes, mime_type)},
                        timeout=30
                    ).json()
                    if res.get('success'):
                        direct_url = res['data']['url']
                        print(f"🔗 Отримано залізобетонне посилання від ImgBB: {direct_url}")
                        return direct_url, None
            except Exception as e:
                print(f"⚠️ Помилка завантаження на ImgBB: {e}")

    print(f"🚨 Всі хостинги відмовили! Аварійний режим для Google Drive ID: {file_id}")
    return f"https://docs.google.com/uc?export=download&id={file_id}", None

def delete_from_imagekit(file_id: str):
    if not file_id: return
    imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
    if not imagekit_key: return
    try:
        requests.delete(f"https://api.imagekit.io/v1/files/{file_id}", auth=(imagekit_key, ''), timeout=15)
        print(f"🗑️ Файл {file_id} видалено з ImageKit.")
    except: pass

def get_manufacturer_header(category, date_str, lang_idx, mode, target_loc=None):
    """Генерує естетичний заголовок відповідно до категорії, обраної мови та локації."""
    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else str(datetime.now().year)
    cat_lower = category.lower()
    
    # Використовуємо глобальний конфіг локалізації
    pref = LANG_CONFIG.get(lang_idx, LANG_CONFIG[0])
    header_lines = []

    invalid_markers = ["невідоме місце", "невідомо", "unknown", "unbekannt", "-", "none", "null"]
    has_valid_loc = target_loc and not any(marker in str(target_loc).lower() for marker in invalid_markers)

    # 1. СПЕЦІАЛЬНІ КАТЕГОРІЇ
    if "montage various" in cat_lower:
        header_lines.append(f"📅 {pref['year']}: {year}")
        if has_valid_loc:
            header_lines.append(f"📍 {pref['loc']}: {target_loc}")
        header_lines.append(f"🛠️ {pref['assembly']}")
        return "\n".join(header_lines) + "\n\n"
        
    if "various" in cat_lower:
        header_lines.append(f"📅 {pref['year']}: {year}")
        if has_valid_loc:
            header_lines.append(f"📍 {pref['loc']}: {target_loc}")
        header_lines.append(f"💡 {pref['concept']}")
        return "\n".join(header_lines) + "\n\n"
        
    if "instruktion" in cat_lower:
        header_lines.append(f"📐 {pref['ergonomics']}")
        if has_valid_loc:
            header_lines.append(f"📍 {pref['loc']}: {target_loc}")
        return "\n".join(header_lines) + "\n\n"

    # 2. ПОШУК У ГЛОБАЛЬНІЙ БАЗІ БРЕНДІВ
    for key, info in COMPANIES_DB.items():
        if key in cat_lower:
            correct_name = info["names"].get(lang_idx, info["names"][0])
            header_lines.append(f"📅 {pref['year']}: {year}")
            if has_valid_loc:
                header_lines.append(f"📍 {pref['loc']}: {target_loc}")
            header_lines.append(f"🛠️ {pref['brand']}: {correct_name}")
            
            # Розподіл посилань залежно від платформи (FB чи IG) з локалізацією заклику
            if "ig_" in mode:
                if info.get("ig_handle"):
                    if isinstance(info['ig_handle'], list):
                        for handle in info['ig_handle']:
                            header_lines.append(f"📸 Instagram: {handle}")
                    else:
                        header_lines.append(f"📸 Instagram: {info['ig_handle']}")
                header_lines.append(pref["link_in_bio"])
            else:
                if info.get("links"):
                    header_lines.extend(info["links"])
                    
            return "\n".join(header_lines) + "\n\n"
            
    header_lines.append(f"📅 {pref['year']}: {year}")
    if has_valid_loc:
        header_lines.append(f"📍 {pref['loc']}: {target_loc}")
    return "\n".join(header_lines) + "\n\n"

def generate_multimodal_caption(image_paths, category, date_str, lang_idx):
    gemini_key = os.environ.get("GEMINI_API_KEY")
    pref = LANG_CONFIG.get(lang_idx, LANG_CONFIG[0])
    
    if not gemini_key:
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
        for key, info in COMPANIES_DB.items():
            if key in cat_lower:
                real_manufacturer = info["names"].get(lang_idx, info["names"][0])
                break

    models_to_try = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]
    
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
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
            try:
                res = requests.post(url, json=payload, timeout=20).json()
                if 'candidates' in res and res['candidates']:
                    return res['candidates'][0]['content']['parts'][0]['text'].strip()
            except Exception as e:
                print(f"⚠️ Модель {model} тимчасово недоступна: {e}. Пробуємо наступну...")
                continue
                
        print("⚠️ Жодна з моделей Gemini не відповіла успішно, активовано дефолт.")
        return pref["fallback_caption"]
        
    except Exception as e:
        print(f"⚠️ Критична помилка виконання функції ШІ: {e}")
        return pref["fallback_caption"]

def wait_for_meta_container(container_id, access_token):
    """Очікує завершення асинхронної обробки відео/медіа контейнера в Meta API."""
    check_url = f"https://graph.facebook.com/v19.0/{container_id}"
    params = {"fields": "status_code,status", "access_token": access_token}
    for _ in range(30):
        try:
            r = requests.get(check_url, params=params).json()
            status = r.get("status_code", "").upper()
            if status == "FINISHED":
                print("✅ Контейнер успішно скомпіровано Meta.")
                return True
            elif status == "ERROR":
                print(f"❌ Помилка обробки контейнера Meta: {r.get('status')}")
                return False
            print(f"⏳ Очікування готовності контейнера... Статус: {status}")
        except Exception as e:
            print(f"⚠️ Помилка перевірки статусу: {e}")
        time.sleep(10)
    return False

def main():
    if len(sys.argv) < 3:
        print("💡 Необхідно передати параметри. Запуск: python script.py <mode> <tab_name>")
        sys.exit(1)

    mode = sys.argv[1].lower()  
    forced_tab = sys.argv[2]
    
    current_tab = forced_tab if forced_tab else TAB_NAME
    print(f"📊 [Режим: {mode.upper()}] Зчитування реєстру '{current_tab}'...")
    
    drive, sheets = get_services()

    res = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{current_tab}'!A2:I").execute()
    rows = res.get('values', [])
    if not rows:
        print("ℹ️ Реєстр порожній.")
        return

    if mode == "ig_post":
        col_idx = 3
        col_letter = "D"
    elif mode == "fb_post":
        col_idx = 5
        col_letter = "F"
    else:
        print(f"❌ Невідомий або непідтримуваний режим публікації: {mode}")
        sys.exit(1)

    valid_rows = []
    for i, r in enumerate(rows):
        if len(r) >= 3:  
            if r[2].lower() == "temporary": continue
            try:
                val = r[col_idx] if len(r) > col_idx and r[col_idx] else "0"
                counter = int(val)
                valid_rows.append({"row_idx": i + 2, "data": r, "counter": counter})
            except ValueError: continue

    if not valid_rows:
        print("ℹ️ Немає валідних рядків для обробки.")
        return

    min_counter = min(item["counter"] for item in valid_rows)
    min_pool = [item for item in valid_rows if item["counter"] == min_counter]

    groups = {}
    for item in min_pool:
        data = item["data"]
        group_key = (data[2], data[6] if len(data) > 6 else "", data[7] if len(data) > 7 else "")
        groups.setdefault(group_key, []).append(item)

    first_key = list(groups.keys())[0]
    selected_group_items = groups[first_key][:4]
    category_name, target_date, target_loc = first_key
    print(f"📂 Обрано групу: {category_name} (Файлів у пулі: {len(selected_group_items)})")

    # =====================================================================
    # 🌐 Управління мовами через фіксовані комірки F2, G2
    # =====================================================================
    lang_value = "UK"
    if mode == "fb_post":
        target_lang_cell = "'⚙️ Налаштування Папок'!F2"
    else:
        target_lang_cell = "'⚙️ Налаштування Папок'!G2"

    try:
        lang_res = sheets.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=target_lang_cell
        ).execute()
        lang_values = lang_res.get('values', [])
        if lang_values and lang_values[0]:
            lang_value = lang_values[0][0].strip().upper()
    except Exception as e:
        print(f"⚠️ Не вдалося прочитати мову з комірки {target_lang_cell}: {e}")

    if any(x in lang_value for x in ["EN", "ENG", "АНГЛ", "ENGLISH"]):
        lang_idx = 1
        next_lang_value = "DE"
    elif any(x in lang_value for x in ["DE", "GER", "НІМ", "DEUTSCH"]):
        lang_idx = 2
        next_lang_value = "UK"
    else:
        lang_idx = 0
        next_lang_value = "EN"
        
    print(f"🌐 Зчитано поточну мову з {target_lang_cell}: {lang_value} (Індекс: {lang_idx}). Наступна мова буде: {next_lang_value}")
    # =====================================================================
    
    os.makedirs('temp_mebli', exist_ok=True)
    local_files = []
    cloud_urls = []
    ik_ids = []
    has_video = False
    ai_analysis_images = []

    for item in selected_group_items:
        f_id, f_name = item["data"][0], item["data"][1]
        lower_name = f_name.lower()
        
        if not lower_name.endswith(VALID_MEDIA_EXTENSIONS):
            reason = "непідтримуваний формат"
            if lower_name.endswith(DOCUMENT_EXTENSIONS):
                reason = "непідтримуваний формат (документ)"
            
            print(f"⚠️ Файл '{f_name}' має непідтримуваний формат. Реєструємо помилку...")
            log_unsupported_to_service(sheets, category_name, f_name, reason=reason)
            continue

        local_path = os.path.join('temp_mebli', f_name)
        print(f"📥 Завантаження з Drive: {f_name}...")
        
        try:
            request = drive.files().get_media(fileId=f_id)
            with open(local_path, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done: _, done = downloader.next_chunk()
        except Exception as e:
            print(f"❌ Помилка завантаження файлу {f_name}: {e}")
            continue

        final_path = local_path
        mime_type = "image/jpeg"
        
        if lower_name.endswith(('.mp4', '.mov', '.avi')):
            has_video = True
            mime_type = "video/mp4"
        elif lower_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join('temp_mebli', f_name.rsplit('.', 1)[0] + '.jpg')
            with Image.open(local_path) as img:
                img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
            final_path = jpg_path
            local_files.append(jpg_path)

        optimized_path = optimize_media_geometry(final_path, f_name, mime_type)
        if optimized_path != final_path and optimized_path != local_path:
            local_files.append(optimized_path)

        if mime_type == "video/mp4":
            frame_path = os.path.join('temp_mebli', f"frame_{f_id}.jpg")
            subprocess.run(['ffmpeg', '-y', '-i', optimized_path, '-ss', '00:00:01', '-vframes', '1', frame_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ai_analysis_images.append(frame_path)
        else:
            ai_analysis_images.append(optimized_path)

        local_files.append(local_path)

        pub_url, ik_id = get_google_drive_direct_url(f_id, local_file_path=optimized_path)
        cloud_urls.append(pub_url)
        if ik_id: ik_ids.append(ik_id)

    if not cloud_urls:
        print("ℹ️ Немає доступних медіафайлів для публікації.")
        return

    header_text = get_manufacturer_header(category_name, target_date, lang_idx, mode, target_loc)
    ai_text = generate_multimodal_caption(ai_analysis_images, category_name, target_date, lang_idx)
    full_caption = f"{header_text}{ai_text}"

    if not FB_PAGE_ID and mode == "fb_post":
        print("❌ Відсутній FB_PAGE_ID для публікації у Facebook!")
        sys.exit(1)
    if not IG_USER_ID and mode == "ig_post":
        print("❌ Відсутній IG_USER_ID для публікації в Instagram!")
        sys.exit(1)

    res = None

    # ==========================================
    # 🌍 ВАРІАНТ 1: FACEBOOK ПОСТ (`fb_post`)
    # ==========================================
    if mode == "fb_post":
        if has_video:
            print("🎬 Публікація відео-поста у Facebook...")
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/videos"
            payload = {"file_url": cloud_urls[0], "description": full_caption, "access_token": META_ACCESS_TOKEN}
            res = requests.post(fb_url, data=payload).json()
        else:
            print(f"🖼️ Публікація фото-альбому ({len(cloud_urls)} шт.) у Facebook...")
            attached_media = []
            for url in cloud_urls:
                photo_res = requests.post(f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/photos", data={
                    "url": url, "published": "false", "access_token": META_ACCESS_TOKEN
                }).json()
                if "id" in photo_res:
                    attached_media.append({"media_fbid": photo_res["id"]})
            
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/feed"
            payload = {
                "message": full_caption,
                "attached_media": json.dumps(attached_media),
                "access_token": META_ACCESS_TOKEN
            }
            res = requests.post(fb_url, data=payload).json()

    # ==========================================
    # 📸 ВАРІАНТ 2: INSTAGRAM ПОСТ (`ig_post`)
    # ==========================================
    elif mode == "ig_post":
        if len(cloud_urls) > 1:
            print(f"🗂️ Створення каруселі Instagram з {len(cloud_urls)} елементів...")
            container_ids = []
            for url in cloud_urls:
                is_vid = url.lower().split('?')[0].endswith(('.mp4', '.mov', '.avi')) or "video" in url
                param_type = "video_url" if is_vid else "image_url"
                
                payload = {
                    param_type: url,
                    "is_carousel_item": "true",
                    "access_token": META_ACCESS_TOKEN
                }
                if is_vid: payload["media_type"] = "VIDEO"
                
                item_res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media", data=payload).json()
                if "id" in item_res:
                    item_id = item_res["id"]
                    if is_vid:
                        wait_for_meta_container(item_id, META_ACCESS_TOKEN)
                    container_ids.append(item_id)
            
            carousel_payload = {
                "media_type": "CAROUSEL",
                "children": json.dumps(container_ids),
                "caption": full_caption,
                "access_token": META_ACCESS_TOKEN
            }
            res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media", data=carousel_payload).json()
            
        else:
            print("🖼️ Створення одиничного контейнера в Instagram...")
            is_vid = cloud_urls[0].lower().split('?')[0].endswith(('.mp4', '.mov', '.avi')) or "video" in cloud_urls[0]
            param_type = "video_url" if is_vid else "image_url"
            
            payload = {
                param_type: cloud_urls[0],
                "caption": full_caption,
                "access_token": META_ACCESS_TOKEN
            }
            if is_vid: payload["media_type"] = "VIDEO"
            
            res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media", data=payload).json()

            if res and "id" in res and is_vid:
                wait_for_meta_container(res["id"], META_ACCESS_TOKEN)

        if res and "id" in res:
            creation_id = res["id"]
            if has_video:
                wait_for_meta_container(creation_id, META_ACCESS_TOKEN)
                
            print("🚀 Фінальна публікація контейнера в Instagram стрічку...")
            
            for attempt in range(6):
                publish_res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={
                    "creation_id": creation_id, "access_token": META_ACCESS_TOKEN
                }).json()
                
                if "error" in publish_res:
                    err = publish_res["error"]
                    if err.get("error_subcode") == 2207027 or err.get("code") == 9007:
                        print(f"⏳ Медіафайл ще обробляється серверами Meta (Спроба {attempt + 1}/6). Очікуємо 10 секунд...")
                        time.sleep(10)
                        continue
                res = publish_res
                break

    # ==========================================
    # 💾 ПЕРЕВІРКА РЕЗУЛЬТАТУ ТА ОНОВЛЕННЯ БАЗИ
    # ==========================================
    if res and ("id" in res or "post_id" in res):
        print(f"✅ Успішно опубліковано! ID контенту: {res.get('id', res.get('post_id'))}")
        
        for item in selected_group_items:
            if item["data"][1].lower().endswith(VALID_MEDIA_EXTENSIONS):
                new_val = item["counter"] + 1
                range_to_update = f"'{current_tab}'!{col_letter}{item['row_idx']}"
                try:
                    sheets.spreadsheets().values().update(
                        spreadsheetId=SPREADSHEET_ID, range=range_to_update,
                        valueInputOption='RAW', body={'values': [[new_val]]}
                    ).execute()
                    print(f"✍️ Лічильник рядка {item['row_idx']} у колонці {col_letter} збільшено до {new_val}.")
                except Exception as e:
                    print(f"⚠️ Помилка збереження лічильника в Таблицю: {e}")
                    
        try:
            sheets.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID, range=target_lang_cell,
                valueInputOption='RAW', body={'values': [[next_lang_value]]}
            ).execute()
            print(f"🔄 Мову на наступний раз для комірки {target_lang_cell} успішно змінено на: {next_lang_value}")
        except Exception as e:
            print(f"⚠️ Не вдалося оновити мову в комірці {target_lang_cell}: {e}")

        if ik_ids:
            print("🧹 Очищення хмари ImageKit...")
            for ik_id in ik_ids:
                delete_from_imagekit(ik_id)
            
    else:
        print(f"❌ Помилка дистриб'юції контенту Meta API: {res}")
        for f in local_files:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass
        print("🧹 Тимчасова папка очищена. Скрипт зупинено аварійно.")
        sys.exit(1)

    for f in local_files:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
    print("🧹 Тимчасова папка очищена.")

if __name__ == "__main__":
    main()
