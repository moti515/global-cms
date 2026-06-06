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

def optimize_media_geometry(local_path, filename, mime_type, mode="post"):
    """Оптимізує пропорції зображень та відео під вимоги Meta (стрічка/stories)."""
    if not os.path.exists(local_path):
        return local_path

    if mode == 'post' and mime_type == "image/jpeg":
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

    elif mode == 'story' and mime_type == "image/jpeg":
        print("📐 Режим Сторіс: вписуємо зображення у формат 1080x1920...")
        story_path = os.path.join('temp_mebli', 'story_padded_' + filename.rsplit('.', 1)[0] + '.jpg')
        try:
            with Image.open(local_path) as img:
                img = img.convert('RGB')
                orig_w, orig_h = img.size
                
                target_w, target_h = 1080, 1920
                canvas = Image.new('RGB', (target_w, target_h), (20, 20, 20))
                
                scale = min(target_w / orig_w, target_h / orig_h)
                new_w = int(orig_w * scale)
                new_h = int(orig_h * scale)
                
                resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                paste_x = (target_w - new_w) // 2
                paste_y = (target_h - new_h) // 2
                canvas.paste(resized_img, (paste_x, paste_y))
                canvas.save(story_path, 'JPEG', quality=95)
                return story_path
        except Exception as e:
            print(f"⚠️ Не вдалося відформатувати Сторіс: {e}")

    elif mode == 'story' and mime_type == "video/mp4":
        print("📐 Режим Сторіс для ВІДЕО: вписуємо у формат 1080x1920 через ffmpeg...")
        story_video_path = os.path.join('temp_mebli', 'story_padded_' + filename.rsplit('.', 1)[0] + '.mp4')
        
        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', local_path,
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black',
            '-movflags', 'faststart', '-pix_fmt', 'yuv420p',
            story_video_path
        ]
        
        result = subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            print("✅ Відео успішно конвертовано у вертикальний формат з полями!")
            return story_video_path
        else:
            print("⚠️ Не вдалося обробити відео через ffmpeg, повертаємо оригінал.")

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
    except: pass

def get_manufacturer_header(category, date_str, lang_idx):
    """Генерує заголовок відповідно до категорії та обраної мови (0=UK, 1=EN, 2=DE)."""
    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else "2026"
    cat_lower = category.lower()
    
    if "goncharenko" in cat_lower:
        if lang_idx == 0: return f"📅 Рік: {year}\n🛠️ Виробник: https://instagram.com/goncharenko8721\n\n"
        elif lang_idx == 1: return f"📅 Year: {year}\n🛠️ Manufacturer: https://instagram.com/goncharenko8721\n\n"
        else: return f"📅 Jahr: {year}\n🛠️ Hersteller: https://instagram.com/goncharenko8721\n\n"
        
    elif "gurov" in cat_lower:
        if lang_idx == 0: return f"📅 Рік: {year}\n🛠️ Виробник: https://www.facebook.com/andrej.gurov.755581\n\n"
        elif lang_idx == 1: return f"📅 Year: {year}\n🛠️ Manufacturer: https://www.facebook.com/andrej.gurov.755581\n\n"
        else: return f"📅 Jahr: {year}\n🛠️ Hersteller: https://www.facebook.com/andrej.gurov.755581\n\n"
        
    elif "solovey" in cat_lower:
        if lang_idx == 0: return f"📅 Рік: {year}\n🛠️ Виробник: https://instagram.com/mebelsolovei\n\n"
        elif lang_idx == 1: return f"📅 Year: {year}\n🛠️ Manufacturer: https://instagram.com/mebelsolovei\n\n"
        else: return f"📅 Jahr: {year}\n🛠️ Hersteller: https://instagram.com/mebelsolovei\n\n"
        
    elif "furniture park" in cat_lower:
        if lang_idx == 0:
            return f"📅 Рік: {year}\n🛠️ Виробник: Furniture Park\n📸 Instagram:\n• https://instagram.com/meblevyi_park\n• https://instagram.com/meblovo_ukraine\n• https://instagram.com/renovaelite\n📢 Telegram:\n• https://t.me/Meblevyi_park\n\n"
        elif lang_idx == 1:
            return f"📅 Year: {year}\n🛠️ Manufacturer: Furniture Park\n📸 Instagram:\n• https://instagram.com/meblevyi_park\n• https://instagram.com/meblovo_ukraine\n• https://instagram.com/renovaelite\n📢 Telegram:\n• https://t.me/Meblevyi_park\n\n"
        else:
            return f"📅 Jahr: {year}\n🛠️ Hersteller: Furniture Park\n📸 Instagram:\n• https://instagram.com/meblevyi_park\n• https://instagram.com/meblovo_ukraine\n• https://instagram.com/renovaelite\n📢 Telegram:\n• https://t.me/Meblevyi_park\n\n"
            
    elif "montage various" in cat_lower:
        if lang_idx == 0: return f"📅 Рік: {year}\n🛠️ Монтаж: Меблі, у монтажі яких мы брали участь (професійне збирання)\n\n"
        elif lang_idx == 1: return f"📅 Year: {year}\n🛠️ Assembly: Furniture we helped assemble (professional installation)\n\n"
        else: return f"📅 Jahr: {year}\n🛠️ Montage: Möbel, bei deren Montage wir mitgewirkt haben (professioneller Aufbau)\n\n"
        
    elif "various" in cat_lower:
        if lang_idx == 0: return f"📅 Рік: {year}\n💡 Концепт: Цікаві меблеві рішення, тренди та ідеї з усього світу\n\n"
        elif lang_idx == 1: return f"📅 Year: {year}\n💡 Concept: Interesting furniture solutions, trends, and ideas from around the world\n\n"
        else: return f"📅 Jahr: {year}\n💡 Konzept: Interessante Möbellösungen, Trends und Ideen aus aller Welt\n\n"
        
    elif "instruktion" in cat_lower:
        # Для папки інструкцій рік повністю прибрано за запитом
        if lang_idx == 0: return "📐 Ергономіка та проектування: Корисні стандарти та розміри, яких варто дотримуватися при проектуванні меблів.\n\n"
        elif lang_idx == 1: return "📐 Ergonomics and Design: Useful standards and dimensions to follow when designing furniture.\n\n"
        else: return "📐 Ergonomie und Konstruktion: Nützliche Standards und Maße, die bei der Möbelkonstruktion beachtet werden sollten.\n\n"
        
    return ""  # Порожньо для всіх інших невизначених папок (Замість "Серія: ...")

def generate_multimodal_caption(image_paths, category, date_str, lang_idx):
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        if lang_idx == 0: return "Якісні меблі для вашого затишку! 👇✨ #меблі #інтерєр"
        elif lang_idx == 1: return "Quality furniture for your comfort! 👇✨ #furniture #interiordesign"
        else: return "Qualitätsmöbel für Ihr gemütliches Zuhause! 👇✨ #moebel #interieur"

    lang_instructions = {
        0: "Напиши text виключно УКРАЇНСЬКОЮ мовою. Використовуй емодзі.",
        1: "Write the text exclusively in ENGLISH. Use emojis.",
        2: "Schreibe den Text ausschließlich auf DEUTSCH. Nutze Emojis."
    }
    
    prompt = (
        f"Ти професійний копірайтер та меблевий експерт. Подивись на ці зображення. "
        f"Напиши один короткий, натхненний пост для соцмереж. "
        f"Категорія об'єкта: '{category}'. {lang_instructions[lang_idx]} "
        f"КРИТИЧНО: Не пиши жодних передмов. Тільки text поста."
    )

    parts = [{"text": prompt}]
    for img_path in image_paths:
        if os.path.exists(img_path):
            try:
                with open(img_path, "rb") as f:
                    base64_image = base64.b64encode(f.read()).decode('utf-8')
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64_image}})
            except: pass

    payload = {"contents": [{"parts": parts}]}
    for model in ["gemini-2.5-flash", "gemini-1.5-flash"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
        try:
            res = requests.post(url, json=payload, timeout=25).json()
            if 'candidates' in res and res['candidates']:
                return res['candidates'][0]['content']['parts'][0]['text'].strip()
        except: continue
    return "Чудова робота нашої команди! Як вам результат? 👇😊"

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
        return

    mode = sys.argv[1].lower()  # fb_post, ig_post, ig_story
    forced_tab = sys.argv[2]
    
    current_tab = forced_tab if forced_tab else TAB_NAME
    print(f"📊 [Режим: {mode.upper()}] Зчитування реєстру '{current_tab}'...")
    
    drive, sheets = get_services()
    
    res = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{current_tab}'!A2:H").execute()
    rows = res.get('values', [])
    if not rows:
        print("ℹ️ Реєстр порожній.")
        return

    valid_rows = []
    for i, r in enumerate(rows):
        if len(r) >= 6:
            if r[2].lower() == "temporary": continue
            try:
                counter = int(r[5]) if r[5] else 0
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

    os.makedirs('temp_mebli', exist_ok=True)
    local_files = []
    cloud_urls = []
    ik_ids = []
    has_video = False
    ai_analysis_images = []

    # Визначаємо тип обробки геометрії
    geom_mode = "story" if mode == "ig_story" else "post"

    # 📥 Фільтрація, завантаження та оптимізація медіафайлів
    for item in selected_group_items:
        f_id, f_name = item["data"][0], item["data"][1]
        lower_name = f_name.lower()
        
        # 🛑 ОБРОБКА НЕПІДТРИМУВАНИХ ФОРМАТІВ
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

        # 📐 Оптимізація геометрії медіа відповідно до режиму
        optimized_path = optimize_media_geometry(final_path, f_name, mime_type, mode=geom_mode)
        if optimized_path != final_path and optimized_path != local_path:
            local_files.append(optimized_path)

        # Створення прев'ю кадру для ШІ
        if mime_type == "video/mp4":
            frame_path = os.path.join('temp_mebli', f"frame_{f_id}.jpg")
            subprocess.run(['ffmpeg', '-y', '-i', optimized_path, '-ss', '00:00:01', '-vframes', '1', frame_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ai_analysis_images.append(frame_path)
        else:
            ai_analysis_images.append(optimized_path)

        local_files.append(local_path)

        # ☁️ Завантаження на хмарний хостинг
        pub_url, ik_id = get_google_drive_direct_url(f_id, local_file_path=optimized_path)
        cloud_urls.append(pub_url)
        if ik_id: ik_ids.append(ik_id)

    if not cloud_urls:
        print("ℹ️ Немає доступних медіафайлів для публікації.")
        return

    # ✍️ Збір контенту для публікації
    header_text = get_manufacturer_header(category_name, target_date)
    ai_text = generate_multimodal_caption(ai_analysis_images, category_name, target_date)
    loc_footer = f"\n\n📍 Локація: {target_loc}" if target_loc and "Невідоме місце" not in target_loc else ""
    full_caption = f"{header_text}{ai_text}{loc_footer}"

    if not FB_PAGE_ID and mode == "fb_post":
        print("❌ Відсутній FB_PAGE_ID для публікації у Facebook!")
        return
    if not IG_USER_ID and mode in ["ig_post", "ig_story"]:
        print("❌ Відсутній IG_USER_ID для публікації в Instagram!")
        return

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
        # Карусель (кілька зображень/відео)
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
                    container_ids.append(item_res["id"])
            
            carousel_payload = {
                "media_type": "CAROUSEL",
                "children": json.dumps(container_ids),
                "caption": full_caption,
                "access_token": META_ACCESS_TOKEN
            }
            res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media", data=carousel_payload).json()
            
        # Один медіафайл (фото або відео)
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

        if res and "id" in res:
            creation_id = res["id"]
            if has_video:
                wait_for_meta_container(creation_id, META_ACCESS_TOKEN)
                
            print("🚀 Фінальна публікація контейнера в Instagram стрічку...")
            publish_res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={
                "creation_id": creation_id, "access_token": META_ACCESS_TOKEN
            }).json()
            res = publish_res

    # ==========================================
    # ⚡ ВАРІАНТ 3: INSTAGRAM СТОРІЗ (`ig_story`)
    # ==========================================
    elif mode == "ig_story":
        print("⚡ Публікація елемента в Instagram Stories...")
        is_vid = cloud_urls[0].lower().split('?')[0].endswith(('.mp4', '.mov', '.avi')) or "video" in cloud_urls[0]
        param_type = "video_url" if is_vid else "image_url"
        
        story_payload = {
            param_type: cloud_urls[0],
            "media_type": "STORIES",
            "access_token": META_ACCESS_TOKEN
        }
        
        res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media", data=story_payload).json()
        if res and "id" in res:
            creation_id = res["id"]
            if is_vid or has_video:
                wait_for_meta_container(creation_id, META_ACCESS_TOKEN)
                
            print("🚀 Фінальний деплой сторіз...")
            publish_res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={
                "creation_id": creation_id, "access_token": META_ACCESS_TOKEN
            }).json()
            res = publish_res

    # ==========================================
    # 💾 ПЕРЕВІРКА РЕЗУЛЬТАТУ ТА ОНОВЛЕННЯ БАЗИ
    # ==========================================
    if res and ("id" in res or "post_id" in res):
        print(f"✅ Успішно опубліковано! ID контенту: {res.get('id', res.get('post_id'))}")
        
        for item in selected_group_items:
            if item["data"][1].lower().endswith(VALID_MEDIA_EXTENSIONS):
                new_val = item["counter"] + 1
                range_to_update = f"'{current_tab}'!F{item['row_idx']}"
                try:
                    sheets.spreadsheets().values().update(
                        spreadsheetId=SPREADSHEET_ID, range=range_to_update,
                        valueInputOption='RAW', body={'values': [[new_val]]}
                    ).execute()
                    print(f"✍️ Лічильник рядка {item['row_idx']} збільшено до {new_val}.")
                except Exception as e:
                    print(f"⚠️ Помилка збереження лічильника в Таблицю: {e}")
    else:
        print(f"❌ Помилка дистриб'юції контенту Meta API: {res}")

    # 🧹 Очищення тимчасових локальних медіафайлів
    for f in local_files:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
    print("🧹 Тимчасова папка очищена.")

if __name__ == "__main__":
    main()
