import os
import sys
import json
import time
import base64
import io
import requests
import subprocess
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image
from pillow_heif import register_heif_opener
# Імпортуємо офіційний клієнт Google GenAI
from google import genai
from google.genai import types

register_heif_opener()

# ⚙️ ДИНАМІЧНІ НАЛАШТУВАННЯ META (Тепер беруться з системних змінних)
IG_USER_ID = os.environ.get("IG_USER_ID")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'

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
        if now.day == 13: active_rules.append("П'ятниця 13-те")
        elif now.day == 12: active_rules.append("П'ятниця 12-те")
            
    # Перевірка на вихідні (Субота та Неділя)
    if day_of_week in ['Saturday', 'Sunday']:
        active_rules.append("Weekend")
    
    active_rules.append(days_map[day_of_week])
    active_rules.append("Різне")
    return active_rules

def generate_multimodal_caption(image_path, category, tab_name):
    """
    ШІ аналізує зображення та генерує гострий тематичний опис трьома мовами.
    Оптимізовано: новий SDK (genai.Client), PIL-компресія медіа, структурований промпт.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY")
    is_furniture = "мебл" in tab_name.lower()
    
    # 1️⃣ Швидкий дефолт, якщо немає ключа API (економимо час без обробки фото)
    if not gemini_key:
        if is_furniture:
            return "Трохи меблевого гумору вам у стрічку! Як вам? 👇😂 #меблі #інтерєр #гумор"
        return "Усміхніться! Гарного настрою! 😉 #гумор #розваги #п_ятниця"
        
    # Пул актуальних та стабільних моделей для генерації контенту
    models_to_try = [
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite"
        ]
    
    # 2️⃣ Динамічний контекст промпту
    topic_context = "розважальної сторінки з гострим гумором"
    if is_furniture:
        topic_context = "популярного пабліку про меблі, дизайн інтер'єрів та запеклі будні меблевиків (майстрів, дизайнерів, збірників)"

    try:
        # 3️⃣ Оптимізація та стиснення зображення (захист від MemoryError та лімітів API)
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Файл {image_path} не знайдено.")
            
        try:
            with Image.open(image_path) as img:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                
                # Обмежуємо розмір до 1024px по довшій стороні та стискаємо якість
                img.thumbnail((1024, 1024))
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=82, optimize=True)
                image_bytes = buffer.getvalue()
        except Exception as img_err:
            print(f"⚠️ Не вдалося оптимізувати зображення через PIL ({img_err}), пробуємо сире зчитування...")
            with open(image_path, "rb") as f:
                image_bytes = f.read()

        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        # 4️⃣ Структурований промпт (блокова система покращує дотримання правил ШІ)
        prompt = (
            f"Ти топ-маркетолог {topic_context}.\n"
            f"Подивись на цю картинку/мем і напиши короткий, влучний і дійсно смішний коментар "
            f"(або життєву фразу/біль клієнта чи майстра) окремо ТРЬОМА мовами.\n\n"
            f"🎯 ТОН ТА СТИЛІСТИКА:\n"
            f"- КРИТИЧНО: Це не має бути дослівний нудний переклад! Жарт має бути якісно адаптований під кожну мову.\n"
            f"- Використовуй живий сленг, професійні жарти або зрозумілий контекст для носіїв кожної з мов.\n"
            f"- Врахуй контекст публікації — категорія '{category}' з розділу '{tab_name}'.\n"
            f"- Додай відповідні емодзі.\n\n"
            f"⚠️ СУВОРІ ОБМЕЖЕННЯ:\n"
            f"1. Не використовуй жодних офіційних вступів, лапок чи підписів на кшталт 'Ось ваш жарт'.\n"
            f"2. Без хештегів та додаткових пояснень копірайтера.\n"
            f"3. Формат відповіді має бути СТРОГО такий (3 абзаци з прапорами):\n\n"
            f"🇺🇦 [Жарт українською]\n\n"
            f"🇬🇧 [Жарт англійською]\n\n"
            f"🇩🇪 [Жарт німецькою]"
        )
        
        # 5️⃣ Формуємо структуру вхідних даних для нового SDK
        inputs = [
            {"type": "text", "text": prompt},
            {
                "type": "image",
                "data": base64_image,
                "mime_type": "image/jpeg"
            }
        ]
        
        # 6️⃣ Каскадний виклик моделей через офіційний клієнт genai
        client = genai.Client()
        for model in models_to_try:
            print(f"🚀 Спроба генерації мультиязичного підпису через {model}...")
            try:
                interaction = client.interactions.create(
                    model=model,
                    input=inputs
                )
                if interaction and interaction.output_text:
                    return interaction.output_text.strip()
                else:
                    print(f"⚠️ Модель {model} повернула порожню відповідь.")
            except Exception as model_err:
                print(f"⚠️ Помилка моделі {model}: {model_err}. Переходимо до наступної.")
                continue
                
        print("⚠️ Жодна з моделей Gemini не відповіла успішно, активовано дефолт.")
        
    except Exception as general_err:
        print(f"⚠️ Загальний збій блоку ШІ-генерації: {general_err}")
        
    # 7️⃣ Залізобетонний дефолт на випадок будь-якого збою в блоці try
    if is_furniture:
        return "Трохи меблевого гумору вам у стрічку! Як вам? 👇😂 #меблі #гумор"
    return "Трохи гумору вам у стрічку! Як вам? 👇😂 #гумор #розваги"

def delete_from_imagekit(file_id: str):
    """Видаляє тимчасовий файл з ImageKit.io за його fileId, щоб не засмічувати хмару"""
    if not file_id:
        return

    imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
    if not imagekit_key:
        print("⚠️ Змінна IMAGEKIT_PRIVATE_KEY відсутня. Автовидалення скасовано.")
        return

    url = f"https://api.imagekit.io/v1/files/{file_id}"
    print(f"🗑️ Видаляємо тимчасовий буферний файл {file_id} з ImageKit.io...")
    try:
        res = requests.delete(url, auth=(imagekit_key, ''), timeout=20)
        if res.status_code == 204:
            print("✅ Файл успішно та безповоротно видалено з ImageKit.")
        else:
            print(f"⚠️ ImageKit не видалив файл (Код {res.status_code}): {res.text}")
    except Exception as e:
        print(f"⚠️ Помилка при виконанні запиту на видалення з ImageKit: {e}")

def get_google_drive_direct_url(file_id, local_file_path=None):
    """
    Каскадний завантажувач медіафайлів на зовнішні хостинги з API.
    Оптимізовано за пам'яттю (стримінг файлів замість f.read()).
    """
    if local_file_path and os.path.exists(local_file_path):
        filename = os.path.basename(local_file_path)
        lower_name = filename.lower()
        
        # 1. Розширена перевірка типу файлу (з 2-го варіанту)
        is_video = lower_name.endswith(('.mp4', '.mov', '.avi'))
        mime_type = "video/mp4" if is_video else "image/jpeg"
        
        # 2. Очищення імені файлу для Meta API та Litterbox (з 2-го варіанту)
        remote_filename = "story.mp4" if is_video else "story.jpg"
        
        browser_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
        }
        
        # 1️⃣ Спроба через Litterbox (Фото + Відео)
        print(f"☁️ Завантажуємо файл {filename} на Litterbox...", flush=True)
        try:
            data_payload = {'reqtype': 'fileupload', 'time': '1h'}
            
            with open(local_file_path, 'rb') as f:
                files_payload = {'fileToUpload': (remote_filename, f, mime_type)}
                res = requests.post(
                    'https://litterbox.catbox.moe/resources/internals/api.php',
                    data=data_payload,
                    files=files_payload,
                    headers=browser_headers,
                    timeout=(10, 60)  # Гнучкий таймаут для великих файлів (з 1-го варіанту)
                )
                
            response_text = res.text.strip()
            if res.status_code == 200 and response_text.startswith('http'):
                print(f"🔗 Отримано стабільне посилання від Litterbox: {response_text}", flush=True)
                return response_text, None
            else:
                # Інформативне логування помилки (з 2-го варіанту)
                print(f"⚠️ Litterbox відмовив (Код {res.status_code}): {response_text[:100]}... Пробуємо ImageKit...", flush=True)
        except Exception as e:
            print(f"⚠️ Помилка з'єднання з Litterbox: {e}. Пробуємо наступний хостинг...", flush=True)

        # 2️⃣ Спроба через ImageKit.io
        imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
        if imagekit_key:
            print(f"☁️ Завантажуємо файл {filename} на ImageKit.io...", flush=True)
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://upload.imagekit.io/api/v1/files/upload',
                        auth=(imagekit_key, ''),
                        files={'file': (filename, f, mime_type)},
                        data={
                            'fileName': filename,
                            'useUniqueFileName': 'true'
                        },
                        timeout=90
                    )
                    
                if res.status_code in [200, 201]:
                    res_data = res.json()
                    direct_url = res_data.get('url')
                    ik_id = res_data.get('fileId')
                    print(f"🔗 Отримано залізобетонне посилання від ImageKit: {direct_url}", flush=True)
                    return direct_url, ik_id
                else:
                    print(f"⚠️ ImageKit відхилив запит (Код {res.status_code}): {res.text}", flush=True)
            except Exception as e:
                print(f"⚠️ Помилка завантаження на ImageKit: {e}", flush=True)

        # 3️⃣ Спроба через ImgBB API (Тільки для Фото)
        imgbb_key = os.environ.get("IMGBB_API_KEY")
        if imgbb_key and mime_type == "image/jpeg":
            print(f"☁️ Завантажуємо photo {filename} на ImgBB API...", flush=True)
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://api.imgbb.com/1/upload',
                        data={'key': imgbb_key, 'expiration': 86400},
                        files={'image': (filename, f, mime_type)},
                        timeout=30
                    ).json()
                    
                if res.get('success'):
                    direct_url = res['data']['url']
                    print(f"🔗 Отримано залізобетонне посилання від ImgBB: {direct_url}", flush=True)
                    return direct_url, None
                else:
                    print(f"⚠️ ImgBB повернув помилку: {res.get('error', {}).get('message')}", flush=True)
            except Exception as e:
                print(f"⚠️ Помилка завантаження на ImgBB: {e}", flush=True)

    print(f"🚨 Всі хостинги відмовили! Аварійний режим для Google Drive ID: {file_id}", flush=True)
    return f"https://docs.google.com/uc?export=download&id={file_id}", None

def wait_for_instagram_media(container_id, access_token, max_retries=15, delay=10):
    """
    Циклічно перевіряє статус готовності медіаконтейнера в Instagram.
    """
    url = f"https://graph.facebook.com/v19.0/{container_id}"
    params = {
        "fields": "status_code",
        "access_token": access_token
    }
    
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, params=params).json()
            status = response.get("status_code")
            
            if status == "FINISHED":
                print("✅ Відео успішно оброблено Instagram і готове до публікації!")
                return True
            elif status == "ERROR":
                print(f"❌ Помилка обробки відео на стороні Instagram: {response}")
                return False
            elif status in ["IN_PROGRESS", "CREATING"]:
                print(f"⏳ Instagram все ще обробляє відео (Статус: {status}). Чекаємо {delay} сек... (Спроба {attempt}/{max_retries})")
                time.sleep(delay)
            else:
                print(f"❓ Отримано невідомий статус: {status}. Очікування...")
                time.sleep(delay)
        except Exception as e:
            print(f"⚠️ Помилка під час запиту статусу: {e}")
            time.sleep(delay)
            
    print("❌ Вийшов таймаут очікування обробки відео в Instagram.")
    return False
    
def publish_to_meta_platforms(media_url, media_type, is_story=False, caption="", local_file_path=None):

    if not IG_USER_ID or not FB_PAGE_ID or not META_ACCESS_TOKEN:
        raise ValueError("❌ Відсутні обов'язкові змінні оточення: IG_USER_ID, FB_PAGE_ID або META_ACCESS_TOKEN!")
        
    print(f"📤 Відправка контенту в Instagram акаунт (ID: {IG_USER_ID})...")
    ig_url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    ig_payload = {
        "access_token": META_ACCESS_TOKEN,
        "media_type": "STORIES" if is_story else ("REELS" if media_type == "video" else "IMAGE")
    }
    if media_type == "video": 
        ig_payload["video_url"] = media_url
    else: 
        ig_payload["image_url"] = media_url
        
    if not is_story and caption: 
        ig_payload["caption"] = caption

    ig_res = requests.post(ig_url, data=ig_payload).json()
    if "id" not in ig_res: 
        raise ValueError(f"❌ Помилка створення контейнера Instagram: {ig_res}")
    
    ig_creation_id = ig_res["id"]
    
    max_retries = 18 if media_type == "video" else 5
    check_delay = 10 if media_type == "video" else 3
    
    print(f"⏳ Запущено моніторинг готовності медіа-контейнера ID: {ig_creation_id}...")
    is_ready = wait_for_instagram_media(ig_creation_id, META_ACCESS_TOKEN, max_retries=max_retries, delay=check_delay)
    
    if not is_ready:
        raise ValueError("❌ Медіафайл не готовий до публікації в Instagram за лімітом часу (Таймаут).")
        
    ig_pub_res = requests.post(
        f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", 
        data={"creation_id": ig_creation_id, "access_token": META_ACCESS_TOKEN}
    ).json()
    
    if "id" not in ig_pub_res:
        raise ValueError(f"❌ Помилка фінальної публікації в Instagram: {ig_pub_res}")
        
    print(f"✅ [Instagram] Успішно в ефірі! ID: {ig_pub_res['id']}")

    # --- БЛОК ФЕЙСБУКУ ---
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
                "caption": caption,   
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
        print("ℹ️ [Facebook] Публікація Сторіз через API обмежена політикою Meta. Пропускаємо цей крок.")

def main():
    if len(sys.argv) < 3:
        print("❌ Помилка: Відсутні обов'язкові аргументи.")
        print("\n📋 ПРАВИЛЬНИЙ ФОРМАТ ЗАПУСКУ:")
        print("  python publish_content.py [mode] \"[sheet_name]\"")
        print("\n💡 Доступні режими [mode]:")
        print("  post  - публікація згенерованого поста у стрічку")
        print("  story - оптимізація та публікація у Сторіс")

        try:
            _, sheets = get_services()
            spreadsheet = sheets.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
            available_sheets = [
                f'"{sheet["properties"]["title"]}"' 
                for sheet in spreadsheet.get('sheets', [])
                if "налаштування" not in sheet["properties"]["title"].lower()
            ]
            if available_sheets:
                print(f"\n📁 Можливі назви аркушів [sheet_name] у вашій таблиці:")
                print(f"  {', '.join(available_sheets)}")
                print(f"\nПриклад запуску: python publish_content.py post {available_sheets[0]}")
        except Exception:
            print("\nПриклад запуску: python publish_content.py post \"Назва_Аркуша\"")
        return
        
    mode = sys.argv[1].lower()
    tab_name = sys.argv[2]
    counter_col_idx = 3 if mode == 'post' else 4
    
    drive, sheets = get_services()
    
    res = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!A2:E").execute()
    rows = res.get('values', [])
    if not rows: 
        print(f"ℹ️ Немає даних на аркуші '{tab_name}'")
        return
        
    valid_rows = []
    for i, r in enumerate(rows):
        if len(r) >= 3:
            while len(r) < 5: r.append("0")
            try:
                r[3], r[4] = (int(r[3]) if r[3] else 0), (int(r[4]) if r[4] else 0)
                valid_rows.append({"row_idx": i + 2, "data": r})
            except ValueError: continue

    if not valid_rows:
        print("ℹ️ Немає валідних рядків для обробки.")
        return

    min_count = min(r["data"][counter_col_idx] for r in valid_rows)
    min_pool = [r for r in valid_rows if r["data"][counter_col_idx] == min_count]
    
    selected_item = None

    if "мебл" in tab_name.lower():
        selected_item = min_pool[0]
        print(f"🪑 Режим Меблів: календарні правила пропущено. Обрано файл з пулу мінімальних публікацій.")
    else:
        active_categories = get_active_rules_ordered()
        for category in active_categories:
            match_files = [item for item in min_pool if item["data"][2] == category]
            if match_files:
                selected_item = match_files[0]
                print(f"📅 Режим Календаря: знайдено збіг за категорією '{category}'")
                break
        
        if not selected_item: 
            selected_item = min_pool[0]
            
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

    files_to_publish = [final_upload_path]

    if mode == 'post' and mime_type == "image/jpeg":
        try:
            with Image.open(final_upload_path) as img:
                img = img.convert('RGB')
                w, h = img.size
                ratio = w / h
                
                if ratio < 0.8 or ratio > 1.91:
                    print(f"📐 Оптимізація Поста: Пропорції картинки ({ratio:.2f}) неприпустимі для стрічки. Коригуємо...")
                    padded_post_path = os.path.join('temp_media', 'post_padded_' + orig_name.rsplit('.', 1)[0] + '.jpg')
                    
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
                    
                    if final_upload_path != local_path and os.path.exists(final_upload_path):
                        os.remove(final_upload_path)
                    final_upload_path = padded_post_path
                    files_to_publish = [padded_post_path]
                    print(f"✅ Стрічка: картинку вписано в безпечні рамки {new_w}x{new_h}.")
        except Exception as e:
            print(f"⚠️ Помилка калібрування геометрії поста: {e}")

    if mode == 'story' and mime_type == "image/jpeg":
        print("📐 Режим Сторіс: вписуємо зображення у format 1080x1920...")
        story_path = os.path.join('temp_media', 'story_padded_' + orig_name.rsplit('.', 1)[0] + '.jpg')
        try:
            with Image.open(final_upload_path) as img:
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
                
            if final_upload_path != local_path and os.path.exists(final_upload_path):
                os.remove(final_upload_path)
                
            final_upload_path = story_path
            files_to_publish = [story_path]
        except Exception as e:
            print(f"⚠️ Не вдалося відформатувати Сторіс: {e}. Буде надіслано оригінал.")

    elif mode == 'story' and mime_type == "video/mp4":
        print("📐 Режим Сторіс для ВІДЕО: інтелектуально нарізаємо на частини по 50 секунд під 1080x1920...")
        segment_pattern = os.path.join('temp_media', 'story_part_' + orig_name.rsplit('.', 1)[0] + '_%03d.mp4')
        
        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', final_upload_path,
            '-f', 'segment',
            '-segment_time', '50',          
            '-reset_timestamps', '1',       
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black',
            '-c:v', 'libx264', 
            '-profile:v', 'high',          
            '-level', '4.2',
            '-crf', '23',                  
            '-preset', 'fast',
            '-g', '60',                    
            '-keyint_min', '60',
            '-sc_threshold', '0',          
            '-r', '30',                    
            '-c:a', 'aac', 
            '-b:a', '128k',
            '-ar', '44100',                
            '-movflags', 'faststart',     
            '-pix_fmt', 'yuv420p',
            segment_pattern
        ]
        
        result = subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            prefix = 'story_part_' + orig_name.rsplit('.', 1)[0] + '_'
            generated_parts = sorted([
                os.path.join('temp_media', f) 
                for f in os.listdir('temp_media') 
                if f.startswith(prefix) and f.endswith('.mp4')
            ])
            if generated_parts:
                files_to_publish = generated_parts
                print(f"✅ Відео успішно розрізано на {len(files_to_publish)} частин(и) під Сторіс!")
                if final_upload_path != local_path and os.path.exists(final_upload_path):
                    os.remove(final_upload_path)
            else:
                print("⚠️ Файли сегментів не знайдені. Спробуємо надіслати єдиний оригінал.")
        else:
            print("⚠️ Не вдалося нарізати відео через ffmpeg, спробуємо надіслати оригінал.")

    caption_text = ""
    if mode == 'post':
        analysis_image = files_to_publish[0]
        if mime_type == "video/mp4":
            analysis_image = os.path.join('temp_media', 'video_frame.jpg')
            subprocess.run(['ffmpeg', '-y', '-i', files_to_publish[0], '-ss', '00:00:01', '-vframes', '1', analysis_image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        print("👁️ ШІ аналізує візуальний вміст файлу...")
        caption_text = generate_multimodal_caption(analysis_image, category_name, tab_name)
        if os.path.exists(os.path.join('temp_media', 'video_frame.jpg')): 
            os.remove(os.path.join('temp_media', 'video_frame.jpg'))

    success_count = 0
    
    for idx, current_file in enumerate(files_to_publish):
        part_info = f" (Частина {idx + 1}/{len(files_to_publish)})" if len(files_to_publish) > 1 else ""
        print(f"🚀 Початок публікації файлу{part_info}: {current_file}")
        
        ik_file_id = None
        try:
            public_url, ik_file_id = get_google_drive_direct_url(file_id, local_file_path=current_file)
            print(f"🔗 Згенеровано посилання для буфера: {public_url}")
            
            publish_to_meta_platforms(
                public_url, 
                "video" if mime_type == "video/mp4" else "image", 
                is_story=(mode == 'story'), 
                caption=caption_text,
                local_file_path=current_file
            )
            success_count += 1
            print(f"✅ Файл{part_info} успішно опубліковано в Instagram!")
            
            if len(files_to_publish) > 1 and idx < len(files_to_publish) - 1:
                print("⏳ Очікуємо 6 секунд перед надсиланням наступної частини для збереження хронології...")
                time.sleep(6)
                
        except Exception as e:
            print(f"❌ Критична помилка під час публікації файлу{part_info}: {e}")
        finally:
            if ik_file_id:
                delete_from_imagekit(ik_file_id)
            if current_file != local_path and os.path.exists(current_file):
                os.remove(current_file)

    if success_count > 0:
        new_counter = selected_item["data"][counter_col_idx] + 1
        col_letter = "D" if mode == 'post' else "E"
        sheets.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID, range=f"'{tab_name}'!{col_letter}{row_line}",
            valueInputOption='RAW', body={'values': [[new_counter]]}
        ).execute()
        print(f"📊 Лічильник оновлено на +1 для аркуша '{tab_name}' (Рядок {row_line}). Опубліковано шматків: {success_count}")
    else:
        print("❌ Жодна з частин медіа не була опублікована через помилки. Лічильник залишено без змін.")
        sys.exit(1)

    if os.path.exists(local_path): 
        os.remove(local_path)
    print("🧹 Всі локальні тимчасові файли повністю видалено. Роботу завершено!")

if __name__ == '__main__':
    main()
