"""
========================================================================================
🗑️ ОЧИЩЕННЯ СТАРИХ FACEBOOK REELS (delete_old_reels.py)
========================================================================================
 Автономний скрипт для пошуку та видалення Facebook Reels, створених раніше ніж N днів тому.
 Призначений для регулярного запуску за розкладом (GitHub Actions Cron).
========================================================================================
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone
import requests

# Спроба зчитати змінні з конфігу або середовища
try:
    import config_meb_insta_story as config

    PAGE_ID = getattr(config, "FB_PAGE_ID", None) or os.environ.get(
        "FB_PAGE_ID"
    )
    ACCESS_TOKEN = getattr(config, "META_ACCESS_TOKEN", None) or os.environ.get(
        "META_ACCESS_TOKEN"
    )
except ImportError:
    PAGE_ID = os.environ.get("FB_PAGE_ID")
    ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")


def delete_old_facebook_reels(
    page_id: str, access_token: str, days_old: int = 30
):
    """Шукає та видаляє Facebook Reels сторінки, які старіші за вказану кількість днів."""
    if not page_id or not access_token:
        print(
            "❌ [FB Reels Cleaner] Відсутній FB_PAGE_ID або META_ACCESS_TOKEN у змінних середовища."
        )
        return False

    # Обчислюємо граничну дату
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)
    print(
        f"🧹 [FB Reels Cleaner] Пошук Reels, створених до {cutoff_date.strftime('%Y-%m-%d %H:%M:%S UTC')} ({days_old} дн. тому)..."
    )

    url = f"https://graph.facebook.com/v21.0/{page_id}/video_reels"
    params = {
        "access_token": access_token,
        "fields": "id,created_time",
        "limit": 25,
    }

    deleted_count = 0

    try:
        while url:
            response = requests.get(url, params=params, timeout=30)
            res = response.json()

            if "error" in res:
                print(
                    f"⚠️ Помилка отримання списку Reels від Meta: {res['error']}"
                )
                break

            videos = res.get("data", [])
            if not videos:
                print("ℹ️ [FB Reels Cleaner] Не знайдено жодного Reel.")
                break

            for video in videos:
                video_id = video.get("id")
                created_str = video.get("created_time")

                if not created_str:
                    continue

                # Парсимо ISO-дату від Meta (наприклад: 2026-08-15T10:20:30+0000)
                clean_iso = created_str.replace("+0000", "+00:00")
                created_time = datetime.fromisoformat(clean_iso)

                # Перевіряємо, чи відео старіше за граничну дату
                if created_time < cutoff_date:
                    delete_url = f"https://graph.facebook.com/v21.0/{video_id}"
                    del_res = requests.delete(
                        delete_url,
                        data={"access_token": access_token},
                        timeout=30,
                    ).json()

                    if del_res.get("success"):
                        print(
                            f"🗑️ [FB Reels Cleaner] Успішно видалено старий Reel ID: {video_id} (створено: {created_str[:10]})"
                        )
                        deleted_count += 1
                    else:
                        print(
                            f"⚠️ Не вдалося видалити Reel ID {video_id}: {del_res}"
                        )

                    time.sleep(1)  # Затримка для Rate Limits API

            # Перехід до наступної сторінки (пагінація)
            url = res.get("paging", {}).get("next")
            params = {}  # Параметри вже запечені в посиланні `next`

        print(
            f"✅ [FB Reels Cleaner] Очищення завершено. Всього видалено Reels: {deleted_count}"
        )
        return True

    except Exception as e:
        print(f"⚠️ Критичний збій під час очищення Facebook Reels: {e}")
        return False


if __name__ == "__main__":
    # Можна змінити кількість днів через змінну середовища DAYS_TO_KEEP (за замовчуванням 30)
    days_to_keep = int(os.environ.get("DAYS_TO_KEEP", "30"))

    success = delete_old_facebook_reels(
        page_id=PAGE_ID, access_token=ACCESS_TOKEN, days_old=days_to_keep
    )

    if not success:
        sys.exit(1)
