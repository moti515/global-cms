import os
import sys
import time
import random
import base64
import subprocess
import textwrap
from datetime import datetime
import requests
from PIL import Image, ImageOps, ImageDraw, ImageFont  # Додано ImageDraw та ImageFont
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
    ss_time = max(0.5, duration / 2)
    cmd = [
        'ffmpeg', '-y', '-ss', str(ss_time), '-i', video_path,
        '-vframes', '1', '-q:v', '2', output_image_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return os.path.exists(output_image_path)

def sanitize_video(input_path):
    """Перезбирає відео за допомогою FFmpeg для виправлення індексів."""
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
    """Конвертує GIF-файл у формат MP4."""
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
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Помилка FFmpeg під час конвертації GIF {gif_path}: {e}")
        return False

# 🧠 ШІ ГЕНЕРАЦІЯ ОПИСУ
def generate_ai_metadata(file_path, date_str, location_geo):
    """Аналізує візуальний контент через Gemini."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    year = date_str.split('.')[-1] if date_str and '.' in date_str else "2026"
    location = location_geo if (location_geo and location_geo != "Невідоме місце") else ""
    
    if not gemini_key:
        print("⚠️ GEMINI_API_KEY не знайдено. Працює дефолтна метадата.")
        return "Естетика моменту", year, location

    temp_frame = None
    is_video = file_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.gif'))
    
    if is_video:
        temp_frame = file_path + "_ai_target.jpg"
        if not extract_frame_from_video(file_path, temp_frame):
            temp_frame = None

    target_image_path = temp_frame if is_video else file_path

    prompt = (
        "Ти професійний тревел-блогер, креативний копірайтер та експерт з вірусного контенту в TikTok.\n"
        "Подивись на це зображення. Напиши ОДИН ультра-сучасний, атмосферний або естетичний підпис "
        "(максимум 1 коротке речення або сильна фраза), який чітко передає вайб і суть того, що на екрані.\n"
        "Уникай банальщини. Зроби підпис живим, емоційним чи інтригуючим.\n"
        f"Контекст для атмосфери (НЕ згадуй ці слова і цифри у тексті прямо): Локація - {location}, Рік - {year}.\n"
        "КРИТИЧНА ЗАБОРОНА: Ні в якому разі НЕ пиши у самому підписі назву локації чи рік.\n"
        "Напиши текст виключно УКРАЇНСЬКОЮ мовою.\n"
        "Видай лише фінальний текст підпису і більше нічого."
    )

    models_to_try = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]
    trending_text = "Краса в простих деталях"

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
    
    if temp_frame and os.path.exists(temp_frame):
        os.remove(temp_frame)

    return trending_text, year, location


# 🎨 ГЕНЕРАЦІЯ АДАПТИВНИХ PNG-ОВЕРЛЕЇВ З АВТОПЕРЕНЕСЕННЯМ РЯДКІВ
def generate_tiktok_overlays(text_info, base_name, target_w=1080, target_h=1920):
    """
    Створює два окремі прозорі PNG-шари (для заголовка та метаданих).
    Автоматично переносить довгий нижній підпис, запобігаючи виходу за межі екрана.
    """
    trending_text, year, location = text_info
    
    title_overlay = Image.new('RGBA', (target_w, target_h), (0, 0, 0, 0))
    meta_overlay = Image.new('RGBA', (target_w, target_h), (0, 0, 0, 0))
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font_size_main = 46
    font_size_meta = 36
    
    try:
        font_main = ImageFont.truetype(font_path, font_size_main)
        font_meta = ImageFont.truetype(font_path, font_size_meta)
    except IOError:
        font_main = ImageFont.load_default()
        font_meta = ImageFont.load_default()

    try:
        from pilmoji import Pilmoji
        has_pilmoji = True
    except ImportError:
        has_pilmoji = False

    def draw_text_dynamic(canvas, position, content, font, fill_color, stroke_color=(0, 0, 0, 255), stroke_width=4):
        if has_pilmoji:
            with Pilmoji(canvas) as pilmoji:
                pilmoji.text(position, content, font=font, fill=fill_color, stroke_width=stroke_width, stroke_fill=stroke_color)
        else:
            d = ImageDraw.Draw(canvas)
            d.text(position, content, font=font, fill=fill_color, stroke_width=stroke_width, stroke_fill=stroke_color)

    # Використовуємо тимчасове полотно для замірів довжини тексту
    draw_measure = ImageDraw.Draw(title_overlay)

    # 1. Форматування головного тексту (Зверху по центру, макс. ширина 920px)
    title_lines = []
    if trending_text:
        words = trending_text.split()
        current_line = []
        for word in words:
            current_line.append(word)
            if draw_measure.textlength(" ".join(current_line), font=font_main) > (target_w - 160):
                current_line.pop()
                title_lines.append(" ".join(current_line))
                current_line = [word]
        if current_line:
            title_lines.append(" ".join(current_line))

    start_y = 250
    line_height = 60
    for i, line in enumerate(title_lines):
        bbox = draw_measure.textbbox((0, 0), line, font=font_main)
        text_w = bbox[2] - bbox[0]
        current_x = (target_w - text_w) // 2
        current_y = start_y + (i * line_height)
        draw_text_dynamic(title_overlay, (current_x, current_y), line, font_main, (255, 255, 255))

    # 2. Форматування метаданих (Знизу зліва, з автоматичним переносом)
    meta_parts = []
    if location and location != "Невідоме місце":
        meta_parts.append(location)
    if year:
        meta_parts.append(str(year))
        
    meta_text = " | ".join(meta_parts)
    
    meta_lines = []
    if meta_text:
        words = meta_text.split()
        current_line = []
        for word in words:
            current_line.append(word)
            # Залишаємо безпечні поля по 70px з кожного боку (1080 - 140 = 940px)
            if draw_measure.textlength(" ".join(current_line), font=font_meta) > (target_w - 140):
                current_line.pop()
                meta_lines.append(" ".join(current_line))
                current_line = [word]
        if current_line:
            meta_lines.append(" ".join(current_line))

    # Малюємо нижній текст. Якщо рядків декілька, вони будуть акуратно будуватися вниз від y=1600
    start_meta_y = 1600
    meta_line_height = 48
    for i, line in enumerate(meta_lines):
        current_y = start_meta_y + (i * meta_line_height)
        draw_text_dynamic(meta_overlay, (70, current_y), line, font_meta, (255, 240, 100))

    title_path = f"temp_title_{base_name}.png"
    meta_path = f"temp_meta_{base_name}.png"
    
    title_overlay.save(title_path, 'PNG')
    meta_overlay.save(meta_path, 'PNG')
    
    return title_path, meta_path


def process_video_item(input_path, output_path, text_info, target_w=1080, target_h=1920, ss=None, t=None, loops=0):
    """ОДИН ПРОХІД ОБРОБКИ ВІДЕО ЧЕРЕЗ PNG-ОВЕРЛЕЇ З ПРИМУСОВОЮ СТАНДАРТИЗАЦІЄЮ"""
    base_name = os.path.basename(input_path).rsplit('.', 1)[0]
    
    # Генеруємо шари тексту
    title_png, meta_png = generate_tiktok_overlays(text_info, base_name, target_w, target_h)
    
    cmd = ['ffmpeg', '-y']
    if loops > 0:
        cmd.extend(['-stream_loop', str(loops)])
        
    cmd.extend([
        '-i', input_path,
        '-i', title_png,
        '-i', meta_png
    ])
    
    has_audio = has_audio_stream(input_path)
    if not has_audio:
        cmd.extend(['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100'])
        
    if ss is not None:
        cmd.extend(['-ss', str(ss)])
    if t is not None:
        cmd.extend(['-t', str(t)])
        
    vf_base = f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black"
    filter_complex = f"[0:v]{vf_base}[bg]; [bg][1:v]overlay=0:0:enable='lt(t,6)'[tmp]; [tmp][2:v]overlay=0:0[outv]"
    
    cmd.extend([
        '-filter_complex', filter_complex,
        '-map', '[outv]'
    ])
    
    # Стандартизація відеопотоку (Додано -video_track_timescale 90000)
    cmd.extend([
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-r', str(FINAL_FPS),
        '-b:v', '3000k', '-maxrate', '4500k', '-bufsize', '9000k',
        '-video_track_timescale', '90000'
    ])
    
    # Стандартизація аудіопотоку (Примусово заганяємо в 44100Hz та Stereo для сумісності з фото)
    if has_audio:
        cmd.extend(['-map', '0:a', '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2'])
    else:
        cmd.extend(['-map', '[3:a]', '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2', '-shortest'])
        
    cmd.append(output_path)
    
    print(f"🎬 Обробка відео з ШІ-титрами (Адаптивний PNG): {os.path.basename(input_path)}")
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    for f in [title_png, meta_png]:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass
            
    return res.returncode == 0


def process_image_item(input_path, output_path, text_info, duration=4.0, target_w=1080, target_h=1920):
    """ОДИН ПРОХІД ОБРОБКИ ФОТО ЧЕРЕЗ PNG-ОВЕРЛЕЇ З ПАРАМЕТРАМИ, ЩО ЗБІГАЮТЬСЯ З ВІДЕО"""
    temp_jpg = output_path + "_pure_canvas.jpg"
    base_name = os.path.basename(input_path).rsplit('.', 1)[0]
    
    try:
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
            
        title_png, meta_png = generate_tiktok_overlays(text_info, base_name, target_w, target_h)
            
        vf_base = f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black"
        filter_complex = f"[0:v]{vf_base}[bg]; [bg][1:v]overlay=0:0:enable='lt(t,6)'[tmp]; [tmp][2:v]overlay=0:0[outv]"
            
        # Усі параметри відео та аудіо тепер ОДИН В ОДИН як у відео (bitrate, maxrate, bufsize, timescale, ar, ac)
        cmd = [
            'ffmpeg', '-y', '-loop', '1', '-i', temp_jpg,
            '-i', title_png,
            '-i', meta_png,
            '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-filter_complex', filter_complex,
            '-map', '[outv]', '-map', '[3:a]',
            '-c:v', 'libx264', '-t', str(duration),
            '-pix_fmt', 'yuv420p', '-r', str(FINAL_FPS),
            '-b:v', '3000k', '-maxrate', '4500k', '-bufsize', '9000k',
            '-video_track_timescale', '90000',
            '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
            '-shortest',
            output_path
        ]
        
        print(f"📸 Обробка фото з ШІ-титрами (Адаптивний PNG): {os.path.basename(input_path)}")
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        for f in [temp_jpg, title_png, meta_png]:
            if os.path.exists(f): 
                try: os.remove(f)
                except: pass
                
        return res.returncode == 0
    except Exception as e:
        print(f"⚠️ Помилка обробки photo {input_path}: {e}")
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
        print("⚠️ Фонова музика не знайдена.")
        return False

    music_file = MUSIC_FALLBACK_PATH
    if os.path.isdir(MUSIC_FALLBACK_PATH):
        tracks = [os.path.join(MUSIC_FALLBACK_PATH, f) for f in os.listdir(MUSIC_FALLBACK_PATH)
                  if f.lower().endswith(('.mp3', '.wav', '.m4a', '.aac'))]
        if not tracks:
            return False
        
        tracks.sort()
        music_file = random.SystemRandom().choice(tracks)
        print(f"🎵 Фоновий трек (обрано рандомно): {os.path.basename(music_file)}")

    cmd = [
        'ffmpeg', '-y',
        '-i', input_path,
        '-stream_loop', '-1', '-i', music_file,
        '-filter_complex', '[0:a]volume=1.0[orig];[1:a]volume=0.15[bg];[orig][bg]amix=inputs=2:duration=longest:dropout_transition=0',
        '-c:v', 'copy',
        '-c:a', 'aac', '-b:a', '128k',
        '-shortest',
        output_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception as e:
        print(f"⚠️ Помилка міксування звуку: {e}")
        return False
