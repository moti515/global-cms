"""
========================================================================================
📦 МОДУЛЬНА АРХІТЕКТУРА ПРОЄКТУ АВТОПУБЛІКАЦІЇ INSTAGRAM STORIES (mebli_instagram_story)
========================================================================================

1. publish_content_mebli_storys.py (Цей модуль)
   - Головний оркестратор конвеєра публікації.
   - Сценарій 1: Отримує 50 кандидатів з Гарячої папки Google Drive, здійснює аналіз EXIF/GPS,
     групує по датах і містах, публікує перші 4 файли з 1-ї групи та переміщує в Кошик.
   - Сценарій 2 (Фолбек): Якщо Гаряча папка порожня, читає реєстр з вкладки Google Таблиці,
     вибирає найменш опубліковані елементи, публікує їх та оновлює лічильник.
   - Автоматично керує ротацією мови через комірку '⚙️ Налаштування Папок'!H2.

2. config_meb_insta_story.py
   - Централізований конфігураційний файл проєкту (ID папок, ключі, бази брендів).

3. media_processor_meb_instagram_story.py
   - Спеціалізований модуль обробки фото, відео (1080x1920), EXIF, геолокації (OSM) та оверлеїв.

4. services_manager_meb_instagram_story.py
   - Менеджер зовнішніх API (Google Auth, Gemini API, Litterbox/ImageKit, Meta Graph API).

5. utils_meb_instagram_story.py
   - Допоміжні утиліти (очищення temp-папок, санітаризація імен, ротація мов, публікація в Meta).
========================================================================================
"""

import os
import sys
from datetime import datetime
from collections import defaultdict

# Централізований конфіг проекту
import config_meb_insta_story as config

# 🛠 Імпорт спеціалізованих модулів
try:
    from services_manager_meb_instagram_story import (
        get_services,
        log_unsupported_to_service,
        get_google_drive_direct_url,
        delete_from_imagekit,
        generate_story_caption,
    )
    from media_processor_meb_instagram_story import (
        optimize_image_story,
        overlay_text_on_image,
        optimize_video_story,
        get_location_data,
        get_intellectual_date,
    )
    from utils_meb_instagram_story import (
        sanitize_filename,
        publish_story_to_meta,
        cleanup_temp_dir,
        rotate_language,
        get_saved_language,
        update_saved_language,
        parse_year,
        parse_location,
    )
except ImportError:
    from mebli_instagram_story.services_manager_meb_instagram_story import (
        get_services,
        log_unsupported_to_service,
        get_google_drive_direct_url,
        delete_from_imagekit,
        generate_story_caption,
    )
    from mebli_instagram_story.media_processor_meb_instagram_story import (
        optimize_image_story,
        overlay_text_on_image,
        optimize_video_story,
        get_location_data,
        get_intellectual_date,
    )
    from mebli_instagram_story.utils_meb_instagram_story import (
        sanitize_filename,
        publish_story_to_meta,
        cleanup_temp_dir,
        rotate_language,
        get_saved_language,
        update_saved_language,
        parse_year,
        parse_location,
    )


def fetch_hot_folder_candidates(drive_service, hot_folder_id, limit=50):
    """
    Отримує список до `limit` файлів з Гарячої папки Google Drive.
    Логіка сортування:
    - До 20:00 -> Найстаріші файли першими (createdTime asc)
    - Після 20:00 -> Найновіші файли першими (createdTime desc)
    """
    now_hour = datetime.now().hour
    is_evening = now_hour >= 20
    order_by = 'createdTime desc' if is_evening else 'createdTime asc'
    
    print(f"🕒 Поточний час: {datetime.now().strftime('%H:%M')}. Режим вибірки: {'НАЙНОВІШІ (вечір)' if is_evening else 'НАЙСТАРІШІ (ранок/день)'}")

    query = f"'{hot_folder_id}' in parents and trashed = false"
    fields = "files(id, name, createdTime, modifiedTime, mimeType)"

    try:
        results = drive_service.files().list(
            q=query,
            orderBy=order_by,
            pageSize=limit,
            fields=fields
        ).execute()
        return results.get('files', [])
    except Exception as e:
        print(f"❌ Помилка отримання файлів з Гарячої папки: {e}")
        return []


def download_and_analyze_hot_files(drive_service, candidate_files):
    """
    Завантажує кандидати з Гарячої папки та аналізує їх EXIF, дату зйомки та геолокацію.
    """
    os.makedirs('temp_mebli', exist_ok=True)
    analyzed_items = []

    print(f"\n📥 Завантаження та глибокий аналіз метаданих {len(candidate_files)} файлів...")

    for idx, f_info in enumerate(candidate_files, 1):
        file_id = f_info['id']
        raw_name = f_info['name']
        
        if not raw_name.lower().endswith(config.VALID_MEDIA_EXTENSIONS):
            continue

        sanitized_name = sanitize_filename(f"{file_id}_{raw_name}")
        local_path = os.path.join('temp_mebli', sanitized_name)

        try:
            request = drive_service.files().get_media(fileId=file_id)
            with open(local_path, 'wb') as f:
                f.write(request.execute())

            dt, lat, lon = get_intellectual_date(local_path, raw_name, f_info)
            display_loc, group_loc = get_location_data(lat, lon)

            date_str = dt.strftime('%d.%m.%Y') if hasattr(dt, 'strftime') else str(dt)
            date_key = dt.strftime('%Y-%m-%d') if hasattr(dt, 'strftime') else str(dt)
            loc_key = group_loc if group_loc else "NO_GPS"

            analyzed_items.append({
                'id': file_id,
                'raw_name': raw_name,
                'sanitized_name': sanitized_name,
                'local_path': local_path,
                'datetime': dt,
                'date_str': date_str,
                'date_key': date_key,
                'location_key': loc_key,
                'display_loc': display_loc,
                'group_loc': group_loc,
                'mode': 'hot_folder',
                'counter_cell': None
            })
            print(f"  [{idx}/{len(candidate_files)}] {raw_name} -> Дата: {date_str} | Локація: {display_loc or 'без GPS'}")

        except Exception as err:
            print(f"  ⚠️ Помилка обробки файлу {raw_name}: {err}")

    return analyzed_items


def fetch_registry_candidates(drive_service, sheets_service, target_tab="Меблі"):
    """
    Сценарій 2 (Фолбек): Отримує чергу публікацій із реєстру Google Таблиці,
    коли Гаряча папка порожня.
    """
    print(f"📊 Гаряча папка порожня. Активуємо Сценарій 2 (Реєстр вкладки '{target_tab}')...")
    
    try:
        res = sheets_service.spreadsheets().values().get(
            spreadsheetId=config.SPREADSHEET_ID, 
            range=f"'{target_tab}'!A2:I"
        ).execute()
        rows = res.get('values', [])
    except Exception as e:
        print(f"❌ Помилка доступу до Google Таблиці: {e}")
        return []

    if not rows:
        print("ℹ️ Реєстр порожній. Публікувати нічого.")
        return []

    valid_rows = []
    col_idx = 4  # Стовпець E (лічильник)
    col_letter = "E"

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
        print("ℹ️ Немає доступних рядків для публікації в реєстрі.")
        return []

    # Беремо елементи з найменшою кількістю публікацій
    min_counter = min(item["counter"] for item in valid_rows)
    min_pool = [item for item in valid_rows if item["counter"] == min_counter]

    # Групуємо по категрії, даті та локації
    groups = {}
    for item in min_pool:
        data = item["data"]
        cat = data[2] if len(data) > 2 else target_tab
        target_date = data[6] if len(data) > 6 else ""
        target_city_json = data[8] if len(data) > 8 else ""
        group_key = (cat, target_date, target_city_json)
        groups.setdefault(group_key, []).append(item)

    first_key = list(groups.keys())[0]
    selected_group_items = groups[first_key][:4]
    category_name, target_date, target_city_json = first_key
    
    print(f"📂 Обрано групу з Реєстру: [{category_name}]. Елементів у черзі: {len(selected_group_items)}")

    os.makedirs('temp_mebli', exist_ok=True)
    registry_queue = []

    for item in selected_group_items:
        data = item["data"]
        file_id = data[0]
        raw_name = data[1]
        sanitized_name = sanitize_filename(f"{file_id}_{raw_name}")
        local_path = os.path.join('temp_mebli', sanitized_name)

        # Завантажуємо файл для обробки
        try:
            request = drive_service.files().get_media(fileId=file_id)
            with open(local_path, 'wb') as f:
                f.write(request.execute())
        except Exception as e:
            print(f"❌ Не вдалося завантажити файл з реєстру {raw_name}: {e}")
            continue

        registry_queue.append({
            'id': file_id,
            'raw_name': raw_name,
            'sanitized_name': sanitized_name,
            'local_path': local_path,
            'datetime': datetime.now(),
            'date_str': target_date if target_date else datetime.now().strftime('%d.%m.%Y'),
            'display_loc': target_city_json,
            'mode': 'sheet',
            'counter_cell': f"'{target_tab}'!{col_letter}{item['row_idx']}",
            'counter_val': item["counter"]
        })

    return registry_queue


def group_analyzed_files(analyzed_items):
    """
    Групує проаналізовані файли за датою зйомки та локацією.
    """
    grouped_dict = defaultdict(list)
    for item in analyzed_items:
        group_key = f"{item['date_key']}_{item['location_key']}"
        grouped_dict[group_key].append(item)
    return list(grouped_dict.values())


def move_file_to_trash_folder(drive_service, file_id, file_name, trash_folder_id):
    """
    Переміщує опублікований файл із Гарячої папки у Кошик на Google Диску.
    """
    try:
        file = drive_service.files().get(fileId=file_id, fields='parents').execute()
        previous_parents = ",".join(file.get('parents', []))
        
        drive_service.files().update(
            fileId=file_id,
            addParents=trash_folder_id,
            removeParents=previous_parents,
            fields='id, parents'
        ).execute()
        print(f"📂 Файл [{file_name}] успішно переміщено в папку Кошика ({trash_folder_id}).")
    except Exception as e:
        print(f"⚠ Не вдалося перемістити файл [{file_name}] у папку Кошика, видаляємо в системний trash: {e}")
        try:
            drive_service.files().update(fileId=file_id, body={'trashed': True}).execute()
        except Exception as tr_err:
            print(f"❌ Помилка видалення файлу: {tr_err}")


def publish_batch_group(drive_service, sheets_service, batch_items, lang_idx, target_tab="Меблі"):
    """
    Оптимізація медіа, генерація підпису Gemini та публікація серії в Instagram Stories.
    Повертає True, якщо хоча б одну сторіс успішно опубліковано.
    """
    access_token = os.environ.get("META_ACCESS_TOKEN") or config.META_ACCESS_TOKEN
    ig_user_id = os.environ.get("INSTAGRAM_ACCOUNT_ID") or config.IG_USER_ID

    if not access_token or not ig_user_id:
        print("⚠️ Відсутні ключі META_ACCESS_TOKEN або INSTAGRAM_ACCOUNT_ID.")
        return False

    previous_captions = []
    published_any = False

    print(f"\n🚀 РОЗПОЧИНАЄМО ПУБЛІКАЦІЮ СЕРІЇ З {len(batch_items)} СТОРІЗ...")
    print(f"🌐 Поточний індекс мови для цієї серії: {lang_idx}")

    for idx, item in enumerate(batch_items, 1):
        file_id = item['id']
        raw_file_name = item['raw_name']
        file_name = item['sanitized_name']
        local_path = item['local_path']
        date_str = item['date_str']
        display_loc = item['display_loc']
        dt = item['datetime']

        print(f"\n--------------------------------------------------")
        print(f"📸 [{idx}/{len(batch_items)}] Обробка {raw_file_name}...")

        # 1. Генерація підпису через Gemini API з поточним lang_idx
        caption_text = generate_story_caption(
            image_paths=[local_path],
            category=target_tab,
            date_str=date_str,
            lang_idx=lang_idx,
            target_loc=display_loc,
            previous_captions=previous_captions
        )
        print(f"💬 Згенерований текст [Мова {lang_idx}]: \"{caption_text}\"")
        previous_captions.append(caption_text)

        # 2. Форматування та оверлей
        lower_name = file_name.lower()
        is_video = lower_name.endswith(('.mp4', '.mov', '.avi'))
        year_val = parse_year(date_str)
        loc_val = parse_location(display_loc, lang_idx)
        
        if is_video:
            processed_files = optimize_video_story(local_path, file_name, caption_text, year=year_val, location=loc_val)
        else:
            padded_img_path = optimize_image_story(local_path, file_name)
            overlay_text_on_image(padded_img_path, caption_text, year=year_val, location=loc_val)
            processed_files = [padded_img_path]

        # 3. Публікація в Meta API
        for ready_file in processed_files:
            direct_url, imagekit_id = get_google_drive_direct_url(file_id, local_file_path=ready_file)
            
            if not direct_url:
                print(f"🚨 Не вдалося отримати URL для Meta API ({file_name}).")
                log_unsupported_to_service(sheets_service, target_tab, raw_file_name, "Помилка генерації URL")
                continue

            print(f"📡 Надсилання в Instagram Stories...")
            success, result_msg = publish_story_to_meta(
                ig_user_id=ig_user_id,
                meta_access_token=access_token,
                pub_url=direct_url,
                is_video=is_video
            )

            if success:
                print(f"✅ УСПІШНО ОПУБЛІКОВАНО! Story ID: {result_msg}")
                published_any = True
                
                # Залежно від сценарію: переміщуємо в Кошик або оновлюємо лічильник Таблиці
                if item.get('mode') == 'hot_folder':
                    move_file_to_trash_folder(drive_service, file_id, raw_file_name, config.TRASH_FOLDER_ID)
                elif item.get('mode') == 'sheet' and item.get('counter_cell'):
                    new_count = item['counter_val'] + 1
                    try:
                        sheets_service.spreadsheets().values().update(
                            spreadsheetId=config.SPREADSHEET_ID,
                            range=item['counter_cell'],
                            valueInputOption='RAW',
                            body={'values': [[new_count]]}
                        ).execute()
                        print(f"✍️ Лічильник реєстру {item['counter_cell']} оновлено на {new_count}.")
                    except Exception as sheet_err:
                        print(f"⚠️ Не вдалося оновити лічильник реєстру: {sheet_err}")

            else:
                print(f"❌ ПОМИЛКА ПУБЛІКАЦІЇ: {result_msg}")
                log_unsupported_to_service(sheets_service, target_tab, raw_file_name, f"Помилка Meta: {result_msg}")

            if imagekit_id:
                delete_from_imagekit(imagekit_id)

    return published_any


def run_story_publisher():
    """
    Головна точка входу.
    """
    forced_tab = sys.argv[2] if len(sys.argv) >= 3 else config.TAB_NAME
    current_tab = forced_tab if forced_tab else config.TAB_NAME

    print("🚀 ІНІЦІАЛІЗАЦІЯ МОДУЛЯ АВТОПУБЛІКАЦІЇ INSTAGRAM STORIES...")
    drive_s, sheets_s = get_services()

    # 0. Зчитуємо збережену мову з комірки H2
    saved_lang_code = get_saved_language(sheets_s)
    lang_idx, next_lang_code = rotate_language(saved_lang_code)
    print(f"🌐 Зчитана мова з H2: {saved_lang_code} -> Встановлено індекс: {lang_idx}. Наступна буде: {next_lang_code}")

    target_batch = []

    # 1. Спроба 1: Перевірка Гарячої папки (Сценарій 1)
    hot_candidates = fetch_hot_folder_candidates(drive_s, config.HOT_FOLDER_ID, limit=50)
    
    if hot_candidates:
        print(f"🔥 Знайдено {len(hot_candidates)} кандидатів у Гарячій папці.")
        analyzed_items = download_and_analyze_hot_files(drive_s, hot_candidates)
        if analyzed_items:
            grouped_batches = group_analyzed_files(analyzed_items)
            print(f"🧩 Сформовано {len(grouped_batches)} тематичних/часових груп з Гарячої папки.")
            target_batch = grouped_batches[0][:4]

    # 2. Спроба 2: Фолбек на реєстр Google Таблиці (Сценарій 2)
    if not target_batch:
        target_batch = fetch_registry_candidates(drive_s, sheets_s, target_tab=current_tab)

    if not target_batch:
        print("ℹ️ Немає доступних матеріалів для публікації. Завершення роботи.")
        cleanup_temp_dir("temp_mebli")
        return

    print(f"🎯 Обрано {len(target_batch)} файлів для публікації.")

    # 3. Публікуємо обрану серію
    success_published = publish_batch_group(
        drive_s, sheets_s, target_batch, lang_idx=lang_idx, target_tab=current_tab
    )

    # 4. Якщо публікація пройшла успішно — зберігаємо наступну мову в H2
    if success_published:
        update_saved_language(sheets_s, next_lang_code)

    # 5. Очищаємо тимчасові файли
    cleanup_temp_dir("temp_mebli")


if __name__ == "__main__":
    run_story_publisher()
