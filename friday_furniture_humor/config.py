import os

# ⚙️ META API CONSTRAINTS & CREDENTIALS
IG_USER_ID = os.environ.get("IG_USER_ID")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")

# 📊 GOOGLE SPREADSHEET CONFIG
SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

# 🤖 GEMINI API & MODELS
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite"
]

# 🗂️ FILE EXTENSIONS
VALID_MEDIA_EXTENSIONS = ('.gif', '.heic', '.heif', '.jpeg', '.jpg', '.mp4', '.png', '.webp')
DOCUMENT_EXTENSIONS = ('.pdf', '.doc', '.docx', '.djvu', '.txt', '.rtf', '.fb2', '.epub')

# ☁️ EXTERNAL HOSTING KEYS
IMAGEKIT_PRIVATE_KEY = os.environ.get("IMAGEKIT_PRIVATE_KEY")
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY")

# 📁 LOCAL DIRECTORY FOR TEMP STORAGE
TEMP_MEDIA_DIR = 'temp_media'
