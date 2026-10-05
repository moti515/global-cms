import os
import sys
import subprocess
import re
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

TEMP_DIR = 'temp_mebli'

def download_drive_file(drive_service, file_id: str, local_path: str) -> bool:
    """Завантажує файл з Google Диску у вказаний локальний шлях."""
    try:
        request = drive_service.files().get_media(fileId=file_id)
        with open(local_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return True
    except Exception as e:
        print(f"❌ Не вдалося завантажити файл [ID: {file_id}] з Drive: {e}")
        return False

def extract_metadata_from_drive_object(f: dict) -> tuple[str, float | None, float | None]:
    """
    Витягує дату та GPS-координати з метаданих Drive API та назви файлу 
    БЕЗ завантаження самого файлу на диск.
    """
    f_name = f.get('name', '')
    final_date = None

    # 1. Пошук дати у назві файлу (наприклад: 2024-05-20, 20.05.2024, 2024_05_20)
    date_match = re.search(r'(\d{4})[-_.](\d{2})[-_.](\d{2})', f_name)
    if date_match:
        try:
            final_date = datetime(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
        except ValueError:
            pass

    if not final_date:
        date_match_rev = re.search(r'(\d{2})[-_.](\d{2})[-_.](\d{4})', f_name)
        if date_match_rev:
            try:
                final_date = datetime(int(date_match_rev.group(3)), int(date_match_rev.group(2)), int(date_match_rev.group(1)))
            except ValueError:
                pass

    # 2. Пошук EXIF метаданих у Google Drive API (без скачування)
    lat, lon = None, None
    img_meta = f.get('imageMediaMetadata') or f.get('videoMediaMetadata') or {}
    if img_meta:
        if not final_date:
            time_str = img_meta.get('time')  # Формат: "2024:05:20 14:30:00"
            if time_str:
                try:
                    final_date = datetime.strptime(time_str[:19].replace('T', ' '), '%Y:%m:%d %H:%M:%S')
                except ValueError:
                    try:
                        final_date = datetime.strptime(time_str[:19], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        pass

        location_data = img_meta.get('location', {})
        if location_data:
            lat = location_data.get('latitude')
            lon = location_data.get('longitude')

    # 3. Якщо дату не знайдено в назві та EXIF — беремо createdTime з Google Drive
    if not final_date:
        created_str = f.get('createdTime', '')
        if created_str:
            try:
                final_date = datetime.strptime(created_str[:19], '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                final_date = datetime.now()
        else:
            final_date = datetime.now()

    date_str = final_date.strftime('%d.%m.%Y')
    return date_str, lat, lon

def get_hot_folder_queue(drive_service) -> tuple[list, bool]:
    """
    Отримує до 100 файлів з гарячої папки, аналізує метадані в пам'яті (без завантаження),
    групує за (дата, локація), обирає першу групу (до 4 файлів) і завантажує ТІЛЬКИ їх.
    """
    current_hour = datetime.now().hour
    print(f"🕒 Серверний час: {current_hour}:00 (UTC)")

    if current_hour >= 16:
        order_by_param = "createdTime desc"
        print("✨ [Стратегія: ВЕЧІР] Отримуємо 100 найновіших матеріалів (Нові -> Старі).")
    else:
        order_by_param = "createdTime"
        print("📦 [Стратегія: РАНОК/ДЕНЬ] Отримуємо 100 найстаріших матеріалів з архіву (Старі -> Нові).")

    try:
        hot_query = f"'{config.HOT_FOLDER_ID}' in parents and trashed = false"
        hot_res = drive_service.files().list(
            q=hot_query,
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size, imageMediaMetadata, videoMediaMetadata)",
            orderBy=order_by_param,
            pageSize=100
        ).execute()
        hot_files = hot_res.get('files', [])
    except Exception as e:
        print(f"❌ ПОМИЛКА під час отримання списку файлів з Google Диску: {e}")
        return [], True

    if not hot_files:
        return [], False

    print(f"🔥 Знайдено {len(hot_files)} файлів у гарячій папці. Легкий аналіз метаданих (без завантаження)...")
    hot_group_items = []

    for f in hot_files:
        f_id, f_name = f['id'], f['name']
        lower_name = f_name.lower()

        if not lower_name.endswith(config.VALID_MEDIA_EXTENSIONS):
            print(f"⚠️ Файл [{f_name}] має непідтримуваний формат для Сторіс. Пропускаємо.")
            continue

        # Зчитуємо метадані без завантаження самого файлу
        try:
            date_str, lat, lon = extract_metadata_from_drive_object(f)
            display_location, group_location = get_location_data(lat, lon)
        except Exception as e:
            print(f"⚠️ Помилка визначення метаданих для {f_name}: {e}")
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
            "category": detected_company,
            "date": date_str,
            "location": display_location,
            "group_location": group_location,
            "mode": "hot_folder",
            "counter_cell": None
        })

    if not hot_group_items:
        return [], False

    # Групуємо файли за (дата, група_локації) із збереженням порядку
    groups = {}
    for item in hot_group_items:
        g_key = (item["date"], item["group_location"])
        groups.setdefault(g_key, []).append(item)

    # Обираємо першу групу за порядком
    first_key = list(groups.keys())[0]
    selected_unloaded = groups[first_key][:4]  # Беремо від 1 до 4 файлів першої групи

    print(f"📂 [Гаряча Папка] Сформовано чергу: Дата={first_key[0]}, Локація='{first_key[1]}'. Елементів для завантаження: {len(selected_unloaded)}")

    # ЗАВАНТАЖУЄМО ТІЛЬКИ ФАЙЛИ ОБРАНОЇ ПЕРШОЇ ГРУПИ (до 4 шт.)
    selected_queue = []
    for item in selected_unloaded:
        f_id, f_name = item["id"], item["name"]
        safe_local_name = sanitize_filename(f"{f_id}_{f_name}")
        local_path = os.path.join(TEMP_DIR, safe_local_name)

        print(f"📥 Завантажуємо для публікації: {f_name} -> {safe_local_name}...")
        if download_drive_file(drive_service, f_id, local_path):
            item["safe_local_name"] = safe_local_name
            item["local_path"] = local_path
            selected_queue.append(item)
        else:
            print(f"❌ Не вдалося завантажити файл [ID: {f_id}] з Drive.")

    return selected_queue, False

def get_sheet_queue(sheets_service, current_tab: str) -> list:
    """Формує чергу публікації на основі реєстру Google Таблиці."""
    print(f"📊 Гаряча папка порожня. Активуємо Сценарій 2 (Реєстр таблиці '{current_tab}')...")
    try:
        res = sheets_service.spreadsheets().values().get(
            spreadsheetId=config.SPREADSHEET_ID, range=f"'{current_tab}'!A2:I"
        ).execute()
        rows = res.get('values', [])
    except Exception as e:
        print(f"❌ Помилка доступу до Google Sheets: {e}")
        sys.exit(1)

    if not rows:
        print("ℹ️ Реєстр порожній. Публікувати нічого.")
        return []

    col_idx, col_letter = 4, "E"
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
        print("ℹ️️ Немає доступних рядків для публікації.")
        return []

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

    selected_queue = []
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

    return selected_queue

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

    # 1. Ініціалізація сервісів та каталогу
    drive, sheets = get_services()
    os.makedirs(TEMP_DIR, exist_ok=True)

    has_global_failures = False

    # --- ОДЕРЖАННЯ ЧЕРГИ (СЦЕНАРІЙ 1 або 2) ---
    print(f"🔍 Перевірка наявності файлів у гарячій папці [{config.HOT_FOLDER_ID}]...")
    selected_queue, hot_folder_error = get_hot_folder_queue(drive)
    if hot_folder_error:
        has_global_failures = True

    if not selected_queue:
        selected_queue = get_sheet_queue(sheets, current_tab)

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

    lang_idx, next_lang_value = rotate_language(lang_value)
    print(f"🌐 Поточна мова Сторіс: {lang_value} (Індекс: {lang_idx}). Наступна буде: {next_lang_value}")

    local_files_to_clean = []
    success_published_any = False
    previous_captions = []

    # --- ЗАГАЛЬНИЙ БЛОК ОБРОБКИ ТА ПУБЛІКАЦІЇ ---
    for idx_item, item in enumerate(selected_queue):
        f_id, f_name = item["id"], item["name"]
        lower_name = f_name.lower()

        if item["mode"] == "sheet":
            if not lower_name.endswith(config.VALID_MEDIA_EXTENSIONS):
                log_unsupported_to_service(sheets, item["category"], f_name, reason="непідтримуваний формат для сторіз")
                continue

            print(f"🔧 [ЛОГ] Джерело: Реєстр Таблиці | ID: {f_id} | Назва: {f_name}")
            safe_local_name = sanitize_filename(f"{f_id}_{f_name}")
            local_path = os.path.join(TEMP_DIR, safe_local_name)

            print(f"\n📥 [{idx_item + 1}/{len(selected_queue)}] Завантаження з Drive: {f_name} -> {safe_local_name}...")
            if not download_drive_file(drive, f_id, local_path):
                has_global_failures = True
                continue
        else:
            local_path = item["local_path"]
            safe_local_name = item["safe_local_name"]
            print(f"\n🎬 [{idx_item + 1}/{len(selected_queue)}] Обробка з гарячої папки: {safe_local_name}...")

        final_path = local_path
        is_video = safe_local_name.endswith(('.mp4', '.mov', '.avi'))

        # Обробка HEIC
        if safe_local_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join(TEMP_DIR, safe_local_name.rsplit('.', 1)[0] + '.jpg')
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

        # Створення стоп-кадру відео для аналізу Gemini
        ai_media_snapshot = final_path
        if is_video:
            frame_path = os.path.join(TEMP_DIR, f"frame_{f_id}.jpg")
            subprocess.run(
                ['ffmpeg', '-y', '-i', final_path, '-ss', '00:00:01', '-vframes', '1', frame_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if os.path.exists(frame_path):
                ai_media_snapshot = frame_path
                local_files_to_clean.append(frame_path)

        # 1. ШІ Генерація підпису
        story_caption_text = generate_story_caption(
            [ai_media_snapshot],
            item["category"],
            item["date"],
            lang_idx,
            item["location"],
            previous_captions=previous_captions
        )
        print(f"💬 Сгенерований текст: \"{story_caption_text}\"")

        if story_caption_text:
            previous_captions.append(story_caption_text)

        year_variable = parse_year(item["date"])
        location_variable = parse_location(item["location"], lang_idx)

        # 2. Оптимізація та рендеринг
        media_parts_to_upload = []
        try:
            if is_video:
                media_parts_to_upload = optimize_video_story(final_path, safe_local_name, story_caption_text, year=year_variable, location=location_variable)
            else:
                optimized_path = optimize_image_story(final_path, safe_local_name)
                overlay_text_on_image(optimized_path, story_caption_text, year=year_variable, location=location_variable)
                media_parts_to_upload = [optimized_path]
        except Exception as e:
            print(f"❌ Помилка рендерингу/оптимізації {safe_local_name}: {e}")
            has_global_failures = True
            continue

        item_published_successfully = False
        all_parts_successful = True

        # 3. Завантаження та публікація у Meta API
        for sub_idx, active_path in enumerate(media_parts_to_upload):
            if len(media_parts_to_upload) > 1:
                print(f"📦 Обробка фрагмента [{sub_idx + 1}/{len(media_parts_to_upload)}] для {safe_local_name}...")

            if active_path not in (final_path, local_path):
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
            print(f"⚠️ Файл [{safe_local_name}] опубліковано не повністю.")

        # --- ФІНАЛІЗАЦІЯ СТАТУСІВ ---
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
                    print(f"🗑️ Файл [{f_name}] переміщено до кошика на Google Диску.")
                except Exception as e:
                    print(f"⚠️ Не вдалося перемістити {f_name} до кошика: {e}")

    # Оновлення мови H2 при успішній публікації
    if success_published_any:
        try:
            sheets.spreadsheets().values().update(
                spreadsheetId=config.SPREADSHEET_ID, range=target_lang_cell,
                valueInputOption='RAW', body={'values': [[next_lang_value]]}
            ).execute()
            print(f"\n🔄 Мову для наступного запуску Сторіс (H2) змінено на: {next_lang_value}")
        except Exception as e:
            print(f"⚠️ Не вдалося оновити мову в H2: {e}")

    # Очищення кешу
    for f in set(local_files_to_clean):
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass
    print("🧹 Тимчасові локальні файли успішно очищені.")

    if has_global_failures:
        print("\n💥 [Система] Виконано з окремими помилками.")
        sys.exit(1)
    else:
        print("\n🚀 [Система] Всі обрані файли успішно опубліковані!")

if __name__ == "__main__":
    main()
