#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
========================================================================================
🎵 АВТОНОМНИЙ МЕНЕДЖЕР ТА РОТАТОР ФОНОВОЇ МУЗИКИ (Jamendo API -> Google Drive)
========================================================================================
Скрипт підтримує компактний пул фонових треків (21 шт) у Google Диску для
автоматичного накладання у Facebook Reels та TikTok.

Основні функції:
1. Контролює розмір пулу музичних файлів (TARGET_POOL_SIZE = 21).
2. Автоматично видаляє найстаріші треки (ROTATE_COUNT = 3) та замінює їх свіжими з Jamendo API.
3. Використовує інструментальну фонову музику під меблеве виробництво та сторітелінг.
========================================================================================
"""

import os
import sys
import random
import re
import shutil
import json
import requests
from datetime import datetime

# Google API Client
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ======================================================================================
# ⚙️ НАЛАШТУВАННЯ ТА КОНФІГУРАЦІЯ
# ======================================================================================

# ID папки на Google Диску для зберігання фонової музики
MUSIC_FOLDER_ID = os.environ.get("MUSIC_FOLDER_ID")

# Ключ Jamendo API (JAMENDO_CLIENT_ID) або Pixabay (якщо використовується стара назва)
JAMENDO_CLIENT_ID = os.environ.get("JAMENDO_CLIENT_ID") or os.environ.get("PIXABAY_API_KEY")

# Параметри пулу
TARGET_POOL_SIZE = 21       # Оптимальна кількість треків у папці
ROTATE_COUNT = 3            # Скільки найстаріших треків замінювати при кожному запуску ротації
TEMP_DIR = "temp_music_sync"

# 🎯 Тематичні теги для пошуку фонової інструментальної музики
THEME_TAGS = [
    "lofi", "acoustic", "lounge", "chillhop", "ambient",
    "chillout", "travel", "vlog", "aesthetic", "lifestyle"
]


def get_google_drive_service():
    """Ініціалізація сервісу Google Drive API з урахуванням секретів GitHub."""
    creds = None

    sa_key_raw = os.environ.get("GDRIVE_SERVICE_ACCOUNT_KEY") or os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if sa_key_raw:
        try:
            info = json.loads(sa_key_raw)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=['https://www.googleapis.com/auth/drive']
            )
            print("🔑 [Auth] Успішна авторизація через Service Account Key.")
        except Exception as e:
            print(f"⚠️ [Auth] Помилка парсингу Service Account JSON: {e}")

    if not creds:
        client_id = os.environ.get("GDRIVE_CLIENT_ID")
        client_secret = os.environ.get("GDRIVE_CLIENT_SECRET")
        refresh_token = os.environ.get("GDRIVE_REFRESH_TOKEN")

        if client_id and client_secret and refresh_token:
            try:
                creds = Credentials(
                    None,
                    refresh_token=refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=['https://www.googleapis.com/auth/drive']
                )
                print("🔑 [Auth] Успішна авторизація через OAuth Refresh Token.")
            except Exception as e:
                print(f"⚠️ [Auth] Помилка авторизації через OAuth: {e}")

    if not creds and os.path.exists("credentials.json"):
        try:
            creds = service_account.Credentials.from_service_account_file(
                "credentials.json", scopes=['https://www.googleapis.com/auth/drive']
            )
            print("🔑 [Auth] Авторизація через локальний файл credentials.json.")
        except Exception as e:
            print(f"⚠️ [Auth] Помилка локального файлу credentials.json: {e}")

    if not creds:
        print("❌ [Auth] Не знайдено дійсної конфігурації для Google Drive API.")
        sys.exit(1)

    return build('drive', 'v3', credentials=creds)


def sanitize_filename(name: str) -> str:
    """Очищає назву файлу від спецсимволів."""
    clean = re.sub(r'[^\w\s-]', '', name).strip()
    return re.sub(r'[-\s]+', '_', clean)


def fetch_existing_drive_tracks(drive_service, folder_id):
    """Отримує список MP3-файлів у папці Google Диска, відсортованих за датою."""
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


def fetch_tracks_from_jamendo(client_id, count_needed, existing_names):
    """
    Шукає та завантажує інструментальні MP3-треки через Jamendo API.
    """
    if not client_id:
        print("⚠️ [Jamendo] Відсутній JAMENDO_CLIENT_ID у змінних оточення.")
        return []

    client_id = client_id.strip().strip("'").strip('"')
    masked = f"{client_id[:4]}...{client_id[-4:]}" if len(client_id) > 8 else "***"
    print(f"🔑 [Jamendo API] Використовується Client ID: {masked}")

    random.shuffle(THEME_TAGS)
    downloaded_tracks = []
    os.makedirs(TEMP_DIR, exist_ok=True)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    for tag in THEME_TAGS:
        if len(downloaded_tracks) >= count_needed:
            break

        print(f"🔎 [Jamendo API] Пошук інструментальних треків за тегом: '{tag}'...")
        url = "https://api.jamendo.com/v3.0/tracks/"
        params = {
            "client_id": client_id,
            "format": "json",
            "limit": 20,
            "tags": tag,
            "vocalinstrumental": "instrumental",  # Тільки фонова інструментальна музика
            "audioformat": "mp32",                 # Якісний MP3
            "order": "popularity_week"
        }

        try:
            resp = session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                print(f"⚠️ Jamendo API повернув статус {resp.status_code}: {resp.text[:200]}")
                continue

            data = resp.json()
            results = data.get("results", [])
            random.shuffle(results)

            for hit in results:
                if len(downloaded_tracks) >= count_needed:
                    break

                track_id = hit.get("id")
                raw_title = hit.get("name", f"jamendo_{track_id}")
                clean_title = sanitize_filename(raw_title)
                filename = f"jamendo_{track_id}_{clean_title}.mp3"

                if any(str(track_id) in ex_name for ex_name in existing_names):
                    continue

                audio_url = hit.get("audio")
                if not audio_url:
                    continue

                local_path = os.path.join(TEMP_DIR, filename)
                duration = hit.get('duration', 0)

                print(f"📥 [Download] Завантаження: {raw_title} ({duration} сек)...")
                audio_data = session.get(audio_url, timeout=30).content
                with open(local_path, "wb") as f:
                    f.write(audio_data)

                if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                    downloaded_tracks.append({
                        'local_path': local_path,
                        'filename': filename,
                        'title': raw_title
                    })

        except Exception as err:
            print(f"⚠️ Помилка завантаження за тегом '{tag}': {err}")

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

        print(f"☁️ ✅ [Upload Successful] Додано на Google Диск: {uploaded.get('name')} (ID: {uploaded.get('id')})")
        return True
    except Exception as e:
        print(f"❌ [Upload Failed] Помилка завантаження {filename}: {e}")
        return False


def run_music_pool_sync():
    """Головний сценарій оновлення та ротації пулу."""
    print("=" * 80)
    print("🚀 ІНІЦІАЛІЗАЦІЯ СИНХРОНІЗАЦІЇ ТА РОТАЦІЇ МУЗИЧНОГО ПУЛУ (JAMENDO -> GOOGLE DRIVE)")
    print(f"🕒 Час запуску: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    if not MUSIC_FOLDER_ID:
        print("❌ Помилка: Вкажіть MUSIC_FOLDER_ID в змінних оточення GitHub Secrets.")
        return

    drive_service = get_google_drive_service()

    # 1. Отримуємо існуючі треки
    existing_files = fetch_existing_drive_tracks(drive_service, MUSIC_FOLDER_ID)
    current_count = len(existing_files)
    print(f"📊 Поточна кількість треків у папці Google Диска: {current_count}/{TARGET_POOL_SIZE}")

    existing_names = [f['name'] for f in existing_files]

    # 2. Ротація
    files_to_delete = []
    if current_count >= TARGET_POOL_SIZE:
        print(f"🔄 Пул заповнений! РОТАЦІЯ: видаляємо {ROTATE_COUNT} найстаріших треків...")
        files_to_delete = existing_files[:ROTATE_COUNT]

    for f in files_to_delete:
        if delete_drive_file(drive_service, f['id'], f['name']):
            existing_names.remove(f['name'])
            current_count -= 1

    needed_count = TARGET_POOL_SIZE - current_count
    if needed_count <= 0:
        print("✨ Пул повністю укомплектований. Оновлення не потрібне.")
        return

    print(f"🎯 Потрібно додати нових треків: {needed_count}")

    # 3. Завантаження з Jamendo API
    new_tracks = fetch_tracks_from_jamendo(JAMENDO_CLIENT_ID, needed_count, existing_names)

    # 4. Завантаження на Google Диск
    uploaded_success = 0
    for track in new_tracks:
        if upload_track_to_drive(drive_service, MUSIC_FOLDER_ID, track['local_path'], track['filename']):
            uploaded_success += 1

    print(f"\n🎉 СИНХРОНІЗАЦІЮ ЗАВЕРШЕНО! Успішно додано {uploaded_success} нових треків.")

    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)


if __name__ == "__main__":
    run_music_pool_sync()
