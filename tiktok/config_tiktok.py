import os
from PIL import Image

# Трюк для сумісності старого MoviePy з новим Pillow в Python 3.12+
if not hasattr(Image, 'ANTIALIAS'):
    if not hasattr(Image, 'Resampling'):
        Image.Resampling = Image
    Image.ANTIALIAS = Image.Resampling.LANCZOS

CLIENT_KEY = os.environ.get('CLIENT_KEY_TIKTOK')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET_TIKTOK')
TOKENS_FILE = 'tiktok_tokens.json'

FOLDER_INPUT_ID = '19wPAbTuyGGqMI4twWXfU5gfs-vk2Ru_G'
FOLDER_TRASH_ID = '1L3veD90e7Fr1acwlK7PmhSs_JrofyT6N'

# Зчитуємо ID папки з музикою з GitHub Secrets
FOLDER_MUSIC_ID = os.environ.get('MUSIC_FOLDER_ID')
MUSIC_FOLDER_ID = FOLDER_MUSIC_ID

FINAL_FPS = 30        

# Локальна папка як фолбек (резервний варіант, якщо на Диску немає музики)
MUSIC_FALLBACK_PATH = 'music'

# Підтримувані формати медіа
VALID_EXTENSIONS = (
    '.3gp', '.avi', '.gif', '.heic', '.heif', '.jpeg', '.jpg', 
    '.mkv', '.mov', '.mp4', '.mpeg', '.mpg', '.tif', '.tiff', '.webp', '.png'
)
