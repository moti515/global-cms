import os
import re
import time
import json
import requests
from datetime import datetime

def sanitize_filename(filename):
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

def rotate_language(lang_value):
    """
    Визначає поточний індекс мови та повертає значення для наступного раунду.
    """
    lang_clean = lang_value.strip().upper()
    if any(x in lang_clean for x in ["EN", "ENG", "АНГЛ", "ENGLISH"]):
        return 1, "DE"
    elif any(x in lang_clean for x in ["DE", "GER", "НІМ", "DEUTSCH"]):
        return 2, "UK"
    else:
        return 0, "EN"

def parse_year(date_str):
    """
    Безпечно витягує рік із рядка дати.
    """
    try:
        return date_str.split(".")[2] if date_str and len(date_str.split(".")) == 3 else str(datetime.now().year)
    except Exception:
        return str(datetime.now().year)

def parse_location(location_str, lang_idx):
    """
    Безпечно парсить JSON локації або повертає рядок-фолбек.
    """
    try:
        loc_json = json.loads(location_str)
        return loc_json.get(str(lang_idx), loc_json.get("0", ""))
    except Exception:
        return location_str

def publish_story_to_meta(ig_user_id, meta_access_token, pub_url, is_video):
    """
    Відправляє медіафайл у Meta API (Створення контейнера -> Очікування готовності -> Публікація).
    Повертає кортеж: (bool_успіх, string_id_або_помилка)
    """
    from services_manager_meb_instagram_story import wait_for_meta_container

    param_type = "video_url" if is_video else "image_url"
    payload = {
        "media_type": "STORIES",
        param_type: pub_url,
        "access_token": meta_access_token
    }
    
    try:
        res = requests.post(f"https://graph.facebook.com/v19.0/{ig_user_id}/media", data=payload).json()
        if not res or "id" not in res:
            return False, f"Помилка створення контейнера сторіз: {res}"
            
        creation_id = res["id"]
        if not wait_for_meta_container(creation_id, meta_access_token):
            return False, "Контейнер медіафайлу не перейшов у стан готовності (Таймаут)."
            
        publish_res = requests.post(f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish", data={
            "creation_id": creation_id, 
            "access_token": meta_access_token
        }).json()
        
        if "id" in publish_res:
            return True, publish_res["id"]
        else:
            return False, f"Помилка публікації сторіз в Meta API: {publish_res}"
            
    except Exception as e:
        return False, f"Критичний збій під час запиту до Meta API: {e}"
