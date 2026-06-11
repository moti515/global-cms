import os
import requests
from auth_tiktok import get_valid_tiktok_token

def upload_to_tiktok(video_path, description):
    access_token = get_valid_tiktok_token()
    if not access_token:
        print("Публікація скасована через відсутність дійсного токена.")
        return False

    video_size = os.path.getsize(video_path)
    MAX_SINGLE_SIZE = 64 * 1024 * 1024       
    DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024    

    if video_size <= MAX_SINGLE_SIZE:
        chunk_size = video_size
        total_chunk_count = 1
    else:
        chunk_size = DEFAULT_CHUNK_SIZE
        total_chunk_count = video_size // chunk_size

    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8"
    }
    
    body = {
        "post_info": {
            "title": description if description else "#travel #Spaß",
            "privacy_level": "SELF_ONLY",  
            "disable_duet": True,
            "disable_comment": True,
            "disable_stitch": True,
            "video_cover_timestamp_ms": 1000
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunk_count
        }
    }
    
    print(f"Надсилання запиту на ініціалізацію в TikTok (Розмір файлу: {video_size} байт)...")
    init_res = requests.post(init_url, headers=headers, json=body)
    
    if init_res.status_code != 200:
        print(f"❌ Помилка ініціалізації чернетки: {init_res.status_code} - {init_res.text}")
        return False
        
    res_data = init_res.json()
    if 'data' not in res_data or 'upload_url' not in res_data['data']:
        print(f"❌ Помилка API TikTok: {res_data.get('error')}")
        return False

    publish_id = res_data['data'].get('publish_id')
    upload_url = res_data['data']['upload_url']
    
    print(f"✅ Успішна ініціалізація TikTok! ID: {publish_id}")
    print(f"Починаємо передачу файлу частинами (Всього чанків: {total_chunk_count})...")

    with open(video_path, 'rb') as video_file:
        for i in range(total_chunk_count):
            first_byte = i * chunk_size
            if i == total_chunk_count - 1:
                last_byte = video_size - 1
            else:
                last_byte = (i + 1) * chunk_size - 1
            
            byte_size_of_this_chunk = last_byte - first_byte + 1
            video_file.seek(first_byte)
            chunk_data = video_file.read(byte_size_of_this_chunk)
            
            put_headers = {
                "Content-Type": "video/mp4",
                "Content-Length": str(byte_size_of_this_chunk),
                "Content-Range": f"bytes {first_byte}-{last_byte}/{video_size}"
            }
            
            print(f"📤 Надсилання чанку {i+1}/{total_chunk_count} (байти {first_byte}-{last_byte})...")
            upload_res = requests.put(upload_url, headers=put_headers, data=chunk_data)
            
            expected_status = 201 if i == total_chunk_count - 1 else 206
            if upload_res.status_code != expected_status:
                print(f"❌ Помилка завантаження чанку {i+1}: Отримано статус {upload_res.status_code}, очікувався {expected_status}.")
                return False

    print("🚀 Відео успішно передано на сервери TikTok!")
    return True
