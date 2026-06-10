import os
import sys
import time
import random
import base64
import subprocess
from datetime import datetime
import requests
from PIL import Image, ImageOps
from config_tiktok import FINAL_FPS, MUSIC_FALLBACK_PATH

def get_video_duration(input_path):
    """Отримує тривалість відео за допомогою ffprobe."""
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', input_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        return float(res.stdout.strip())
    except ValueError:
        return 0.0

def has_audio_stream(input_path):
    """Перевіряє, чи має відео аудіодорожку."""
    cmd = [
        'ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 
        'stream=codec_type', '-of', 'default=noprint_wrappers=1:nokey=1', input_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return bool(res.stdout.strip())

def extract_frame_from_video(video_path, output_image_path):
    """Витягує один кадр із середини відео для аналізу ШІ."""
    duration = get_video_duration(video_path)
    ss_time = max(0.5, duration / 2) # беремо кадр із середини ролика
    cmd = [
        'ffmpeg', '-y', '-ss', str(ss_time), '-i', video_path,
        '-vframes', '1', '-q:v', '2', output_image_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return os.path.exists(output_image_path)

def sanitize_video(input_path):
    """
    Перезбирає відео за допомогою FFmpeg, щоб виправити пошкоджені індекси,
    проблеми з першим кадром та кодеками.
    """
    temp_path = input_path.replace(".mp4", "_sanitized.mp4")
    print(f"🔧 [FFmpeg] Лікування файлу: {os.path.basename(input_path)}...")   

    cmd = [
        'ffmpeg', '-y',
        '-i', input_path,
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        temp_path
    ]    

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(temp_path):
            os.replace(temp_path, input_path)
            print(f"✅ Файл успішно відновлено.")
            return True

    except Exception as e:
        print(f"⚠️ Не вдалося вилікувати відео через FFmpeg: {e}")
        if os.path.exists(temp_path): os.remove(temp_path)
    return False

def gif_to_mp4(gif_path, mp4_path):
    """
    Конвертує GIF-файл у формат MP4 за допомогою FFmpeg.
    Додано фільтр scale, щоб ширина та висота ділилися на 2 (вимога кодека H.264).
    """
    if not os.path.exists(gif_path):
        print(f"❌ Помилка: GIF файл не знайдено: {gif_path}")
        return False
        
    cmd = [
        'ffmpeg', '-y',
        '-i', gif_path,
        '-movflags', 'faststart',
        '-pix_fmt', 'yuv420p',
        '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
        mp4_path
    ]
    
    try:
        # Запускаємо FFmpeg в тихійному режимі
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Помилка FFmpeg під час конвертації GIF {gif_path}: {e}")
        return False

# 🧠 ШІ ГЕНЕРАЦІЯ СУЧАСНОГО ОПИСУ ДЛЯ TIKTOK
def generate_ai_metadata(file_path, date_str, location_geo):
    """
    Аналізує візуальний контент (фото або кадр з відео) через Gemini 
    та повертає трендовий підпис, рік і локацію.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY")
    year = date_str.split('.')[-1] if date_str and '.' in date_str else "2026"
    location = location_geo if (location_geo and location_geo != "Невідоме місце") else "Магія природи"
    
    if not gemini_key:
        print("⚠️ GEMINI_API_KEY не знайдено. Працює дефолтна метадата.")
        return "Естетика моменту", year, location

    # Визначаємо, з чим маємо справу, та готуємо картинку для ШІ
    temp_frame = None
    is_video = file_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.gif'))
    
    if is_video:
        temp_frame = file_path + "_ai_target.jpg"
        if not extract_frame_from_video(file_path, temp_frame):
            temp_frame = None # якщо не вдалося витягти кадр

    target_image_path = temp_frame if is_video else file_path

    # Промпт заточений під сучасні тренди travel/aesthetic відео
    prompt = (
        "Ти професійний тревел-блогер, креативний копірайтер та експерт з вірусного контенту в TikTok та Reels.\n"
        "Подивись на це зображення. Напиши ОДИН ультра-сучасний, атмосферний або естетичний підпис "
        "(максимум 1 коротке речення або сильна фраза), який чітко передає вайб і суть того, що на екрані.\n"
        "Уникай банальщини на кшталт 'Ласкаво просимо', 'Подивіться на це'. Зроби підпис живим, емоційним чи інтригуючим.\n"
        f"Контекст події: Локація - {location}, Рік - {year}.\n"
        "Напиши текст виключно УКРАЇНСЬКОЮ мовою. КРИТИЧНО: НЕ використовуй емодзі, смайли, лапки чи хештеги.\n"
        "Видай ЛИШЕ фінальний текст підпису і більше нічого."
    )

    models_to_try = ["gemini-2.5-flash", "gemini-1.5-flash"]
    trending_text = "Краса в простих деталях" # дефолт

    if target_image_path and os.path.exists(target_image_path):
        try:
            with open(target_image_path, "rb") as f:
                image_bytes = f.read()
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": "image/jpeg", "data": base64_image}}
                    ]
                }]
            }
            
            for model in models_to_try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
                try:
                    res = requests.post(url, json=payload, timeout=15).json()
                    if 'candidates' in res and res['candidates']:
                        text_output = res['candidates'][0]['content']['parts'][0]['text'].strip()
                        if text_output:
                            trending_text = text_output
                            break
                except:
                    continue
        except Exception as e:
            print(f"⚠️ Помилка ШІ аналізу медіафайлу: {e}")
    
    # Вичищаємо тимчасовий кадр, якщо він створювався
    if temp_frame and os.path.exists(temp_frame):
        os.remove(temp_frame)

    return trending_text, year, location

def get_ffmpeg_filters(text_info, target_w=1080, target_h=1920):
    """Генерує єдині фільтри геометрії та тексту для фото і відео."""
    trending_text, year, location = text_info
    
    # Екрануємо символи для FFmpeg drawtext
    clean_title = trending_text.replace("'", "").replace(":", "\\:").replace(",", "\\,")
    clean_meta = f"{location} | {year}".replace("'", "").replace(":", "\\:").replace(",", "\\,")
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    
    # Базове масштабування з чорними полями
    vf = f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black"
    
    if os.path.exists(font_path):
        # 1. Головний підпис: по центру вгорі (y=250), білий, чорний контур (borderw=5), зникає через 3 секунди (enable='lt(t,3)')
        vf += f",drawtext=fontfile={font_path}:text='{clean_title}':x=(w-text_w)/2:y=250:fontsize=46:fontcolor=white:borderw=5:bordercolor=black:enable='lt(t,3)':fix_bounds=1"
        
        # 2. Метадата: знизу зліва (x=70, y=1600 - безпечна зона TikTok), світло-жовтий, чорний контур, показується ЗАВЖДИ
        vf += f",drawtext=fontfile={font_path}:text='{clean_meta}':x=70:y=1600:fontsize=36:fontcolor=yellow:borderw=4:bordercolor=black:fix_bounds=1"
        
    return vf

def process_video_item(input_path, output_path, text_info, target_w=1080, target_h=1920, ss=None, t=None, loops=0):
    """ОДИН ПРОХІД ОБРОБКИ ВІДЕО"""
    vf_filters = get_ffmpeg_filters(text_info, target_w, target_h)
    cmd = ['ffmpeg', '-y']
    
    if loops > 0:
        cmd.extend(['-stream_loop', str(loops)])
        
    cmd.extend(['-i', input_path])
    
    has_audio = has_audio_stream(input_path)
    if not has_audio:
        cmd.extend(['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100'])
        
    if ss is not None:
        cmd.extend(['-ss', str(ss)])
    if t is not None:
        cmd.extend(['-t', str(t)])
        
    cmd.extend([
        '-vf', vf_filters,
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-r', str(FINAL_FPS),
        '-b:v', '3000k', '-maxrate', '4500k', '-bufsize', '9000k',
    ])
    
    if has_audio:
        cmd.extend(['-c:a', 'aac', '-b:a', '128k'])
    else:
        cmd.extend(['-c:a', 'aac', '-b:a', '128k', '-shortest'])
        
    cmd.append(output_path)
    
    print(f"🎬 Обробка відео з ШІ-титрами: {os.path.basename(input_path)}")
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0

def process_image_item(input_path, output_path, text_info, duration=3.0, target_w=1080, target_h=1920):
    """ОДИН ПРОХІД ОБРОБКИ ФОТО (Тепер рендеринг тексту теж через FFmpeg!)"""
    temp_jpg = output_path + "_pure_canvas.jpg"
    
    try:
        # Pillow тепер тільки пропорційно масштабує картинку на чорне полотно (без тексту)
        with Image.open(input_path) as img:
            img = ImageOps.exif_transpose(img)
            
            img_w, img_h = img.size
            scale = min(target_w / img_w, target_h / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)
            
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            canvas = Image.new('RGB', (target_w, target_h), (15, 15, 15))
            offset = ((target_w - img.width) // 2, (target_h - img.height) // 2)
            canvas.paste(img, offset)
            canvas.save(temp_jpg, 'JPEG', quality=95)
            
        # Отримуємо ідентичні фільтри тексту
        vf_filters = get_ffmpeg_filters(text_info, target_w, target_h)
            
        # Створюємо відео з фото та накладаємо титри через FFmpeg (динаміка зникнення працюватиме ідеально)
        cmd = [
            'ffmpeg', '-y', '-loop', '1', '-i', temp_jpg,
            '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-vf', vf_filters,
            '-c:v', 'libx264', '-t', str(duration),
            '-pix_fmt', 'yuv420p', '-r', str(FINAL_FPS),
            '-b:v', '2500k', '-c:a', 'aac', '-b:a', '128k', '-shortest',
            output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(temp_jpg): os.remove(temp_jpg)
        return True
    except Exception as e:
        print(f"⚠️ Помилка обробки фото {input_path}: {e}")
        if os.path.exists(temp_jpg): os.remove(temp_jpg)
        return False

def fast_concat_videos(video_paths, final_output_path):
    """Миттєво склеює вже оптимізовані відео."""
    list_path = "concat_list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for p in video_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
            
    cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path,
        '-c', 'copy',
        final_output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(list_path): os.remove(list_path)

def add_background_music(input_path, output_path):
    """Накладає випадкову фонову музику."""
    if not MUSIC_FALLBACK_PATH or not os.path.exists(MUSIC_FALLBACK_PATH):
        print("⚠️ Фонова музика не знайдено.")
        return False

    music_file = MUSIC_FALLBACK_PATH
    if os.path.isdir(MUSIC_FALLBACK_PATH):
        tracks = [os.path.join(MUSIC_FALLBACK_PATH, f) for f in os.listdir(MUSIC_FALLBACK_PATH)
                  if f.lower().endswith(('.mp3', '.wav', '.m4a', '.aac'))]
        if not tracks:
            return False
        music_file = random.choice(tracks)
        print(f"🎵 Фоновий трек: {os.path.basename(music_file)}")

    cmd = [
        'ffmpeg', '-y',
        '-i', input_path,
        '-stream_loop', '-1', '-i', music_file,
        '-filter_complex', '[0:a]volume=1.0[orig];[1:a]volume=0.15[bg];[orig][bg]amix=inputs=2:duration=first:dropout_transition=0',
        '-c:v', 'copy',
        '-c:a', 'aac', '-b:a', '128k',
        output_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception as e:
        print(f"⚠️ Помилка міксування звуку: {e}")
        return False
