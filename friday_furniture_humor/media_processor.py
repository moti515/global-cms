import os
import requests
import subprocess
import uuid
from PIL import Image
from pillow_heif import register_heif_opener

from config import IMAGEKIT_PRIVATE_KEY, IMGBB_API_KEY, TEMP_MEDIA_DIR

register_heif_opener()


def get_safe_filename(orig_name: str, prefix: str = "", target_ext: str = None) -> str:
    """
    Генерує безпечне латинське ім'я файлу (ASCII), що повністю усуває
    помилки Meta/Instagram API через кирилицю, спецсимволи та подвійні розширення.
    """
    if target_ext:
        ext = target_ext.lower() if target_ext.startswith('.') else f".{target_ext.lower()}"
    else:
        ext = os.path.splitext(orig_name)[1].lower()
        if not ext or ext not in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.mp4', '.mov', '.avi']:
            ext = '.jpg'

    unique_id = uuid.uuid4().hex[:10]
    if prefix:
        return f"{prefix}_{unique_id}{ext}"
    return f"{unique_id}{ext}"


def convert_and_format_media(local_path, orig_name, mode):
    """
    Конвертує HEIC/GIF та налаштовує пропорції зображень і відео під вимоги Feed / Stories,
    використовуючи безпечні латинські імена файлів.
    """
    lower_name = orig_name.lower()
    mime_type = "image/jpeg" if lower_name.endswith(('.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp')) else "video/mp4"
    final_upload_path = local_path

    # 1. Конвертація форматів із безпечними іменами та правильними розширеннями
    if lower_name.endswith('.gif'):
        safe_gif_name = get_safe_filename(orig_name, prefix="gif_conv", target_ext=".mp4")
        mp4_path = os.path.join(TEMP_MEDIA_DIR, safe_gif_name)

        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', local_path,
            '-movflags', '+faststart',
            '-pix_fmt', 'yuv420p',
            '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
            '-c:v', 'libx264',
            mp4_path
        ]
        subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        final_upload_path = mp4_path
        mime_type = "video/mp4"

    elif lower_name.endswith(('.heic', '.heif')):
        safe_jpg_name = get_safe_filename(orig_name, prefix="heic_conv", target_ext=".jpg")
        jpg_path = os.path.join(TEMP_MEDIA_DIR, safe_jpg_name)
        with Image.open(local_path) as img:
            img.convert('RGB').save(jpg_path, 'JPEG', quality=90)
        final_upload_path = jpg_path
        mime_type = "image/jpeg"

    files_to_publish = [final_upload_path]

    # 2. Оптимізація пропорцій для Постів (Feed)
    if mode == 'post' and mime_type == "image/jpeg":
        try:
            with Image.open(final_upload_path) as img:
                img = img.convert('RGB')
                w, h = img.size
                ratio = w / h

                if ratio < 0.8 or ratio > 1.91:
                    print(f"📐 Оптимізація Поста: Пропорції картинки ({ratio:.2f}) коригуються...")
                    safe_post_name = get_safe_filename(orig_name, prefix="post_padded", target_ext=".jpg")
                    padded_post_path = os.path.join(TEMP_MEDIA_DIR, safe_post_name)

                    if ratio < 0.8:
                        new_w, new_h = int(h * 0.8), h
                    else:
                        new_w, new_h = w, int(w / 1.91)

                    canvas = Image.new('RGB', (new_w, new_h), (255, 255, 255))
                    canvas.paste(img, ((new_w - w) // 2, (new_h - h) // 2))
                    canvas.save(padded_post_path, 'JPEG', quality=95)

                    if final_upload_path != local_path and os.path.exists(final_upload_path):
                        os.remove(final_upload_path)
                    files_to_publish = [padded_post_path]
        except Exception as e:
            print(f"⚠️ Помилка калібрування геометрії поста: {e}")

    # 3. Оптимізація та нарізка під Сторіз (1080x1920)
    elif mode == 'story' and mime_type == "image/jpeg":
        print("📐 Режим Сторіс: вписуємо зображення у формат 1080x1920...")
        safe_story_name = get_safe_filename(orig_name, prefix="story_padded", target_ext=".jpg")
        story_path = os.path.join(TEMP_MEDIA_DIR, safe_story_name)
        try:
            with Image.open(final_upload_path) as img:
                img = img.convert('RGB')
                orig_w, orig_h = img.size
                target_w, target_h = 1080, 1920
                canvas = Image.new('RGB', (target_w, target_h), (20, 20, 20))

                scale = min(target_w / orig_w, target_h / orig_h)
                new_w, new_h = int(orig_w * scale), int(orig_h * scale)

                resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                canvas.paste(resized_img, ((target_w - new_w) // 2, (target_h - new_h) // 2))
                canvas.save(story_path, 'JPEG', quality=95)

            if final_upload_path != local_path and os.path.exists(final_upload_path):
                os.remove(final_upload_path)
            files_to_publish = [story_path]
        except Exception as e:
            print(f"⚠️ Не вдалося відформатувати Сторіс: {e}")

    elif mode == 'story' and mime_type == "video/mp4":
        print("📐 Режим Сторіс для ВІДЕО: нарізаємо на частини по 50 сек (1080x1920)...")
        part_prefix = f"story_part_{uuid.uuid4().hex[:8]}"
        segment_pattern = os.path.join(TEMP_MEDIA_DIR, f"{part_prefix}_%03d.mp4")

        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', final_upload_path,
            '-f', 'segment', '-segment_time', '50', '-reset_timestamps', '1',
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black',
            '-c:v', 'libx264', '-profile:v', 'high', '-level', '4.2', '-crf', '23', '-preset', 'fast',
            '-g', '60', '-keyint_min', '60', '-sc_threshold', '0', '-r', '30',
            '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-movflags', '+faststart', '-pix_fmt', 'yuv420p',
            segment_pattern
        ]

        result = subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            generated_parts = sorted([
                os.path.join(TEMP_MEDIA_DIR, f)
                for f in os.listdir(TEMP_MEDIA_DIR)
                if f.startswith(part_prefix) and f.endswith('.mp4')
            ])
            if generated_parts:
                files_to_publish = generated_parts
                if final_upload_path != local_path and os.path.exists(final_upload_path):
                    os.remove(final_upload_path)

    return files_to_publish, mime_type


def extract_frame_from_video(video_path):
    """Витягує один кадр із відео для передачі ШІ на аналіз."""
    analysis_image = os.path.join(TEMP_MEDIA_DIR, 'video_frame.jpg')
    subprocess.run(['ffmpeg', '-y', '-i', video_path, '-ss', '00:00:01', '-vframes', '1', analysis_image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return analysis_image


def get_google_drive_direct_url(file_id, local_file_path=None):
    """Каскадне завантаження на зовнішні хостинги для отримання прямого посилання (Meta API)."""
    if local_file_path and os.path.exists(local_file_path):
        raw_filename = os.path.basename(local_file_path)
        is_video = raw_filename.lower().endswith(('.mp4', '.mov', '.avi'))
        mime_type = "video/mp4" if is_video else "image/jpeg"

        clean_filename = raw_filename
        browser_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

        # 1️⃣ Litterbox
        print(f"☁️ Завантажуємо файл {clean_filename} на Litterbox...", flush=True)
        try:
            with open(local_file_path, 'rb') as f:
                res = requests.post(
                    'https://litterbox.catbox.moe/resources/internals/api.php',
                    data={'reqtype': 'fileupload', 'time': '1h'},
                    files={'fileToUpload': (clean_filename, f, mime_type)},
                    headers=browser_headers,
                    timeout=(10, 60)
                )
            response_text = res.text.strip()
            if res.status_code == 200 and response_text.startswith('http'):
                print(f"🔗 Отримано посилання від Litterbox: {response_text}", flush=True)
                return response_text, None
        except Exception as e:
            print(f"⚠️ Litterbox недоступний: {e}. Переходимо до ImageKit...")

        # 2️⃣ ImageKit.io
        if IMAGEKIT_PRIVATE_KEY:
            print(f"☁️ Завантажуємо файл {clean_filename} на ImageKit.io...", flush=True)
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://upload.imagekit.io/api/v1/files/upload',
                        auth=(IMAGEKIT_PRIVATE_KEY, ''),
                        files={'file': (clean_filename, f, mime_type)},
                        data={'fileName': clean_filename, 'useUniqueFileName': 'true'},
                        timeout=90
                    )
                if res.status_code in [200, 201]:
                    res_data = res.json()
                    return res_data.get('url'), res_data.get('fileId')
            except Exception as e:
                print(f"⚠️ Помилка ImageKit: {e}")

        # 3️⃣ Tmpfiles.org
        print(f"☁️ Завантажуємо файл {clean_filename} на Tmpfiles.org...", flush=True)
        try:
            with open(local_file_path, 'rb') as f:
                res = requests.post(
                    'https://tmpfiles.org/api/v1/upload',
                    files={'file': (clean_filename, f, mime_type)},
                    data={'expire': '86400'},
                    headers=browser_headers,
                    timeout=(10, 60)
                )
            if res.status_code == 200:
                res_data = res.json()
                if res_data.get('status') == 'success':
                    page_url = res_data.get('data', {}).get('url')
                    if page_url:
                        direct_url = page_url.replace('tmpfiles.org/', 'tmpfiles.org/dl/')
                        print(f"🔗 Отримано пряме посилання від Tmpfiles: {direct_url}", flush=True)
                        return direct_url, None
        except Exception as e:
            print(f"⚠️ Помилка Tmpfiles: {e}")

        # 4️⃣ ImgBB
        if IMGBB_API_KEY and mime_type == "image/jpeg":
            print(f"☁️ Завантажуємо фото {clean_filename} на ImgBB...", flush=True)
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://api.imgbb.com/1/upload',
                        data={'key': IMGBB_API_KEY, 'expiration': 86400},
                        files={'image': (clean_filename, f, mime_type)},
                        timeout=30
                    ).json()
                if res.get('success'):
                    return res['data']['url'], None
            except Exception as e:
                print(f"⚠️ Помилка ImgBB: {e}")

    return f"https://docs.google.com/uc?export=download&id={file_id}", None


def delete_from_imagekit(file_id: str):
    """Видаляє тимчасовий файл з ImageKit.io."""
    if not file_id or not IMAGEKIT_PRIVATE_KEY:
        return
    url = f"https://api.imagekit.io/v1/files/{file_id}"
    try:
        requests.delete(url, auth=(IMAGEKIT_PRIVATE_KEY, ''), timeout=20)
        print(f"🗑️ Тимчасовий файл {file_id} видалено з ImageKit.")
    except Exception as e:
        print(f"⚠️ Помилка видалення з ImageKit: {e}")
 if res.status_code == 200:
                res_data = res.json()
                if res_data.get('status') == 'success':
                    page_url = res_data.get('data', {}).get('url')
                    if page_url:
                        direct_url = page_url.replace('tmpfiles.org/', 'tmpfiles.org/dl/')
                        print(f"🔗 Отримано пряме посилання від Tmpfiles: {direct_url}", flush=True)
                        return direct_url, None
        except Exception as e:
            print(f"⚠️ Помилка Tmpfiles: {e}")

        # 4️⃣ ImgBB
        if IMGBB_API_KEY and mime_type == "image/jpeg":
            print(f"☁️ Завантажуємо фото {clean_filename} на ImgBB...", flush=True)
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://api.imgbb.com/1/upload',
                        data={'key': IMGBB_API_KEY, 'expiration': 86400},
                        files={'image': (clean_filename, f, mime_type)},
                        timeout=30
                    ).json()
                if res.get('success'):
                    return res['data']['url'], None
            except Exception as e:
                print(f"⚠️ Помилка ImgBB: {e}")

    return f"https://docs.google.com/uc?export=download&id={file_id}", None


def delete_from_imagekit(file_id: str):
    """Видаляє тимчасовий файл з ImageKit.io."""
    if not file_id or not IMAGEKIT_PRIVATE_KEY:
        return
    url = f"https://api.imagekit.io/v1/files/{file_id}"
    try:
        requests.delete(url, auth=(IMAGEKIT_PRIVATE_KEY, ''), timeout=20)
        print(f"🗑️ Тимчасовий файл {file_id} видалено з ImageKit.")
    except Exception as e:
        print(f"⚠️ Помилка видалення з ImageKit: {e}")
                     print(f"🔗 Отримано пряме посилання від Tmpfiles: {direct_url}", flush=True)
                        return direct_url, None
        except Exception as e:
            print(f"⚠️ Помилка Tmpfiles: {e}")

        # 4️⃣ ImgBB
        if IMGBB_API_KEY and mime_type == "image/jpeg":
            print(f"☁️ Завантажуємо фото {clean_filename} на ImgBB...", flush=True)
            try:
                with open(local_file_path, 'rb') as f:
                    res = requests.post(
                        'https://api.imgbb.com/1/upload',
                        data={'key': IMGBB_API_KEY, 'expiration': 86400},
                        files={'image': (clean_filename, f, mime_type)},
                        timeout=30
                    ).json()
                if res.get('success'):
                    return res['data']['url'], None
            except Exception as e:
                print(f"⚠️ Помилка ImgBB: {e}")

    return f"https://docs.google.com/uc?export=download&id={file_id}", None


def delete_from_imagekit(file_id: str):
    """Видаляє тимчасовий файл з ImageKit.io."""
    if not file_id or not IMAGEKIT_PRIVATE_KEY:
        return
    url = f"https://api.imagekit.io/v1/files/{file_id}"
    try:
        requests.delete(url, auth=(IMAGEKIT_PRIVATE_KEY, ''), timeout=20)
        print(f"🗑️ Тимчасовий файл {file_id} видалено з ImageKit.")
    except Exception as e:
        print(f"⚠️ Помилка видалення з ImageKit: {e}")
