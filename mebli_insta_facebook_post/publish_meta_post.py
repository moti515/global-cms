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
from datetime import datetime

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
            print(f"⚠️ Спроба {attempt + 1}/3 невдала для фото. Meta API: {photo_res}")
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
        print("ℹ️ Реєстр порожній.")
        return

    col_idx, col_letter = (3, "D") if mode == "ig_post" else ((5, "F") if mode
