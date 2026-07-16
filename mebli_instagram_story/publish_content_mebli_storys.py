import os
import sys
from datetime import datetime
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image

# Імпорт конфігурацій та сервісів
import config_meb_insta_story as config
from media_processor_meb_instagram_story import *
from services_manager_meb_instagram_story import *
# Імпорт винесених утиліт
from utils_meb_instagram_story import (
    sanitize_filename, rotate_language, parse_year, parse_location, publish_story_to_meta
)

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

    ig_user_id = os.environ.get("IG_USER_ID")
    meta_access_token = os.environ.get("META_ACCESS_TOKEN")

    # 1. Ініціалізація сервісів та локальних папок
    drive, sheets = get_services()
    os.makedirs('temp_mebli', exist_ok=True)
    
    selected_queue = []
    has_global_failures = False  # 🚩 Головний індикатор помилок для GitHub Actions
    
    # --- ВАРІАНТ 1: ПЕРЕВІРКА ГАРЯЧОЇ ПАПКИ ---
    print(f"🔍 Перевірка наявності файлів у гарячій папці [{config.HOT_FOLDER_ID}]...")
    
    current_hour = datetime.now().hour
    print(f"🕒 Системна година на сервері (UTC): {current_hour}:00")

    if current_hour >= 16:  
        order_by_param = "createdTime desc"
        print("✨ [Стратегія: ВЕЧІР] Публікуємо СВІЖІ матеріали (Нові -> Старі).")
    else:
        order_by_param = "createdTime"
        print("📦 [Стратегія: РАНОК/ДЕНЬ] Розгрібаємо меблевий АРХІВ (Старі -> Нові).")
        
    try:
        hot_query = f"'{config.HOT_FOLDER_ID}' in parents and trashed = false"
        hot_res = drive.files().list(
            q=hot_query,
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size)",
            orderBy=order_by_param,
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
            
            print(f"🔧 [ЛОГ] Джерело: Гаряча папка | Унікальний ID: {f_id} | Назва файлу: {f_name}")
            
            safe_local_name = sanitize_filename(f"{f_id}_{f_name}")
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
            
            try:
                final_date, lat, lon = get_intellectual_date(local_path, f_name, f)
                date_str = final_date.strftime('%d.%m.%Y') if hasattr(final_date, 'strftime') else str(final_date)
                display_location, group_location = get_location_data(lat, lon)
            except Exception as e:
                print(f"⚠️ Помилка автоматичного визначення дати/локації для {f_name}: {e}")
                date_str = datetime.now().strftime('%d.%m.%Y')
                display_location, group_location = "", ""

            detected_company = "Загальне"
            for key in config.COMPANIES_DB.keys():
                if key in lower_name:
                    detected_company = key
                    break
            
            hot_group_items.append({
                "id": f_id,
                "name": f_name,
                "safe_local_name": safe_local_name, 
                "local_path": local_path,
                "category": detected_company,
                "date": date_str,
                "location": display_location,      
                "group_location": group_location,  
                "mode": "hot_folder",
                "counter_cell": None
            })

        if hot_group_items:
            groups = {}
            for item in hot_group_items:
                g_key = (item["date"], item["group_location"])
                groups.setdefault(g_key, []).append(item)
            
            first_key = list(groups.keys())[0]
            selected_queue = groups[first_key][:4]
            print(f"📂 [Гаряча Папка] Сформовано чергу: Дата={first_key[0]}, Локація={first_key[1]}. Елементів: {len(selected_queue)}")
            
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

        col_idx = 4  # Стовпець E
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

        min_counter = min(item["counter"] for item in valid_rows)
        min_pool = [item for item in valid_rows if item["counter"] == min_counter]

        groups = {}
        for item in min_pool:
            data = item["data"]
            group_key = (data[2], data[6] if len(data) > 6 else "", data[8] if len(data) > 8 else "")
            groups.setdefault(group_key, []).append(item)

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
            lang_value = lang_values[0][0]
    except Exception as e:
        print(f"⚠️ Не вдалося зчитати мову з комірки H2: {e}")

    # Ротація мовного циклу через хелпер
    lang_idx, next_lang_value = rotate_language(lang_value)
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

            print(f"🔧 [ЛОГ] Джерело: Реєстр Таблиці | Унікальний ID: {f_id} | Назва файлу: {f_name}")

            safe_local_name = sanitize_filename(f"{f_id}_{f_name}")
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

        # Використовуємо хелпери для парсингу параметрів
        year_variable = parse_year(item["date"])
        location_variable = parse_location(item["location"], lang_idx)

        # 2-3. Оптимізація та накладання тексту на фото/відео
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

        # Завантаження та публікація у Meta API через новий хелпер
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
            success_meta, result_meta = publish_story_to_meta(ig_user_id, meta_access_token, pub_url, is_video)
            
            if success_meta:
                print(f"✅ Фрагмент [{sub_idx + 1}/{len(media_parts_to_upload)}] успішно опубліковано! ID: {result_meta}")
                success_published_any = True
            else:
                print(f"❌ {result_meta}")
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
