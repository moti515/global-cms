import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image
from pillow_heif import register_heif_opener

# Імпорт власних модулів
from config_tiktok import FOLDER_INPUT_ID, FOLDER_TRASH_ID, VALID_EXTENSIONS
from auth_tiktok import get_gdrive_service
from drive_manager_tiktok import count_total_files, download_file, move_files_to_trash
from metadata_extractor_tiktok import get_intellectual_date, get_location_name
from media_processor_tiktok import (
    gif_to_mp4, generate_ai_metadata, sanitize_video,
    get_video_duration, process_video_item, process_image_item, fast_concat_videos,
    add_background_music
)
from tiktok_uploader import upload_to_tiktok

register_heif_opener()

def main():
    run_mode = os.environ.get('RUN_MODE', 'manual')
    print(f"⚙️ Запуск у режимі: {run_mode.upper()}")

    def upload_with_music_wrapper(file_path, description):
        """Додає музику до файлу (якщо це можливо) і викликає оригінальний аплоадер."""
        temp_music_path = file_path.replace(".mp4", "_music_applied.mp4")
        if add_background_music(file_path, temp_music_path):
            if os.path.exists(temp_music_path):
                os.replace(temp_music_path, file_path)
                print("✅ Фонова музика успішно інтегрована в ролик!")
        else:
            print("⏩ Публікуємо відео з оригінальним звуком (без фонової музики).")
        return upload_to_tiktok(file_path, description)
    
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
        
        # Крок 1: Валідація та збір чистих метаданих тривалості через ffprobe
        valid_items = []
        for item in items:
            mime = item['mime']
            local_path = item['local_path']
            if 'video' in mime:
                duration = get_video_duration(local_path)
                if duration > 0:
                    item['duration'] = duration
                    valid_items.append(item)
                else:
                    print(f"⚠️ Спроба відкрити через ffprobe '{item['name']}' провалилася. Лікуємо...")
                    if sanitize_video(local_path):
                        duration = get_video_duration(local_path)
                        if duration > 0:
                            item['duration'] = duration
                            valid_items.append(item)
                            print(f"✅ Файл '{item['name']}' успішно інтегровано після лікування.")
                            continue
                    print(f"⏩ Пропускаємо пошкоджений файл '{item['name']}'.")
            elif 'image' in mime:
                item['duration'] = PHOTO_DURATION
                valid_items.append(item)

        if not valid_items:
            print("☕ Немає валідних медіафайлів у цій групі.")
            continue

        # Крок 2: Розумний відбір файлів під ліміт 40 секунд
        selected_items = []
        if len(valid_items) == 1 and valid_items[0]['duration'] > MAX_DURATION:
            selected_items = [valid_items[0]]
        else:
            accumulated_duration = 0
            for item in valid_items:
                if item['duration'] > MAX_DURATION and len(selected_items) == 0:
                    selected_items = [item]
                    break
                if accumulated_duration + item['duration'] <= MAX_DURATION:
                    selected_items.append(item)
                    accumulated_duration += item['duration']
                else:
                    break

        print(f"📐 Результат відбору: {len(selected_items)} файлів готово до обробки.")
        text_info = generate_ai_metadata(date, loc)
        tiktok_description = f"{text_info[0]} 🌍 #travel #{text_info[2].split(',')[0].strip().replace(' ', '')}"
        final_file = f"ready_tiktok_{int(time.time())}.mp4"

        # --- РОЗПОДІЛ ЗА СЦЕНАРІЯМИ ПУБЛІКАЦІЇ ---

        if len(selected_items) == 1:
            single_item = selected_items[0]
            
            # СЦЕНАРІЙ А: Одне фото -> перетворюємо в 3 сек відео
            if 'image' in single_item['mime']:
                print(f"📸 Сценарій А: Поодиноке фото. Створюємо відео тривалістю {PHOTO_DURATION} сек.")
                if process_image_item(single_item['local_path'], final_file, text_info, duration=PHOTO_DURATION):
                    if upload_with_music_wrapper(final_file, tiktok_description):
                        move_files_to_trash(service, [single_item])
                        if os.path.exists(final_file): os.remove(final_file)
                        if os.path.exists(single_item['local_path']): os.remove(single_item['local_path'])
                return

            # СЦЕНАРІЙ Б: Одне коротке відео (< 3 сек) -> зациклюємо
            elif 'video' in single_item['mime'] and single_item['duration'] < 3.0:
                print(f"🔄 Сценарій Б: Коротуни ({single_item['duration']:.2f} сек). Зациклюємо через stream_loop...")
                loops = int(sorted([1, np.ceil(3.0 / single_item['duration']) - 1, 10])[1])
                # Вираховуємо точний ліміт часу для фільтра
                total_t = single_item['duration'] * (loops + 1)
                
                if process_video_item(single_item['local_path'], final_file, text_info, loops=loops, t=total_t):
                    if upload_with_music_wrapper(final_file, tiktok_description):
                        move_files_to_trash(service, [single_item])
                        if os.path.exists(final_file): os.remove(final_file)
                        if os.path.exists(single_item['local_path']): os.remove(single_item['local_path'])
                return

            # СЦЕНАРІЙ В: Один великий файл (> 40 сек) -> ріжемо на РІВНІ частини
            elif 'video' in single_item['mime'] and single_item['duration'] > MAX_DURATION:
                total_dur = single_item['duration']
                num_parts = int(np.ceil(total_dur / MAX_DURATION))
                chunk_length = total_dur / num_parts
                print(f"✂️ Сценарій В: Велике відео ({total_dur:.1f} сек). Ріжемо на {num_parts} частин по {chunk_length:.1f} сек...")
                
                all_parts_success = True
                generated_files = []
                
                trending_text, year, location_name = text_info
                
                for part_idx in range(num_parts):
                    start = part_idx * chunk_length
                    part_num = part_idx + 1
                    part_output = f"ready_tiktok_part_{part_num}_{int(time.time())}.mp4"
                    
                    print(f"📦 Рендеринг частини {part_num}/{num_parts} ({start:.1f}s - {start+chunk_length:.1f}s)")
                    modified_text_info = (f"{trending_text} (Ч. {part_num})", year, location_name)
                    
                    success = process_video_item(
                        single_item['local_path'], part_output, modified_text_info, 
                        ss=start, t=chunk_length
                    )
                    
                    if success and os.path.exists(part_output):
                        generated_files.append(part_output)
                        hash_tag = location_name.split(',')[0].strip().replace(" ", "")
                        part_description = f"{trending_text} (Частина {part_num}) 🌍 #travel #{hash_tag}"
                        
                        if not upload_with_music_wrapper(part_output, part_description):
                            all_parts_success = False
                            break
                    else:
                        all_parts_success = False
                        break
                
                if all_parts_success:
                    move_files_to_trash(service, [single_item])
                    for gf in generated_files:
                        if os.path.exists(gf): os.remove(gf)
                    if os.path.exists(single_item['local_path']): os.remove(single_item['local_path'])
                    print("🏁 Серійну публікацію всіх частин завершено успішно!")
                else:
                    sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Публікація однієї з частин серіалу провалилася.")
                return

        # СЦЕНАРІЙ Г: Класична збірка (декілька файлів, сумарно <= 40 сек)
        print("🎬 Сценарій Г: Монтаж стандартної групи медіафайлів.")
        
        final_items_to_render = list(selected_items)
        accumulated_duration = sum(i['duration'] for i in selected_items)
        if accumulated_duration < MIN_DURATION:
            while accumulated_duration < MIN_DURATION:
                for item in selected_items:
                    final_items_to_render.append(item)
                    accumulated_duration += item['duration']
                    if accumulated_duration >= MIN_DURATION:
                        break

        temp_clips = []
        
        for idx, item in enumerate(final_items_to_render):
            local_path = item['local_path']
            mime = item['mime']
            part_out = os.path.join('downloaded', f"processed_part_{idx}_{int(time.time())}.mp4")
            success = False
            
            if 'video' in mime:
                dur = item['duration']
                total_d = get_video_duration(local_path)
                ss_param, t_param = None, None
                
                # Якщо кліп потребує часткового обрізання всередині конвеєра дублювання
                if dur < total_d:
                    ss_param = max(0, total_d / 2 - dur / 2)
                    t_param = dur
                    
                success = process_video_item(local_path, part_out, text_info, ss=ss_param, t=t_param)
            elif 'image' in mime:
                success = process_image_item(local_path, part_out, text_info, duration=PHOTO_DURATION)
                
            if success and os.path.exists(part_out):
                temp_clips.append(part_out)

        if not temp_clips:
            sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Не вдалося підготувати жодного фрагмента для збірки.")

        # Миттєве склеювання без рендерингу
        fast_concat_videos(temp_clips, final_file)
        
        if os.path.exists(final_file):
            if upload_with_music_wrapper(final_file, tiktok_description):
                move_files_to_trash(service, selected_items)
                for tc in temp_clips:
                    if os.path.exists(tc): os.remove(tc)
                for si in selected_items:
                    if os.path.exists(si['local_path']): os.remove(si['local_path'])
                if os.path.exists(final_file): os.remove(final_file)
                print("🏁 Груповий ролик успішно опубліковано через швидкий конкатенатор.")
            else:
                sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Офіційний аплоадер TikTok відхилив груве відео.")
        return

if __name__ == '__main__':
    main()
