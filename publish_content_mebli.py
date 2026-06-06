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

def get_google_drive_direct_url(file_id, local_file_path=None):
    """Каскадний завантажувач медіа на зовнішні хостинги (Catbox -> ImageKit -> Google Drive UC)"""
    if local_file_path and os.path.exists(local_file_path):
        filename = os.path.basename(local_file_path)
        lower_name = filename.lower()
        mime_type = "video/mp4" if lower_name.endswith(('.mp4', '.mov', '.avi')) else "image/jpeg"
        browser_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        
        # 1. Catbox.moe
        try:
            with open(local_file_path, 'rb') as f:
                file_bytes = f.read()
            if file_bytes:
                res = requests.post(
                    'https://catbox.moe/user/api.php',
                    data={'reqtype': 'fileupload'},
                    files={'fileToUpload': (filename, file_bytes, mime_type)},
                    headers=browser_headers, timeout=25
                )
                if res.status_code == 200 and res.text.startswith('http'):
                    return res.text.strip(), None
        except Exception as e:
            print(f"⚠️ Помилка з Catbox: {e}")

        # 2. ImageKit.io
        imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
        if imagekit_key:
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://upload.imagekit.io/api/v1/files/upload',
                        auth=(imagekit_key, ''),
                        files={'file': (filename, f, mime_type)},
                        data={'fileName': filename, 'useUniqueFileName': 'true'},
                        timeout=40
                    )
                if res.status_code in [200, 201]:
                    res_data = res.json()
                    return res_data.get('url'), res_data.get('fileId')
            except Exception as e:
                print(f"⚠️ Помилка з ImageKit: {e}")

    return f"https://docs.google.com/uc?export=download&id={file_id}", None

def delete_from_imagekit(file_id: str):
    if not file_id: return
    imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
    if not imagekit_key: return
    try:
        requests.delete(f"https://api.imagekit.io/v1/files/{file_id}", auth=(imagekit_key, ''), timeout=15)
    except: pass

def get_manufacturer_header(category, date_str):
    """Формує кастомний заголовок на основі категорії фабрики та дати"""
    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else "2026"
    cat_lower = category.lower()
    
    if "goncharenko" in cat_lower:
        return f"📅 Рік: {year}\n🛠️ Виробник: @goncharenko8721\n\n"
    elif "gurov" in cat_lower:
        return f"📅 Рік: {year}\n🛠️ Виробник: https://www.facebook.com/andrej.gurov.755581\n\n"
    elif "solovey" in cat_lower:
        return f"📅 Рік: {year}\n🛠️ Виробник: @mebelsolovei\n\n"
    elif "furniture park" in cat_lower:
        return f"📅 Рік: {year}\n🛠️ Виробник: Instagram @meblevyi_park + @meblovo_ukraine + @renovaelite | Telegram @Meblevyi_park\n\n"
    elif "montage various" in cat_lower:
        return f"📅 Рік: {year}\n🛠️ Монтаж: Меблі, у монтажі яких ми брали участь\n\n"
    elif "various" in cat_lower:
        return f"📅 Рік: {year}\n💡 Концепт: Цікаві меблеві рішення та ідеї\n\n"
    elif "instruktion" in cat_lower:
        return "📐 Ергономіка та проектування: Корисні розміри, яких варто дотримуватися при дизайні меблів.\n\n"
    else:
        return f"📅 Рік: {year}\n📦 Серія: {category}\n\n"

def generate_multimodal_caption(image_paths, category, date_str):
    """ШІ аналізує до 4-х зображень одночасно і генерує опис однією з мов за графіком"""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return "Якісні меблі для вашого затишку! 👇✨ #меблі #інтерєр"

    # Ротація мов кожні 8.5 годин (0 = UA, 1 = EN, 2 = DE)
    lang_idx = int(time.time() // (8.5 * 3600)) % 3
    lang_instructions = {
        0: "Напиши текст виключно УКРАЇНСЬКОЮ мовою. Використовуй емодзі.",
        1: "Write the text exclusively in ENGLISH. Use emojis.",
        2: "Schreibe den Text ausschließlich auf DEUTSCH. Nutze Emojis."
    }
    
    prompt = (
        f"Ти професійний копірайтер та меблевий експерт. Подивись на ці зображення (це один об'єкт або серія корисних схем). "
        f"Напиши один короткий, натхненний, мотиваційний або експертний пост для соцмереж. "
        f"Врахуй, що категорія об'єкта: '{category}'. "
        f"{lang_instructions[lang_idx]} "
        f"КРИТИЧНО: Не пиши жодних передмов чи системних повідомлень. Тільки готовий текст поста."
    )

    parts = [{"text": prompt}]
    for img_path in image_paths:
        if os.path.exists(img_path):
            try:
                with open(img_path, "rb") as f:
                    base64_image = base64.b64encode(f.read()).decode('utf-8')
                parts.append({
                    "inlineData": {"mimeType": "image/jpeg", "data": base64_image}
                })
            except: pass

    payload = {"contents": [{"parts": parts}]}
    models = ["gemini-2.5-flash", "gemini-1.5-flash"]
    
    for model in models:
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
            # Тимчасові папки ігноруються автоматично, але зробимо додатковий захист
            if r[2].lower() == "temporary": continue
            try:
                fb_counter = int(r[5]) if r[5] else 0
                valid_rows.append({"row_idx": i + 2, "data": r, "fb_counter": fb_counter})
            except ValueError: continue

    if not valid_rows:
        print("ℹ️ Немає валідних рядків для Facebook.")
        return

    # 🎯 1. Знаходимо мінімальну кількість публікацій у FB
    min_fb = min(item["fb_counter"] for item in valid_rows)
    min_pool = [item for item in valid_rows if item["fb_counter"] == min_fb]

    # 🗂️ 2. Групуємо пул за принципом: Категорія (Index 2) - Дата (Index 6) - Місцеположення (Index 7)
    groups = {}
    for item in min_pool:
        data = item["data"]
        category = data[2]
        date_str = data[6] if len(data) > 6 else ""
        location = data[7] if len(data) > 7 else ""
        
        group_key = (category, date_str, location)
        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append(item)

    # 📦 3. Беремо першу сформовану групу та обмежуємо її до 4-х медіафайлів
    first_key = list(groups.keys())[0]
    selected_group_items = groups[first_key][:4]
    
    category_name, target_date, target_loc = first_key
    print(f"📂 Обрано групу: {category_name} | {target_date} | {target_loc} (Всього файлів у пості: {len(selected_group_items)})")

    os.makedirs('temp_fb', exist_ok=True)
    local_files = []
    cloud_urls = []
    ik_ids = []
    has_video = False
    ai_analysis_images = []

    # 📥 4. Завантаження та підготовка обраних файлів
    for item in selected_group_items:
        f_id = item["data"][0]
        f_name = item["data"][1]
        lower_name = f_name.lower()
        
        local_path = os.path.join('temp_fb', f_name)
        print(f"📥 Завантаження з Drive: {f_name}...")
        
        request = drive.files().get_media(fileId=f_id)
        with open(local_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done: _, done = downloader.next_chunk()

        # Конвертація/Оптимізація форматів
        final_path = local_path
        mime_type = "image/jpeg"
        if lower_name.endswith(('.mp4', '.mov', '.avi')):
            has_video = True
            mime_type = "video/mp4"
            # Для відео робимо один прев'ю-кадр для ШІ аналізу
            frame_path = os.path.join('temp_fb', f"frame_{f_id}.jpg")
            subprocess.run(['ffmpeg', '-y', '-i', local_path, '-ss', '00:00:01', '-vframes', '1', frame_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ai_analysis_images.append(frame_path)
        elif lower_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join('temp_fb', f_name.rsplit('.', 1)[0] + '.jpg')
            with Image.open(local_path) as img:
                img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
            final_path = jpg_path
            ai_analysis_images.append(final_path)
        else:
            ai_analysis_images.append(final_path)

        # Отримуємо хмарне посилання для Meta API
        pub_url, ik_id = get_google_drive_direct_url(f_id, local_file_path=final_path)
        
        local_files.append(local_path)
        if final_path != local_path: local_files.append(final_path)
        cloud_urls.append(pub_url)
        if ik_id: ik_ids.append(ik_id)

    # ✍️ 5. Генерація контенту (Шапка + ШІ опис)
    header_text = get_manufacturer_header(category_name, target_date)
    ai_text = generate_multimodal_caption(ai_analysis_images, category_name, target_date)
    
    # Додаємо локацію, якщо вона відома
    loc_footer = f"\n\n📍 Локація: {target_loc}" if target_loc and "Невідоме місце" not in target_loc else ""
    full_caption = f"{header_text}{ai_text}{loc_footer}"

    # 📤 6. Публікація на Сторінку Facebook
    if not FB_PAGE_ID or not META_ACCESS_TOKEN:
        print("❌ Відсутні MEBLI_FB_PAGE_ID або MEBLI_ACCESS_TOKEN!")
        return

    try:
        if has_video:
            # Якщо є відео, публікуємо перше відео з групи
            print("🎬 Публікація відео-поста у Facebook...")
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/videos"
            payload = {"file_url": cloud_urls[0], "description": full_caption, "access_token": META_ACCESS_TOKEN}
            res = requests.post(fb_url, data=payload).json()
        else:
            # Якщо тільки фото, робимо альбомний мульти-пост (Unpublished Photos -> Feed)
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
            print(f"✅ Пост успішно опубліковано! ID: {res.get('id', res.get('post_id'))}")
            
            # 📊 7. Оновлюємо лічильники на +1 в Sheets для ВСІХ файлів цієї групи
            for item in selected_group_items:
                row_line = item["row_idx"]
                new_counter = item["fb_counter"] + 1
                sheets.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID, range=f"'{TAB_NAME}'!F{row_line}",
                    valueInputOption='RAW', body={'values': [[new_counter]]}
                ).execute()
            print(f"📊 Лічильники для {len(selected_group_items)} файлів оновлено в колонці F.")
        else:
            print(f"⚠️ Помилка відповіді Meta API: {res}")

    except Exception as e:
        print(f"❌ Критична помилка під час публікації: {e}")
    finally:
        # 🧹 Очищення
        for ik_id in ik_ids: delete_from_imagekit(ik_id)
        for f in local_files:
            if os.path.exists(f): os.remove(f)
        for f in ai_analysis_images:
            if os.path.exists(f): os.remove(f)
        print("🧹 Тимчасові медіафайли успішно видалено.")

if __name__ == '__main__':
    main()
