import os
import sys
import time
import random
import subprocess
from PIL import Image, ImageOps, ImageDraw, ImageFont
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

def gif_to_mp4(input_path, output_path):
    print(f"🎞️ Конвертуємо GIF {input_path} в MP4 відео...")
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-movflags', 'faststart', '-pix_fmt', 'yuv420p',
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
        output_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg Error: {res.stderr}")

def generate_ai_metadata(date_str, location_geo):
    year = date_str.split('.')[-1] if '.' in date_str else "2026"
    generic_phrases = [
        "Магія природи, яка заліковує душу ✨",
        "Там, де час зупиняється... 🌿",
        "Краса в простих деталях ⛰️",
        "Естетика цього світу зашкалює 😍",
        "Моменти, які залишаються назавжди"
    ]
    location_phrases = [
        f"Місце, куди хочеться повертатися: {location_geo} ✨",
        f"Атмосфера цього дня: {location_geo} 🌍",
        f"Відкриваючи неймовірні куточки: {location_geo} 🌿",
        f"Подорож, що надихає | {location_geo} 🧭",
        f"Спогади з серця: {location_geo}"
    ]
    if location_geo and location_geo != "Невідоме місце":
        trending_text = random.choice(location_phrases)
        location = location_geo
    else:
        trending_text = random.choice(generic_phrases)
        location = "Магія природи"
    return trending_text, year, location

def process_video_item(input_path, output_path, text_info, target_w=1080, target_h=1920, ss=None, t=None, loops=0):
    """
    ОДИН ПРОХІД: Лікує відео, масштабує з падінгом під 1080x1920, 
    накладає текст через FFmpeg drawtext, підтримує обрізання та зациклення.
    Гарантує наявність аудіопотоку для подальшого швидкого склеювання.
    """
    trending_text, year, location = text_info
    clean_title = trending_text.replace("'", "").replace(":", "\\:")
    clean_meta = f"{location} | {year}".replace("'", "").replace(":", "\\:")
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    
    # Базовий фільтр геометрії
    vf_filters = f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black"
    
    if os.path.exists(font_path):
        vf_filters += f",drawtext=fontfile={font_path}:text='{clean_title}':x=(w-text_w)/2:y=400:fontsize=44:fontcolor=white:box=1:boxcolor=black@0.4:boxborderw=15:fix_bounds=1"
        vf_filters += f",drawtext=fontfile={font_path}:text='{clean_meta}':x=(w-text_w)/2:y=1500:fontsize=36:fontcolor=yellow:box=1:boxcolor=black@0.4:boxborderw=10:fix_bounds=1"

    cmd = ['ffmpeg', '-y']
    
    # Якщо потрібно зациклити вхідний потік
    if loops > 0:
        cmd.extend(['-stream_loop', str(loops)])
        
    cmd.extend(['-i', input_path])
    
    # Додаємо генератор тиші, якщо у відео немає власного звуку
    has_audio = has_audio_stream(input_path)
    if not has_audio:
        cmd.extend(['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100'])
        
    # Параметри обрізання за часом (якщо передані)
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
    
    # Налаштування аудіокодеків
    if has_audio:
        cmd.extend(['-c:a', 'aac', '-b:a', '128k'])
    else:
        cmd.extend(['-c:a', 'aac', '-b:a', '128k', '-shortest'])
        
    cmd.append(output_path)
    
    print(f"🎬 Оптимізація та лікування відео: {os.path.basename(input_path)}")
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0

def process_image_item(input_path, output_path, text_info, duration=3.0, target_w=1080, target_h=1920):
    """
    Оптимізує фото через Pillow, накладає текст і через FFmpeg створює MP4 ролик.
    Додає тиху аудіодорожку, щоб файл мав ідентичну структуру з іншими відео.
    """
    trending_text, year, location = text_info
    temp_jpg = output_path + "_temp.jpg"
    
    try:
        with Image.open(input_path) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
            canvas = Image.new('RGB', (target_w, target_h), (15, 15, 15))
            offset = ((target_w - img.width) // 2, (target_h - img.height) // 2)
            canvas.paste(img, offset)
            
            draw = ImageDraw.Draw(canvas)
            try:
                font_main = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44)
                font_meta = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
            except IOError:
                font_main = font_meta = ImageFont.load_default()
                
            draw.text((target_w//2, 400), trending_text, font=font_main, fill=(255, 255, 255), anchor="mm")
            draw.text((target_w//2, 1500), f"{location} | {year}", font=font_meta, fill=(255, 255, 0), anchor="mm")
            
            canvas.save(temp_jpg, 'JPEG', quality=95)
            
        # Створення відео з фото + додавання тихого аудіо для сумісності з concat
        cmd = [
            'ffmpeg', '-y', '-loop', '1', '-i', temp_jpg,
            '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
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
    """
    Миттєво склеює вже оптимізовані відео між собою без перекодування відеопотоку!
    """
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
