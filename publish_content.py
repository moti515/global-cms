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

IG_USER_ID = "17841429409435438"
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "ВСТАВТЕ_СЮДИ_НОВИЙ_PAGE_ACCESS_TOKEN")
SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

VALID_MEDIA_EXTENSIONS = ('.gif', '.heic', '.heif', '.jpeg', '.jpg', '.mp4', '.png', '.webp')
DOCUMENT_EXTENSIONS = ('.pdf', '.doc', '.docx', '.djvu', '.txt', '.rtf', '.fb2', '.epub')

def get_services():
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds), build('sheets', 'v4', credentials=creds)

def log_unsupported_to_service(sheets_service, folder_name, file_name, reason="непідтримуваний формат"):
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

def get_active_rules_ordered():
    now = datetime.now()
    day_of_week = now.strftime('%A')
    day_month = now.strftime('%d.%m')
    
    days_map = {
        'Monday': 'Понеділок', 'Tuesday': 'Вівторок', 'Wednesday': 'Середа',
        'Thursday': 'Четвер', 'Friday': "П'ятниця", 'Saturday': 'Субота', 'Sunday': 'Неділя'
    }
    
    active_rules = []
    if "22.12" <= day_month <= "31.12" or "01.01" == day_month: active_rules.append("Новий рік")
    if "01.04" <= day_month <= "02.04": active_rules.append("1 квітня")
    if "22.02" <= day_month <= "23.02": active_rules.append("23 лютого")
    if day_month == "08.03": active_rules.append("8 Березня")
    if day_month == "03.09": active_rules.append("3 вересня")
    if "31.05" <= day_month <= "15.06": active_rules.append("31 травня")
    if now.month == 11 and 23 <= now.day <= 30: active_rules.append("Чорна п'ятниця")
    
    # Перевірка на специфічні п'ятниці
    if day_of_week == 'Friday':
        if now.day == 13:
            active_rules.append("П'ятниця 13-те")
        elif now.day == 12:
            active_rules.append("П'ятниця 12-те")
            
    # Перевірка на вихідні (Субота та Неділя)
    if day_of_week in ['Saturday', 'Sunday']:
        active_rules.append("Weekend")
    
    active_rules.append(days_map[day_of_week])
    active_rules.append("Різне")
    return active_rules

def generate_multimodal_caption(image_path, category):
    """ШІ аналізує зображення за допомогою актуального REST API Gemini v1beta та моделей серії 3.x"""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return "Усміхніться! 😉 #гумор #меблі"
        
    # Використовуємо найактуальніші моделі серії Gemini 3.x згідно з документацією
    models_to_try = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]
    
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        prompt = (
            f"Ти топ-маркетолог розважальної сторінки з гострим гумором. Подивись на цю картинку/мем. "
            f"Напиши до неї короткий, влучний і дуже смішний коментар (або життєву фразу) ТРЬОМА мовами окремими абзацах: "
            f"спочатку українською, потім англійською, і німецькою. "
            f"КРИТИЧНО: Це не має бути дослівний нудний переклад! Жарт має бути адаптований (живий сленг, "
            f"зрозумілий контекст для носіїв кожної з мов). "
            f"Врахуй, що контекст публікації — категорія '{category}'. Додай відповідні емодзі. "
            f"Не використовуй офіційних вступів. Формат відповіді строго такий:\n\n"
            f"🇺🇦 [Жарт українською]\n\n"
            f"🇬🇧 [Жарт англійською]\n\n"
            f"🇩🇪 [Жарт німецькою]"
        )
        
        # Точний camelCase синтаксис структури payload
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {  
                            "mimeType": "image/jpeg",  
                            "data": base64_image
                        }
                    }
                ]
            }]
        }
        
        for model in models_to_try:
            # Оновлено URL: v1beta замість v1 для повної підтримки моделей 3.5 / 3.1
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
            try:
                res = requests.post(url, json=payload, timeout=20).json()
                if 'candidates' in res and res['candidates']:
                    return res['candidates'][0]['content']['parts'][0]['text']
            except Exception as e:
                print(f"⚠️ Модель {model} тимчасово недоступна або виникла помилка: {e}. Пробуємо наступну...")
                continue
                
        print("⚠️ Жодна з моделей Gemini не відповіла успішно, активовано дефолт.")
        return "Трохи гумору вам у стрічку! Як вам? 👇😂"
    except Exception as e:
        print(f"⚠️ Помилка обробки запиту до ШІ: {e}")
        return "Трохи гумору вам у стрічку! Як вам? 👇😂"

def get_google_drive_direct_url(file_id):
    """
    Генерує пряме публічне посилання на файл з Google Drive.
    Працює для розшарених папок (доступ: Усі, хто мають посилання).
    """
    print(f"🔗 Формуємо пряме посилання для Google Drive ID: {file_id}")
    # Стандартний веб-ендпоінт для прямого скачування файлів з Google Drive
    return f"https://docs.google.com/uc?export=download&id={file_id}"

def publish_to_meta_platforms(media_url, media_type, is_story=False, caption="", local_file_path=None):
    print("📤 Відправка контенту в Instagram...")
    ig_url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    ig_payload = {
        "access_token": META_ACCESS_TOKEN,
        "media_type": "STORIES" if is_story else ("VIDEO" if media_type == "video" else "IMAGE")
    }
    if media_type == "video": 
        ig_payload["video_url"] = media_url
    else: 
        ig_payload["image_url"] = media_url
        
    if not is_story and caption: 
        ig_payload["caption"] = caption

    ig_res = requests.post(ig_url, data=ig_payload).json()
    if "id" not in ig_res: 
        print(f"❌ Помилка створення контейнера Instagram: {ig_res}")
    else:
        ig_creation_id = ig_res["id"]
        
        if media_type == "video":
            print("⏳ Очікуємо обробки відео серверами Instagram (30 сек)...")
            time.sleep(30)
        else:
            print("⏳ Очікуємо завантаження фото серверами Instagram (12 сек)...")
            time.sleep(12)
            
        ig_pub_res = requests.post(
            f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", 
            data={"creation_id": ig_creation_id, "access_token": META_ACCESS_TOKEN}
        ).json()
        if "id" in ig_pub_res:
            print(f"✅ [Instagram] Успішно в ефірі! ID: {ig_pub_res['id']}")
        else:
            print(f"❌ Помилка публікації в Instagram: {ig_pub_res}")

    # --- БЛОК ФЕЙСБУКУ ---
    if not is_story:
        print("📤 Дублювання поста на Сторінку Facebook...")
        if media_type == "video":
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/videos"
            fb_payload = {
                "file_url": media_url,
                "description": caption, # Для відео залишається description
                "access_token": META_ACCESS_TOKEN
            }
        else:
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/photos"
            fb_payload = {
                "url": media_url,
                "caption": caption,   # ВИПРАВЛЕНО: саме 'caption' замість 'message' для /photos
                "access_token": META_ACCESS_TOKEN
            }
            
        try:
            fb_res = requests.post(fb_url, data=fb_payload).json()
            if "id" in fb_res or "post_id" in fb_res:
                print(f"✅ [Facebook Page] Пост успішно продубльовано! ID: {fb_res.get('id', fb_res.get('post_id'))}")
            else:
                print(f"⚠️ [Facebook Page] Сервер повернув дивну відповідь (перевірте тип токена!): {fb_res}")
        except Exception as e:
            print(f"❌ Не вдалося надіслати пост у Facebook: {e}")

    else:
        # Безпечно пропускаємо Facebook для Сторіз, оскільки Meta API не підтримує цей функціонал для сторонніх додатків
        print("ℹ️ [Facebook] Публікація Сторіз через API обмежена політикою Meta. Пропускаємо цей крок.")

def main():
    if len(sys.argv) < 3:
        print("Приклад: python publish_content.py post П'ятниця")
        return
        
    mode = sys.argv[1].lower()
    tab_name = sys.argv[2]
    counter_col_idx = 3 if mode == 'post' else 4
    
    drive, sheets = get_services()
    
    res = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A2:E").execute()
    rows = res.get('values', [])
    if not rows: return
        
    valid_rows = []
    for i, r in enumerate(rows):
        if len(r) >= 3:
            while len(r) < 5: r.append("0")
            try:
                r[3], r[4] = (int(r[3]) if r[3] else 0), (int(r[4]) if r[4] else 0)
                valid_rows.append({"row_idx": i + 2, "data": r})
            except ValueError: continue

    min_count = min(r["data"][counter_col_idx] for r in valid_rows)
    min_pool = [r for r in valid_rows if r["data"][counter_col_idx] == min_count]
    
    active_categories = get_active_rules_ordered()
    selected_item = None
    for category in active_categories:
        match_files = [item for item in min_pool if item["data"][2] == category]
        if match_files:
            selected_item = match_files[0]
            break
            
    if not selected_item: selected_item = min_pool[0]

    file_id = selected_item["data"][0]
    orig_name = selected_item["data"][1]
    category_name = selected_item["data"][2]
    row_line = selected_item["row_idx"]
    
    lower_name = orig_name.lower()
    if lower_name.endswith(DOCUMENT_EXTENSIONS):
        print(f"📄 Знайдено текстовий документ/книгу ({orig_name}). Пропускаємо публікацію.")
        log_unsupported_to_service(sheets, category_name, orig_name, reason="Знайдено текстовий документ/книгу (PDF/DOC/DJVU)")
        return
        
    if not lower_name.endswith(VALID_MEDIA_EXTENSIONS):
        print(f"❌ Невідомий формат файлу: {orig_name}.")
        log_unsupported_to_service(sheets, category_name, orig_name, reason="Непідтримуваний формат медіа")
        return

    os.makedirs('temp_media', exist_ok=True)
    local_path = os.path.join('temp_media', orig_name)
    
    print(f"📥 Завантажуємо медіа з Google Диску: {orig_name}...")
    request = drive.files().get_media(fileId=file_id)
    with open(local_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done: _, done = downloader.next_chunk()

    mime_type = "image/jpeg" if lower_name.endswith(('.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp')) else "video/mp4"
    final_upload_path = local_path
    
    if lower_name.endswith('.gif'):
        mp4_path = os.path.join('temp_media', orig_name.rsplit('.', 1)[0] + '_gif.mp4')
        subprocess.run(['ffmpeg', '-y', '-i', local_path, '-movflags', 'faststart', '-pix_fmt', 'yuv420p', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', mp4_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        final_upload_path = mp4_path
        mime_type = "video/mp4"
    elif lower_name.endswith(('.heic', '.heif')):
        jpg_path = os.path.join('temp_media', orig_name.rsplit('.', 1)[0] + '.jpg')
        with Image.open(local_path) as img: img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
        final_upload_path = jpg_path
        mime_type = "image/jpeg"

    # 🔥 ОПТИМІЗАЦІЯ ФОТО ПІД СТОРІС (1080x1920)
    if mode == 'story' and mime_type == "image/jpeg":
        print("📐 Режим Сторіс: вписуємо зображення у формат 1080x1920, щоб уникнути кропу...")
        story_path = os.path.join('temp_media', 'story_padded_' + orig_name.rsplit('.', 1)[0] + '.jpg')
        try:
            with Image.open(final_upload_path) as img:
                img = img.convert('RGB')
                orig_w, orig_h = img.size
                
                target_w, target_h = 1080, 1920
                # Створюємо нейтральне темне полотно-контейнер (колір 20, 20, 20)
                canvas = Image.new('RGB', (target_w, target_h), (20, 20, 20))
                
                # Обчислюємо коефіцієнт стиснення/розширення (Fit)
                scale = min(target_w / orig_w, target_h / orig_h)
                new_w = int(orig_w * scale)
                new_h = int(orig_h * scale)
                
                resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                
                # Центруємо картинку на полотні сторіс
                paste_x = (target_w - new_w) // 2
                paste_y = (target_h - new_h) // 2
                canvas.paste(resized_img, (paste_x, paste_y))
                canvas.save(story_path, 'JPEG', quality=95)
                
            # Видаляємо проміжний тимчасовий jpg (якщо він створювався раніше)
            if final_upload_path != local_path and os.path.exists(final_upload_path):
                os.remove(final_upload_path)
                
            final_upload_path = story_path
        except Exception as e:
            print(f"⚠️ Не вдалося відформатувати Сторіс: {e}. Буде надіслано оригінал.")

    # ОПТИМІЗАЦІЯ ВІДЕО ПІД СТОРІС (1080x1920)
    elif mode == 'story' and mime_type == "video/mp4":
        print("📐 Режим Сторіс для ВІДЕО: інтелектуально вписуємо у формат 1080x1920 через ffmpeg...")
        story_video_path = os.path.join('temp_media', 'story_padded_' + orig_name.rsplit('.', 1)[0] + '.mp4')
        
        # Фільтр ffmpeg стискає відео пропорційно під 1080x1920 і додає чорні поля (padding)
        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', final_upload_path,
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black',
            '-movflags', 'faststart',
            '-pix_fmt', 'yuv420p',
            story_video_path
        ]
        
        result = subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            if final_upload_path != local_path and os.path.exists(final_upload_path): 
                os.remove(final_upload_path)
            final_upload_path = story_video_path
            print("✅ Відео успішно конвертовано у вертикальний формат з полями!")
        else:
            print("⚠️ Не вдалося обробити відео через ffmpeg, буде надіслано оригінал (можливе обрізання).")

    caption_text = ""
    if mode == 'post':
        analysis_image = final_upload_path
        if mime_type == "video/mp4":
            analysis_image = os.path.join('temp_media', 'video_frame.jpg')
            subprocess.run(['ffmpeg', '-y', '-i', final_upload_path, '-ss', '00:00:01', '-vframes', '1', analysis_image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        print("👁️ ШІ аналізує візуальний вміст файлу...")
        caption_text = generate_multimodal_caption(analysis_image, category_name)
        if os.path.exists(os.path.join('temp_media', 'video_frame.jpg')): os.remove(os.path.join('temp_media', 'video_frame.jpg'))

    try:
        # Беремо пряме посилання безпосередньо з розшарованого Google Диску
        public_url = get_google_drive_direct_url(file_id)
        print(f"🔗 Згенеровано стабільне посилання: {public_url}")
        
        # Передаємо локальний файл останнім параметром для FB Stories
        publish_to_meta_platforms(
            public_url, 
            "video" if mime_type == "video/mp4" else "image", 
            is_story=(mode == 'story'), 
            caption=caption_text,
            local_file_path=final_upload_path
        )
        
        new_counter = selected_item["data"][counter_col_idx] + 1
        col_letter = "D" if mode == 'post' else "E"
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!{col_letter}{row_line}",
            valueInputOption='RAW', body={'values': [[new_counter]]}
        ).execute()
        print(f"📊 Лічильник оновлено на +1 (Рядок {row_line})")
        
    except Exception as e:
        print(f"❌ Критична помилка під час публікації: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(local_path): os.remove(local_path)
        if final_upload_path != local_path and os.path.exists(final_upload_path): os.remove(final_upload_path)

if __name__ == '__main__':
    main()
