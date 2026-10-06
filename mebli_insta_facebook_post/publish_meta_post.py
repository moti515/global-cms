"""
================================================================================
🏛️ АРХІТЕКТУРА СИСТЕМИ ПУБЛІКАЦІЇ КОНТЕНТУ META (Facebook & Instagram)
================================================================================

Даний проєкт призначений для автоматизованого публікування меблевого контенту 
з Google Drive / Google Sheets у мережі Facebook та Instagram через Meta Graph API.

📦 СТРУКТУРА ТА ВЗАЄМОДІЯ МОДУЛІВ:

 1. publish_meta_post.py (ГОЛОВНИЙ ОРКЕСТРАТОР)
    - Зчитує параметри запуску (режим fb_post / ig_post, назву вкладки).
    - Формує чергу публікацій з огляду на найменші лічильники запусків.
    - Керує циклом обробки медіа, викликами ШІ-генератора та публікацією у Meta Graph API.
    - Забезпечує ретраї (повторні спроби) при тимчасових збоях мережі та очищення файлів.

 2. service_manager_meta_post.py (ЗОВНІШНІ СЕРВІСИ ТА ШІ)
    - Взаємодіє з Google Drive (скачування) та Google Sheets (читання/запис лічильників і мов).
    - Завантажує тимчасові файли на хостинги Litterbox (1h) / ImageKit / ImgBB.
    - Генерує креативні підписи через новий офіційний SDK google-genai (Gemini AI).

 3. media_processor_meta_post.py (ОБРОБКА МЕДІА ТА ФОРМАТУВАННЯ)
    - Нормалізує зображення (конвертація HEIC/PNG/WEBP у JPEG, RGB-режим).
    - Калібрує геометрію: додає естетичні ЧОРНІ поля (0, 0, 0) для недопустимих пропорцій.
    - Витягує кадр з відео через FFmpeg для візуального аналізу ШІ.
    - Генерує базований на брендах заголовок поста.

 4. config_meta_post.py (КОНФІГУРАЦІЯ ТА ЛОКАЛІЗАЦІЯ)
    - Централізоване сховище ключів оточення (ENV), ID таблиць та форматів.
    - Словники локалізації (UK, EN, DE) та база брендів/виробників (COMPANIES_DB).

================================================================================
"""

import os
import sys
import json
import time
import re
import requests

# Імпорт модулів проєкту
import config_meta_post as config
import media_processor_meta_post as media_processor
import service_manager_meta_post as service_manager


def sanitize_filename(filename: str) -> str:
    """Замінює кирилицю, пробіли та спецсимволи на дефіси для безпеки FFmpeg/PIL та Meta API."""
    name, ext = os.path.splitext(filename)
    sanitized_name = re.sub(r'[^a-zA-Z0-9_\-]', '-', name)
    sanitized_name = re.sub(r'-+', '-', sanitized_name).strip('-')
    if not sanitized_name:
        sanitized_name = f"media_{int(time.time())}"
    return f"{sanitized_name}{ext.lower()}"


def wait_for_meta_container(container_id: str, access_token: str) -> bool:
    """Очікує завершення асинхронної обробки відео/медіа контейнера в Meta API."""
    check_url = f"https://graph.facebook.com/v19.0/{container_id}"
    params = {"fields": "status_code,status", "access_token": access_token}
    for _ in range(30):
        try:
            r = requests.get(check_url, params=params, timeout=15).json()
            status = r.get("status_code", "").upper()
            if status == "FINISHED":
                print("✅ Контейнер успішно скомпіровано Meta.")
                return True
            elif status == "ERROR":
                print(f"❌ Помилка обробки контейнера Meta: {r.get('status')}")
                return False
            print(f"⏳ Очікування готовності контейнера... Статус: {status}")
        except Exception as e:
            print(f"⚠️ Помилка перевірки статусу: {e}")
        time.sleep(10)
    return False


def clean_up_local_files(files: list):
    """Видаляє всі тимчасові файли на диску хоста без повторів."""
    for f in set(files):
        if f and os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass


def publish_to_facebook(cloud_urls: list, full_caption: str, has_video: bool, local_files: list) -> dict:
    """Публікує одиничне відео або альбоми фото у Facebook Page Feed."""
    if has_video:
        print("🎬 Публікація відео-поста у Facebook...")
        fb_url = f"https://graph.facebook.com/v19.0/{config.FB_PAGE_ID}/videos"
        payload = {"file_url": cloud_urls[0], "description": full_caption, "access_token": config.META_ACCESS_TOKEN}
        return requests.post(fb_url, data=payload, timeout=60).json()

    print(f"🖼️ Публікація фото-альбому ({len(cloud_urls)} шт.) у Facebook...")
    attached_media = []
    for url in cloud_urls:
        photo_id = None
        for attempt in range(3):
            photo_res = requests.post(f"https://graph.facebook.com/v19.0/{config.FB_PAGE_ID}/photos", data={
                "url": url, "published": "false", "access_token": config.META_ACCESS_TOKEN
            }, timeout=30).json()

            if "id" in photo_res:
                photo_id = photo_res["id"]
                break
            print(f"⚠️️ Спроба {attempt + 1}/3 невдала для фото. Meta API: {photo_res}")
            time.sleep(5)

        if not photo_id:
            print(f"❌ Не вдалося завантажити під-елемент photo після 3 спроб: {url}")
            clean_up_local_files(local_files)
            sys.exit(1)

        attached_media.append({"media_fbid": photo_id})

    fb_url = f"https://graph.facebook.com/v19.0/{config.FB_PAGE_ID}/feed"
    payload = {"message": full_caption, "attached_media": json.dumps(attached_media), "access_token": config.META_ACCESS_TOKEN}
    return requests.post(fb_url, data=payload, timeout=30).json()


def publish_to_instagram(uploaded_media: list, full_caption: str, local_files: list) -> dict:
    """Публікує карусель, Reels або одиничне фото в Instagram Feed."""
    cloud_urls = [m["url"] for m in uploaded_media]

    if len(uploaded_media) > 1:
        print(f"🗂️ Створення каруселі Instagram з {len(uploaded_media)} елементів...")
        container_ids = []
        for item in uploaded_media:
            url, is_vid = item["url"], item["is_video"]
            param_type = "video_url" if is_vid else "image_url"
            payload = {param_type: url, "is_carousel_item": "true", "access_token": config.META_ACCESS_TOKEN}
            if is_vid:
                payload["media_type"] = "VIDEO"

            item_id = None
            for attempt in range(3):
                item_res = requests.post(f"https://graph.facebook.com/v19.0/{config.IG_USER_ID}/media", data=payload, timeout=30).json()
                if "id" in item_res:
                    item_id = item_res["id"]
                    break
                print(f"⚠️ Спроба {attempt + 1}/3 створення елемента каруселі невдала: {item_res}")
                time.sleep(5)

            if not item_id:
                print(f"❌ Помилка створення контейнера каруселі після спроб: {url}")
                clean_up_local_files(local_files)
                sys.exit(1)

            if is_vid and not wait_for_meta_container(item_id, config.META_ACCESS_TOKEN):
                print(f"❌ Відео-контейнер {item_id} зафейлився.")
                clean_up_local_files(local_files)
                sys.exit(1)

            container_ids.append(item_id)

        carousel_payload = {
            "media_type": "CAROUSEL",
            "children": json.dumps(container_ids),
            "caption": full_caption,
            "access_token": config.META_ACCESS_TOKEN
        }
        res = requests.post(f"https://graph.facebook.com/v19.0/{config.IG_USER_ID}/media", data=carousel_payload, timeout=30).json()

    else:
        print("🎬 Створення одиничного контейнера в Instagram...")
        is_vid = uploaded_media[0]["is_video"]
        param_type = "video_url" if is_vid else "image_url"
        payload = {param_type: cloud_urls[0], "caption": full_caption, "access_token": config.META_ACCESS_TOKEN}

        if is_vid:
            payload["media_type"] = "REELS"
            payload["share_to_feed"] = "true"

        res = None
        for attempt in range(3):
            res = requests.post(f"https://graph.facebook.com/v19.0/{config.IG_USER_ID}/media", data=payload, timeout=30).json()
            if res and "id" in res:
                break
            print(f"⚠️ Спроба {attempt + 1}/3 створення одиничного контейнера невдала: {res}")
            time.sleep(5)

        if not res or "id" not in res:
            print(f"❌ КРИТИЧНА ПОМИЛКА: Не вдалося створити контейнер в Instagram: {res}")
            clean_up_local_files(local_files)
            sys.exit(1)

        if is_vid and not wait_for_meta_container(res["id"], config.META_ACCESS_TOKEN):
            print("❌ Одиничний відео-контейнер зафейлився.")
            clean_up_local_files(local_files)
            sys.exit(1)

    # Публікація створеного контейнера
    if res and "id" in res:
        creation_id = res["id"]
        print("🚀 Фінальна публікація контейнера в Instagram...")
        for attempt in range(6):
            pub_res = requests.post(f"https://graph.facebook.com/v19.0/{config.IG_USER_ID}/media_publish", data={
                "creation_id": creation_id, "access_token": config.META_ACCESS_TOKEN
            }, timeout=30).json()

            if "error" in pub_res:
                err = pub_res["error"]
                if err.get("error_subcode") == 2207027 or err.get("code") == 9007:
                    print(f"⏳ Сервери Meta зайняті (Спроба {attempt + 1}/6). Чекаємо 10 сек...")
                    time.sleep(10)
                    continue
            return pub_res

    return res


def main():
    if len(sys.argv) < 3:
        print("💡 Необхідно передати параметри. Запуск: python main.py <mode> <tab_name>")
        sys.exit(1)

    mode = sys.argv[1].lower()
    forced_tab = sys.argv[2]
    current_tab = forced_tab if forced_tab else config.TAB_NAME

    print(f"📊 [Режим: {mode.upper()}] Зчитування реєстру '{current_tab}'...")
    drive, sheets = service_manager.get_services()

    res = sheets.spreadsheets().values().get(
        spreadsheetId=config.SPREADSHEET_ID, range=f"'{current_tab}'!A2:I"
    ).execute()
    rows = res.get('values', [])
    if not rows:
        print("ℹ️️ Реєстр порожній.")
        return

    col_idx, col_letter = (3, "D") if mode == "ig_post" else ((5, "F") if mode == "fb_post" else (None, None))
    if col_idx is None:
        print(f"❌ Невідомий режим публікації: {mode}")
        sys.exit(1)

    valid_rows = []
    for i, r in enumerate(rows):
        if len(r) >= 3 and r[2].lower() != "temporary":
            try:
                val = r[col_idx] if len(r) > col_idx and r[col_idx] else "0"
                valid_rows.append({"row_idx": i + 2, "data": r, "counter": int(val)})
            except ValueError:
                continue

    if not valid_rows:
        print("ℹ️ Немає валідних рядків для обробки.")
        return

    # Групування за проектом / типом
    min_counter = min(item["counter"] for item in valid_rows)
    min_pool = [item for item in valid_rows if item["counter"] == min_counter]

    groups = {}
    for item in min_pool:
        data = item["data"]
        group_loc_json = data[8] if len(data) > 8 else (data[7] if len(data) > 7 else "")
        group_key = (data[2], data[6] if len(data) > 6 else "", group_loc_json)
        groups.setdefault(group_key, []).append(item)

    first_key = list(groups.keys())[0]
    selected_group_items = groups[first_key][:4]
    category_name, target_date, target_loc = first_key
    print(f"📂 Обрано групу: {category_name} (Файлів у пулі: {len(selected_group_items)})")

    # Управління мовою
    target_lang_cell = "'⚙️ Налаштування Папок'!F2" if mode == "fb_post" else "'⚙️ Налаштування Папок'!G2"
    lang_value = service_manager.read_current_language(sheets, target_lang_cell)
    
    lang_idx, lang_flag, next_lang_value = (
        (1, "🇬🇧", "DE") if lang_value == "EN" else (
        (2, "🇩🇪", "UK") if lang_value == "DE" else 
        (0, "🇺🇦", "EN"))
    )
    print(f"🌐 Поточна мова: {lang_value} (Індекс: {lang_idx}, Прапор: {lang_flag}). Наступна: {next_lang_value}")

    # Валідація розширень
    for item in selected_group_items:
        f_name = item["data"][1]
        if not f_name.lower().endswith(config.VALID_MEDIA_EXTENSIONS):
            print(f"❌ КРИТИЧНА ПОМИЛКА: Файл '{f_name}' має непідтримуваний формат!")
            sys.exit(1)

    os.makedirs('temp_mebli', exist_ok=True)
    local_files, uploaded_media, ik_ids, ai_analysis_images = [], [], [], []
    has_video = False

    # Завантаження та єдина обробка медіа
    for item in selected_group_items:
        f_id, f_name = item["data"][0], item["data"][1]
        safe_local_name = sanitize_filename(f"{f_id[:8]}_{f_name}")
        local_path = os.path.join('temp_mebli', safe_local_name)

        print(f"📥 Завантаження з Drive: {f_name} -> {safe_local_name}...")
        try:
            service_manager.download_file_from_drive(drive, f_id, local_path)
        except Exception as e:
            print(f"❌ КРИТИЧНА ПОМИЛКА скачування '{f_name}': {e}")
            clean_up_local_files(local_files)
            sys.exit(1)

        local_files.append(local_path)
        mime_type = "video/mp4" if safe_local_name.lower().endswith(('.mp4', '.mov', '.avi')) else "image/jpeg"
        is_current_video = (mime_type == "video/mp4")
        if is_current_video:
            has_video = True

        # Уніфікована обробка через media_processor (конвертація HEIC/PNG/WEBP, нормалізація, чорні поля)
        optimized_path = media_processor.optimize_media_geometry(local_path, safe_local_name, mime_type)
        if optimized_path != local_path:
            local_files.append(optimized_path)

        if is_current_video:
            frame_path = os.path.join('temp_mebli', f"frame_{f_id}.jpg")
            extracted_frame = media_processor.extract_video_frame(optimized_path, frame_path)
            if extracted_frame and os.path.exists(extracted_frame):
                ai_analysis_images.append(extracted_frame)
                local_files.append(extracted_frame)
        else:
            ai_analysis_images.append(optimized_path)

        # Завантаження на зовнішні сервери
        try:
            pub_url, ik_id = service_manager.get_google_drive_direct_url(f_id, local_file_path=optimized_path)
            if not pub_url:
                raise ValueError("Порожній URL публікації.")
            uploaded_media.append({"url": pub_url, "is_video": is_current_video})
            if ik_id:
                ik_ids.append(ik_id)
        except Exception as e:
            print(f"❌ КРИТИЧНА ПОМИЛКА генерування посилання '{f_name}': {e}")
            clean_up_local_files(local_files)
            sys.exit(1)

    # Генерація підпису
    header_text = media_processor.get_manufacturer_header(category_name, target_date, lang_idx, mode, target_loc)
    ai_text = service_manager.generate_multimodal_caption(ai_analysis_images, category_name, target_date, lang_idx)
    full_caption = f"{lang_flag} {header_text}{ai_text}"

    # Перевірка ENV ключів
    if mode == "fb_post" and not config.FB_PAGE_ID:
        print("❌ Відсутній FB_PAGE_ID!")
        clean_up_local_files(local_files)
        sys.exit(1)
    if mode == "ig_post" and not config.IG_USER_ID:
        print("❌ Відсутній IG_USER_ID!")
        clean_up_local_files(local_files)
        sys.exit(1)

    # Виклик потрібної платформи
    if mode == "fb_post":
        res = publish_to_facebook([m["url"] for m in uploaded_media], full_caption, has_video, local_files)
    else:
        res = publish_to_instagram(uploaded_media, full_caption, local_files)

    # Фіналізація та оновлення даних
    if res and ("id" in res or "post_id" in res):
        print(f"✅ Успішно опубліковано! ID: {res.get('id', res.get('post_id'))}")
        service_manager.update_sheets_registry_and_lang(
            sheets, selected_group_items, current_tab, col_letter, target_lang_cell, next_lang_value
        )
        if ik_ids:
            print("🧹 Очищення тимчасового сховища ImageKit...")
            for ik_id in ik_ids:
                service_manager.delete_from_imagekit(ik_id)
    else:
        print(f"❌ КРИТИЧНА ПОМИЛКА Meta API: {res}")
        clean_up_local_files(local_files)
        sys.exit(1)

    clean_up_local_files(local_files)
    print("🎯 Скрипт успішно завершив роботу.")


if __name__ == "__main__":
    main()
