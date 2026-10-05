"""
========================================================================================
 🛠 УТИЛІТИ ТА ХЕЛПЕРІ ПРОЄКТУ (utils_meb_instagram_story.py)
========================================================================================
 Набір допоміжних функцій: валідація файлових імен, ротація мов, парсинг дат/локацій,
 взаємодія з Meta API та безпечне очищення тимчасових директорій.
========================================================================================
"""

import os
import re
import time
import json
import shutil
import requests
from datetime import datetime

# Імпорт перевірки контейнера з менеджера сервісів
try:
    from services_manager_meb_instagram_story import wait_for_meta_container
except ImportError:
    from mebli_instagram_story.services_manager_meb_instagram_story import wait_for_meta_container


def sanitize_filename(filename: str) -> str:
    """
    Замінює кирилицю, пробіли та спецсимволи на дефіси, 
    зберігаючи розширення, щоб уникнути збоїв у FFmpeg/PIL.
    """
    name, ext = os.path.splitext(filename)
    sanitized_name = re.sub(r'[^a-zA-Z0-9_\-]', '-', name)
    sanitized_name = re.sub(r'-+', '-', sanitized_name).strip('-')
    
    if not sanitized_name:
        sanitized_name = f"media_{int(time.time())}"
        
    return f"{sanitized_name}{ext.lower()}"


def rotate_language(lang_value: str):
    """
    Визначає поточний індекс мови та повертає значення для наступного раунду (UK -> EN -> DE -> UK).
    """
    lang_clean = str(lang_value).strip().upper()
    if any(x in lang_clean for x in ["EN", "ENG", "АНГЛ", "ENGLISH"]):
        return 1, "DE"
    elif any(x in lang_clean for x in ["DE", "GER", "НІМ", "DEUTSCH"]):
        return 2, "UK"
    else:
        return 0, "EN"


def parse_year(date_str: str) -> str:
    """
    Безпечно витягує рік із рядка дати (формат ДД.ММ.РРРР).
    """
    try:
        if date_str and len(date_str.split(".")) == 3:
            return date_str.split(".")[2]
        return str(datetime.now().year)
    except Exception:
        return str(datetime.now().year)


def parse_location(location_str: str, lang_idx: int) -> str:
    """
    Безпечно парсить JSON локації або повертає рядок-фолбек.
    """
    if not location_str:
        return ""
    try:
        loc_json = json.loads(location_str)
        if isinstance(loc_json, dict):
            return loc_json.get(str(lang_idx), loc_json.get("0", ""))
        return str(location_str)
    except Exception:
        return str(location_str)


def publish_story_to_meta(ig_user_id: str, meta_access_token: str, pub_url: str, is_video: bool):
    """
    Відправляє медіафайл у Meta API (Створення контейнера -> Очікування готовності -> Публікація).
    Використовує актуальну версію Graph API v21.0.
    Повертає кортеж: (bool_успіх, string_id_або_помилка)
    """
    param_type = "video_url" if is_video else "image_url"
    payload = {
        "media_type": "STORIES",
        param_type: pub_url,
        "access_token": meta_access_token
    }
    
    try:
        # 1️⃣ Створення медіа-контейнера в Instagram
        container_endpoint = f"https://graph.facebook.com/v21.0/{ig_user_id}/media"
        response = requests.post(container_endpoint, data=payload, timeout=(10, 120))
        res = response.json()
        
        if not res or "id" not in res:
            return False, f"Помилка створення контейнера сторіз: {res}"
            
        creation_id = res["id"]
        
        # 2️⃣ Очікування обробки відео/фото серверами Meta
        if not wait_for_meta_container(creation_id, meta_access_token):
            return False, "Контейнер медіафайлу не перейшов у стан готовності (Таймаут/Помилка Meta)."
            
        # 3️⃣ Фінальна публікація контейнера
        publish_endpoint = f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish"
        pub_response = requests.post(
            publish_endpoint, 
            data={"creation_id": creation_id, "access_token": meta_access_token}, 
            timeout=(10, 120)
        )
        publish_res = pub_response.json()
        
        if "id" in publish_res:
            return True, publish_res["id"]
        else:
            return False, f"Помилка публікації сторіз в Meta API: {publish_res}"
            
    except requests.exceptions.Timeout:
        return False, "Перевищено час очікування відповіді від Meta API (Timeout)."
    except Exception as e:
        return False, f"Критичний збій під час запиту до Meta API: {e}"


def cleanup_temp_dir(directory: str = "temp_mebli"):
    """
    Безпечно видаляє тимчасові файли та очищає робочу папку після завершення роботи скрипта.
    """
    if os.path.exists(directory):
        try:
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            print(f"🧹 Тимчасова папка '{directory}' успішно очищена.")
        except Exception as e:
            print(f"⚠️ Не вдалося повністю очистити '{directory}': {e}")
