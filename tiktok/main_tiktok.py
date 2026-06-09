import os
import sys
import time
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image
from pillow_heif import register_heif_opener
from moviepy.editor import VideoFileClip, concatenate_videoclips, ImageClip

# Імпорт власних модулів
from config import FOLDER_INPUT_ID, FOLDER_TRASH_ID, VALID_EXTENSIONS
from auth import get_gdrive_service
from drive_manager import count_total_files, download_file, move_files_to_trash
from metadata_extractor import get_intellectual_date, get_location_name
from media_processor import (
    gif_to_mp4, prepare_padded_image, fit_video_with_background,
    generate_ai_metadata, compile_final_video
)
from tiktok_uploader import upload_to_tiktok

register_heif_opener()

def main():
    run_mode = os.environ.get('RUN_MODE', 'manual')
    print(f"⚙️ Запуск у режимі: {run_mode.upper()}")
    
    service = get_gdrive_service()
    
    if run_mode == 'cron':
        print("🔍 Підраховуємо загальну кількість файлів у папці...")
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
            sys.exit(0)
            
        print("✅ Успішно! Умови графіку виконано. Переходимо до відбору та обробки медіа.")

    try:
        results = service.files().list(
            q=f"'{FOLDER_INPUT_ID}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size)",
            orderBy="createdTime",
            pageSize=50
        ).execute()
    except Exception as e:
        sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Помилка отримання файлів з Google Диску: {e}")
        
    gdrive_files = results.get('files', [])
    gdrive_files = [f for f in gdrive_files if f['id'] != FOLDER_TRASH_ID]
    
    if not gdrive_files:
        print("☕ [ШТАТНИЙ ПРОПУСК] Папка вхідних медіа порожня.")
        sys.exit(0)

    print(f"Знайдено файлів для поточної збірки: {len(gdrive_files)}")
    processed_items = []
    os.makedirs('downloaded', exist_ok=True)
    
    for f in gdrive_files:
        mime_type = f.get('mimeType', '')
        lower_name = f['name'].lower()
        
        is_valid_media = mime_type.startswith(('image/', 'video/')) or lower_name.endswith(VALID_EXTENSIONS)
        if not is_valid_media:
            continue
            
        local_path = os.path.join('downloaded', f['name'])
        print(f"Завантаження {f['name']}...")
        
        download_file(service, f['id'], f['name'], local_path)

        now = datetime.now()
        final_dt, lat, lon = get_intellectual_date(local_path, f['name'], f, now)
        file_date = final_dt.strftime('%d.%m.%Y')
        
        location = "Невідоме місце"
        if lat and lon:
            time.sleep(1)  
            location = get_location_name(lat, lon) or "Невідоме місце"
            
        # Конвертація GIF
        if mime_type == 'image/gif' or lower_name.endswith('.gif'):
            mp4_path = os.path.join('downloaded', f['name'].rsplit('.', 1)[0] + '_gif.mp4')
            try:
                gif_to_mp4(local_path, mp4_path)
            except Exception as e:
                sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося конвертувати GIF у MP4. Деталі: {e}")
            
            if os.path.exists(local_path): os.remove(local_path)
            local_path = mp4_path
            mime_type = 'video/mp4'
                
        # Конвертація HEIC / HEIF
        elif mime_type in ['image/heic', 'image/heif'] or lower_name.endswith(('.heic', '.heif')):
            jpg_path = os.path.join('downloaded', f['name'].rsplit('.', 1)[0] + '.jpg')
            try:
                with Image.open(local_path) as img:
                    img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
            except Exception as e:
                sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося розкодувати iPhone-формат HEIC/HEIF. Деталі: {e}")
            
            if os.path.exists(local_path): os.remove(local_path)
            local_path = jpg_path
            mime_type = 'image/jpeg'

            processed_items.append({
                'id': f['id'],
                'name': f['name'],
                'mime': mime_type,
                'local_path': local_path,
                'date': file_date,
                'location': location
            })

    # --- ГРУПУВАННЯ ТА МОНТАЖ ---
    groups = {}
    for item in processed_items:
        key = (item['date'], item['location'])
        if key not in groups:
            groups[key] = []
        groups[key].append(item)
        
    MIN_DURATION = 20
    MAX_DURATION = 40
    PHOTO_DURATION = 3.0

    for (date, loc), items in groups.items():
        print(f"🎬 Знайдено групу для монтажу: Дата {date} | Локація: {loc}. Всього файлів у групі: {len(items)}")
        
        valid_items = []
        for item in items:
            mime = item['mime']
            local_path = item['local_path']
            if 'video' in mime:
                try:
                    with VideoFileClip(local_path) as clip:
                        item['duration'] = clip.duration
                    valid_items.append(item)
                except Exception as e:
                    print(f"⚠️ Відео '{item['name']}' пошкоджене: {e}. Пропускаємо.")
            elif 'image' in mime:
                item['duration'] = PHOTO_DURATION
                valid_items.append(item)

        if not valid_items:
            print("☕ Немає валідних медіафайлів у цій групі.")
            continue

        # --- КЕЙС 1: ОДИН ДОВГИЙ ФАЙЛ (> 40 секунд) ---
        if len(valid_items) == 1 and valid_items[0]['duration'] > MAX_DURATION:
            single_item = valid_items[0]
            total_dur = single_item['duration']
            print(f"✂️ Виявлено один довгий файл ({total_dur:.1f} сек). Ріжемо на частини...")
            
            start = 0
            part_num = 1
            all_parts_success = True
            generated_files = []
            chunk_length = 35.0
            
            while start < total_dur:
                end = min(start + chunk_length, total_dur)
                part_duration = end - start
                print(f"📦 Обробка частини {part_num} ({start:.1f}s - {end:.1f}s)")
                
                try:
                    with VideoFileClip(single_item['local_path']) as full_video:
                        trimmed = full_video.subclip(start, end)
                        if part_duration < MIN_DURATION:
                            loops = int(np.ceil(MIN_DURATION / part_duration))
                            trimmed = concatenate_videoclips([trimmed] * loops)
                        
                        smart_video = fit_video_with_background(trimmed, 1080, 1920)
                        text_info = generate_ai_metadata(date, loc)
                        trending_text, year, location = text_info
                        hash_tag = location.split(',')[0].strip().replace(" ", "")
                        tiktok_description = f"{trending_text} (Частина {part_num}) 🌍 #travel #{hash_tag}"
                        
                        final_file = compile_final_video([smart_video], text_info)
                        generated_files.append(final_file)
                        
                        upload_success = upload_to_tiktok(final_file, tiktok_description)
                        if not upload_success:
                            all_parts_success = False
                            break
                except Exception as e:
                    print(f"❌ Помилка обробки довгого відео на частині {part_num}: {e}")
                    all_parts_success = False
                    break
                
                start = end
                part_num += 1
                
            if all_parts_success:
                move_files_to_trash(service, [single_item])
                for gf in generated_files:
                    if os.path.exists(gf): os.remove(gf)
                if os.path.exists(single_item['local_path']): os.remove(single_item['local_path'])
                print("🏁 Послідовну публікацію великого файлу завершено успішно.")
            else:
                sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Публікація однієї з частин ролика зазнала невдачі.")
            return

        # --- КЕЙС 2 ТА 3: ЗВИЧАЙНА ГРУПА ---
        selected_items = []
        accumulated_duration = 0
        
        for item in valid_items:
            if accumulated_duration + item['duration'] <= MAX_DURATION:
                selected_items.append(item)
                accumulated_duration += item['duration']
            else:
                if accumulated_duration >= MIN_DURATION:
                    break
                else:
                    remaining_space = MAX_DURATION - accumulated_duration
                    if remaining_space >= 4.0 and 'video' in item['mime']:
                        item['crop_to_duration'] = remaining_space
                        item['duration'] = remaining_space
                        selected_items.append(item)
                        accumulated_duration += remaining_space
                    break

        print(f"📐 Розумний відбір: {len(selected_items)} файлів. Чиста тривалість збірки: {accumulated_duration:.1f} сек.")

        final_items_to_render = list(selected_items)
        if accumulated_duration < MIN_DURATION:
            while accumulated_duration < MIN_DURATION:
                for item in selected_items:
                    final_items_to_render.append(item)
                    accumulated_duration += item['duration']
                    if accumulated_duration >= MIN_DURATION:
                        break

        clips = []
        temp_images_to_clean = []
        
        for item in final_items_to_render:
            local_path = item['local_path']
            mime = item['mime']
            
            if 'video' in mime:
                try:
                    full_video = VideoFileClip(local_path)
                    dur = item.get('crop_to_duration', full_video.duration)
                    start_time = max(0, full_video.duration / 2 - dur / 2)
                    end_time = min(full_video.duration, start_time + dur)
                    
                    trimmed = full_video.subclip(start_time, end_time)
                    smart_video = fit_video_with_background(trimmed, 1080, 1920)
                    clips.append(smart_video)
                except Exception as e:
                    print(f"⚠️ Пропуск кліпу відео через помилку рендеру: {e}")
            elif 'image' in mime:
                try:
                    temp_img_path = os.path.join('downloaded', f"padded_{int(time.time())}_{os.path.basename(local_path)}")
                    prepare_padded_image(local_path, temp_img_path, 1080, 1920)
                    temp_images_to_clean.append(temp_img_path)
                    
                    img_clip = ImageClip(temp_img_path).set_duration(PHOTO_DURATION)
                    clips.append(img_clip)
                except Exception as e:
                    print(f"⚠️ Не вдалося обробити фото {local_path}: {e}")

        if not clips:
            sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося зібрати жодного кліпу для монтажу.")

        text_info = generate_ai_metadata(date, loc)
        trending_text, year, location = text_info
        hash_tag = location.split(',')[0].strip().replace(" ", "")
        tiktok_description = f"{trending_text} 🌍 #travel #{hash_tag}"
        
        final_file = compile_final_video(clips, text_info)
        print(f"🎉 Фінальне відео зібрано: {final_file}")
        
        upload_success = upload_to_tiktok(final_file, tiktok_description)
        
        if upload_success:
            move_files_to_trash(service, selected_items)
            for tf in temp_images_to_clean:
                if os.path.exists(tf): os.remove(tf)
            for si in selected_items:
                if os.path.exists(si['local_path']): os.remove(si['local_path'])
            if os.path.exists(final_file): os.remove(final_file)
        return

if __name__ == '__main__':
    main()
