import os

# ⚙️ НАЛАШТУВАННЯ (GitHub Actions)
IG_USER_ID = os.environ.get("IG_USER_ID")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")

SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
TAB_NAME = "Меблі"

HOT_FOLDER_ID = '1BlPC3ua00pHnqdwpy2EA3EzOA-tCmt2N'
TRASH_FOLDER_ID = '1L3veD90e7Fr1acwlK7PmhSs_JrofyT6N'

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

VALID_MEDIA_EXTENSIONS = ('.gif', '.heic', '.heif', '.jpeg', '.jpg', '.mp4', '.png', '.webp', '.mov', '.avi')
DOCUMENT_EXTENSIONS = ('.pdf', '.doc', '.docx', '.djvu', '.txt', '.rtf', '.fb2', '.epub')

# 🌍 ЛОКАЛІЗАЦІЯ ДЛЯ СТОРІС (Сувора відповідність: без емодзі та хештегів, лаконічно)
LANG_CONFIG = {
    0: {  # 🇺🇦 Українська
        "fallback_caption": "Професійний підхід до створення меблів та увага до кожної деталі.",
        "no_gemini_caption": "Якісні меблі для вашого затишку та комфорту.",
        "categories": {
            "montage various": "Професійний монтаж меблів",
            "various": "Сучасні меблеві тренди",
            "instruktion": "Конструкторські стандарти"
        }
    },
    1: {  # 🇬🇧 Англійська
        "fallback_caption": "Professional approach to furniture design and attention to every detail.",
        "no_gemini_caption": "Quality furniture for your comfort and cozy home.",
        "categories": {
            "montage various": "Professional furniture installation",
            "various": "Modern furniture concepts",
            "instruktion": "Furniture design standards"
        }
    },
    2: {  # 🇩🇪 Німецька
        "fallback_caption": "Professioneller Ansatz beim Möbeldesign und Liebe zum Detail.",
        "no_gemini_caption": "Qualitätsmöbel für Ihr gemütliches Zuhause.",
        "categories": {
            "montage various": "Professioneller Möbelaufbau",
            "various": "Moderne Möbeltrends",
            "instruktion": "Konstruktionsstandards"
        }
    }
}

# 🏢 БАЗА БРЕНДІВ (Тільки те, що потрібно для генерації нанесення на Сторіс)
COMPANIES_DB = {
    "goncharenko": {0: "Олександр Гончаренко", 1: "Oleksandr Goncharenko", 2: "Oleksandr Goncharenko"},
    "gurov": {0: "Андрій Гуров", 1: "Andrii Gurov", 2: "Andrii Gurov"},
    "solovey": {0: "Студія меблів «Соловей»", 1: "Solovey Furniture Studio", 2: "Möbelstudio Solovey"},
    "furniture park": {0: "Меблевий парк", 1: "Furniture Park", 2: "Furniture Park"}
}
