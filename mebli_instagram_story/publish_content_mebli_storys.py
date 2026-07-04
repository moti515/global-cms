import os
import sys
import json
import time
import requests
import subprocess
import re  # 💡 Додано для безпечної фільтрації імен
from datetime import datetime
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image

# Імпорт модулів конфігурації та сервісів
import config_meb_insta_story as config
from media_processor_meb_instagram_story import *
from services_manager_meb_instagram_story import *

def sanitize_filename(filename):
    """
    Замінює кирилицю, пробіли та спецсимволи на дефіси, 
    зберігаючи розширення, щоб уникнути збоїв у FFmpeg/PIL.
    """
    name, ext = os.path.splitext(filename)
    # Замінюємо все, що НЕ є латиницею, цифрою, дефісом чи підкресленням, на дефіс
    sanitized_name = re.sub(r'[^a-zA-Z0-9_\-]', '-', name)
    # Прибираємо подвійні дефіси, якщо त्यांनी утворилися
    sanitized_name = re.sub(r'-+', '-', sanitized_name).strip('-')
    
    # Фолбек, якщо ім'я повністю складалося з кирилиці і стало пустим
    if not sanitized_name:
        sanitized_name = f"media_{int(time.time())}"
        
    return f"{sanitized_name}{ext.lower()}"

def main():
    # 0. Перевірка вхідних параметрів воркфлоу
    if len(sys.argv) < 3:
        print("💡 Запуск: python publish_content_mebli_storys.py ig_story <tab_name>")
        sys.exit(1)

    mode = sys.argv[1].lower()
    forced_tab = sys.argv[2]
    current_tab = forced_tab if forced_tab else config.TAB_NAME
    
    if mode != "ig_story":
        print(f"❌ Цей скрипт сконструйовано виключно під 'ig_story'. Передано: {mode}")
        sys.exit(1)

    # Отримуємо токени з системних змінних
    ig_user_id = os.environ.get("IG_USER_ID")
    meta_access_token = os.environ.get("META_ACCESS_TOKEN")

    # 1. Ініціалізація сервісів та локальних папок
    drive, sheets = get_services()
    os.makedirs('temp_mebli', exist_ok=True)
    
    selected_queue = []
    has_global_failures = False  # 🚩 Головний індикатор помилок для GitHub Actions
    
    # --- ВАРІАНТ 1: ПЕРЕВІРКА ГАРЯЧОЇ ПАПКИ ---
    print(f"🔍 Перевірка наявності файлів у гарячій папці [{config.HOT_FOLDER_ID}]...")
    try:
        hot_query = f"'{config.HOT_FOLDER_ID}' in parents and trashed = false"
        hot_res = drive.files().list(
            q=hot_query,
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size)",
            orderBy="createdTime",
            pageSize=50
        ).execute()
        hot_files = hot_res.get('files', [])
    except Exception as e:
        print(f"❌ ПОМИЛКА під час отримання списку файлів з Google Диску: {e}")
        hot_files = []
        has_global_failures = True

    if hot_files:
        print(f"🔥 Знайдено файли в гарячій папці ({len(hot_files)}). Працює Сценарій 1.")
        hot_group_items = []
        
        for f in hot_files:
            f_id, f_name = f['id'], f['name']
            lower_name = f_name.lower()
            
            if not lower_name.endswith(config.VALID_MEDIA_EXTENSIONS):
                print(f"⚠️ Файл [{f_name}] має непідтримуваний формат для Сторіс. Пропускаємо.")
                continue
            
            # 🔧 ЛОГ: Відстежуємо формування префіксу з Гарячої Папки
            print(f"🔧 [ЛОГ] Джерело: Гаряча папка | Оригінальний ID: {f_id} -> Сформований префікс: {f_id[:8]} | Назва файлу: {f_name}")
            
            # 🌟 ЗАХИСТ: Очищаємо назву файлу для локального збереження
            safe_local_name = sanitize_filename(f"{f_id[:8]}_{f_name}")
            local_path = os.path.join('temp_mebli', safe_local_name)
            print(f"📥 Попереднє завантаження для аналізу метаданих: {f_name} -> {safe_local_name}...")
            try:
                request = drive.files().get_media(fileId=f_id)
                with open(local_path, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
            except Exception as e:
                print(f"❌ Не вдалося завантажити {f_name} для аналізу: {e}")
                has_global_failures = True
                continue
            
            # Визначення інтелектуальної дати та локації
            try:
                final_date, lat, lon = get_intellectual_date(local_path, f_name, f)
                date_str = final_date.strftime('%d.%m.%Y') if hasattr(final_date, 'strftime') else str(final_date)
                # Повертає локацію мовою країни, де знято медіафайл
                display_location, group_location = get_location_data(lat, lon)
            except Exception as e:
                print(f"⚠️ Помилка автоматичного визначення дати/локації для {f_name}: {e}")
                date_str = datetime.now().strftime('%d.%m.%Y')
                display_location, group_location = "", ""

            # Визначення компанії за назвою файлу (шукаємо в оригінальній назві)
            detected_company = "Загальне"
            for key in config.COMPANIES_DB.keys():
                if key in lower_name:
                    detected_company = key
                    break
            
            hot_group_items.append({
                "id": f_id,
                "name": f_name,
                "safe_local_name": safe_local_name, # зберігаємо безпечне ім'я
                "local_path": local_path,
                "category": detected_company,
                "date": date_str,
                "location": display_location,      
                "group_location": group_location,  
                "mode": "hot_folder",
                "counter_cell": None
            })

        if hot_group_items:
            # Групування за датою та локацією
            groups = {}
            for item in hot_group_items:
                g_key = (item["date"], item["group_location"])
                groups.setdefault(g_key, []).append(item)
            
            # Беремо першу сформовану групу (до 4-х елементів)
            first_key = list(groups.keys())[0]
            selected_queue = groups[first_key][:4]
            print(f"📂 [Гаряча Папка] Сформовано чергу: Дата={first_key[0]}, Локація={first_key[1]}. Елементів: {len(selected_queue)}")
            
            # Видаляємо локальні копії файлів, які не потрапили в поточную чергу
            selected_ids = {x["id"] for x in selected_queue}
            for item in hot_group_items:
                if item["id"] not in selected_ids and os.path.exists(item["local_path"]):
                    os.remove(item["local_path"])

    # --- ВАРІАНТ 2: ФОЛБЕК НА РЕЄСТР ТАБЛИЦІ ---
    else:
        print(f"📊 Гаряча папка порожня. Активуємо Сценарій 2 (Реєстр таблиці '{current_tab}')...")
        try:
            res = sheets.spreadsheets().values().get(spreadsheetId=config.SPREADSHEET_ID, range=f"'{current_tab}'!A2:I").execute()
            rows = res.get('values', [])
        except Exception as e:
            print(f"❌ Помилка доступу до Google Sheets: {e}")
            sys.exit(1)

        if not rows:
            print("ℹ️ Реєстр порожній. Публікувати нічого.")
            return

        col_idx = 4  # Стовпець E (лічильник)
        col_letter = "E"
        valid_rows = []

        for i, r in enumerate(rows):
            if len(r) >= 3:  
                if r[2].lower() == "temporary": 
                    continue
                try:
                    val = r[col_idx] if len(r) > col_idx and r[col_idx] else "0"
                    counter = int(val)
                    valid_rows.append({"row_idx": i + 2, "data": r, "counter": counter})
                except ValueError: 
                    continue

        if not valid_rows:
            print("ℹ️ Немає доступних рядків для публікації.")
            return

        # Шукаємо мінімальне значення лічильника запуску
        min_counter = min(item["counter"] for item in valid_rows)
        min_pool = [item for item in valid_rows if item["counter"] == min_counter]

        # Групуємо рядки за Категорією, Датою та МІСТОМ (стовпець I, JSON)
        groups = {}
        for item in min_pool:
            data = item["data"]
            group_key = (data[2], data[6] if len(data) > 6 else "", data[8] if len(data) > 8 else "")
            groups.setdefault(group_key, []).append(item)

        # Формуємо чергу з першої групи
        first_key = list(groups.keys())[0]
        selected_group_items = groups[first_key][:4]
        category_name, target_date, target_city_json = first_key
        print(f"📂 Обрано групу з Таблиці: [{category_name}]. Елементів у черзі: {len(selected_group_items)}")
        
        for item in selected_group_items:
            data = item["data"]
            selected_queue.append({
                "id": data[0],
                "name": data[1],
                "local_path": None,
                "category": category_name,
                "date": target_date,
                "location": target_city_json,  
                "exact_location": data[7] if len(data) > 7 else "",  
                "mode": "sheet",
                "counter_cell": f"'{current_tab}'!{col_letter}{item['row_idx']}",
                "counter_val": item["counter"]
            })

    if not selected_queue:
        print("ℹ️ Черга порожня. Публікувати нічого.")
        return

    # --- НАЛАШТУВАННЯ МОВИ ПУБЛІКАЦІЇ ---
    target_lang_cell = "'⚙️ Налаштування Папок'!H2"
    lang_value = "UK"
    try:
        lang_res = sheets.spreadsheets().values().get(spreadsheetId=config.SPREADSHEET_ID, range=target_lang_cell).execute()
        lang_values = lang_res.get('values', [])
        if lang_values and lang_values[0]:
            lang_value = lang_values[0][0].strip().upper()
    except Exception as e:
        print(f"⚠️ Не вдалося зчитати мову з комірки H2: {e}")

    # Ротація мовного циклу
    if any(x in lang_value for x in ["EN", "ENG", "АНГЛ", "ENGLISH"]):
        lang_idx = 1
        next_lang_value = "DE"
    elif any(x in lang_value for x in ["DE", "GER", "НІМ", "DEUTSCH"]):
        lang_idx = 2
        next_lang_value = "UK"
    else:
        lang_idx = 0
        next_lang_value = "EN"
        
    print(f"🌐 Поточна мова Сторіс: {lang_value} (Індекс: {lang_idx}). Наступна буде: {next_lang_value}")
    
    local_files_to_clean = []
    success_published_any = False

    # --- ЗАГАЛЬНИЙ БЛОК ОБРОБКИ ТА ПУБЛІКАЦІЇ ---
    for idx_item, item in enumerate(selected_queue):
        f_id, f_name = item["id"], item["name"]
        lower_name = f_name.lower()
        
        if item["mode"] == "sheet":
            if not lower_name.endswith(config.VALID_MEDIA_EXTENSIONS):
                log_unsupported_to_service(sheets, item["category"], f_name, reason="непідтримуваний формат для сторіз")
                continue

            # 🔧 ЛОГ: Відстежуємо формування префіксу з Google Таблиці
            print(f"🔧 [ЛОГ] Джерело: Реєстр Таблиці | Оригінальний ID з рядка: {f_id} -> Сформований префікс: {f_id[:8]} | Назва файлу: {f_name}")

            # 🌟 ЗАХИСТ: Очищаємо назву файлу з таблиці перед збереженням на диск
            safe_local_name = sanitize_filename(f"{f_id[:8]}_{f_name}")
            local_path = os.path.join('temp_mebli', safe_local_name)
            
            print(f"\n📥 [{idx_item + 1}/{len(selected_queue)}] Завантаження з Drive: {f_name} -> {safe_local_name}...")
            try:
                request = drive.files().get_media(fileId=f_id)
                with open(local_path, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done: 
                        _, done = downloader.next_chunk()
            except Exception as e:
                print(f"❌ Не вдалося завантажити {f_name}: {e}")
                has_global_failures = True
                continue
        else:
            local_path = item["local_path"]
            safe_local_name = item["safe_local_name"]
            print(f"\n🎬 [{idx_item + 1}/{len(selected_queue)}] Обробка файлу з гарячої папки: {safe_local_name}...")

        final_path = local_path
        # Перевірку розширення робимо по safe_local_name, воно гарантовано приведене до нижнього регістру в sanitize_filename
        is_video = safe_local_name.endswith(('.mp4', '.mov', '.avi'))
        
        # Обробка HEIC
        if safe_local_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join('temp_mebli', safe_local_name.rsplit('.', 1)[0] + '.jpg')
            try:
                with Image.open(local_path) as img:
                    img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
                final_path = jpg_path
                local_files_to_clean.append(jpg_path)
            except Exception as e:
                print(f"❌ Помилка конвертації HEIC для {safe_local_name}: {e}")
                has_global_failures = True
                continue

        local_files_to_clean.append(local_path)

        # Створення стоп-кадру для відео аналізу Gemini
        ai_media_snapshot = final_path
        if is_video:
            frame_path = os.path.join('temp_mebli', f"frame_{f_id}.jpg")
            subprocess.run(['ffmpeg', '-y', '-i', final_path, '-ss', '00:00:01', '-vframes', '1', frame_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(frame_path):
                ai_media_snapshot = frame_path
                local_files_to_clean.append(frame_path)

        # 1. ШІ Генерація підпису
        story_caption_text = generate_story_caption([ai_media_snapshot], item["category"], item["date"], lang_idx, item["location"])
        print(f"💬 Сгенерований текст: \"{story_caption_text}\"")

        # Парсинг року
        try:
            year_variable = item["date"].split(".")[2] if item["date"] and len(item["date"].split(".")) == 3 else str(datetime.now().year)
        except Exception:
            year_variable = str(datetime.now().year)
            
        # 🌟 БЕЗПЕЧНИЙ ПАРСИНГ ЛОКАЦІЇ
        try:
            loc_json = json.loads(item["location"])
            location_variable = loc_json.get(str(lang_idx), loc_json.get("0", ""))
        except Exception:
            location_variable = item["location"]

        # 2-3. Оптимізація та накладання тексту на photo/відео (передаємо очищений safe_local_name)
        media_parts_to_upload = []
        try:
            if is_video:
                media_parts_to_upload = optimize_video_story(final_path, safe_local_name, story_caption_text, year=year_variable, location=location_variable)
            else:
                optimized_path = optimize_image_story(final_path, safe_local_name)
                overlay_text_on_image(optimized_path, story_caption_text, year=year_variable, location=location_variable)
                media_parts_to_upload = [optimized_path]
        except Exception as e:
            print(f"❌ Помилка рендерингу/оптимізації файлу {safe_local_name}: {e}")
            has_global_failures = True
            continue

        item_published_successfully = False
        all_parts_successful = True  

        # Завантаження та публікація у Meta API
        for sub_idx, active_path in enumerate(media_parts_to_upload):
            if len(media_parts_to_upload) > 1:
                print(f"📦 Обробка фрагмента [{sub_idx + 1}/{len(media_parts_to_upload)}] для файлу {safe_local_name}...")
                
            if active_path != final_path and active_path != local_path:
                local_files_to_clean.append(active_path)

            pub_url, ik_id = get_google_drive_direct_url(f_id, local_file_path=active_path)
            
            if not pub_url:
                print(f"⚠️ Не вдалося отримати публічне посилання для фрагмента {active_path}.")
                has_global_failures = True
                all_parts_successful = False
                continue

            print(f"📡 Надсилання сторіз в Meta API...")
            param_type = "video_url" if is_video else "image_url"
            payload = {
                "media_type": "STORIES",
                "param_type": pub_url,
                "access_token": meta_access_token
            }
            
            try:
                res = requests.post(f"https://graph.facebook.com/v19.0/{ig_user_id}/media", data=payload).json()
                if res and "id" in res:
                    creation_id = res["id"]
                    is_ready = wait_for_meta_container(creation_id, meta_access_token)
                    
                    if is_ready:
                        publish_res = requests.post(f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish", data={
                            "creation_id": creation_id, "access_token": meta_access_token
                        }).json()
                        
                        if "id" in publish_res:
                            print(f"✅ Фрагмент [{sub_idx + 1}/{len(media_parts_to_upload)}] успішно опубліковано! ID: {publish_res['id']}")
                            success_published_any = True
                        else:
                            print(f"❌ Помилка публікації сторіз в Meta API: {publish_res}")
                            has_global_failures = True
                            all_parts_successful = False
                    else:
                        print(f"❌ Контейнер медіафайлу не перейшов у стан готовності (Таймаут).")
                        has_global_failures = True
                        all_parts_successful = False
                else:
                    print(f"❌ Помилка створення контейнера сторіз: {res}")
                    has_global_failures = True
                    all_parts_successful = False
            except Exception as e:
                print(f"❌ Критичний збій під час запиту до Meta API: {e}")
                has_global_failures = True
                all_parts_successful = False

            if ik_id: 
                delete_from_imagekit(ik_id)

        if all_parts_successful and media_parts_to_upload:
            item_published_successfully = True
        else:
            print(f"⚠️ Файл [{safe_local_name}] опубліковано не повністю. Він залишається у черзі.")

        # --- ФІНАЛІЗАЦІЯ СТАТУСІВ ЕЛЕМЕНТІВ ---
        if item_published_successfully:
            if item["mode"] == "sheet":
                new_val = item["counter_val"] + 1
                try:
                    sheets.spreadsheets().values().update(
                        spreadsheetId=config.SPREADSHEET_ID, range=item["counter_cell"],
                        valueInputOption='RAW', body={'values': [[new_val]]}
                    ).execute()
                    print(f"✍️ Лічильник у {item['counter_cell']} оновлено на {new_val}.")
                except Exception as e:
                    print(f"⚠️ Не вдалося зберегти лічильник: {e}")
            
            elif item["mode"] == "hot_folder":
                try:
                    file_meta = drive.files().get(fileId=f_id, fields='parents').execute()
                    previous_parents = ",".join(file_meta.get('parents', []))
                    drive.files().update(
                        fileId=f_id,
                        addParents=config.TRASH_FOLDER_ID,
                        removeParents=previous_parents,
                        fields='id, parents'
                    ).execute()
                    print(f"🗑️ Файл [{f_name}] успішно переміщено до кошика на Google Диску.")
                except Exception as e:
                    print(f"⚠️ Не вдалося перемістити файл {f_name} до кошика: {e}")

    # Оновлення мовної комірки H2
    if success_published_any:
        try:
            sheets.spreadsheets().values().update(
                spreadsheetId=config.SPREADSHEET_ID, range=target_lang_cell,
                valueInputOption='RAW', body={'values': [[next_lang_value]]}
            ).execute()
            print(f"\n🔄 Мову для наступного запуска Сторіс (H2) змінено на: {next_lang_value}")
        except Exception as e:
            print(f"⚠️ Не вдалося оновити мову в комірці H2: {e}")

    # Очищення кешу
    for f in local_files_to_clean:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
    print("🧹 Тимчасові локальні файли успішно очищені.")

    if has_global_failures:
        print("\n💥 [Система] Скрипт виконав частину роботи, але зафіксовано помилки.")
        sys.exit(1)  
    else:
        print("\n🚀 [Система] Зовнішній запуск API успішний! Всі обрані файли опубліковані.")

if __name__ == "__main__":
    main()
