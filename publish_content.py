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

# ⚙️ НАЛАШТУВАННЯ ТА СЕКРЕТИ
FB_PAGE_ID = os.environ.get("MEBLI_FB_PAGE_ID")
META_ACCESS_TOKEN = os.environ.get("MEBLI_ACCESS_TOKEN")
SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
TAB_NAME = "Меблі"

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']
VALID_MEDIA_EXTENSIONS = ('.gif', '.heic', '.heif', '.jpeg', '.jpg', '.mp4', '.png', '.webp', '.mov', '.avi')

def get_services():
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds), build('sheets', 'v4', credentials=creds)

def optimize_media_geometry(local_path, filename, mime_type, mode="post"):
    """
    Оптимізує пропорції зображень та відео під вимоги Meta (стрічка/stories),
    щоб запобігти помилкам кропу чи помилці API 36003.
    """
    if not os.path.exists(local_path):
        return local_path

    # 1️⃣ ОПТИМІЗАЦІЯ ПРОПОРЦІЙ ДЛЯ ЗВИЧАЙНИХ ПОСТІВ (Межі Meta: 0.8 - 1.91)
    if mode == 'post' and mime_type == "image/jpeg":
        try:
            with Image.open(local_path) as img:
                img = img.convert('RGB')
                w, h = img.size
                ratio = w / h
                
                if ratio < 0.8 or ratio > 1.91:
                    print(f"📐 Оптимізація Поста: Пропорції картинки ({ratio:.2f}) неприпустимі. Коригуємо...")
                    padded_post_path = os.path.join('temp_fb', 'post_padded_' + filename.rsplit('.', 1)[0] + '.jpg')
                    
                    if ratio < 0.8:
                        new_w = int(h * 0.8)
                        new_h = h
                    else:
                        new_w = w
                        new_h = int(w / 1.91)
                        
                    canvas = Image.new('RGB', (new_w, new_h), (255, 255, 255)) # Елегантне біле тло
                    paste_x = (new_w - w) // 2
                    paste_y = (new_h - h) // 2
                    
                    canvas.paste(img, (paste_x, paste_y))
                    canvas.save(padded_post_path, 'JPEG', quality=95)
                    return padded_post_path
        except Exception as e:
            print(f"⚠️ Помилка калібрування геометрії поста: {e}")

    # 2️⃣ ОПТИМІЗАЦІЯ ФОТО ПІД СТОРІС (1080x1920)
    elif mode == 'story' and mime_type == "image/jpeg":
        print("📐 Режим Сторіс: вписуємо зображення у формат 1080x1920...")
        story_path = os.path.join('temp_fb', 'story_padded_' + filename.rsplit('.', 1)[0] + '.jpg')
        try:
            with Image.open(local_path) as img:
                img = img.convert('RGB')
                orig_w, orig_h = img.size
                
                target_w, target_h = 1080, 1920
                canvas = Image.new('RGB', (target_w, target_h), (20, 20, 20)) # Нейтральне темне тло
                
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

    # 3️⃣ ОПТИМІЗАЦІЯ ВІДЕО ПІД СТОРІС (1080x1920 через FFmpeg)
    elif mode == 'story' and mime_type == "video/mp4":
        print("📐 Режим Сторіс для ВІДЕО: вписуємо у формат 1080x1920 через ffmpeg...")
        story_video_path = os.path.join('temp_fb', 'story_padded_' + filename.rsplit('.', 1)[0] + '.mp4')
        
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
    """
    Каскадний завантажувач медіафайлів на зовнішні хостинги з API.
    Порядок: Catbox.moe -> ImageKit.io (Універсальний) -> ImgBB (Тільки фото) -> Google Drive API
    """
    if local_file_path and os.path.exists(local_file_path):
        filename = os.path.basename(local_file_path)
        lower_name = filename.lower()
        mime_type = "video/mp4" if lower_name.endswith(('.mp4', '.mov', '.avi')) else "image/jpeg"
        
        browser_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
        }
        
        # 1️⃣ Спроба через Catbox.moe (Фото + Відео)
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

        # 2️⃣ Спроба через ImageKit.io
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

        # 3️⃣ Спроба через ImgBB API (Тільки для Фото)
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

def wait_for_instagram_media(container_id, access_token, max_retries=15, delay=10):
    """Циклічно перевіряє статус готовності медіаконтейнера в Instagram."""
    url = f"https://graph.facebook.com/v19.0/{container_id}"
    params = {"fields": "status_code", "access_token": access_token}
    
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, params=params).json()
            status = response.get("status_code")
            if status == "FINISHED":
                print("✅ Відео успішно оброблено й готове до публікації!")
                return True
            elif status == "ERROR":
                print(f"❌ Помилка обробки відео на стороні Meta: {response}")
                return False
            elif status in ["IN_PROGRESS", "CREATING"]:
                print(f"⏳ Обробка відео (Статус: {status}). Чекаємо {delay} сек... ({attempt}/{max_retries})")
                time.sleep(delay)
        except Exception as e:
            print(f"⚠️ Помилка запиту статусу: {e}")
            time.sleep(delay)
    return False

def get_manufacturer_header(category, date_str):
    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else "2026"
    cat_lower = category.lower()
    
    if "goncharenko" in cat_lower:
        return f"📅 Рік: {year}\n🛠️ Виробник: https://instagram.com/goncharenko8721\n\n"
    elif "gurov" in cat_lower:
        return f"📅 Рік: {year}\n🛠️ Виробник: https://www.facebook.com/andrej.gurov.755581\n\n"
    elif "solovey" in cat_lower:
        return f"📅 Рік: {year}\n🛠️ Виробник: https://instagram.com/mebelsolovei\n\n"
    elif "furniture park" in cat_lower:
        return (
            f"📅 Рік: {year}\n🛠️ Виробник: Furniture Park\n📸 Instagram:\n"
            f"• https://instagram.com/meblevyi_park\n• https://instagram.com/meblovo_ukraine\n• https://instagram.com/renovaelite\n"
            f"📢 Telegram:\n• https://t.me/Meblevyi_park\n\n"
        )
    elif "montage various" in cat_lower:
        return f"📅 Рік: {year}\n🛠️ Монтаж: Меблі, у монтажі яких ми брали участь (професійне збирання)\n\n"
    elif "various" in cat_lower:
        return f"📅 Рік: {year}\n💡 Концепт: Цікаві меблеві рішення, тренди та ідеї з усього світу\n\n"
    elif "instruktion" in cat_lower:
        return "📐 Ергономіка та проектування: Корисні стандарти та розміри, яких варто дотримуватися при проектуванні меблів.\n\n"
    else:
        return f"📅 Рік: {year}\n📦 Серія: {category}\n\n"

def generate_multimodal_caption(image_paths, category, date_str):
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return "Якісні меблі для вашого затишку! 👇✨ #меблі #інтерєр"

    lang_idx = int(time.time() // (8.5 * 3600)) % 3
    lang_instructions = {
        0: "Напиши текст виключно УКРАЇНСЬКОЮ мовою. Використовуй емодзі.",
        1: "Write the text exclusively in ENGLISH. Use emojis.",
        2: "Schreibe den Text ausschließlich auf DEUTSCH. Nutze Emojis."
    }
    
    prompt = (
        f"Ти професійний копірайтер та меблевий експерт. Подивись на ці зображення (це один об'єкт або серія корисних схем). "
        f"Напиши один короткий, натхненний, мотиваційний або експертний пост для соцмереж. "
        f"Врахуй, що категорія об'єкта: '{category}'. {lang_instructions[lang_idx]} "
        f"КРИТИЧНО: Не пиши жодних передмов чи системних повідомлень. Тільки готовий текст поста."
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

def main():
    if len(sys.argv) < 2 or sys.argv[1].lower() != "fb_post":
        print("💡 Автономний режим Facebook. Запуск: python script.py fb_post")
        return

    print("📊 [FB Mode] Зчитування реєстру 'Меблі'...")
    drive, sheets = get_services()
    
    res = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{TAB_NAME}'!A2:H").execute()
    rows = res.get('values', [])
    if not rows:
        print("ℹ️ Реєстр порожній.")
        return

    valid_rows = []
    for i, r in enumerate(rows):
        if len(r) >= 6:
            if r[2].lower() == "temporary": continue
            try:
                fb_counter = int(r[5]) if r[5] else 0
                valid_rows.append({"row_idx": i + 2, "data": r, "fb_counter": fb_counter})
            except ValueError: continue

    if not valid_rows:
        print("ℹ️ Немає валідних рядків для Facebook.")
        return

    min_fb = min(item["fb_counter"] for item in valid_rows)
    min_pool = [item for item in valid_rows if item["fb_counter"] == min_fb]

    groups = {}
    for item in min_pool:
        data = item["data"]
        group_key = (data[2], data[6] if len(data) > 6 else "", data[7] if len(data) > 7 else "")
        groups.setdefault(group_key, []).append(item)

    first_key = list(groups.keys())[0]
    selected_group_items = groups[first_key][:4]
    category_name, target_date, target_loc = first_key
    print(f"📂 Обрано групу: {category_name} (Файлів: {len(selected_group_items)})")

    os.makedirs('temp_fb', exist_ok=True)
    local_files = []
    cloud_urls = []
    ik_ids = []
    has_video = False
    ai_analysis_images = []

    # 📥 Завантаження, оптимізація геометрії та деплой на хостинги
    for item in selected_group_items:
        f_id, f_name = item["data"][0], item["data"][1]
        lower_name = f_name.lower()
        
        local_path = os.path.join('temp_fb', f_name)
        print(f"📥 Завантаження з Drive: {f_name}...")
        
        request = drive.files().get_media(fileId=f_id)
        with open(local_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done: _, done = downloader.next_chunk()

        # Первинна фільтрація розширень
        final_path = local_path
        mime_type = "image/jpeg"
        
        if lower_name.endswith(('.mp4', '.mov', '.avi')):
            has_video = True
            mime_type = "video/mp4"
        elif lower_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join('temp_fb', f_name.rsplit('.', 1)[0] + '.jpg')
            with Image.open(local_path) as img:
                img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
            final_path = jpg_path
            local_files.append(jpg_path)

        # 📐 Запуск блоку інваріантної оптимізації пропорцій (за замовчуванням 'post')
        optimized_path = optimize_media_geometry(final_path, f_name, mime_type, mode="post")
        if optimized_path != final_path and optimized_path != local_path:
            local_files.append(optimized_path)

        # Генерація кадрів для ШІ аналізу відео
        if mime_type == "video/mp4":
            frame_path = os.path.join('temp_fb', f"frame_{f_id}.jpg")
            subprocess.run(['ffmpeg', '-y', '-i', optimized_path, '-ss', '00:00:01', '-vframes', '1', frame_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ai_analysis_images.append(frame_path)
        else:
            ai_analysis_images.append(optimized_path)

        local_files.append(local_path)

        # ☁️ Відправка готового оптимізованого медіа на хостинг
        pub_url, ik_id = get_google_drive_direct_url(f_id, local_file_path=optimized_path)
        cloud_urls.append(pub_url)
        if ik_id: ik_ids.append(ik_id)

    # ✍️ Формування контенту поста
    header_text = get_manufacturer_header(category_name, target_date)
    ai_text = generate_multimodal_caption(ai_analysis_images, category_name, target_date)
    loc_footer = f"\n\n📍 Локація: {target_loc}" if target_loc and "Невідоме місце" not in target_loc else ""
    full_caption = f"{header_text}{ai_text}{loc_footer}"

    if not FB_PAGE_ID or not META_ACCESS_TOKEN:
        print("❌ Відсутні ключі авторизації Facebook!")
        return

    # 📤 Публікація в Meta API
    try:
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

        if "id" in res or "post_id" in res:
            print(f"✅ Успішно опубліковано! ID: {res.get('id', res.get('post_id'))}")
            for item in selected_group_items:
                sheets.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID, range=f"'{TAB_NAME}'!F{item['row_idx']}",
                    valueInputOption='RAW', body={'values': [[item["fb_counter"] + 1]]}
                ).execute()
            print(f"📊 Лічильники в Sheets оновлено.")
        else:
            print(f"⚠️ Помилка відповіді Meta API: {res}")

    except Exception as e:
        print(f"❌ Критична помилка під час публікації: {e}")
    finally:
        # 🧹 Повне очищення середовища
        for ik_id in ik_ids: delete_from_imagekit(ik_id)
        for f in set(local_files + ai_analysis_images):
            if os.path.exists(f): os.remove(f)
        print("🧹 Тимчасові медіафайли успішно видалено.")

if __name__ == '__main__':
    main()
