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

  # 🌍 ЦЕНТРАЛІЗОВАНИЙ КАСКАДНИЙ ЗАВАНТАЖУВАЧ З ПІДТРИМКОЮ IMGBB
def get_google_drive_direct_url(file_id, local_file_path=None):
    if local_file_path and os.path.exists(local_file_path):
        filename = os.path.basename(local_file_path)
        lower_name = filename.lower()
        mime_type = "video/mp4" if lower_name.endswith(('.mp4', '.mov', '.avi')) else "image/jpeg"
        browser_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
        }
        
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
            print(f"☁️ Завантажуємо сторіс-файл {filename} на ImageKit.io...")
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

        # 3️⃣ ImgBB API (Синхронізовано з основним модулем)
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

  # 🧠 ШІ ГЕНЕРАЦІЯ ЛАКОНІЧНОГО ОПИСУ ДЛЯ КОНКРЕТНОЇ СТОРІС
def generate_story_caption(image_paths, category, date_str, lang_idx, target_loc):
    gemini_key = os.environ.get("GEMINI_API_KEY")
    year = date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else str(datetime.now().year)
    
    # Використовуємо глобальний конфіг для фолбеків
    pref = LANG_CONFIG.get(lang_idx, LANG_CONFIG[0])
    
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
    # -----------------------------------------------

    # --- ВИЗНАЧЕННЯ БРЕНДУ ТА СПЕЦ-КАТЕГОРІЙ ---
    cat_lower = category.lower()
    real_manufacturer = category
    
    if "montage various" in cat_lower:
        real_manufacturer = "Професійний монтаж меблів" if lang_idx == 0 else "Professional furniture installation"
    elif "various" in cat_lower:
        real_manufacturer = "Сучасні меблеві тренди" if lang_idx == 0 else "Modern furniture concepts"
    elif "instruktion" in cat_lower:
        real_manufacturer = "Конструкторські стандарти" if lang_idx == 0 else "Furniture design standards"
    else:
        for key, info in COMPANIES_DB.items():
            if key in cat_lower:
                real_manufacturer = info["names"].get(lang_idx, info["names"][0])
                break

    if not gemini_key:
        return pref.get("no_gemini_caption", "Професійна якість та увага до деталей!")

    models_to_try = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]
    lang_instructions = {
        0: "Напиши текст виключно УКРАЇНСЬКОЮ мовою. КРИТИЧНО: НЕ використовуй жодних емодзі, смайлів чи спеціальних символів.",
        1: "Write the text exclusively in ENGLISH. CRITICAL: Do NOT use any emojis or special symbols.",
        2: "Schreibe den Text ausschließlich auf DEUTSCH. KRITISCH: Nutze absolute KEINE Emojis oder Sonderzeichen."
    }
    
    prompt = (
        f"Ти професійний копірайтер та меблевий конструктор. Подивись на це зображення (або кадр з відео).\n"
        f"Напиши ОДНУ коротку, мотиваційну або інформативну фразу (максимум 1-2 речення) для Instagram Stories.\n"
        f"Врахуй контекст: на фото може бути як готовий меблевий шедевр, так і брудний процес виробництва, технічна документація, "
        f"заміри приміщення, скріншоти програм, робочі моменти команди або навіть виправлення браку.\n"
        f"Зроби опис живим, експертним, без банальних закликів. Текст буде нанесено прямо на медіафайл.\n"
        f"Бренд/Концепт: '{real_manufacturer}'. Рік: {year}. Локація: {resolved_loc if resolved_loc else 'Робочий процес'}.\n"
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
            except: 
                continue
    except Exception as e:
        print(f"⚠️ Помилка генерації текста ШІ: {e}")
        
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
