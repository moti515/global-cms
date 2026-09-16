import json
import os
import sys
import requests

from auth_tiktok import get_gdrive_service
from config_tiktok import FOLDER_INPUT_ID, FOLDER_TRASH_ID


def check_gdrive_has_files():
    """1. Перевіряє наявність файлів у папці Google Drive."""
    try:
        service = get_gdrive_service()
        results = (
            service.files()
            .list(
                q=f"'{FOLDER_INPUT_ID}' in parents and trashed = false",
                fields="files(id, name)",
                pageSize=10,
            )
            .execute()
        )
        files = [
            f for f in results.get("files", []) if f["id"] != FOLDER_TRASH_ID
        ]
        if not files:
            print(
                "☕ PRE-FLIGHT: Папка Google Диску порожня. Скасовуємо подальше виконання."
            )
            sys.exit(0)
        print(f"✅ PRE-FLIGHT: Знайдено файлів у Google Диску: {len(files)}")
        return True
    except Exception as e:
        print(f"❌ PRE-FLIGHT ERROR: Помилка доступу до Google Диску: {e}")
        return False


def get_valid_tiktok_token():
    """2. Оновлює та повертає валідний access_token TikTok."""
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
    """3. Перевіряє, чи акаунт TikTok знаходиться в ПРИВАТНОМУ режимі (Dummy Init Check)."""
    url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    # Відправляємо тестовий мета-запит на публікацію (1 МБ фейковий розмір)
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
        response = requests.post(
            url, headers=headers, json=dummy_payload, timeout=10
        )
        res_data = response.json()

        error_info = res_data.get("error", {})
        error_code = error_info.get("code")

        if error_code == "unaudited_client_can_only_post_to_private_accounts":
            print(
                "⚠️ PRE-FLIGHT CANCELLED: Акаунт TikTok зараз у ПУБЛІЧНОМУ режимі!"
            )
            print(
                "👉 Переключіть акаунт у приватний режим у додатку TikTok (Налаштування -> Конфіденційність -> Приватний акаунт), щоб дозволити завантаження."
            )
            return False

        if response.status_code == 200 and error_code == "ok":
            print(
                "✅ PRE-FLIGHT: Акаунт TikTok у приватній формі! Публікація дозволена."
            )
            return True

        print(
            f"❌ PRE-FLIGHT ERROR: TikTok відхилив тестовий запит (Код: {error_code}): {error_info.get('message')}"
        )
        return False

    except Exception as e:
        print(f"❌ PRE-FLIGHT ERROR: Помилка під час тестового запиту TikTok: {e}")
        return False


def main():
    print("🔍 [PRE-FLIGHT CHECK] Старт швидкої перевірки умов публікації...")

    # 1. Перевірка файлів у Google Drive
    check_gdrive_has_files()

    # 2. Перевірка токенів TikTok
    access_token = get_valid_tiktok_token()
    if not access_token:
        sys.exit(1)

    # 3. Перевірка режиму акаунту (Public / Private)
    if not test_tiktok_account_privacy_mode(access_token):
        print(
            "🛑 Процес зупинено за 3 секунди без скачування та обробки медіафайлів."
        )
        sys.exit(1)

    print(
        "🚀 PRE-FLIGHT CHECK успішно пройдено! Переходимо до обробки медіа..."
    )


if __name__ == "__main__":
    main()
