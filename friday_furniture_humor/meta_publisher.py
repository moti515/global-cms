import time
import requests
from config import IG_USER_ID, FB_PAGE_ID, META_ACCESS_TOKEN


def wait_for_instagram_media(container_id, access_token, max_retries=15, delay=10):
    """Циклічно перевіряє статус обробки контейнера в Instagram Graph API."""
    url = f"https://graph.facebook.com/v19.0/{container_id}"
    params = {"fields": "status_code", "access_token": access_token}
    
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, params=params).json()
            status = response.get("status_code")
            
            if status == "FINISHED":
                print("✅ Відео успішно оброблено Instagram і готове до публікації!")
                return True
            elif status == "ERROR":
                print(f"❌ Помилка обробки відео на стороні Instagram: {response}")
                return False
            elif status in ["IN_PROGRESS", "CREATING"]:
                print(f"⏳ Instagram обробляє відео (Статус: {status}). Чекаємо {delay} сек... ({attempt}/{max_retries})")
                time.sleep(delay)
            else:
                time.sleep(delay)
        except Exception as e:
            print(f"⚠️ Помилка запиту статусу: {e}")
            time.sleep(delay)
            
    print("❌ Таймаут очікування обробки відео в Instagram.")
    return False


def publish_to_meta_platforms(media_url, media_type, is_story=False, caption="", local_file_path=None):
    """Надсилає медіаконтент у Instagram та Facebook через Meta Graph API."""
    if not IG_USER_ID or not FB_PAGE_ID or not META_ACCESS_TOKEN:
        raise ValueError("❌ Відсутні обов'язкові змінні оточення: IG_USER_ID, FB_PAGE_ID або META_ACCESS_TOKEN!")
        
    print(f"📤 Відправка контенту в Instagram (ID: {IG_USER_ID})...")
    ig_url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    ig_payload = {
        "access_token": META_ACCESS_TOKEN,
        "media_type": "STORIES" if is_story else ("REELS" if media_type == "video" else "IMAGE")
    }
    
    if media_type == "video": 
        ig_payload["video_url"] = media_url
    else: 
        ig_payload["image_url"] = media_url
        
    if not is_story and caption: 
        ig_payload["caption"] = caption

    ig_res = requests.post(ig_url, data=ig_payload).json()
    if "id" not in ig_res: 
        raise ValueError(f"❌ Помилка створення контейнера Instagram: {ig_res}")
    
    ig_creation_id = ig_res["id"]
    max_retries = 18 if media_type == "video" else 5
    check_delay = 10 if media_type == "video" else 3
    
    is_ready = wait_for_instagram_media(ig_creation_id, META_ACCESS_TOKEN, max_retries=max_retries, delay=check_delay)
    if not is_ready:
        raise ValueError("❌ Медіафайл не готовий до публікації в Instagram (Таймаут).")
        
    ig_pub_res = requests.post(
        f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish", 
        data={"creation_id": ig_creation_id, "access_token": META_ACCESS_TOKEN}
    ).json()
    
    if "id" not in ig_pub_res:
        raise ValueError(f"❌ Помилка публікації в Instagram: {ig_pub_res}")
        
    print(f"✅ [Instagram] Опубліковано! ID: {ig_pub_res['id']}")

    if not is_story:
        print("📤 Дублювання поста на Сторінку Facebook...")
        if media_type == "video":
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/videos"
            fb_payload = {
                "file_url": media_url,
                "description": caption,
                "access_token": META_ACCESS_TOKEN
            }
        else:
            fb_url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/photos"
            fb_payload = {
                "url": media_url,
                "caption": caption,   
                "access_token": META_ACCESS_TOKEN
            }
            
        try:
            fb_res = requests.post(fb_url, data=fb_payload).json()
            if "id" in fb_res or "post_id" in fb_res:
                print(f"✅ [Facebook Page] Продубльовано! ID: {fb_res.get('id', fb_res.get('post_id'))}")
            else:
                print(f"⚠️ [Facebook Page] Помилка або нестандартна відповідь: {fb_res}")
        except Exception as e:
            print(f"❌ Не вдалося надіслати пост у Facebook: {e}")
    else:
        print("ℹ️ [Facebook] Публікація Сторіз через API пропускається.")
