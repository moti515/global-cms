"""
========================================================================================
 📦 МОДУЛЬНА АРХІТЕКТУРА ПРОЄКТУ АВТОПУБЛІКАЦІЇ INSTAGRAM STORIES (mebli_instagram_story)
========================================================================================

1. publish_content_mebli_storys.py (Цей модуль)
   - Головний виконавчий модуль (оркестратор) автоматичної публікації Instagram Stories.
   - Відповідає за повний конвеєр: завантаження файлів з гарячої папки Google Drive,
     запуск графічної обробки, генерацію AI-підписів, завантаження на хостинги та
     фінальну публікацію через Meta Graph API.

2. config_meb_insta_story.py
   - Централізований конфігураційний файл проєкту.
   - Містить ID Google Sheets/Drive, OAuth Scopes, мовні конфігурації (LANG_CONFIG),
     базу даних брендів/виробників (COMPANIES_DB) та глобальні налаштування.

3. media_processor_meb_instagram_story.py
   - Спеціалізований модуль обробки фото, відео, EXIF та геолокації.
   - Відповідає за формати 1080x1920 (Pillow/FFmpeg), нарізку відео по 60 секунд,
     створення прозорих PNG-оверлеїв з текстом/емодзі, витягування EXIF-метаданих
     та реверсивне геокодування координат через OpenStreetMap Nominatim.

4. services_manager_meb_instagram_story.py
   - Менеджер зовнішніх API, авторизації та хмарної інфраструктури.
   - Забезпечує авторизацію Google Drive/Sheets, завантаження медіафайлів на тимчасові
     хостинги (Litterbox, ImageKit, Tmpfiles, ImgBB), генерацію підписів через Gemini API,
     очікування обробки контейнерів Meta API та логування помилок у Google Таблиці.

5. utils_meb_instagram_story.py
   - Набір загальних допоміжних утиліт та хелперів.
   - Містить функції очищення тимчасових папок (temp_mebli), валідації файлових імен,
     безпечного форматування рядків, відправки в Meta API та обробки системних винятків.
========================================================================================
"""

import os
import sys

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


def publish_story_item(drive_service, sheets_service, file_info, target_tab="Меблі"):
    """
    Основна функція конвеєра: завантажує файл з Google Drive, проводить інтелектуальну обробку,
    генерує підпис Gemini, накладає оверлей і публікує в Instagram Stories через Meta Graph API.
    """
    file_id = file_info['id']
    raw_file_name = file_info['name']
    file_name = sanitize_filename(raw_file_name)
    
    print(f"\n🎬 Обробка файлу з гарячої папки: {raw_file_name} -> {file_name} (ID: {file_id})...")
    
    # Створюємо робочу директорію для тимчасових файлів
    os.makedirs('temp_mebli', exist_ok=True)
    local_path = os.path.join('temp_mebli', file_name)
    
    # 1️⃣ Завантаження файлу з Google Drive
    try:
        request = drive_service.files().get_media(fileId=file_id)
        with open(local_path, 'wb') as f:
            f.write(request.execute())
    except Exception as download_err:
        print(f"❌ Не вдалося завантажити файл з Google Drive: {download_err}")
        return False

    # 2️⃣ Аналіз дат, EXIF та геолокації через media_processor
    dt, lat, lon = get_intellectual_date(local_path, file_name, file_info)
    date_str = dt.strftime('%d.%m.%Y')
    
    display_loc, group_loc = get_location_data(lat, lon)
    
    # 3️⃣ Генерація опису/підпису через services_manager (Gemini API)
    caption_text = generate_story_caption(
        image_paths=[local_path],
        category=target_tab,
        date_str=date_str,
        lang_idx=0,  # За замовчуванням українська
        target_loc=display_loc
    )
    print(f"💬 Згенерований текст: \"{caption_text}\"")

    # 4️⃣ Оптимізація та накладання оверлею на медіафайл
    lower_name = file_name.lower()
    is_video = lower_name.endswith(('.mp4', '.mov', '.avi'))
    
    processed_files = []
    if is_video:
        processed_files = optimize_video_story(local_path, file_name, caption_text, year=dt.year, location=display_loc)
    else:
        padded_img_path = optimize_image_story(local_path, file_name)
        overlay_text_on_image(padded_img_path, caption_text, year=dt.year, location=display_loc)
        processed_files = [padded_img_path]

    # 5️⃣ Завантаження та публікація фрагментів
    access_token = os.environ.get("META_ACCESS_TOKEN")
    ig_user_id = os.environ.get("INSTAGRAM_ACCOUNT_ID")
    
    if not access_token or not ig_user_id:
        print("⚠️ Відсутні ключі META_ACCESS_TOKEN або INSTAGRAM_ACCOUNT_ID у змінних оточення.")
        return False

    all_published_successfully = True

    for ready_file in processed_files:
        direct_url, imagekit_id = get_google_drive_direct_url(file_id, local_file_path=ready_file)
        
        if not direct_url:
            print("🚨 Не вдалося отримати пряме посилання для Meta API. Пропускаємо публікацію.")
            log_unsupported_to_service(sheets_service, target_tab, raw_file_name, "Помилка генерації URL")
            all_published_successfully = False
            break

        # 6️⃣ Публікація в Meta API через стандартизовану функцію з utils
        print("📡 Надсилання сторіз в Meta API...")
        success, result_msg = publish_story_to_meta(
            ig_user_id=ig_user_id,
            meta_access_token=access_token,
            pub_url=direct_url,
            is_video=is_video
        )
        
        if success:
            print(f"✅ Фрагмент успішно опубліковано! ID: {result_msg}")
        else:
            print(f"❌ Помилка публікації: {result_msg}")
            all_published_successfully = False
            
        # Видаляємо тимчасовий файл з ImageKit, якщо використовувався бізнес-резерв
        if imagekit_id:
            delete_from_imagekit(imagekit_id)

    # 7️⃣ Переміщення вихідного файлу на Google Диску у кошик після успішної публікації
    if all_published_successfully:
        try:
            drive_service.files().update(fileId=file_id, body={'trashed': True}).execute()
            print(f"🗑️ Файл [{raw_file_name}] переміщено до кошика на Google Диску.")
        except Exception as e:
            print(f"⚠️ Не вдалося видалити файл з Google Диску: {e}")

    # Очищаємо тимчасові файли
    cleanup_temp_dir("temp_mebli")

    return all_published_successfully


if __name__ == "__main__":
    print("🚀 Запуск модуля публікації сторіс...")
    drive_s, sheets_s = get_services()
    print("✅ Сервіси Google успішно авторизовано.")
