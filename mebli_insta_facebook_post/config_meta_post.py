import os

# ⚙️ СИСТЕМНІ ЗМІННІ (GitHub Actions / ENV)
IG_USER_ID = os.environ.get("IG_USER_ID")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
IMAGEKIT_PRIVATE_KEY = os.environ.get("IMAGEKIT_PRIVATE_KEY")
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY")
GDRIVE_SERVICE_ACCOUNT_KEY = os.environ.get("GDRIVE_SERVICE_ACCOUNT_KEY")

# 📊 КОНФІГУРАЦІЯ ТАБЛИЦЬ
SPREADSHEET_ID = '1dPObaOYc2C_NuDfgaFXMM9KByjGAVrIiOsiOuY6c6v0'
TAB_NAME = "Меблі"
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']

# 📁 ФОРМАТИ ФАЙЛІВ
VALID_MEDIA_EXTENSIONS = ('.gif', '.heic', '.heif', '.jpeg', '.jpg', '.mp4', '.png', '.webp', '.mov', '.avi')

# 🌍 ЦЕНТРАЛІЗОВАНА ЛОКАЛІЗАЦІЯ ІНТЕРФЕЙСУ ПОСТІВ
LANG_CONFIG = {
    0: {  # 🇺🇦 Українська
        "year": "Рік", 
        "brand": "Виробник", 
        "loc": "Локація", 
        "assembly": "Монтаж: Меблі, які ми допомагали створювати (професійний монтаж)", 
        "concept": "Концепт: Цікаві меблеві рішення, тренди та ідеї з усього світу", 
        "ergonomics": "Ергономіка та проектування: Корисні стандарти та розміри, яких варто дотримуватися при проектуванні меблів.",
        "link_in_bio": "🔗 Посилання на портфоліо — у шапці нашого профілю!",
        "fallback_caption": "Чудова робота нашої команди! Як вам результат? 👇😊",
        "no_gemini_caption": "Якісні меблі для вашого затишку! 👇✨ #меблі #інтерєр"
    },
    1: {  # 🇬🇧 Англійська
        "year": "Year", 
        "brand": "Manufacturer", 
        "loc": "Location", 
        "assembly": "Assembly: Furniture we helped assemble (professional installation)", 
        "concept": "Concept: Interesting furniture solutions, trends, and ideas from around the world", 
        "ergonomics": "Ergonomics and Design: Useful standards and dimensions to follow when designing furniture.",
        "link_in_bio": "🔗 Portfolio link is in our bio!",
        "fallback_caption": "Great work by our team! How do you like the result? 👇😊",
        "no_gemini_caption": "Quality furniture for your comfort! 👇✨ #furniture #interiordesign"
    },
    2: {  # 🇩🇪 Німецька
        "year": "Jahr", 
        "brand": "Hersteller", 
        "loc": "Standort", 
        "assembly": "Montage: Möbel, bei deren Montage wir mitgewirkt haben (professioneller Aufbau)", 
        "concept": "Konzept: Interessante Möbellösungen, Trends und Ideen aus aller Welt", 
        "ergonomics": "Eronomie und Konstruktion: Nützliche Standards und Maße, die bei der Möbelkonstruktion beachtet werden sollten.",
        "link_in_bio": "🔗 Link zum Portfolio finden Sie in unserer Bio!",
        "fallback_caption": "Tolle Arbeit unseres Teams! Wie gefällt Ihnen das Ergebnis? 👇😊",
        "no_gemini_caption": "Qualitätsmöbel für Ihr gemütliches Zuhause! 👇✨ #moebel #interieur"
    }
}

# 🏢 ГЛОБАЛЬНА БАЗА ДАНИХ КОМПАНІЙ ТА КАТЕГОРІЙ
COMPANIES_DB = {
    "goncharenko": {
        "names": {0: "Олександр Гончаренко", 1: "Oleksandr Goncharenko", 2: "Oleksandr Goncharenko"},
        "links": ["📸 Instagram: instagr.am/goncharenko8721"],
        "ig_handle": "@goncharenko8721"
    },
    "gurov": {
        "names": {0: "Андрій Гуров", 1: "Andrii Gurov", 2: "Andrii Gurov"},
        "links": ["🌐 Facebook: fb.com/andrej.gurov.755581"],
        "ig_handle": "@gurov_andrey0612"
    },
    "solovey": {
        "names": {0: "Студія меблів «Соловей»", 1: "Solovey Furniture Studio", 2: "Möbelstudio Solovey"},
        "links": ["📸 Instagram: instagr.am/mebelsolovei"],
        "ig_handle": "@mebelsolovei"
    },
    "furniture park": {
        "names": {0: "Меблевий парк", 1: "Furniture Park", 2: "Furniture Park"},
        "links": [
            "📸 Instagram: instagr.am/meblevyi_park",
            "📸 Instagram: instagr.am/meblovo_ukraine",
            "📢 Telegram: t.me/Meblevyi_park",
            "📸 Instagram: instagr.am/renovaelite"
        ],
        "ig_handle": ["@meblevyi_park", "@meblovo_ukraine", "@renovaelite"]
    }
}
