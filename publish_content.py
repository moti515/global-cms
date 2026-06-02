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
# Зчитуємо токен із секретів GitHub. Старий заблокований токен видалено.
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "ВСТАВТЕ_СЮДИ_НОВИЙ_ТОКЕН_ЯКЩО_ТЕСТУЄТЕ_ЛОКАЛЬНО")
SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
FB_PAGE_ID = "1313824565399163" 
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
    
    active_rules.append(days_map[day_of_week])
    active_rules.append("Різне")
    return active_rules

def generate_multimodal_caption(image_path, category):
    """ШІ аналізує зображення. Виправлено версію API на v1 для стабільної роботи"""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return "Усміхніться! 😉 #гумор #меблі"
        
    # ВИПРАВЛЕНО: v1beta змінено на v1
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={gemini_key}"
    
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
            
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        prompt = f"Ти топ-маркетолог розважальної сторінки. Подивись на цю картинку/мем. Напиши до неї ОДИН короткий, влучний і дуже смішний коментар (або життєву фразу/жарт) українською мовою. Врахуй, що сьогодні контекст публікації: категорія '{category}'. Додай відповідні емодзі. Не використовуй нудних і офіційних вступів."
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64_image
                        }
                    }
                ]
            }]
        }
        
        res = requests.post(url, json=payload, timeout=20).json()
        return res['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"⚠️ Очі ШІ підвели, ставимо текстовий дефолт. Помилка: {e}")
        if 'res' in locals():
            print(f"🔍 Технічна відповідь від сервера Gemini: {res}")
        return "Трохи гумору вам у стрічку! Як вам? 👇😂"

def upload_to_temporary_host(file_path):
    print("📤 Завантажуємо тимчасовий файл на публічні хостинги...")
    
    # КРОК 1: Tmpfiles.org
    try:
        url = "https://tmpfiles.org/api/v1/upload"
        with open(file_path, 'rb') as f:
            res = requests.post(url, files={'file': f}, timeout=30).json()
            if res.get("status") == "success":
                viewer_url = res["data"]["url"]
                return viewer_url.replace("https://tmpfiles.org/", "https://tmpfiles.org/dl/")
    except Exception as e:
        print(f"⚠️ Tmpfiles.org не спрацював: {e}. Пробуємо резервний варіант...")
        
    # КРОК 2: Pixeldrain.com
    try:
        url = "https://pixeldrain.com/api/file"
        with open(file_path, 'rb') as f:
            res = requests.post(url, files={'file': f}, timeout=30).json()
            if res.get("success") is True or "id" in res:
                return f"https://pixeldrain.com/api/file/{res['id']}"
    except Exception as e:
        print(f"⚠️ Pixeldrain не спрацював: {e}. Пробуємо фінальний варіант...")

    # КРОК 3: Catbox.moe
    try:
        url = "https://catbox.moe/user/api.php"
        with open(file_path, 'rb') as f:
            files = {'fileToUpload': f}
            data = {'reqtype': 'fileupload'}
            res = requests.post(url, data=data, files=files, timeout=30)
            if res.status_code == 200 and res.text.strip().startswith("https://"):
                return res.text.strip()
    except Exception as e:
        print(f"⚠️ Catbox не спрацював: {e}")

    raise Exception("Усі доступні тимчасові хостинги заблоковані мережею або лежать.")

def publish_to_meta_platforms(media_url, media_type, is_story=False, caption=""):
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
            print("⏳ Очікуємо обробки відео серверами Instagram...")
            time.sleep(30)
            
        ig_pub_res = requests.post(
            f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", 
            data={"creation_id": ig_creation_id, "access_token": META_ACCESS_TOKEN}
        ).json()
        if "id" in ig_pub_res:
            print(f"✅ [Instagram] Успішно в ефірі! ID: {ig_pub_res['id']}")
        else:
            print(f"❌ Помилка публікації в Instagram: {ig_pub_res}")

    if not is_story:
        print("📤 Дублювання поста на Сторінку Facebook...")
        if media_type == "video":
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/videos"
            fb_payload = {
                "file_url": media_url,
                "description": caption,
                "access_token": META_ACCESS_TOKEN
            }
        else:
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/photos"
            fb_payload = {
                "url": media_url,
                "message": caption,
                "access_token": META_ACCESS_TOKEN
            }
            
        try:
            fb_res = requests.post(fb_url, data=fb_payload).json()
            if "id" in fb_res or "post_id" in fb_res:
                print(f"✅ [Facebook Page] Пост успешно продублировано! ID: {fb_res.get('id', fb_res.get('post_id'))}")
            else:
                print(f"⚠️ [Facebook Page] Сервер повернув дивну відповідь: {fb_res}")
        except Exception as e:
            print(f"❌ Не вдалося надіслати пост у Facebook: {e}")

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
        public_url = upload_to_temporary_host(final_upload_path)
        print(f"🔗 Успішно згенеровано пряме посилання: {public_url}")
        
        publish_to_meta_platforms(public_url, "video" if mime_type == "video/mp4" else "image", is_story=(mode == 'story'), caption=caption_text)
        
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
