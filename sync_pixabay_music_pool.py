#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
========================================================================================
🎵 АВТОНОМНИЙ МЕНЕДЖЕР ТА РОТАТОР ФОНОВОЇ МУЗИКИ (Pixabay API -> Google Drive)
========================================================================================
Скрипт підтримує компактний пул фонових треків (10-12 шт) у Google Диску для
автоматичного накладання у Facebook Reels та TikTok.

Основні функції:
1. Контролює розмір пулу музичних файлів.
2. Автоматично видаляє найстаріші треки та замінює їх свіжими з Pixabay API.
3. Використовує тематичні запити під меблеве виробництво та естетику сторітелінгу.
========================================================================================
"""

import os
import sys
import random
import re
import shutil
import requests
from datetime import datetime

# Google API Client
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ======================================================================================
# ⚙️ НАЛАШТУВАННЯ ТА КОНФІГУРАЦІЯ
# ======================================================================================

# ID папки на Google Диску для зберігання фонової музики
MUSIC_FOLDER_ID = os.environ.get("MUSIC_FOLDER_ID", "ВАШ_MUSIC_FOLDER_ID_ТУТ")

# Ключ Pixabay API (безкоштовно на https://pixabay.com/api/docs/)
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "ВАШ_PIXABAY_API_KEY_ТУТ")

# Параметри пулу
TARGET_POOL_SIZE = 12       # Оптимальна кількість треків у папці
ROTATE_COUNT = 3            # Скільки найстаріших треків замінювати при кожному запуску ротації
TEMP_DIR = "temp_music_sync"

# 🎯 Тематичні ключеві слова Pixabay (Меблевик + ТікТок / Атмосфера)
THEME_KEYWORDS = [
    # Тематика: Меблі, Майстерня, Конструювання, Професіоналізм (FB Reels)
    "lofi", "acoustic", "lounge", "workshop", "chillhop", "craft", "background", "design",
    # Тематика: Життя поза меблями, Travel, Естетика, TikTok Vibe
    "chillout", "travel", "vlog", "aesthetic", "lifestyle", "ambient", "summer"
]


def get_google_drive_service():
    """Ініціалізація сервісу Google Drive API."""
    creds = None
    
    # 1. Спроба зчитати з оточення GOOGLE_CREDENTIALS_JSON (для GitHub Actions)
    credentials_raw = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if credentials_raw:
        import json
        info = json.loads(credentials_raw)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/drive']
        )
    # 2. Спроба зчитати з файлу credentials.json
    elif os.path.exists("credentials.json"):
        creds = service_account.Credentials.from_service_account_file(
            "credentials.json", scopes=['https://www.googleapis.com/auth/drive']
        )

    if not creds:
        print("❌ [Auth] Не знайдено облікових даних Google Drive API.")
        sys.exit(1)

    return build('drive', 'v3', credentials=creds)


def sanitize_filename(name: str) -> str:
    """Очищає назву файлу від спецсимволів."""
    clean = re.sub(r'[^\w\s-]', '', name).strip()
    return re.sub(r'[-\s]+', '_', clean)


def fetch_existing_drive_tracks(drive_service, folder_id):
    """
    Отримує список існуючих MP3-файлів у папці Google Диска,
    відсортованих за датою створення (від найстаріших до найновіших).
    """
    query = f"'{folder_id}' in parents and trashed = false and (mimeType contains 'audio/' or name contains '.mp3')"
    try:
        results = drive_service.files().list(
            q=query,
            fields="files(id, name, createdTime)",
            orderBy="createdTime asc",
            pageSize=100
        ).execute()
        return results.get('files', [])
    except Exception as e:
        print(f"❌ [Drive] Помилка отримання списку файлів: {e}")
        return []


def delete_drive_file(drive_service, file_id, file_name):
    """Видаляє застарілий файл з Google Диска."""
    try:
        drive_service.files().delete(fileId=file_id).execute()
        print(f"🗑️ [Drive] Видалено застарілий трек: {file_name}")
        return True
    except Exception as e:
        print(f"⚠️ [Drive] Не вдалося видалити файл {file_name}: {e}")
        return False


def fetch_tracks_from_pixabay(api_key, count_needed, existing_names):
    """
    Шукає та завантажує нові Royalty-Free треки через Pixabay API.
    """
    if not api_key or api_key.startswith("ВАШ_"):
        print("⚠️ [Pixabay] Відсутній дійсна ключ PIXABAY_API_KEY.")
        return []

    # Вибираємо випадкову тематику з нашого списку
    random.shuffle(THEME_KEYWORDS)
    downloaded_tracks = []

    os.makedirs(TEMP_DIR, exist_ok=True)

    for keyword in THEME_KEYWORDS:
        if len(downloaded_tracks) >= count_needed:
            break

        print(f"🔎 [Pixabay API] Пошук музики за тегом: '{keyword}'...")
        url = "https://pixabay.com/api/audio/"
        params = {
            "key": api_key,
            "q": keyword,
            "per_page": 20,
            "order": "popular"
        }

        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                print(f"⚠️ Pixabay API повернув статус {resp.status_code}")
                continue

            data = resp.json()
            hits = data.get("hits", [])
            random.shuffle(hits)

            for hit in hits:
                if len(downloaded_tracks) >= count_needed:
                    break

                track_id = hit.get("id")
                raw_title = hit.get("title", f"pixabay_{track_id}")
                clean_title = sanitize_filename(raw_title)
                filename = f"pixabay_{track_id}_{clean_title}.mp3"

                # Перевірка, чи немає вже такого треку на Диску
                if any(str(track_id) in ex_name for ex_name in existing_names):
                    continue

                audio_url = hit.get("audio") or hit.get("download")
                if not audio_url:
                    continue

                local_path = os.path.join(TEMP_DIR, filename)

                print(f"📥 [Download] Завантаження: {raw_title} ({hit.get('duration', 0)} сек)...")
                audio_data = requests.get(audio_url, timeout=30).content
                with open(local_path, "wb") as f:
                    f.write(audio_data)

                if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                    downloaded_tracks.append({
                        'local_path': local_path,
                        'filename': filename,
                        'title': raw_title
                    })

        except Exception as err:
            print(f"⚠️ Помилка під час пошуку/завантаження за тегом '{keyword}': {err}")

    return downloaded_tracks


def upload_track_to_drive(drive_service, folder_id, local_path, filename):
    """Завантажує файл у папку Google Диска."""
    try:
        file_metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        media = MediaFileUpload(local_path, mimetype='audio/mpeg', resumable=True)

        uploaded = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name'
        ).execute()

        print(f"☁️ ✅ [Upload Successful] Файл додано на Google Диск: {uploaded.get('name')} (ID: {uploaded.get('id')})")
        return True
    except Exception as e:
        print(f"❌ [Upload Failed] Помилка завантаження {filename}: {e}")
        return False


def run_music_pool_sync():
    """Головний сценарій оновлення та ротації пулу."""
    print("=" * 80)
    print("🚀 ІНІЦІАЛІЗАЦІЯ СИНХРОНІЗАЦІЇ ТА РОТАЦІЇ МУЗИЧНОГО ПУЛУ (PIXABAY -> GOOGLE DRIVE)")
    print(f"🕒 Час запуску: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    if not MUSIC_FOLDER_ID or MUSIC_FOLDER_ID.startswith("ВАШ_"):
        print("❌ Помилка: Вкажіть MUSIC_FOLDER_ID в змінних оточення або конфігу.")
        return

    drive_service = get_google_drive_service()

    # 1. Отримуємо список існуючих треків
    existing_files = fetch_existing_drive_tracks(drive_service, MUSIC_FOLDER_ID)
    current_count = len(existing_files)
    print(f"📊 Поточна кількість треків у папці Google Диска: {current_count}/{TARGET_POOL_SIZE}")

    existing_names = [f['name'] for f in existing_files]

    # 2. Розраховуємо стратегію ротації
    files_to_delete = []

    if current_count >= TARGET_POOL_SIZE:
        print(f"🔄 Пул заповнений! Активуємо РОТАЦІЮ: видаляємо {ROTATE_COUNT} найстаріших треків...")
        # Вибираємо найстаріші файли (вони вже відсортовані за createdTime asc)
        files_to_delete = existing_files[:ROTATE_COUNT]
    
    # Видаляємо вибрані застарілі файли
    for f in files_to_delete:
        if delete_drive_file(drive_service, f['id'], f['name']):
            existing_names.remove(f['name'])
            current_count -= 1

    # Розраховуємо, скільки нових треків потрібно завантажити
    needed_count = TARGET_POOL_SIZE - current_count
    if needed_count <= 0:
        print("✨ Пул повністю укомплектований. Оновлення не потрібне.")
        return

    print(f"🎯 Потрібно додати нових треків: {needed_count}")

    # 3. Завантажуємо нові треки з Pixabay
    new_tracks = fetch_tracks_from_pixabay(PIXABAY_API_KEY, needed_count, existing_names)

    # 4. Завантажуємо нові треки на Google Диск
    uploaded_success = 0
    for track in new_tracks:
        if upload_track_to_drive(drive_service, MUSIC_FOLDER_ID, track['local_path'], track['filename']):
            uploaded_success += 1

    print(f"\n🎉 СИНХРОНІЗАЦІЮ ЗАВЕРШЕНО! Успішно додано {uploaded_success} нових треків.")

    # 5. Очищення тимчасової папки
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)


if __name__ == "__main__":
    run_music_pool_sync()
