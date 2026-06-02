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
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "EAAXCuIxnsWQBRpJP7hbZBPchMZBcZBucLPArTryPFNhhrl9mbHHWZBP8jpKTUjeHgERWwZBbdDa9b3c2as9LQZC83RRHzFCrF5km4vVnL8IRowwiCDMorqugQHymZBYNRShZA67sUUOBvoyHKcqh6AaQB5KQBBDywUBWr6ZCLLE7sMVaKLglNzyNYlPxadJu8HQ5t")
SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
TEMP_PUBLIC_FOLDER_ID = '1L3veD90e7Fr1acwlK7PmhSs_JrofyT6N'
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

# Суворий поділ форматів
VALID_MEDIA_EXTENSIONS = ('.gif', '.heic', '.heif', '.jpeg', '.jpg', '.mp4', '.png', '.webp')
DOCUMENT_EXTENSIONS = ('.pdf', '.doc', '.docx', '.djvu', '.txt', '.rtf', '.fb2', '.epub')

def get_services():
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
    return build('drive', 'v3', credentials=creds), build('sheets', 'v4', credentials=creds)

def log_unsupported_to_service(sheets_service, folder_name, file_name, reason="непідтримуваний формат"):
    """Записує помилку формату на службовий аркуш навпроти папки"""
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
    """ШІ аналізує саме ЗОБРАЖЕННЯ (або кадр відео) і генерує точний гумор"""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return "Усміхніться! 😉 #гумор #меблі"
        
    # Використовуємо модель 1.5-flash, яка ідеально і дешево аналізує картинки
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
    
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
        return "Трохи гумору вам у стрічку! Як вам? 👇😂"

def make_file_public_and_get_link(drive_service, file_id):
    user_permission = {'type': 'anyone', 'role': 'reader'}
    drive_service.permissions().create(fileId=file_id, body=user_permission).execute()
    file_info = drive_service.files().get(fileId=file_id, fields='webContentLink').execute()
    return file_info.get('webContentLink')

def publish_to_instagram(media_url, media_type, is_story=False, caption=""):
    url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    payload = {
        "access_token": META_ACCESS_TOKEN,
        "media_type": "STORIES" if is_story else ("VIDEO" if media_type == "video" else "IMAGE")
    }
    if media_type == "video": payload["video_url"] = media_url
    else: payload["image_url"] = media_url
    if not is_story and caption: payload["caption"] = caption

    res = requests.post(url, data=payload).json()
    if "id" not in res: raise Exception(f"Помилка контейнера: {res}")
    creation_id = res["id"]
    
    if media_type == "video":
        print("⏳ Очікуємо конвертації відео на серверах інсти...")
        time.sleep(30)
        
    pub_res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={"creation_id": creation_id, "access_token": META_ACCESS_TOKEN}).json()
    if "id" not in pub_res: raise Exception(f"Помилка ефіру: {pub_res}")
    print(f"✅ Успішно в ефірі! ID: {pub_res['id']}")

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
    
    # --- БЛОК ПЕРЕВІРКИ ФОРМАТІВ ТА ДОКУМЕНТІВ (PDF, DJVU тощо) ---
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
    
    print(f"📥 Завантажуємо медіа: {orig_name}...")
    request = drive.files().get_media(fileId=file_id)
    with open(local_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done: _, done = downloader.next_chunk()

    # Конвертація
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

    # --- ПІДГОТОВКА ЗОБРАЖЕННЯ ДЛЯ ОЧЕЙ ШІ (ГЕНЕРАЦІЯ ОПИСУ) ---
    caption_text = ""
    if mode == 'post':
        analysis_image = final_upload_path
        # Якщо це відео, «відкушуємо» перший кадр як картинку для аналізу ШІ
        if mime_type == "video/mp4":
            analysis_image = os.path.join('temp_media', 'video_frame.jpg')
            subprocess.run(['ffmpeg', '-y', '-i', final_upload_path, '-ss', '00:00:01', '-vframes', '1', analysis_image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        print("👁️ ШІ аналізує візуальний вміст файлу...")
        caption_text = generate_multimodal_caption(analysis_image, category_name)
        if os.path.exists(os.path.join('temp_media', 'video_frame.jpg')): os.remove(os.path.join('temp_media', 'video_frame.jpg'))

    # Тимчасовий публічний лінк
    file_metadata = {'name': os.path.basename(final_upload_path), 'parents': [TEMP_PUBLIC_FOLDER_ID]}
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(final_upload_path, mimetype=mime_type, resumable=True)
    temp_drive_file = drive.files().create(body=file_metadata, media_body=media, fields='id').execute()
    temp_file_id = temp_drive_file.get('id')
    
    public_url = make_file_public_and_get_link(drive, temp_file_id)
    
    try:
        publish_to_instagram(public_url, "video" if mime_type == "video/mp4" else "image", is_story=(mode == 'story'), caption=caption_text)
        
        # Обновлення таблиці
        new_counter = selected_item["data"][counter_col_idx] + 1
        col_letter = "D" if mode == 'post' else "E"
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!{col_letter}{row_line}",
            valueInputOption='RAW', body={'values': [[new_counter]]}
        ).execute()
        print(f"📊 Лічильник оновлено на +1 (Рядок {row_line})")
    finally:
        try: drive.files().delete(fileId=temp_file_id).execute()
        except: pass
        if os.path.exists(local_path): os.remove(local_path)
        if final_upload_path != local_path and os.path.exists(final_upload_path): os.remove(final_upload_path)

if __name__ == '__main__':
    main()
