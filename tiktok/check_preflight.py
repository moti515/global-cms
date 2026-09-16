import os
import sys
import requests

# Імпорт легких модулів авторизації
from auth_tiktok import get_gdrive_service
from config_tiktok import FOLDER_INPUT_ID, FOLDER_TRASH_ID


def check_tiktok_authorization():
    """Перевіряє наявність токена та можливість з'єднання з TikTok API."""
    token_path = "tiktok_tokens.json"
    if not os.path.exists(token_path):
        print("❌ PRE-FLIGHT ERROR: Файл tiktok_tokens.json відсутній!")
        return False

    client_key = os.environ.get("CLIENT_KEY_TIKTOK")
    client_secret = os.environ.get("CLIENT_SECRET_TIKTOK")

    if not client_key or not client_secret:
        print(
            "❌ PRE-FLIGHT ERROR: Відсутні CLIENT_KEY_TIKTOK або CLIENT_SECRET_TIKTOK у Secrets!"
        )
        return False

    # Спроба прочитати токен
    try:
        import json

        with open(token_path, "r", encoding="utf-8") as f:
            tokens = json.load(f)

        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print("❌ PRE-FLIGHT ERROR: refresh_token відсутній у токенах!")
            return False

        # Спроба оновити access_token через TikTok API для перевірки авторизації
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
            print("✅ PRE-FLIGHT: Авторизація TikTok успішна! Токен валідний.")
            # Зберігаємо новий access_token
            tokens["access_token"] = res_data["access_token"]
            if "refresh_token" in res_data:
                tokens["refresh_token"] = res_data["refresh_token"]
            with open(token_path, "w", encoding="utf-8") as f:
                json.dump(tokens, f, indent=4)
            return True
        else:
            print(
                f"❌ PRE-FLIGHT ERROR: Помилка оновлення токена TikTok (HTTP {response.status_code}): {res_data}"
            )
            return False

    except Exception as e:
        print(f"❌ PRE-FLIGHT ERROR: Виняток під час перевірки TikTok API: {e}")
        return False


def check_gdrive_has_files():
    """Перевіряє, чи є файли для обробки у папці Google Drive."""
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
            f
            for f in results.get("files", [])
            if f["id"] != FOLDER_TRASH_ID
        ]
        if not files:
            print(
                "☕ PRE-FLIGHT: Папка Google Диску порожня. Скасовуємо подальше виконання."
            )
            sys.exit(0)  # Успішний вихід без виконання важких кроків
        print(f"✅ PRE-FLIGHT: Знайдено файлів у Google Диску: {len(files)}")
        return True
    except Exception as e:
        print(f"❌ PRE-FLIGHT ERROR: Помилка доступу до Google Диску: {e}")
        return False


def main():
    print("🔍 [PRE-FLIGHT CHECK] Старт швидкої перевірки умов публікації...")

    # 1. Перевірка Google Диску
    check_gdrive_has_files()

    # 2. Перевірка TikTok API
    if not check_tiktok_authorization():
        sys.exit(
            "❌ АВАРІЙНЕ ЗАВЕРШЕННЯ PRE-FLIGHT: Доступ до TikTok API відхилено або токен невалідний."
        )

    print(
        "🚀 PRE-FLIGHT CHECK успішно пройдено! Переходимо до розгортання важких залежностей та обробки медіа."
    )


if __name__ == "__main__":
    main()
