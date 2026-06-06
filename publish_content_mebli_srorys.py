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
from PIL import Image, ImageDraw, ImageFont
from pillow_heif import register_heif_opener

# Реєстрація підтримки HEIF/HEIC
register_heif_opener()

# ⚙️ НАЛАШТУВАННЯ (Беруться напряму з системних змінних GitHub Actions)
IG_USER_ID = os.environ.get("IG_USER_ID")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")

SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
TAB_NAME = "Меблі"

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

VALID_MEDIA_EXTENSIONS = ('.gif', '.heic', '.heif', '.jpeg', '.jpg', '.mp4', '.png', '.webp', '.mov', '.avi')
DOCUMENT_EXTENSIONS = ('.pdf', '.doc', '.docx', '.djvu', '.txt', '.rtf', '.fb2', '.epub')

# 🏢 ГЛОБАЛЬНА БАЗА ДАНИХ КОМПАНІЙ ТА КАТЕГОРІЙ
COMPANIES_DB = {
    "goncharenko": {
        "names": {0: "Олександр Гончаренко", 1: "Oleksandr Goncharenko", 2: "Oleksandr Goncharenko"},
        "links": ["📸 Instagram: instagr.am/goncharenko8721"]
    },
    "gurov": {
        "names": {0: "Андрій Гуров", 1: "Andrii Gurov", 2: "Andrii Gurov"},
        "links": ["🌐 Facebook: fb.com/andrej.gurov.755581"]
    },
    "solovey": {
        "names": {0: "Студія меблів «Соловей»", 1: "Solovey Furniture Studio", 2: "Möbelstudio Solovey"},
        "links": ["📸 Instagram: instagr.am/mebelsolovei"]
    },
    "furniture park": {
        "names": {0: "Меблевий парк", 1: "Furniture Park", 2: "Furniture Park"},
        "links": [
            "📸 Instagram: instagr.am/meblevyi_park",
            "📸 Instagram: instagr.am/meblovo_ukraine",
            "📢 Telegram: t.me/Meblevyi_park",
            "📸 Instagram: instagr.am/renovaelite"
        ]
    }
}

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

# 📐 ОПТИМІЗАЦІЯ ФОТО ПІД СТОРІЗ (1080x1920)
def optimize_image_story(final_upload_path, orig_name):
    print("📐 Режим Сторіс: вписуємо зображення у формат 1080x1920, щоб уникнути кропу...")
    story_path = os.path.join('temp_mebli', 'story_padded_' + orig_name.rsplit('.', 1)[0] + '.jpg')
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
            
        return story_path
    except Exception as e:
        print(f"⚠️ Не вдалося відформатувати фото під сторіз: {e}")
        return final_upload_path

# 📐 ОПТИМІЗАЦІЯ ВІДЕО ПІД СТОРІЗ (1080x1920) + НАКЛАДАННЯ ТЕКСТУ ЧЕРЕЗ FFMPEG
def optimize_video_story(local_path, f_name, text):
    print("🎬 Оптимізація Відео: підганяємо під формат 1080x1920 за допомогою FFmpeg...")
    story_video_path = os.path.join('temp_mebli', 'story_padded_' + f_name.rsplit('.', 1)[0] + '.mp4')
    
    # Фільтр для вписування відео у 1080x1920 з темно-сірим бекграундом
    vf_filters = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black"
    
    # Якщо є згенерований текст, накладаємо його за допомогою drawtext (типовий шрифт в Ubuntu Runner)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.exists(font_path) and text:
        clean_text = text.replace("'", "").replace(":", "\\:")
        # Малюємо текст на безпечній висоті (y=1550) з напівпрозорою підкладкою
        vf_filters += f",drawtext=fontfile={font_path}:text='{clean_text}':x=(w-text_w)/2:y=1550:fontsize=34:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=20"
    
    cmd = [
        'ffmpeg', '-y', '-i', local_path,
        '-vf', vf_filters,
        '-c:v', 'libx264', '-profile:v', 'main', '-level:v', '4.0', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k',
        story_video_path
    ]
    
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(story_video_path):
            return story_video_path
    except Exception as e:
        print(f"⚠️ FFmpeg завершився з помилкою: {e}")
    return local_path

# ✍️ ГАРМОНІЙНЕ НАКЛАДАННЯ ТЕКСТУ НА ЗОБРАЖЕННЯ (PILLOW)
def overlay_text_on_image(image_path, text):
    try:
        with Image.open(image_path) as img:
            img = img.convert('RGBA')
            draw = ImageDraw.Draw(img)
            
            font_size = 38
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except IOError:
                font = ImageFont.load_default()
            
            # Автоматичний перенос тексту по словах під ширину сторіс (макс 900px)
            words = text.split()
            lines = []
            current_line = []
            for word in words:
                current_line.append(word)
                if draw.textlength(" ".join(current_line), font=font) > 900:
                    current_line.pop()
                    lines.append(" ".join(current_line))
                    current_line = [word]
            if current_line:
                lines.append(" ".join(current_line))
            
            clean_text = "\n".join(lines)
            
            # Обчислення габаритів тексту для створення фонової плашки
            bbox = draw.multiline_textbbox((0, 0), clean_text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            
            # Позиціонування плашки у нижній третині екрана (y=1550)
            rect_x1 = (1080 - text_w) // 2 - 25
            rect_y1 = 1550 - 20
            rect_x2 = (1080 + text_w) // 2 + 25
            rect_y2 = rect_y1 + text_h + 40
            
            # Малюємо стильну темну капсулу-підкладку (альфа 160)
            overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rectangle([rect_x1, rect_y1, rect_x2, rect_y2], fill=(0, 0, 0, 160))
            
            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)
            
            # Наносимо сам текст рівно по центру плашки
            draw.multiline_text(((1080 - text_w) // 2, rect_y1 + 20), clean_text, font=font, fill=(255, 255, 255), align="center")
            
            final_img = img.convert('RGB')
            final_img.save(image_path, 'JPEG', quality=95)
            print(f"🎨 Текст успішно нанесено на зображення сторіс.")
    except Exception as e:
        print(f"⚠️ Помилка графічного накладання тексту: {e}")

def get_google_drive_direct_url(file_id, local_file_path=None):
    if local_file_path and os.path.exists(local_file_path):
        filename = os.path.basename(local_file_path)
        lower_name = filename.lower()
        mime_type = "video/mp4" if lower_name.endswith(('.mp4', '.mov', '.avi')) else "image/jpeg"
        browser_headers = {'User-Agent': 'Mozilla/5.0'}
        
        # 1️⃣ Catbox.moe
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
                    return res.text.strip(), None
        except: pass

        # 2️⃣ ImageKit.io
        imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
        if imagekit_key:
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://upload.imagekit.io/api/v1/files/upload',
                        auth=(imagekit_key, ''),
                        files={'file': (filename, f, mime_type)},
                        data={'fileName': filename, 'useUniqueFileName': 'true'}, timeout=60
                    )
                    if res.status_code in [200, 201]:
                        res_data = res.json()
                        return res_data.get('url'), res_data.get('fileId')
            except: pass

    return f"https://docs.google.com/uc?export=download&id={file_id}", None

def delete_from_imagekit(file_id: str):
    if not file_id: return
    imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
    if not imagekit_key: return
    try: requests.delete(f"https://api.imagekit.io/v1/files/{file_id}", auth=(imagekit_key, ''), timeout=15)
    except: pass

# 🧠 ШІ ГЕНЕРАЦІЯ ЛАКОНІЧНОГО ОПИСУ ДЛЯ КОНКРЕТНОЇ СТОРІС
def generate_story_caption(image_paths, category, date_str, lang_idx, target_loc):
    gemini_key = os.environ.get("GEMINI_API_KEY")
    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else str(datetime.now().year)
    
    cat_lower = category.lower()
    real_manufacturer = category
    for key, info in COMPANIES_DB.items():
        if key in cat_lower:
            real_manufacturer = info["names"].get(lang_idx, info["names"][0])
            break

    if not gemini_key:
        return "Професійна якість та увага до деталей! ✨🛠️"

    models_to_try = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]
    lang_instructions = {
        0: "Напиши текст виключно УКРАЇНСЬКОЮ мовою. Використовуй емодзі.",
        1: "Write the text exclusively in ENGLISH. Use emojis.",
        2: "Schreibe den Text ausschließlich auf DEUTSCH. Nutze Emojis."
    }
    
    # Спеціалізований промпт під реалії розробки, креслень, замірів та браку
    prompt = (
        f"Ти професійний копірайтер та меблевий конструктор. Подивись на це зображення (або кадр з відео).\n"
        f"Напиши ОДНУ коротку, мотиваційну або інформативну фразу (максимум 1-2 речення) для Instagram Stories.\n"
        f"Врахуй контекст: на фото може бути як готовий меблевий шедевр, так і брудний процес виробництва, технічна документація, "
        f"заміри приміщення, скріншоти програм, робочі моменти команди або навіть виправлення браку/дефектів.\n"
        f"Зроби опис живим, експертним, без банальних закликів. Текст буде нанесено прямо на медіафайл.\n"
        f"Бренд/Концепт: '{real_manufacturer}'. Рік: {year}. Локація: {target_loc if target_loc else 'Робочий процес'}.\n"
        f"{lang_instructions.get(lang_idx, lang_instructions[0])}\n"
        f"КРИТИЧНО: Видай ЛІШЕ фінальний текст підпису без лапок, вступів та хештегів."
    )

    try:
        parts = [{"text": prompt}]
        for img_path in image_paths:
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    image_bytes = f.read()
                base64_image = base64.b64encode(image_bytes).decode('utf-8')
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64_image}})
        
        payload = {"contents": [{"parts": parts}]}
        for model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
            try:
                res = requests.post(url, json=payload, timeout=20).json()
                if 'candidates' in res and res['candidates']:
                    return res['candidates'][0]['content']['parts'][0]['text'].strip()
            except: continue
    except Exception as e:
        print(f"⚠️ Помилка генерації тексту ШІ: {e}")
    return "Створюємо меблі з душею та точним розрахунком! 📐✨"

def wait_for_meta_container(container_id, access_token):
    check_url = f"https://graph.facebook.com/v19.0/{container_id}"
    params = {"fields": "status_code,status", "access_token": access_token}
    for _ in range(30):
        try:
            r = requests.get(check_url, params=params).json()
            status = r.get("status_code", "").upper()
            if status == "FINISHED": return True
            elif status == "ERROR": return False
            print(f"⏳ Очікування обробки відео для сторіс... Статус: {status}")
        except: pass
        time.sleep(10)
    return False

def main():
    if len(sys.argv) < 3:
        print("💡 Запуск: python script.py ig_story <tab_name>")
        return

    mode = sys.argv[1].lower()  # Очікується 'ig_story'
    forced_tab = sys.argv[2]
    current_tab = forced_tab if forced_tab else TAB_NAME
    
    if mode != "ig_story":
        print(f"❌ Цей скрипт сконструйовано виключно під 'ig_story'. Передано: {mode}")
        return

    print(f"📊 [Режим: INSTAGRAM STORIES] Аналіз реєстру '{current_tab}'...")
    drive, sheets = get_services()

    res = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"'{current_tab}'!A2:H").execute()
    rows = res.get('values', [])
    if not rows:
        print("ℹ️ Реєстр порожній.")
        return

    # Динамічні лічильники для Сторіз (Колонка E, Індекс 4)
    col_idx = 4
    col_letter = "E"
    target_lang_cell = "'⚙️ Налаштування Папок'!H2"

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
        print("ℹ️ Немає доступних рядків.")
        return

    # Шукаємо файли з мінімальним лічильником використання
    min_counter = min(item["counter"] for item in valid_rows)
    min_pool = [item for item in valid_rows if item["counter"] == min_counter]

    groups = {}
    for item in min_pool:
        data = item["data"]
        group_key = (data[2], data[6] if len(data) > 6 else "", data[7] if len(data) > 7 else "")
        groups.setdefault(group_key, []).append(item)

    first_key = list(groups.keys())[0]
    # Беремо до 4 файлів з медіа-групи для публікації серії сторіз
    selected_group_items = groups[first_key][:4]
    category_name, target_date, target_loc = first_key
    print(f"📂 Обрано групу: {category_name}. Елементів для публікації один за одним: {len(selected_group_items)}")

    # 🌐 Управління мовною каруселлю з комірки H2
    lang_value = "UK"
    try:
        lang_res = sheets.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=target_lang_cell).execute()
        lang_values = lang_res.get('values', [])
        if lang_values and lang_values[0]:
            lang_value = lang_values[0][0].strip().upper()
    except Exception as e:
        print(f"⚠️ Не вдалося зчитати мову з комірки H2: {e}")

    if any(x in lang_value for x in ["EN", "ENG", "АНГЛ", "ENGLISH"]):
        lang_idx = 1
        next_lang_value = "DE"
    elif any(x in lang_value for x in ["DE", "GER", "НІМ", "DEUTSCH"]):
        lang_idx = 2
        next_lang_value = "UK"
    else:
        lang_idx = 0
        next_lang_value = "EN"
        
    print(f"🌐 Поточна мова Сторіз: {lang_value} (Індекс: {lang_idx}). Наступна буде: {next_lang_value}")
    
    os.makedirs('temp_mebli', exist_ok=True)
    local_files_to_clean = []
    success_published_any = False

    # 📥 ПОКРОКОВА ОБРОБКА ТА ПУБЛІКАЦІЯ СТОРІЗ ОДИН ЗА ОДНИМ
    for idx_item, item in enumerate(selected_group_items):
        f_id, f_name = item["data"][0], item["data"][1]
        lower_name = f_name.lower()
        
        if not lower_name.endswith(VALID_MEDIA_EXTENSIONS):
            log_unsupported_to_service(sheets, category_name, f_name, reason="непідтримуваний формат для сторіз")
            continue

        local_path = os.path.join('temp_mebli', f_name)
        print(f"\n📥 [{idx_item + 1}/{len(selected_group_items)}] Завантаження з Drive: {f_name}...")
        
        try:
            request = drive.files().get_media(fileId=f_id)
            with open(local_path, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done: _, done = downloader.next_chunk()
        except Exception as e:
            print(f"❌ Не вдалося завантажити {f_name}: {e}")
            continue

        final_path = local_path
        mime_type = "image/jpeg"
        is_video = False
        
        if lower_name.endswith(('.mp4', '.mov', '.avi')):
            is_video = True
            mime_type = "video/mp4"
        elif lower_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join('temp_mebli', f_name.rsplit('.', 1)[0] + '.jpg')
            with Image.open(local_path) as img:
                img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
            final_path = jpg_path
            local_files_to_clean.append(jpg_path)

        local_files_to_clean.append(local_path)

        # 🎞️ Генерація прев'ю-кадру для аналізу ШІ (якщо це відео)
        ai_media_snapshot = final_path
        if is_video:
            frame_path = os.path.join('temp_mebli', f"frame_{f_id}.jpg")
            subprocess.run(['ffmpeg', '-y', '-i', final_path, '-ss', '00:00:01', '-vframes', '1', frame_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(frame_path):
                ai_media_snapshot = frame_path
                local_files_to_clean.append(frame_path)

        # 🧠 ШІ створює унікальну мотиваційну фразу під цей конкретний кадр
        story_caption_text = generate_story_caption([ai_media_snapshot], category_name, target_date, lang_idx, target_loc)
        print(f"💬 Текст для Сторіс: \"{story_caption_text}\"")

        # 📐 Оптимізація під формат Сторіс (1080x1920) + Вбудовування тексту
        if is_video:
            # Оптимізуємо відео та накладаємо текст засобами FFmpeg drawtext
            optimized_path = optimize_video_story(final_path, f_name, story_caption_text)
        else:
            # Оптимізуємо зображення (вписуємо в полотно)
            optimized_path = optimize_image_story(final_path, f_name)
            # Накладаємо підготовлений текст зверху засобами Pillow
            overlay_text_on_image(optimized_path, story_caption_text)

        if optimized_path != final_path and optimized_path != local_path:
            local_files_to_clean.append(optimized_path)

        # ☁️ Завантаження готового медіафайлу на хмару
        pub_url, ik_id = get_google_drive_direct_url(f_id, local_file_path=optimized_path)
        
        if not pub_url:
            print(f"⚠️ Не вдалося отримати публічне посилання для {f_name}.")
            continue

        # 🚀 Відправка у контейнер Meta Graph API (media_type=STORIES)
        print(f"📡 Надсилання сторіз в Meta API...")
        param_type = "video_url" if is_video else "image_url"
        payload = {
            "media_type": "STORIES",
            param_type: pub_url,
            "access_token": META_ACCESS_TOKEN
        }
        
        res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media", data=payload).json()
        
        if res and "id" in res:
            creation_id = res["id"]
            if is_video:
                wait_for_meta_container(creation_id, META_ACCESS_TOKEN)
                
            # Фінальний запуск публікації в Instagram Stories
            publish_res = requests.post(f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", data={
                "creation_id": creation_id, "access_token": META_ACCESS_TOKEN
            }).json()
            
            if "id" in publish_res:
                print(f"✅ Сторіз [{f_name}] успішно опубліковано! ID: {publish_res['id']}")
                success_published_any = True
                
                # Оновлюємо лічильник використання саме для цього файлу в таблиці
                new_val = item["counter"] + 1
                range_to_update = f"'{current_tab}'!{col_letter}{item['row_idx']}"
                try:
                    sheets.spreadsheets().values().update(
                        spreadsheetId=SPREADSHEET_ID, range=range_to_update,
                        valueInputOption='RAW', body={'values': [[new_val]]}
                    ).execute()
                    print(f"✍️ Лічильник у {col_letter}{item['row_idx']} оновлено на {new_val}.")
                except Exception as e:
                    print(f"⚠️ Не вдалося зберегти лічильник: {e}")
            else:
                print(f"❌ Помилка публікації сторіз в Meta API: {publish_res}")
        else:
            print(f"❌ Помилка створення контейнера сторіз: {res}")

        # Очищення хмари хостингу ImageKit (якщо був задіяний)
        if ik_id: delete_from_imagekit(ik_id)

    # 🔄 Якщо відбулася хоча б одна публікація — перемикаємо мову на наступну в черзі (в комірці H2)
    if success_published_any:
        try:
            sheets.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID, range=target_lang_cell,
                valueInputOption='RAW', body={'values': [[next_lang_value]]}
            ).execute()
            print(f"\n🔄 Мову для наступного запуску Сторіз (комірка H2) змінено на: {next_lang_value}")
        except Exception as e:
            print(f"⚠️ Не вдалося оновити мову в комірці H2: {e}")

    # 🧹 Тотальне очищення тимчасових файлів
    for f in local_files_to_clean:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
    print("🧹 Тимчасові локальні файли успішно очищені.")

if __name__ == "__main__":
    main()
