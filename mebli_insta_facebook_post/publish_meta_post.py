import os
import sys
import json
import time
import re  # Додано для роботи з регулярними виразами
import requests
from datetime import datetime
from PIL import Image
from pillow_heif import register_heif_opener

# Обов'язкова реєстрація підтримки форматів HEIF/HEIC для Pillow
register_heif_opener()

# Імпорт власних модулів проєкту
import config_meta_post as config
import media_processor_meta_post as media_processor
import service_manager_meta_post as service_manager

def sanitize_filename(filename):
    """
    Замінює кирилицю, пробіли та спецсимволи на дефіси, 
    зберігаючи розширення, щоб уникнути збоїв у FFmpeg/PIL та Meta API.
    """
    name, ext = os.path.splitext(filename)
    # Замінюємо все, що НЕ є латиницею, цифрою, дефісом чи підкресленням, на дефіс
    sanitized_name = re.sub(r'[^a-zA-Z0-9_\-]', '-', name)
    # Прибираємо подвійні дефіси, якщо вони утворилися
    sanitized_name = re.sub(r'-+', '-', sanitized_name).strip('-')
    
    # Фолбек, якщо ім'я повністю складалося з кирилиці і стало порожнім
    if not sanitized_name:
        sanitized_name = f"media_{int(time.time())}"
        
    return f"{sanitized_name}{ext.lower()}"

def wait_for_meta_container(container_id, access_token):
    """Очікує завершення асинхронної обробки відео/медіа контейнера в Meta API."""
    check_url = f"https://graph.facebook.com/v19.0/{container_id}"
    params = {"fields": "status_code,status", "access_token": access_token}
    for _ in range(30):
        try:
            r = requests.get(check_url, params=params).json()
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

def clean_up_local_files(files):
    """Видаляє всі тимчасові файли на диску хоста (без дублікатів)."""
    for f in set(files):
        if os.path.exists(f):
            try: 
                os.remove(f)
            except: 
                pass

def main():
    if len(sys.argv) < 3:
        print("💡 Необхідно передати параметри. Запуск: python main.py <mode> <tab_name>")
        sys.exit(1)

    mode = sys.argv[1].lower()  
    forced_tab = sys.argv[2]
    
    current_tab = forced_tab if forced_tab else config.TAB_NAME
    print(f"📊 [Режим: {mode.upper()}] Зчитування реєстру '{current_tab}'...")
    
    drive, sheets = service_manager.get_services()

    res = sheets.spreadsheets().values().get(spreadsheetId=config.SPREADSHEET_ID, range=f"'{current_tab}'!A2:I").execute()
    rows = res.get('values', [])
    if not rows:
        print("ℹ️ Реєстр порожній.")
        return

    if mode == "ig_post":
        col_idx = 3
        col_letter = "D"
    elif mode == "fb_post":
        col_idx = 5
        col_letter = "F"
    else:
        print(f"❌ Невідомий або непідтримуваний режим публікації: {mode}")
        sys.exit(1)

    valid_rows = []
    for i, r in enumerate(rows):
        if len(r) >= 3:  
            if r[2].lower() == "temporary": continue
            try:
                val = r[col_idx] if len(r) > col_idx and r[col_idx] else "0"
                counter = int(val)
                valid_rows.append({"row_idx": i + 2, "data": r, "counter": counter})
            except ValueError: continue

    if not valid_rows:
        print("ℹ️ Немає валідних рядків для обробки.")
        return

    # ==========================================
    # 📂 ГРУПУВАННЯ ТА ВИБІР ПУЛУ ДЛЯ ПУБЛІКАЦІЇ
    # ==========================================
    min_counter = min(item["counter"] for item in valid_rows)
    min_pool = [item for item in valid_rows if item["counter"] == min_counter]

    groups = {}
    for item in min_pool:
        data = item["data"]
        group_location_json = data[8] if len(data) > 8 else (data[7] if len(data) > 7 else "")
        group_key = (data[2], data[6] if len(data) > 6 else "", group_location_json)
        groups.setdefault(group_key, []).append(item)

    first_key = list(groups.keys())[0]
    selected_group_items = groups[first_key][:4]
    category_name, target_date, target_loc = first_key
    print(f"📂 Обрано групу: {category_name} (Файлів у пулі: {len(selected_group_items)})")

    # =====================================================================
    # 🌍 УПРАВЛІННЯ МОВАМИ
    # =====================================================================
    lang_value = "UK"
    target_lang_cell = "'⚙️ Налаштування Папок'!F2" if mode == "fb_post" else "'⚙️ Налаштування Папок'!G2"

    try:
        lang_res = sheets.spreadsheets().values().get(spreadsheetId=config.SPREADSHEET_ID, range=target_lang_cell).execute()
        lang_values = lang_res.get('values', [])
        if lang_values and lang_values[0]:
            lang_value = lang_values[0][0].strip().upper()
    except Exception as e:
        print(f"⚠️ Не вдалося прочитати мову з комірки {target_lang_cell}: {e}")

    if lang_value == "EN":
        lang_idx = 1
        lang_flag = "🇬🇧"
        next_lang_value = "DE"
    elif lang_value == "DE":
        lang_idx = 2
        lang_flag = "🇩🇪"
        next_lang_value = "UK"
    else:  # За замовчуванням UK
        lang_idx = 0
        lang_flag = "🇺🇦"
        next_lang_value = "EN"
        
    print(f"🌐 Поточна мова з {target_lang_cell}: {lang_value} (Індекс: {lang_idx}, Прапор: {lang_flag}). Наступна: {next_lang_value}")

    # 🚨 КРИТИЧНИЙ КОНТРОЛЬ ФОРМАТІВ ФАЙЛІВ
    for item in selected_group_items:
        f_name = item["data"][1]
        if not f_name.lower().endswith(config.VALID_MEDIA_EXTENSIONS):
            print(f"❌ КРИТИЧНА ПОМИЛКА: Файл '{f_name}' має непідтримуваний формат контенту!")
            sys.exit(1)
    
    os.makedirs('temp_mebli', exist_ok=True)
    local_files, uploaded_media, ik_ids, ai_analysis_images = [], [], [], []
    has_video = False

    # =====================================================================
    # 📥 ЗАВАНТАЖЕННЯ ТА ОБРОБКА МЕДІА
    # =====================================================================
    for item in selected_group_items:
        f_id, f_name = item["data"][0], item["data"][1]
        
        # 🌟 ЗАХИСТ: Створення безпечного імені з унікальним префіксом ID для уникнення колізій
        safe_local_name = sanitize_filename(f"{f_id[:8]}_{f_name}")
        local_path = os.path.join('temp_mebli', safe_local_name)
        print(f"📥 Завантаження з Drive: {f_name} -> {safe_local_name}...")
        
        try:
            service_manager.download_file_from_drive(drive, f_id, local_path)
        except Exception as e:
            print(f"❌ КРИТИЧНА ПОМИЛКА: Не вдалося скачати файл '{f_name}': {e}")
            clean_up_local_files(local_files)
            sys.exit(1)

        final_path = local_path
        mime_type = "image/jpeg"
        lower_name = safe_local_name.lower()
        is_current_video = lower_name.endswith(('.mp4', '.mov', '.avi'))
        
        if is_current_video:
            has_video = True
            mime_type = "video/mp4"
        elif lower_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join('temp_mebli', safe_local_name.rsplit('.', 1)[0] + '.jpg')
            try:
                with Image.open(local_path) as img:
                    img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
                final_path = jpg_path
                local_files.append(jpg_path)
            except Exception as e:
                print(f"❌ КРИТИЧНА ПОМИЛКА: Збій конвертації HEIC '{f_name}': {e}")
                local_files.append(local_path)
                clean_up_local_files(local_files)
                sys.exit(1)

        # Передаємо safe_local_name, щоб утиліта䪱 оптимізації геометрії теж зберегла файл без кирилиці
        optimized_path = media_processor.optimize_media_geometry(final_path, safe_local_name, mime_type)
        if optimized_path != final_path and optimized_path != local_path:
            local_files.append(optimized_path)

        if is_current_video:
            frame_path = os.path.join('temp_mebli', f"frame_{f_id}.jpg")
            media_processor.extract_video_frame(optimized_path, frame_path)
            ai_analysis_images.append(frame_path)
            local_files.append(frame_path)
        else:
            ai_analysis_images.append(optimized_path)

        local_files.append(local_path)

        try:
            pub_url, ik_id = service_manager.get_google_drive_direct_url(f_id, local_file_path=optimized_path)
            if not pub_url: raise ValueError("Порожній URL публікації.")
            
            uploaded_media.append({"url": pub_url, "is_video": is_current_video})
            if ik_id: ik_ids.append(ik_id)
        except Exception as e:
            print(f"❌ КРИТИЧНА ПОМИЛКА: Не вдалося згенерувати лінку для '{f_name}': {e}")
            clean_up_local_files(local_files)
            sys.exit(1)

    if not uploaded_media:
        print("ℹ️ Немає доступних медіафайлів для публікації.")
        clean_up_local_files(local_files)
        return

    # Створення повного підпису
    header_text = media_processor.get_manufacturer_header(category_name, target_date, lang_idx, mode, target_loc)
    ai_text = service_manager.generate_multimodal_caption(ai_analysis_images, category_name, target_date, lang_idx)
    full_caption = f"{lang_flag} {header_text}{ai_text}"

    if not config.FB_PAGE_ID and mode == "fb_post":
        print("❌ Відсутній FB_PAGE_ID для Facebook!"); clean_up_local_files(local_files); sys.exit(1)
    if not config.IG_USER_ID and mode == "ig_post":
        print("❌ Відсутній IG_USER_ID для Instagram!"); clean_up_local_files(local_files); sys.exit(1)

    res = None
    cloud_urls = [m["url"] for m in uploaded_media]

    # =====================================================================
    # 🌍 ПУБЛІКАЦІЯ FACEBOOK
    # =====================================================================
    if mode == "fb_post":
        if has_video:
            print("🎬 Публікація відео-поста у Facebook...")
            fb_url = f"https://graph.facebook.com/v19.0/{config.FB_PAGE_ID}/videos"
            payload = {"file_url": cloud_urls[0], "description": full_caption, "access_token": config.META_ACCESS_TOKEN}
            res = requests.post(fb_url, data=payload).json()
        else:
            print(f"🖼️ Публікація фото-альбому ({len(cloud_urls)} шт.) у Facebook...")
            attached_media = []
            for url in cloud_urls:
                photo_res = requests.post(f"https://graph.facebook.com/v19.0/{config.FB_PAGE_ID}/photos", data={
                    "url": url, "published": "false", "access_token": config.META_ACCESS_TOKEN
                }).json()
                if "id" not in photo_res:
                    print(f"❌ Не вдалося завантажити під-елемент photo: {photo_res}")
                    clean_up_local_files(local_files); sys.exit(1)
                attached_media.append({"media_fbid": photo_res["id"]})
            
            fb_url = f"https://graph.facebook.com/v19.0/{config.FB_PAGE_ID}/feed"
            payload = {"message": full_caption, "attached_media": json.dumps(attached_media), "access_token": config.META_ACCESS_TOKEN}
            res = requests.post(fb_url, data=payload).json()

    # =====================================================================
    # 📸 ПУБЛІКАЦІЯ INSTAGRAM
    # =====================================================================
    elif mode == "ig_post":
        if len(uploaded_media) > 1:
            print(f"🗂️ Створення каруселі Instagram з {len(uploaded_media)} елементів...")
            container_ids = []
            for item in uploaded_media:
                url = item["url"]
                is_vid = item["is_video"]
                
                param_type = "video_url" if is_vid else "image_url"
                payload = {param_type: url, "is_carousel_item": "true", "access_token": config.META_ACCESS_TOKEN}
                if is_vid: payload["media_type"] = "VIDEO"
                
                item_res = requests.post(f"https://graph.facebook.com/v19.0/{config.IG_USER_ID}/media", data=payload).json()
                if "id" not in item_res:
                    print(f"❌ Помилка створення контейнера каруселі: {item_res}")
                    clean_up_local_files(local_files); sys.exit(1)
                    
                item_id = item_res["id"]
                if is_vid and not wait_for_meta_container(item_id, config.META_ACCESS_TOKEN):
                    print(f"❌ Відео-контейнер {item_id} зафейлився."); clean_up_local_files(local_files); sys.exit(1)
                container_ids.append(item_id)
            
            carousel_payload = {"media_type": "CAROUSEL", "children": json.dumps(container_ids), "caption": full_caption, "access_token": config.META_ACCESS_TOKEN}
            res = requests.post(f"https://graph.facebook.com/v19.0/{config.IG_USER_ID}/media", data=carousel_payload).json()
        else:
            print("🖼️ Створення одиничного контейнера в Instagram...")
            is_vid = uploaded_media[0]["is_video"]
            param_type = "video_url" if is_vid else "image_url"
            payload = {param_type: cloud_urls[0], "caption": full_caption, "access_token": config.META_ACCESS_TOKEN}
            if is_vid: payload["media_type"] = "VIDEO"
            
            res = requests.post(f"https://graph.facebook.com/v19.0/{config.IG_USER_ID}/media", data=payload).json()
            if res and "id" in res and is_vid:
                if not wait_for_meta_container(res["id"], config.META_ACCESS_TOKEN):
                    print("❌ Одиничний відео-контейнер зафейлився."); clean_up_local_files(local_files); sys.exit(1)

        if res and "id" in res:
            creation_id = res["id"]
            print("🚀 Фінальна публікація контейнера в Instagram стрічку...")
            published_successfully = False
            
            for attempt in range(6):
                publish_res = requests.post(f"https://graph.facebook.com/v19.0/{config.IG_USER_ID}/media_publish", data={
                    "creation_id": creation_id, "access_token": config.META_ACCESS_TOKEN
                }).json()
                if "error" in publish_res:
                    err = publish_res["error"]
                    if err.get("error_subcode") == 2207027 or err.get("code") == 9007:
                        print(f"⏳ Сервери Meta зайняті (Спроба {attempt + 1}/6). Чекаємо 10 сек...")
                        time.sleep(10)
                        continue
                res = publish_res
                if "id" in res: published_successfully = True
                break

            if not published_successfully:
                print(f"❌ Помилка публікації після ліміту спроб: {res}")
                clean_up_local_files(local_files); sys.exit(1)

    # =====================================================================
    # 💾 ОНОВЛЕННЯ БАЗИ ТА ТАБЛИЦЬ ПО РЕЗУЛЬТАТУ
    # =====================================================================
    if res and ("id" in res or "post_id" in res):
        print(f"✅ Успішно опубліковано! ID: {res.get('id', res.get('post_id'))}")
        
        for item in selected_group_items:
            new_val = item["counter"] + 1
            range_to_update = f"'{current_tab}'!{col_letter}{item['row_idx']}"
            try:
                sheets.spreadsheets().values().update(
                    spreadsheetId=config.SPREADSHEET_ID, range=range_to_update,
                    valueInputOption='RAW', body={'values': [[new_val]]}
                ).execute()
                print(f"✍️ Лічильник рядка {item['row_idx']} збільшено до {new_val}.")
            except Exception as e:
                print(f"⚠️ Помилка лічильника в Таблиці: {e}")
                    
        try:
            sheets.spreadsheets().values().update(
                spreadsheetId=config.SPREADSHEET_ID, range=target_lang_cell,
                valueInputOption='RAW', body={'values': [[next_lang_value]]}
            ).execute()
            print(f"🔄 Мову на наступний раз змінено на: {next_lang_value}")
        except Exception as e:
            print(f"⚠️ Не вдалося оновити комірку мови: {e}")

        if ik_ids:
            print("🧹 Очищення хмари ImageKit...")
            for ik_id in ik_ids: service_manager.delete_from_imagekit(ik_id)
            
    else:
        print(f"❌ КРИТИЧНА ПОМИЛКА Meta API: {res}")
        clean_up_local_files(local_files)
        sys.exit(1)

    clean_up_local_files(local_files)
    print("🎯 Скрипт успішно завершив роботу.")

if __name__ == "__main__":
    main()
