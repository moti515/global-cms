import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

from auth_tiktok import get_gdrive_service
from config_tiktok import FOLDER_INPUT_ID, FOLDER_TRASH_ID
from drive_manager_tiktok import count_total_files


def set_github_output(key, value):
    """Записує змінну у GITHUB_OUTPUT для використання у кроках GitHub Actions."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def check_cron_schedule(service, run_mode):
    """1. Перевіряє розклад публікацій у режимі cron."""
    if run_mode != "cron":
        return True

    print("🔍 [PRE-FLIGHT] Перевірка графіку публікації (режим CRON)...")
    try:
        total_files = count_total_files(service)
        berlin_hour = datetime.now(ZoneInfo("Europe/Berlin")).hour
        print(f"📊 На Диску знайдено файлів: {total_files} | Поточна година в DE: {berlin_hour}")

        allowed_hours = []
        if total_files <= 1000:
            allowed_hours = [11]
        elif total_files <= 2000:
            allowed_hours = [11, 17]
        elif total_files <= 3000:
            allowed_hours = [5, 11, 17]
        else:
            allowed_hours = [5, 11, 17, 23]

        if berlin_hour not in allowed_hours:
            print(f"☕ [ШТАТНИЙ ПРОПУСК] Для {total_files} файлів година {berlin_hour} не передбачена графіком.")
            return False

        print("✅ [PRE-FLIGHT] Умови графіку виконано!")
        return True
    except Exception as e:
        print(f"❌ PRE-FLIGHT ERROR: Помилка перевірки графіку: {e}")
        return False


def check_gdrive_has_files(service):
    """2. Перевіряє наявність файлів у папці Google Drive."""
    try:
        results = (
            service.files()
            .list(
                q=f"'{FOLDER_INPUT_ID}' in parents and trashed = false",
                fields="files(id, name)",
                pageSize=10,
            )
            .execute()
        )
        files = [f for f in results.get("files", []) if f["id"] != FOLDER_TRASH_ID]
        if not files:
            print("☕ [ШТАТНИЙ ПРОПУСК] Папка Google Диску порожня. Скасовуємо подальше виконання.")
            return False
        print(f"✅ PRE-FLIGHT: Знайдено файлів у Google Диску: {len(files)}")
        return True
    except Exception as e:
        print(f"❌ PRE-FLIGHT ERROR: Помилка доступу до Google Диску: {e}")
        return False


def get_valid_tiktok_token():
    """3. Оновлює та повертає валідний access_token TikTok."""
    token_path = "tiktok_tokens.json"
    if not os.path.exists(token_path):
        print("❌ PRE-FLIGHT ERROR: Файл tiktok_tokens.json відсутній!")
        return None

    client_key = os.environ.get("CLIENT_KEY_TIKTOK")
    client_secret = os.environ.get("CLIENT_SECRET_TIKTOK")

    if not client_key or not client_secret:
        print("❌ PRE-FLIGHT ERROR: Відсутні keys у Secrets!")
        return None

    try:
        with open(token_path, "r", encoding="utf-8") as f:
            tokens = json.load(f)

        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print("❌ PRE-FLIGHT ERROR: refresh_token відсутній у токенах!")
            return None

        url = "https://open.tiktokapis.com/v2/oauth/token/"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        response = requests.post(url, headers=headers, data=data, timeout=10)
        res_data = response.json()

        if response.status_code == 200 and "access_token" in res_data:
            tokens["access_token"] = res_data["access_token"]
            if "refresh_token" in res_data:
                tokens["refresh_token"] = res_data["refresh_token"]
            with open(token_path, "w", encoding="utf-8") as f:
                json.dump(tokens, f, indent=4)
            print("✅ PRE-FLIGHT: Авторизація TikTok успішна!")
            return res_data["access_token"]
        else:
            print(f"❌ PRE-FLIGHT ERROR: Помилка токена TikTok: {res_data}")
            return None
    except Exception as e:
        print(f"❌ PRE-FLIGHT ERROR: Виняток під час оновлення токена: {e}")
        return None


def test_tiktok_account_privacy_mode(access_token):
    """4. Перевіряє, чи акаунт TikTok знаходиться в ПРИВАТНОМУ режимі."""
    url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    dummy_payload = {
        "post_info": {
            "title": "#preflight_test",
            "privacy_level": "SELF_ONLY",
            "disable_duet": True,
            "disable_comment": True,
            "disable_stitch": True,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": 1048576,
            "chunk_size": 1048576,
            "total_chunk_count": 1,
        },
    }

    try:
        response = requests.post(url, headers=headers, json=dummy_payload, timeout=10)
        res_data = response.json()

        error_info = res_data.get("error", {})
        error_code = error_info.get("code")

        if error_code == "unaudited_client_can_only_post_to_private_accounts":
            print("⚠️ PRE-FLIGHT CANCELLED: Акаунт TikTok зараз у ПУБЛІЧНОМУ режимі!")
            print("👉 Переключіть акаунт у приватний режим у додатку TikTok.")
            return False

        if response.status_code == 200 and error_code == "ok":
            print("✅ PRE-FLIGHT: Акаунт TikTok у приватній формі! Публікація дозволена.")
            return True

        print(f"❌ PRE-FLIGHT ERROR: TikTok відхилив тестовий запит (Код: {error_code}): {error_info.get('message')}")
        return False

    except Exception as e:
        print(f"❌ PRE-FLIGHT ERROR: Помилка під час тестового запиту TikTok: {e}")
        return False


def main():
    run_mode = os.environ.get("RUN_MODE", "manual")
    print(f"🔍 [PRE-FLIGHT CHECK] Старт швидкої перевірки умов (режим: {run_mode.upper()})...")

    # Авторизація в GDrive
    try:
        service = get_gdrive_service()
    except Exception as e:
        print(f"❌ PRE-FLIGHT ERROR: Помилка доступу до GDrive: {e}")
        set_github_output("should_run", "false")
        sys.exit(1)

    # 1. Швидка перевірка графіку публікацій (якщо CRON)
    if not check_cron_schedule(service, run_mode):
        set_github_output("should_run", "false")
        sys.exit(0)  # Штатний пропуск (зелена галочка в GitHub)

    # 2. Перевірка файлів у Google Drive
    if not check_gdrive_has_files(service):
        set_github_output("should_run", "false")
        sys.exit(0)  # Штатний пропуск (зелена галочка в GitHub)

    # 3. Перевірка та оновлення токенів TikTok
    access_token = get_valid_tiktok_token()
    if not access_token:
        set_github_output("should_run", "false")
        sys.exit(1)  # Помилка авторизації (червоний хрестик)

    # 4. Перевірка режиму акаунту (Public / Private)
    if not test_tiktok_account_privacy_mode(access_token):
        set_github_output("should_run", "false")
        sys.exit(1)  # Помилка налаштування акаунту (червоний хрестик)

    # Все пройшло успішно -> дозволяємо запуск наступних важких кроків
    set_github_output("should_run", "true")
    print("🚀 PRE-FLIGHT CHECK успішно пройдено! Переходимо до обробки медіа...")


if __name__ == "__main__":
    main()
