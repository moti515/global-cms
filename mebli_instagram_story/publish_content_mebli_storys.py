"""
========================================================================================
 📦 МОДУЛЬНА АРХІТЕКТУРА ПРОЄКТУ АВТОПУБЛІКАЦІЇ INSTAGRAM STORIES (mebli_instagram_story)
========================================================================================

1. publish_content_mebli_storys.py (Цей модуль)
   - Головний оркестратор конвеєра публікації.
   - Завантажує 50 кандидатів з Google Drive, проводить глибокий аналіз EXIF/геолокації
     через media_processor, групує їх по датах і містах, публікує перші 4 файли з 1-ї групи
     в Instagram Stories та переміщує опубліковане в Кошик на Google Диску.

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
    )


def fetch_candidate_files(drive_service, hot_folder_id, limit=50):
    """
    Отримує список до `limit` файлів (за замовчуванням 50) з Google Drive.
    Логіка сортування:
    - До 21:00 -> Найстаріші файли першими (createdTime asc)
    - Після 21:00 -> Найновіші файли першими (createdTime desc)
    """
    now_hour = datetime.now().hour
    is_evening = now_hour >= 21
    order_by = 'createdTime desc' if is_evening else 'createdTime asc'
    
    print(f"🕒 Поточний час: {datetime.now().strftime('%H:%M')}. Режим вибірки: {'НАЙНОВІШІ (після 21:00)' if is_evening else 'НАЙСТАРІШІ (до 21:00)'}")

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
        print(f"❌ Помилка отримання файлів з Google Drive: {e}")
        return []


def download_and_analyze_candidates(drive_service, candidate_files):
    """
    Завантажує 50 кандидатів у папку temp_mebli та аналізує їх EXIF, точну дату зйомки 
    та геолокацію за допомогою інструментів media_processor.
    Повертає список словників із розширеною інформацією.
    """
    os.makedirs('temp_mebli', exist_ok=True)
    analyzed_items = []

    print(f"\n📥 Завантаження та глибокий аналіз метаданих {len(candidate_files)} файлів...")

    for idx, f_info in enumerate(candidate_files, 1):
        file_id = f_info['id']
        raw_name = f_info['name']
        sanitized_name = sanitize_filename(raw_name)
        local_path = os.path.join('temp_mebli', sanitized_name)

        try:
            # 1. Завантаження файлу
            request = drive_service.files().get_media(fileId=file_id)
            with open(local_path, 'wb') as f:
                f.write(request.execute())

            # 2. Глибокий аналіз дати та координат
            dt, lat, lon = get_intellectual_date(local_path, sanitized_name, f_info)
            display_loc, group_loc = get_location_data(lat, lon)

            date_str = dt.strftime('%d.%m.%Y')
            date_key = dt.strftime('%Y-%m-%d')
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
                'mimeType': f_info.get('mimeType', '')
            })
            print(f"  [{idx}/{len(candidate_files)}] {raw_name} -> Дата: {date_str} | Локація: {display_loc or 'без GPS'}")

        except Exception as err:
            print(f"  ⚠️ Помилка обробки файлу {raw_name}: {err}")

    return analyzed_items


def group_analyzed_files(analyzed_items):
    """
    Групує проаналізовані файли за датою зйомки та назвою міста/регіону.
    """
    grouped_dict = defaultdict(list)

    for item in analyzed_items:
        # Формуємо ключ групи, наприклад: "2024-10-12_Київ, Україна"
        group_key = f"{item['date_key']}_{item['location_key']}"
        grouped_dict[group_key].append(item)

    return list(grouped_dict.values())


def move_file_to_trash_folder(drive_service, file_id, file_name, trash_folder_id):
    """
    Переміщує опублікований файл у папку Кошика на Google Диску.
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
        print(f"⚠️️ Не вдалося перемістити файл [{file_name}] у папку Кошика, видаляємо в системний trash: {e}")
        try:
            drive_service.files().update(fileId=file_id, body={'trashed': True}).execute()
        except Exception as tr_err:
            print(f"❌ Помилка видалення файлу: {tr_err}")


def publish_batch_group(drive_service, sheets_service, batch_items, target_tab="Меблі"):
    """
    Оптимізація медіа, генерація підпису Gemini та публікація серії (до 4 файлів) в Instagram Stories.
    """
    access_token = os.environ.get("META_ACCESS_TOKEN") or config.META_ACCESS_TOKEN
    ig_user_id = os.environ.get("INSTAGRAM_ACCOUNT_ID") or config.IG_USER_ID

    if not access_token or not ig_user_id:
        print("⚠️ Відсутні ключі META_ACCESS_TOKEN або INSTAGRAM_ACCOUNT_ID.")
        return

    previous_captions = []  # Зберігаємо підписи для запобігання дублюванню в межах серії

    print(f"\n🚀 РОЗПОЧИНАЄМО ПУБЛІКАЦІЮ СЕРІЇ З {len(batch_items)} СТОРІЗ...")

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

        # 1. Генерація унікального підпису Gemini API
        caption_text = generate_story_caption(
            image_paths=[local_path],
            category=target_tab,
            date_str=date_str,
            lang_idx=0,
            target_loc=display_loc,
            previous_captions=previous_captions
        )
        print(f"💬 Згенерований текст: \"{caption_text}\"")
        previous_captions.append(caption_text)

        # 2. Форматування та накладання оверлею (1080x1920)
        lower_name = file_name.lower()
        is_video = lower_name.endswith(('.mp4', '.mov', '.avi'))
        
        if is_video:
            processed_files = optimize_video_story(local_path, file_name, caption_text, year=dt.year, location=display_loc)
        else:
            padded_img_path = optimize_image_story(local_path, file_name)
            overlay_text_on_image(padded_img_path, caption_text, year=dt.year, location=display_loc)
            processed_files = [padded_img_path]

        # 3. Публікація кожного фрагмента в Meta API
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
                # Переміщуємо опублікований файл у папку Кошика
                move_file_to_trash_folder(drive_service, file_id, raw_file_name, config.TRASH_FOLDER_ID)
            else:
                print(f"❌ ПОМИЛКА ПУБЛІКАЦІЇ: {result_msg}")
                log_unsupported_to_service(sheets_service, target_tab, raw_file_name, f"Помилка Meta: {result_msg}")

            if imagekit_id:
                delete_from_imagekit(imagekit_id)


def run_story_publisher():
    """
    Головна точка входу.
    """
    print("🚀 ІНІЦІАЛІЗАЦІЯ МОДУЛЯ АВТОПУБЛІКАЦІЇ INSTAGRAM STORIES...")
    drive_s, sheets_s = get_services()

    # 1. Отримуємо список із 50 кандидатів
    candidates = fetch_candidate_files(drive_s, config.HOT_FOLDER_ID, limit=50)
    if not candidates:
        print("📁 Гаряча папка порожня або файли відсутні. Завершення роботи.")
        return

    print(f"📊 Отримано {len(candidates)} кандидатів з Google Drive.")

    # 2. Завантажуємо файли та проводимо повний аналіз через media_processor
    analyzed_items = download_and_analyze_candidates(drive_s, candidates)
    if not analyzed_items:
        print("❌ Не вдалося обробити жодного файла. Завершення.")
        return

    # 3. Групуємо файли по датах та геолокаціях
    grouped_batches = group_analyzed_files(analyzed_items)
    print(f"\n🧩 Сформовано {len(grouped_batches)} тематичних/часових груп.")

    # 4. Беремо першу групу та її перші 4 файли
    first_group = grouped_batches[0]
    target_batch = first_group[:4]

    print(f"🎯 Обрано 1-шу групу (Дата: {target_batch[0]['date_str']}, Локація: {target_batch[0]['display_loc'] or 'без GPS'}).")
    print(f"   Файлів для публікації: {len(target_batch)} (макс. 4).")

    # 5. Публікуємо обрану серію
    publish_batch_group(drive_s, sheets_s, target_batch, target_tab=config.TAB_NAME)

    # 6. Очищуємо тимчасові файли
    cleanup_temp_dir("temp_mebli")


if __name__ == "__main__":
    run_story_publisher()
