import os
import sys
import shutil
from datetime import datetime
import config
from media_processor import *
from services_manager import *

def main():
    # 1. Ініціалізація сервісів
    drive, sheets = get_services()
    os.makedirs('temp_mebli', exist_ok=True)
    
    selected_queue = []
    has_global_failures = False
    
    # --- ВАРІАНТ 1: ПЕРЕВІРКА ГАРЯЧОЇ ПАПКИ ---
    print("🔍 Перевірка наявності файлів у гарячій папці...")
    hot_files = [] # код запиту drive.files().list() под HOT_FOLDER_ID
    
    if hot_files:
        print(f"🔥 Знайдено файли в гарячій папці ({len(hot_files)}). Працює Сценарій 1.")
        # 1. Скачуємо, зчитуємо метадані (get_intellectual_date, get_location_data)
        # 2. Групуємо за датою/локацією
        # 3. Відбираємо до 4-х елементів першої групи в selected_queue
    
    # --- ВАРІАНТ 2: ФОЛБЕК НА РЕЄСТР ТАБЛИЦІ ---
    else:
        print("📊 Гаряча папка порожня. Активуємо Сценарій 2 (Реєстр таблиці)...")
        # 1. Зчитуємо дані через sheets.spreadsheets().values().get()
        # 2. Відфільтровуємо тимчасові рядки
        # 3. Шукаємо мінімальне значення лічильника: min_counter = min(...)
        # 4. Групуємо найменш опубліковані рядки та формуємо selected_queue
        
    if not selected_queue:
        print("ℹ️ Черга порожня. Публікувати нічого.")
        return

    # --- ЗАГАЛЬНИЙ БЛОК ОБРОБКИ ТА ПУБЛІКАЦІЇ ---
    # Цей блок однаковий для обох варіантів:
    for item in selected_queue:
        # 1. ШІ Генерація опису (generate_story_caption)
        # 2. Оптимізація під сторіс (optimize_image_story / optimize_video_story)
        # 3. Накладання тексту на фото (overlay_text_on_image) або через FFmpeg для відео
        # 4. Завантаження на CDN (get_google_drive_direct_url)
        # 5. Публікація в Meta API + Очікування контейнера (wait_for_meta_container)
        
    # --- ФІНАЛІЗАЦІЯ ТА ОЧИЩЕННЯ ---
    # Сценарій 1: Переносимо опубліковане з гарячої папки в TRASH_FOLDER_ID на Диску
    # Сценарій 2: Збільшуємо лічильник в таблиці для використаних рядків (+1)
    
    # Очищення локальної папки temp_mebli
    # Фінальний акорд для GitHub Actions (sys.exit(1) якщо has_global_failures)

if __name__ == "__main__":
    main()
