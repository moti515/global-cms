import os
import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from moviepy.editor import VideoFileClip, ImageClip, concatenate_videoclips, AudioFileClip, TextClip, CompositeVideoClip

# --- НАЛАШТУВАННЯ ---
FOLDER_INPUT_ID = 'ID_ВАШОЇ_ВХІДНОЇ_ПАПКИ'
FOLDER_TRASH_ID = 'ID_ПАПКИ_ДЛЯ_ОБРОБЛЕНИХ_ФАЙЛІВ'
TARGET_DURATION = 30  # Цільова тривалість відео в секундах
MUSIC_FALLBACK_PATH = 'assets/trending_travel_music.mp3' # Музика за замовчуванням

# Авторизація в Google Drive (код з минулого кроку)
def get_drive_service():
    # Повертає налаштований сервіс drive api
    pass

# 1. ЗБІР ТА ГРУПУВАННЯ МЕДІАФАЙЛІВ
def get_and_group_files(service):
    query = f"'{FOLDER_INPUT_ID}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name, mimeType, createdTime)").execute()
    files = results.get('files', [])
    
    if not files:
        return {}

    # Групування за датою (YYYY-MM-DD)
    groups = {}
    for f in files:
        date_str = f['createdTime'].split('T')[0]
        if date_str not in groups:
            groups[date_str] = []
        groups[date_str].append(f)
        
    return groups

# 2. ЗАВАНТАЖЕННЯ ТА ОБРІЗАННЯ НА ЛЬОТУ
def process_media_group(service, file_list):
    clips = []
    # Рахуємо, скільки часу виділити на один файл, щоб вкластися в ліміт
    clip_duration = max(2.5, TARGET_DURATION / len(file_list)) 
    
    for f in file_list:
        file_id = f['id']
        file_name = f['name']
        mime = f['mimeType']
        
        # Завантажуємо файл локально (тимчасово)
        # (Код завантаження через MediaIoBaseDownload...)
        local_path = file_name 
        
        if 'video' in mime:
            try:
                # Обробка об'ємних відео: відкриваємо і одразу ріжемо фрагмент
                full_video = VideoFileClip(local_path)
                # Беремо 3 секунди з середини відео, щоб не вантажити систему
                start_time = max(0, full_video.duration / 2 - clip_duration / 2)
                end_time = min(full_video.duration, start_time + clip_duration)
                
                trimmed = full_video.subclip(start_time, end_time).resize(newsize=(1080, 1920))
                clips.append(trimmed)
            except Exception as e:
                print(f"Помилка обробки відео {file_name}: {e}")
                
        elif 'image' in mime:
            # Перетворюємо фото на динамічний кліп
            img_clip = ImageClip(local_path).set_duration(clip_duration).resize(newsize=(1080, 1920))
            clips.append(img_clip)
            
    return clips

# 3. ШІ АНАЛІЗ ТА ГЕНЕРАЦІЯ ТЕКСТУ (Приклад структури)
def generate_ai_metadata(date_context):
    # Тут може бути виклик OpenAI / Gemini API
    # На основі дати, гео-тегів або аналізу першого кадру ШІ повертає текст:
    location = "Карпати, Україна"
    year = date_context.split('-')[0]
    trending_text = "Місце, куди хочеться повертатися знову і знову ✨"
    return trending_text, year, location

# 4. МОНТАЖ ТА НАКЛАДАННЯ ЕФЕКТІВ
def compile_final_video(clips, text_info):
    trending_text, year, location = text_info
    
    # Склеюємо всі шматочки в один трек
    final_video = concatenate_videoclips(clips, method="compose")
    
    # Перевірка аудіо: якщо звуку немає або він занадто тихий, додаємо тренд
    if final_video.audio is None:
        bg_music = AudioFileClip(MUSIC_FALLBACK_PATH).set_duration(final_video.duration)
        final_video = final_video.set_audio(bg_music)
        
    # Створення текстових оверлеїв (Потребує встановленого ImageMagick на сервері)
    # Якщо ImageMagick немає, текст можна «випалювати» через ffmpeg або бібліотеку Pillow
    main_txt = TextClip(trending_text, fontsize=50, color='white', font='Arial-Bold', method='caption', size=(900, None)).set_position(('center', 400)).set_duration(final_video.duration)
    meta_txt = TextClip(f"{location} | {year}", fontsize=40, color='yellow', font='Arial').set_position(('center', 1500)).set_duration(final_video.duration)
    
    # Збираємо фінальний пак
    result_video = CompositeVideoClip([final_video, main_txt, meta_txt])
    output_name = f"ready_tiktok_{year}.mp4"
    result_video.write_videofile(output_name, fps=30, codec="libx264", audio_codec="aac")
    
    return output_name

# 5. ПЕРЕМІЩЕННЯ В КОРЗИНУ GOOGLE DRIVE
def move_files_to_trash(service, file_list):
    for f in file_list:
        file_id = f['id']
        # Отримуємо поточних батьків файлу
        file = service.files().get(fileId=file_id, fields='parents').execute()
        previous_parents = ",".join(file.get('parents'))
        
        # Переміщуємо файл, прибираючи старого батька та додаючи папку "Корзина"
        service.files().update(
            fileId=file_id,
            addParents=FOLDER_TRASH_ID,
            removeParents=previous_parents,
            fields='id, parents'
        ).execute()
    print("Всі оброблені файли переміщено до архіву/корзини.")

# ГОЛОВНИЙ ЗАПУСК
def main():
    service = get_drive_service()
    groups = get_and_group_files(service)
    
    if not groups:
        print("Нічого обробляти.")
        return
        
    # Беремо першу доступну групу (наприклад, найдавнішу дату)
    target_date = list(groups.keys())[0]
    media_files = groups[target_date]
    
    print(f"Обробляємо групу за дату: {target_date}. Файлів: {len(media_files)}")
    
    # Процесинг
    clips = process_media_group(service, media_files)
    text_info = generate_ai_metadata(target_date)
    
    final_file = compile_final_video(clips, text_info)
    print(f"Відео готове до публікації: {final_file}")
    
    # Очищення диска
    move_files_to_trash(service, media_files)
    
    # Тут додається блок автоматичного постінгу в TikTok (як обговорювали раніше)

if __name__ == '__main__':
    main()
