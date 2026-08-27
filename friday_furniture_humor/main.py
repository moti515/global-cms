import os
import sys
import time

from config import (
    SPREADSHEET_ID, VALID_MEDIA_EXTENSIONS, DOCUMENT_EXTENSIONS, TEMP_MEDIA_DIR
)
from google_services import (
    get_services, log_unsupported_to_service, get_valid_rows_from_sheet,
    update_sheet_counter, download_file_from_drive
)
from ai_generator import get_active_rules_ordered, generate_multimodal_caption
from media_processor import (
    convert_and_format_media, extract_frame_from_video,
    get_google_drive_direct_url, delete_from_imagekit
)
from meta_publisher import publish_to_meta_platforms


def main():
    if len(sys.argv) < 3:
        print("❌ Помилка: Відсутні обов'язкові аргументи.")
        print("\n📋 ПРАВИЛЬНИЙ ФОРМАТ ЗАПУСКУ:")
        print("  python main.py [mode] \"[sheet_name]\"")
        print("\n💡 Доступні режими [mode]:")
        print("  post  - публікація згенерованого поста у стрічку")
        print("  story - оптимізація та публікація у Сторіс")

        try:
            _, sheets = get_services()
            spreadsheet = sheets.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
            available_sheets = [
                f'"{sheet["properties"]["title"]}"' 
                for sheet in spreadsheet.get('sheets', [])
                if "налаштування" not in sheet["properties"]["title"].lower()
            ]
            if available_sheets:
                print(f"\n📁 Можливі назви аркушів [sheet_name]:")
                print(f"  {', '.join(available_sheets)}")
        except Exception:
            pass
        return

    mode = sys.argv[1].lower()
    tab_name = sys.argv[2]
    counter_col_idx = 3 if mode == 'post' else 4

    drive, sheets = get_services()
    valid_rows = get_valid_rows_from_sheet(sheets, tab_name, counter_col_idx)

    if not valid_rows:
        print(f"ℹ️ Немає валідних рядків для обробки на аркуші '{tab_name}'.")
        return

    min_count = min(r["data"][counter_col_idx] for r in valid_rows)
    min_pool = [r for r in valid_rows if r["data"][counter_col_idx] == min_count]
    
    selected_item = None
    if "мебл" in tab_name.lower():
        selected_item = min_pool[0]
        print(f"🪑 Режим Меблів: обрано перший файл із мінімальною кількістю публікацій.")
    else:
        active_categories = get_active_rules_ordered()
        for category in active_categories:
            match_files = [item for item in min_pool if item["data"][2] == category]
            if match_files:
                selected_item = match_files[0]
                print(f"📅 Режим Календаря: знайдено збіг за категорією '{category}'")
                break
        if not selected_item:
            selected_item = min_pool[0]

    file_id = selected_item["data"][0]
    orig_name = selected_item["data"][1]
    category_name = selected_item["data"][2]
    row_line = selected_item["row_idx"]

    lower_name = orig_name.lower()
    if lower_name.endswith(DOCUMENT_EXTENSIONS):
        print(f"📄 Знайдено документ/книгу ({orig_name}). Пропускаємо.")
        log_unsupported_to_service(sheets, category_name, orig_name, reason="Знайдено текстовий документ/книгу (PDF/DOC)")
        return
        
    if not lower_name.endswith(VALID_MEDIA_EXTENSIONS):
        print(f"❌ Невідомий формат файлу: {orig_name}.")
        log_unsupported_to_service(sheets, category_name, orig_name, reason="Непідтримуваний формат медіа")
        return

    os.makedirs(TEMP_MEDIA_DIR, exist_ok=True)
    local_path = os.path.join(TEMP_MEDIA_DIR, orig_name)
    download_file_from_drive(drive, file_id, local_path)

    files_to_publish, mime_type = convert_and_format_media(local_path, orig_name, mode)

    caption_text = ""
    if mode == 'post':
        analysis_image = files_to_publish[0]
        if mime_type == "video/mp4":
            analysis_image = extract_frame_from_video(files_to_publish[0])
            
        print("👁️ ШІ аналізує візуальний вміст файлу...")
        caption_text = generate_multimodal_caption(analysis_image, category_name, tab_name)
        
        if os.path.exists(os.path.join(TEMP_MEDIA_DIR, 'video_frame.jpg')): 
            os.remove(os.path.join(TEMP_MEDIA_DIR, 'video_frame.jpg'))

    success_count = 0
    for idx, current_file in enumerate(files_to_publish):
        part_info = f" (Частина {idx + 1}/{len(files_to_publish)})" if len(files_to_publish) > 1 else ""
        print(f"🚀 Початок публікації файлу{part_info}: {current_file}")
        
        ik_file_id = None
        try:
            public_url, ik_file_id = get_google_drive_direct_url(file_id, local_file_path=current_file)
            
            publish_to_meta_platforms(
                public_url, 
                "video" if mime_type == "video/mp4" else "image", 
                is_story=(mode == 'story'), 
                caption=caption_text,
                local_file_path=current_file
            )
            success_count += 1
            
            if len(files_to_publish) > 1 and idx < len(files_to_publish) - 1:
                print("⏳ Очікуємо 6 секунд перед надсиланням наступної частини...")
                time.sleep(6)
                
        except Exception as e:
            print(f"❌ Критична помилка під час публікації{part_info}: {e}")
        finally:
            if ik_file_id:
                delete_from_imagekit(ik_file_id)
            if current_file != local_path and os.path.exists(current_file):
                os.remove(current_file)

    if success_count > 0:
        update_sheet_counter(sheets, tab_name, row_line, mode, selected_item["data"][counter_col_idx])
    else:
        print("❌ Жодна з частин не була опублікована. Лічильник залишено без змін.")
        sys.exit(1)

    if os.path.exists(local_path): 
        os.remove(local_path)
    print("🧹 Всі тимчасові файли видалено. Роботу завершено!")


if __name__ == '__main__':
    main()
