import os
import sys
import time
import random
import subprocess
import numpy as np
from PIL import Image, ImageOps
from moviepy.editor import VideoFileClip, ImageClip, concatenate_videoclips, AudioFileClip, TextClip, CompositeVideoClip
from moviepy.audio.AudioClip import AudioArrayClip
from config import TEST_FPS, MUSIC_FALLBACK_PATH

def fit_video_with_background(clip, target_w=1080, target_h=1920):
    target_ar = target_w / target_h
    clip_ar = clip.w / clip.h
    if clip_ar > target_ar:
        resized = clip.resize(width=target_w)
    else:
        resized = clip.resize(height=target_h)
    return CompositeVideoClip([resized.set_position("center")], size=(target_w, target_h))

def prepare_padded_image(local_path, output_path, target_w=1080, target_h=1920):
    with Image.open(local_path) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        background = Image.new('RGB', (target_w, target_h), (0, 0, 0))
        offset = ((target_w - img.width) // 2, (target_h - img.height) // 2)
        background.paste(img, offset)
        background.save(output_path, 'JPEG', quality=95)

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

def compile_final_video(clips, text_info):
    trending_text, year, location = text_info
    try:
        final_video = concatenate_videoclips(clips, method="compose")
        if final_video.audio is None:
            if os.path.exists(MUSIC_FALLBACK_PATH):
                bg_music = AudioFileClip(MUSIC_FALLBACK_PATH).set_duration(final_video.duration)
                final_video = final_video.set_audio(bg_music)
            else:
                silence_array = np.zeros((int(44100 * final_video.duration), 2))
                silent_audio = AudioArrayClip(silence_array, fps=44100).set_duration(final_video.duration)
                final_video = final_video.set_audio(silent_audio)
            
        main_txt = TextClip(trending_text, fontsize=48, color='white', font='Arial-Bold', method='caption', size=(950, None)).set_position(('center', 400)).set_duration(final_video.duration)
        meta_txt = TextClip(f"{location} | {year}", fontsize=38, color='yellow', font='Arial').set_position(('center', 1500)).set_duration(final_video.duration)
        
        result_video = CompositeVideoClip([final_video, main_txt, meta_txt])
        output_name = f"ready_tiktok_{int(time.time())}.mp4"
        
        result_video.write_videofile(
            output_name, 
            fps=TEST_FPS, 
            codec="libx264", 
            audio_codec="aac",
            bitrate="2500k",
            ffmpeg_params=["-pix_fmt", "yuv420p"]
        )
        result_video.close()
        final_video.close()
        return output_name
    except Exception as e:
        sys.exit(f"❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Помилка рендерингу відео: {e}")
