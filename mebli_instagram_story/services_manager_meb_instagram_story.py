import os
import json
import time
import base64
import requests
import subprocess
import io
from PIL import Image as PILImage
from google import genai
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Явно імпортуємо оптимізований конфіг для централізованого доступу до налаштувань
import config_meb_insta_story as config

def get_services():
    key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
    creds = service_account.Credentials.from_service_account_info(key_dict, scopes=config.SCOPES)
    return build('drive', 'v3', credentials=creds), build('sheets', 'v4', credentials=creds)

def log_unsupported_to_service(sheets_service, folder_name, file_name, reason="непідтримуваний формат"):
    try:
        res = sheets_service.spreadsheets().values().get(
            spreadsheetId=config.SPREADSHEET_ID, range="'⚙️ Налаштування Папок'!A2:E"
        ).execute()
        rows = res.get('values', [])
        
        for idx, row in enumerate(rows):
            if len(row) > 1 and row[1] == folder_name:
                range_to_update = f"'⚙️ Налаштування Папок'!E{idx + 2}"
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=config.SPREADSHEET_ID, range=range_to_update,
                    valueInputOption='RAW', body={'values': [[f"⚠️ {reason}: {file_name}"]]}
                ).execute()
                print(f"📝 Зафіксовано системне попередження для [{folder_name}] на службовому аркуші.")
                break
    except Exception as e:
        print(f"❌ Не вдалося записати помилку на службовий аркуш: {e}")

def get_google_drive_direct_url(file_id, local_file_path=None):
    if local_file_path and os.path.exists(local_file_path):
        filename = os.path.basename(local_file_path)
        lower_name = filename.lower()
        mime_type = "video/mp4" if lower_name.endswith(('.mp4', '.mov', '.avi')) else "image/jpeg"
        browser_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
        }
        
        # 0️⃣ GitHub Репозиторій (Основний супер-стабільний варіант)
        github_repo = os.environ.get("GITHUB_REPOSITORY")  # Змінна автоматично надається GitHub Actions
        if github_repo:
            print(f"🐙 [GitHub] Завантажуємо тимчасовий медіафайл {filename} у репозиторій...")
            try:
                target_dir = os.path.join("docs", "temp_media")
                os.makedirs(target_dir, exist_ok=True)
                github_local_path = os.path.join(target_dir, filename)
                
                import shutil
                shutil.copy(local_file_path, github_local_path)
                
                # Автоматичне налаштування профілю Git в середовищі Actions
                subprocess.run(["git", "config", "user.name", "github-actions[bot]"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Робимо pull з ребейзом, щоб уникнути конфліктів, якщо обробляється кілька файлів підряд
                subprocess.run(["git", "pull", "origin", "main", "--rebase"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["git", "add", github_local_path], check=True)
                subprocess.run(["git", "commit", "-m", f"⚡ Наживо: {filename}"], check=True)
                subprocess.run(["git", "push", "origin", "main"], check=True)
                
                # Формуємо сире пряме посилання, яке працює МИТТЄВО
                raw_url = f"https://raw.githubusercontent.com/{github_repo}/main/docs/temp_media/{filename}"
                print(f"✅ Файл успішно опубліковано на GitHub: {raw_url}")
                return raw_url, "github_skip"
            except Exception as git_err:
                print(f"⚠️ Збій GitHub-хостингу: {git_err}. Переходимо до резервних хмар...")

        # 1️⃣ Catbox.moe
        print(f"☁️ Завантажуємо сторіс-файл {filename} на Catbox.moe...")
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
            print(f"☁️ Завантажуємо сторіс-файл {filename} on ImageKit.io...")
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

        # 3️⃣ ImgBB API
        imgbb_key = os.environ.get("IMGBB_API_KEY")
        if imgbb_key and mime_type == "image/jpeg":
            print(f"☁️ Завантажуємо фото сторіс {filename} на ImgBB API...")
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
                        return res['data']['url'], None
            except: pass

    print(f"🚨 Аварійний режим завантаження для Google Drive ID: {file_id}")
    return f"https://docs.google.com/uc?export=download&id={file_id}", None

def delete_from_imagekit(file_id: str):
    if not file_id: return
    imagekit_key = os.environ.get("IMAGEKIT_PRIVATE_KEY")
    if not imagekit_key: return
    try: requests.delete(f"https://api.imagekit.io/v1/files/{file_id}", auth=(imagekit_key, ''), timeout=15)
    except: pass

def generate_story_caption(image_paths, category, date_str, lang_idx, target_loc):
    gemini_key = os.environ.get("GEMINI_API_KEY")
    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else str(datetime.now().year)
    
    # Фолбеки з LANG_CONFIG
    pref = config.LANG_CONFIG.get(lang_idx, config.LANG_CONFIG[0])
    
    # --- БЛОК ОБРОБКИ БАГАТОМОВНОЇ ЛОКАЦІЇ ---
    resolved_loc = ""
    if target_loc:
        try:
            loc_json = json.loads(target_loc)
            if isinstance(loc_json, dict):
                resolved_loc = loc_json.get(str(lang_idx), loc_json.get("0", ""))
            else:
                resolved_loc = str(target_loc)
        except (json.JSONDecodeError, TypeError):
            resolved_loc = str(target_loc)

    invalid_markers = ["невідоме місце", "невідомо", "unknown", "unbekannt", "-", "none", "null", "невідоме місто"]
    if any(marker in resolved_loc.lower() for marker in invalid_markers):
        resolved_loc = ""

    # --- ДИНАМІЧНЕ ВИЗНАЧЕННЯ БРЕНДУ ТА КАТЕГОРІЙ ---
    cat_lower = category.lower()
    real_manufacturer = category
    matched_special = False
    
    for spec_key, spec_translation in pref.get("categories", {}).items():
        if spec_key in cat_lower:
            real_manufacturer = spec_translation
            matched_special = True
            break
            
    if not matched_special:
        for key, names_dict in config.COMPANIES_DB.items():
            if key in cat_lower:
                real_manufacturer = names_dict.get(lang_idx, names_dict.get(0, category))
                break

    if not gemini_key:
        return pref.get("no_gemini_caption", "Професійна якість та увага до деталей!")

    # 🌟 Актуальний пул моделей
    models_to_try = ["gemini-3.5-flash", "gemini-3.1-flash-lite"]
    
    lang_instructions = {
        0: "Напиши текст виключно УКРАЇНСЬКОЮ мовою. Дозволено додати 1-2 доречних емодзі.",
        1: "Write the text exclusively in ENGLISH. You may include 1-2 relevant emojis.",
        2: "Schreibe den Text ausschließlich auf DEUTSCH. Du darfst 1-2 passende Emojis hinzufügen."
    }
    
    prompt = (
        f"Ти — досвідчений копірайтер із тонким почуттям гумору та експертний меблевий конструктор.\n"
        f"Подивись на це зображення (або кадр з відео) і придумай ОДНУ коротку, влучну та чіпляючу фразу "
        f"(максимум 1-2 речення) для Instagram Stories. Текст буде нанесено прямо поверх медіафайлу.\n\n"
        f"🎯 ТОН ТА СТИЛІСТИКА:\n"
        f"- Будь живим та іронічним. Якщо на фото робочий процес, пил, креслення чи інструменти — "
        f"пожартуй про залаштунки, перфекціонізм, каву на тирсі чи складні技術ні вузли.\n"
        f"- Якщо на фото готовий виріб — пиши про естетику, меблеву філософію, ергономіку або «білямеблеві» теми "
        f"(домашній затишок, ідеальні зазори, радість від завершеного проєкту).\n"
        f"- Уникай банальних штампів: 'найкраща якість', 'купуйте у нас', 'індивідуальний підхід'.\n\n"
        f"📋 КОНТЕКСТ ДЛЯ АНАЛІЗУ:\n"
        f"Категорія/Бренд: '{real_manufacturer}'. Рік зйомки: {year}. Локація: {resolved_loc if resolved_loc else 'Меблеве виробництво'}.\n\n"
        f"⚠️ СУВОРІ ОБМЕЖЕННЯ:\n"
        f"1. {lang_instructions.get(lang_idx, lang_instructions[0])}\n"
        f"2. Поверни ЛИШЕ фінальний текст підпису. Без лапок, без вступних слів, без хэштегів та пояснень копірайтера.\n"
        f"3. Роби речення короткими, щоб вони легко читалися на екрані телефону."
    )

    # 🌟 Ініціалізуємо новий SDK клієнт (ключ підтягнеться з os.environ['GEMINI_API_KEY'])
    try:
        client = genai.Client()
        
        # Готуємо уніфікований масив вхідних даних (Interactions API)
        inputs = [{"type": "text", "text": prompt}]
        
        # Обробляємо та стискаємо кожне зображення перед відправкою
        for img_path in image_paths:
            if os.path.exists(img_path):
                try:
                    with PILImage.open(img_path) as img:
                        if img.mode in ("RGBA", "P"):
                            img = img.convert("RGB")
                        
                        # Зменшуємо роздільну здатність до розумного максимуму для ШІ
                        img.thumbnail((1024, 1024))
                        
                        buffer = io.BytesIO()
                        img.save(buffer, format="JPEG", quality=82, optimize=True)
                        image_bytes = buffer.getvalue()
                    
                    base64_image = base64.b64encode(image_bytes).decode('utf-8')
                    
                    # Додаємо у структурованому вигляді нового API
                    inputs.append({
                        "type": "image",
                        "data": base64_image,
                        "mime_type": "image/jpeg"
                    })
                except Exception as img_err:
                    print(f"⚠️ Не вдалося оптимізувати зображення {img_path}: {img_err}")

        # Каскадний перебір моделей через новий інтерфейс
        for model in models_to_try:
            print(f"🚀 Спроба генерації підпису через {model}...")
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

    except Exception as general_err:
        print(f"⚠️ Загальний збій блоку ШІ-генерації: {general_err}")
        
    return pref.get("fallback_caption", "Створюємо меблі з точним розрахунком!")
    
def wait_for_meta_container(container_id, access_token):
    check_url = f"https://graph.facebook.com/v19.0/{container_id}"
    params = {"fields": "status_code,status", "access_token": access_token}
    for _ in range(30):
        try:
            r = requests.get(check_url, params=params).json()
            status = r.get("status_code", "").upper()
            if status == "FINISHED": return True
            elif status == "ERROR": return False
            print(f"⏳ Очікування обробки медіафайлу в Meta... Статус: {status}")
        except: pass
        time.sleep(5)
    return False
