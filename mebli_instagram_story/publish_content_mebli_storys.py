import os
import sys
import json
import time
import requests
from datetime import datetime

# Централізований конфіг проекту
import config_meb_insta_story as config

# 🛠 Імпорт сервісів (Google, Gemini, Meta API, Хмарні хостинги)
try:
    from services_manager_meb_instagram_story import (
        get_services,
        log_unsupported_to_service,
        get_google_drive_direct_url,
        delete_from_imagekit,
        generate_story_caption,
        wait_for_meta_container,
    )
    from media_processor_meb_instagram_story import (
        optimize_image_story,
        overlay_text_on_image,
        optimize_video_story,
        extract_date_from_filename,
        get_exif_data,
        get_video_metadata,
        get_location_data,
        get_intellectual_date,
    )
except ImportError:
    from mebli_instagram_story.services_manager_meb_instagram_story import (
        get_services,
        log_unsupported_to_service,
        get_google_drive_direct_url,
        delete_from_imagekit,
        generate_story_caption,
        wait_for_meta_container,
    )
    from mebli_instagram_story.media_processor_meb_instagram_story import (
        optimize_image_story,
        overlay_text_on_image,
        optimize_video_story,
        extract_date_from_filename,
        get_exif_data,
        get_video_metadata,
        get_location_data,
        get_intellectual_date,
    )

# =====================================================================
# 🚀 ОСНОВНА ЛОГІКА ПУБЛІКАЦІЇ СТОРІС В INSTAGRAM / FACEBOOK
# =====================================================================

def publish_story_item(drive_service, sheets_service, file_info, target_tab="Меблі"):
    """
    Основна функція конвеєра: завантажує файл з Google Drive, проводить інтелектуальну обробку,
    генерує підпис Gemini, накладає оверлей і публікує в Instagram Stories через Meta Graph API.
    """
    file_id = file_info['id']
    file_name = file_info['name']
    
    print(f"\n🎬 Обробка файлу з гарячої папки: {file_name} (ID: {file_id})...")
    
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

    # 5️⃣ Завантаження обробленого медіафайлу на тимчасовий публічний хостинг
    for ready_file in processed_files:
        direct_url, imagekit_id = get_google_drive_direct_url(file_id, local_file_path=ready_file)
        
        if not direct_url:
            print("🚨 Не вдалося отримати пряме посилання для Meta API. Пропускаємо публікацію.")
            log_unsupported_to_service(sheets_service, target_tab, file_name, "Помилка генерації URL")
            return False

        # 6️⃣ Публікація в Instagram Stories через Meta Graph API
        access_token = os.environ.get("META_ACCESS_TOKEN")
        ig_user_id = os.environ.get("INSTAGRAM_ACCOUNT_ID")
        
        if not access_token or not ig_user_id:
            print("⚠️ Відсутні ключі META_ACCESS_TOKEN або INSTAGRAM_ACCOUNT_ID у змінних оточення.")
            return False

        print("📡 Надсилання сторіз в Meta API...")
        media_type = "VIDEO" if is_video else "IMAGE"
        param_key = "video_url" if is_video else "image_url"
        
        container_url = f"https://graph.facebook.com/v21.0/{ig_user_id}/media"
        container_payload = {
            'media_type': 'STORIES',
            param_key: direct_url,
            'access_token': access_token
        }

        try:
            res = requests.post(container_url, data=container_payload, timeout=30).json()
            container_id = res.get('id')
            
            if container_id:
                if wait_for_meta_container(container_id, access_token):
                    pub_url = f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish"
                    pub_res = requests.post(pub_url, data={'creation_id': container_id, 'access_token': access_token}, timeout=30).json()
                    
                    if 'id' in pub_res:
                        print(f"✅ Фрагмент успішно опубліковано! ID: {pub_res['id']}")
                    else:
                        print(f"❌ Помилка публікації контейнера: {pub_res}")
                else:
                    print("❌ Контейнер Meta завершився з помилкою або не встиг обробитися.")
            else:
                print(f"❌ Помилка створення контейнера Meta: {res}")
        except Exception as meta_err:
            print(f"⚠️ Помилка з'єднання з Meta API: {meta_err}")
            
        # Видаляємо тимчасовий файл з ImageKit, якщо використовувався бізнес-резерв
        if imagekit_id:
            delete_from_imagekit(imagekit_id)

    # 7️⃣ Переміщення вихідного файлу на Google Диску у кошик / архів після успішної публікації
    try:
        drive_service.files().update(fileId=file_id, body={'trashed': True}).execute()
        print(f"🗑️ Файл [{file_name}] переміщено до кошика на Google Диску.")
    except Exception as e:
        print(f"⚠️ Не вдалося видалити файл з Google Диску: {e}")

    return True


if __name__ == "__main__":
    print("🚀 Запуск модуля публікації сторіс...")
    drive_s, sheets_s = get_services()
    print("✅ Сервіси Google успішно авторизовано.")
