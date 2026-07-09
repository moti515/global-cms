import os
import sys
import json
import time
import requests
import subprocess
import re
import shutil
from datetime import datetime
from googleapiclient.http import MediaIoBaseDownload
from PIL import Image

import config_meb_insta_story as config
from media_processor_meb_instagram_story import *
from services_manager_meb_instagram_story import *

def sanitize_filename(filename):
    name, ext = os.path.splitext(filename)
    sanitized_name = re.sub(r'[^a-zA-Z0-9_\-]', '-', name)
    sanitized_name = re.sub(r'-+', '-', sanitized_name).strip('-')
    if not sanitized_name:
        sanitized_name = f"media_{int(time.time())}"
    return f"{sanitized_name}{ext.lower()}"

def main():
    if len(sys.argv) < 3:
        print("💡 Запуск: python publish_content_mebli_storys.py ig_story <tab_name>")
        sys.exit(1)

    mode = sys.argv[1].lower()
    forced_tab = sys.argv[2]
    current_tab = forced_tab if forced_tab else config.TAB_NAME
    
    if mode != "ig_story":
        print(f"❌ Цей скрипт сконструйовано виключно під 'ig_story'. Передано: {mode}")
        sys.exit(1)

    ig_user_id = os.environ.get("IG_USER_ID")
    meta_access_token = os.environ.get("META_ACCESS_TOKEN")

    drive, sheets = get_services()
    os.makedirs('temp_mebli', exist_ok=True)
    
    # Очищення папки на GitHub перед стартом нової сесії
    github_repo = os.environ.get("GITHUB_REPOSITORY")
    target_dir = os.path.join("docs", "temp_media")
    if github_repo and os.path.exists(target_dir):
        print("🧹 [GitHub] Очищення папки тимчасових медіа...")
        try:
            shutil.rmtree(target_dir)
            os.makedirs(target_dir, exist_ok=True)
            subprocess.run(["git", "config", "user.name", "github-actions[bot]"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "pull", "origin", "main", "--rebase"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "add", "docs/temp_media"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
            if status.stdout.strip():
                subprocess.run(["git", "commit", "-m", "🧹 Очищення залишків минулих публікацій"], check=True)
                subprocess.run(["git", "push", "origin", "main"], check=True)
        except Exception as clean_err:
            print(f"⚠️ Не вдалося очистити GitHub-папку: {clean_err}")
    else:
        os.makedirs(target_dir, exist_ok=True)
        
    selected_queue = []
    has_global_failures = False
    
    print(f"🔍 Перевірка наявності файлів у гарячій папці [{config.HOT_FOLDER_ID}]...")
    try:
        hot_query = f"'{config.HOT_FOLDER_ID}' in parents and trashed = false"
        hot_res = drive.files().list(q=hot_query, fields="files(id, name, mimeType)", pageSize=50).execute()
        hot_files = hot_res.get('files', [])
    except Exception as e:
        print(f"❌ ПОМИЛКА отримання файлів з Google Диску: {e}")
        hot_files = []
        has_global_failures = True

    if hot_files:
        hot_group_items = []
        for f in hot_files:
            f_id, f_name = f['id'], f['name']
            if not f_name.lower().endswith(config.VALID_MEDIA_EXTENSIONS):
                continue
            
            safe_local_name = sanitize_filename(f"{f_id}_{f_name}")
            local_path = os.path.join('temp_mebli', safe_local_name)
            
            try:
                request = drive.files().get_media(fileId=f_id)
                with open(local_path, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done: _, done = downloader.next_chunk()
            except Exception as e:
                print(f"❌ Не вдалося завантажити {f_name} для аналізу: {e}")
                has_global_failures = True
                continue
            
            try:
                final_date, lat, lon = get_intellectual_date(local_path, f_name, f)
                date_str = final_date.strftime('%d.%m.%Y') if hasattr(final_date, 'strftime') else str(final_date)
                display_location, group_location = get_location_data(lat, lon)
            except Exception:
                date_str = datetime.now().strftime('%d.%m.%Y')
                display_location, group_location = "", ""

            detected_company = "Загальне"
            for key in config.COMPANIES_DB.keys():
                if key in f_name.lower():
                    detected_company = key
                    break
            
            hot_group_items.append({
                "id": f_id, "name": f_name, "safe_local_name": safe_local_name, "local_path": local_path,
                "category": detected_company, "date": date_str, "location": display_location,      
                "group_location": group_location, "mode": "hot_folder", "counter_cell": None
            })

        if hot_group_items:
            groups = {}
            for item in hot_group_items:
                groups.setdefault((item["date"], item["group_location"]), []).append(item)
            first_key = list(groups.keys())[0]
            selected_queue = groups[first_key][:4]
            
            selected_ids = {x["id"] for x in selected_queue}
            for item in hot_group_items:
                if item["id"] not in selected_ids and os.path.exists(item["local_path"]):
                    os.remove(item["local_path"])
    else:
        print(f"📊 Гаряча папка порожня. Активуємо Сценарій 2 (Реєстр '{current_tab}')...")
        try:
            res = sheets.spreadsheets().values().get(spreadsheetId=config.SPREADSHEET_ID, range=f"'{current_tab}'!A2:I").execute()
            rows = res.get('values', [])
        except Exception as e:
            print(f"❌ Помилка доступу до Sheets: {e}")
            sys.exit(1)

        if not rows: return

        valid_rows = []
        for i, r in enumerate(rows):
            if len(r) >= 3 and r[2].lower() != "temporary":
                try:
                    val = r[4] if len(r) > 4 and r[4] else "0"
                    valid_rows.append({"row_idx": i + 2, "data": r, "counter": int(val)})
                except ValueError: continue

        if not valid_rows: return

        min_counter = min(item["counter"] for item in valid_rows)
        min_pool = [item for item in valid_rows if item["counter"] == min_counter]

        groups = {}
        for item in min_pool:
            data = item["data"]
            groups.setdefault((data[2], data[6] if len(data) > 6 else "", data[8] if len(data) > 8 else ""), []).append(item)

        first_key = list(groups.keys())[0]
        for item in groups[first_key][:4]:
            data = item["data"]
            selected_queue.append({
                "id": data[0], "name": data[1], "local_path": None, "category": first_key[0], "date": first_key[1],
                "location": first_key[2], "exact_location": data[7] if len(data) > 7 else "", "mode": "sheet",
                "counter_cell": f"'{current_tab}'!E{item['row_idx']}", "counter_val": item["counter"]
            })

    if not selected_queue: return

    # Зчитування та ротація мови
    target_lang_cell = "'⚙️ Налаштування Папок'!H2"
    lang_value = "UK"
    try:
        lang_res = sheets.spreadsheets().values().get(spreadsheetId=config.SPREADSHEET_ID, range=target_lang_cell).execute()
        lang_values = lang_res.get('values', [])
        if lang_values and lang_values[0]: lang_value = lang_values[0][0].strip().upper()
    except: pass

    if any(x in lang_value for x in ["EN", "ENG", "ENGLISH"]): lang_idx, next_lang_value = 1, "DE"
    elif any(x in lang_value for x in ["DE", "GER", "DEUTSCH"]): lang_idx, next_lang_value = 2, "UK"
    else: lang_idx, next_lang_value = 0, "EN"
        
    local_files_to_clean = []
    success_published_any = False

    for idx_item, item in enumerate(selected_queue):
        f_id, f_name = item["id"], item["name"]
        
        if item["mode"] == "sheet":
            if not f_name.lower().endswith(config.VALID_MEDIA_EXTENSIONS):
                log_unsupported_to_service(sheets, item["category"], f_name)
                continue

            safe_local_name = sanitize_filename(f"{f_id}_{f_name}")
            local_path = os.path.join('temp_mebli', safe_local_name)
            try:
                request = drive.files().get_media(fileId=f_id)
                with open(local_path, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done: _, done = downloader.next_chunk()
            except Exception:
                has_global_failures = True
                continue
        else:
            local_path = item["local_path"]
            safe_local_name = item["safe_local_name"]

        final_path = local_path
        is_video = safe_local_name.endswith(('.mp4', '.mov', '.avi'))
        
        if safe_local_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join('temp_mebli', safe_local_name.rsplit('.', 1)[0] + '.jpg')
            try:
                with Image.open(local_path) as img: img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
                final_path = jpg_path
                local_files_to_clean.append(jpg_path)
            except:
                has_global_failures = True
                continue

        local_files_to_clean.append(local_path)

        ai_media_snapshot = final_path
        if is_video:
            frame_path = os.path.join('temp_mebli', f"frame_{f_id}.jpg")
            subprocess.run(['ffmpeg', '-y', '-i', final_path, '-ss', '00:00:01', '-vframes', '1', frame_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(frame_path):
                ai_media_snapshot = frame_path
                local_files_to_clean.append(frame_path)

        story_caption_text = generate_story_caption([ai_media_snapshot], item["category"], item["date"], lang_idx, item["location"])
        
        try: year_variable = item["date"].split(".")[2]
        except: year_variable = str(datetime.now().year)
            
        try:
            loc_json = json.loads(item["location"])
            location_variable = loc_json.get(str(lang_idx), loc_json.get("0", ""))
        except: location_variable = item["location"]

        media_parts_to_upload = []
        try:
            if is_video:
                media_parts_to_upload = optimize_video_story(final_path, safe_local_name, story_caption_text, year=year_variable, location=location_variable)
            else:
                optimized_path = optimize_image_story(final_path, safe_local_name)
                overlay_text_on_image(optimized_path, story_caption_text, year=year_variable, location=location_variable)
                media_parts_to_upload = [optimized_path]
        except Exception as e:
            print(f"❌ Помилка обробки файлу {safe_local_name}: {e}")
            has_global_failures = True
            continue

        item_published_successfully = True

        for sub_idx, active_path in enumerate(media_parts_to_upload):
            if active_path != final_path and active_path != local_path:
                local_files_to_clean.append(active_path)

            # 🌟 ЦЕНТРАЛІЗОВАНИЙ ВИКЛИК (Тепер без конфліктів)
            pub_url, ik_id = get_google_drive_direct_url(f_id, local_file_path=active_path)
            
            if not pub_url:
                item_published_successfully = False
                has_global_failures = True
                continue

            payload = {
                "media_type": "STORIES",
                "param_type": pub_url, # Meta розпізнає посилання автоматично
                "access_token": meta_access_token
            }
            
            try:
                res = requests.post(f"https://graph.facebook.com/v19.0/{ig_user_id}/media", data=payload).json()
                if res and "id" in res:
                    if wait_for_meta_container(res["id"], meta_access_token):
                        publish_res = requests.post(f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish", data={
                            "creation_id": res["id"], "access_token": meta_access_token
                        }).json()
                        if "id" in publish_res: success_published_any = True
                        else: item_published_successfully, has_global_failures = False, True
                    else: item_published_successfully, has_global_failures = False, True
                else: item_published_successfully, has_global_failures = False, True
            except:
                item_published_successfully, has_global_failures = False, True

            if ik_id and ik_id != "github_skip": 
                delete_from_imagekit(ik_id)

        if item_published_successfully and media_parts_to_upload:
            if item["mode"] == "sheet":
                new_val = item["counter_val"] + 1
                try: sheets.spreadsheets().values().update(spreadsheetId=config.SPREADSHEET_ID, range=item["counter_cell"], valueInputOption='RAW', body={'values': [[new_val]]}).execute()
                except: pass
            elif item["mode"] == "hot_folder":
                try:
                    file_meta = drive.files().get(fileId=f_id, fields='parents').execute()
                    previous_parents = ",".join(file_meta.get('parents', []))
                    drive.files().update(fileId=f_id, addParents=config.TRASH_FOLDER_ID, removeParents=previous_parents).execute()
                except: pass

    if success_published_any:
        try: sheets.spreadsheets().values().update(spreadsheetId=config.SPREADSHEET_ID, range=target_lang_cell, valueInputOption='RAW', body={'values': [[next_lang_value]]}).execute()
        except: pass

    for f in local_files_to_clean:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass

    if has_global_failures: sys.exit(1)

if __name__ == "__main__":
    main()
