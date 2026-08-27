import os
import io
import base64
from datetime import datetime, date, timedelta
from PIL import Image
from google import genai

from config import GEMINI_API_KEY, GEMINI_MODELS


def get_orthodox_easter(year: int) -> date:
    """Обчислює дату Православної Пасхи для заданого року."""
    a = year % 4
    b = year % 7
    c = year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month = (d + e + 114) // 31
    day = ((d + e + 114) % 31) + 1
    julian_easter = date(year, month, day)
    return julian_easter + timedelta(days=13)


def get_active_rules_ordered():
    """
    Визначає актуальні календарні свята, сезони, місяці та день тижня.
    Правила впорядковано строго від найконкретніших дат до найзагальніших.
    """
    now = datetime.now()
    today = now.date()
    day_of_week = now.strftime('%A')
    day_month = now.strftime('%d.%m')
    month = now.month
    day = now.day
    
    days_map = {
        'Monday': 'Понеділок', 
        'Tuesday': 'Вівторок', 
        'Wednesday': 'Середа',
        'Thursday': 'Четвер', 
        'Friday': "П'ятниця", 
        'Saturday': 'Субота', 
        'Sunday': 'Неділя'
    }
    
    active_rules = []
    
    # -------------------------------------------------------------
    # 1. ТОЧНІ СВЯТА ТА КОНКРЕТНІ ДАТИ (Найвищий пріоритет)
    # -------------------------------------------------------------
    # Новий рік (30.12 - 01.01)
    if day_month in ["30.12", "31.12", "01.01"]:
        active_rules.append("Новий рік")
        
    # Різдво (23.12 - 25.12)
    if "23.12" <= day_month <= "25.12":
        active_rules.append("Різдво")
        
    # Конкретні дні року
    if day_month == "14.02":
        active_rules.append("14 лютого")
    if day_month == "23.02":
        active_rules.append("23 лютого")
    if day_month == "07.03":
        active_rules.append("7 березня")
    if day_month == "08.03":
        active_rules.append("8 березня")    
    if day_month == "12.04":
        active_rules.append("12 квітня")
    if day_month == "03.09":
        active_rules.append("3 вересня")

    # Пасха (Страсна П'ятниця, Великодня Субота, Великдень)
    easter_date = get_orthodox_easter(now.year)
    if (easter_date - timedelta(days=2)) <= today <= easter_date:
        active_rules.append("Пасха")

    # Спеціальні п'ятниці
    if day_of_week == 'Friday':
        if day == 13:
            active_rules.append("П'ятниця 13-те")
        elif day == 12:
            active_rules.append("П'ятниця 12-те")
            
        # Чорна п'ятниця (будь-яка п'ятниця між 11.11 та 30.11)
        if "11.11" <= day_month <= "30.11":
            active_rules.append("Чорна п'ятниця")

    # -------------------------------------------------------------
    # 2. МІСЯЦІ ТА СЕЗОНИ (Середній пріоритет)
    # -------------------------------------------------------------
    # Місяці
    if month == 2:
        active_rules.append("Лютий")
    elif month == 4:
        active_rules.append("Квітень")
    elif month == 6:
        active_rules.append("Червень")
    elif month == 9:
        active_rules.append("Вересень")

    # Пори року
    if month in [12, 1, 2]:
        active_rules.append("Зима")
    if month in [4, 5, 6]:
        active_rules.append("Весна")
    if month in [6, 7, 8]:
        active_rules.append("Літо")
    if month in [9, 10, 11]:
        active_rules.append("Осінь")

    # -------------------------------------------------------------
    # 3. ДНІ ТИЖНЯ ТА ВИХІДНІ (Загальний пріоритет)
    # -------------------------------------------------------------
    if day_of_week in ['Saturday', 'Sunday']:
        active_rules.append("Weekend")

    active_rules.append(days_map[day_of_week])
    
    # -------------------------------------------------------------
    # 4. ФОЛБЕК
    # -------------------------------------------------------------
    active_rules.append("Різне")
    
    return active_rules


def generate_multimodal_caption(image_path, category, tab_name):
    """
    Аналізує зображення за допомогою Google GenAI SDK та генерує тримовний гумористичний підпис.
    """
    is_furniture = "мебл" in tab_name.lower()
    
    # 1️⃣ Швидкий дефолт, якщо відсутній API-ключ
    if not GEMINI_API_KEY:
        if is_furniture:
            return "Трохи меблевого гумору вам у стрічку! Як вам? 👇😂 #меблі #інтерєр #гумор"
        return "Усміхніться! Гарного настрою! 😉 #гумор #розваги #п_ятниця"

    if is_furniture:
        topic_context = (
            "культового пабліку про меблі, дизайн інтер'єрів, виробництво та запеклі будні меблевиків "
            "(майстрів, збірників, конструкторів і дизайнерів)"
        )
    else:
        topic_context = (
            "популярного розважального пабліку з гострим, життєвим та безкомпромісним гумором. "
            "Паблік виріс із мемів про П'ятницю та вихідні у простір про життєві будні: "
            "болі робочого тижня, дедлайни, дні тижня, сезонний настрій, абсурдність життя та ситуації, "
            "в яких кожен впізнає себе (аж до сліз)"
        )

    try:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Файл {image_path} не знайдено.")
            
        # Стиснення зображення під ліміти API
        try:
            with Image.open(image_path) as img:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.thumbnail((1024, 1024))
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=82, optimize=True)
                image_bytes = buffer.getvalue()
        except Exception as img_err:
            print(f"⚠️ Помилка PIL-оптимізації ({img_err}), зчитуємо оригінальний файл...")
            with open(image_path, "rb") as f:
                image_bytes = f.read()

        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        prompt = (
            f"Ти — геній мемології та креативний автор {topic_context}.\n"
            f"Подивись на це зображення/мем і придумай короткий, влучний, саркастичний і дійсно смішний коментар "
            f"(або влучно підхоплену життєву фразу / душевний біль), орієнтуючись окремо на ТРИ мови.\n\n"
            f"🎯 КОНТЕКСТ ПУБЛІКАЦІЇ:\n"
            f"- Категорія / Тема: '{category}'\n"
            f"- Розділ: '{tab_name}'\n\n"
            f"🔥 СТИЛЬ ТА ВИМОГИ КОРПУСУ:\n"
            f"1. Максимальна життєвість (relatable humor): бий у біль дня тижня, роботи, втоми, вихідних або сезонного настрою.\n"
            f"2. ЖОДНОГО сухого або дослівного перекладу! Жарт має бути створений заново під культурний код та меми кожної з трьох мов.\n"
            f"3. Використовуй живий розмовний сленг, іронію, емодзі та формат punchline (коротко, але влучно в яблучко).\n\n"
            f"⚠️ СУВОРІ ФОРМАТНІ ОБМЕЖЕННЯ:\n"
            f"- Не додавай вступів, лапок, пояснень від себе чи хештегів.\n"
            f"- Формат відповіді роби СТРОГО таким (3 коротких абзаци з відповідними прапорами):\n\n"
            f"🇺🇦 [Жарт/коментар українською]\n\n"
            f"🇬🇧 [Жарт/коментар англійською]\n\n"
            f"🇩🇪 [Жарт/коментар німецькою]"
        )
        
        inputs = [
            {"type": "text", "text": prompt},
            {
                "type": "image",
                "data": base64_image,
                "mime_type": "image/jpeg"
            }
        ]
        
        client = genai.Client()
        for model in GEMINI_MODELS:
            print(f"🚀 Спроба генерації підпису через {model}...")
            try:
                interaction = client.interactions.create(
                    model=model,
                    input=inputs
                )
                if interaction and interaction.output_text:
                    return interaction.output_text.strip()
                else:
                    print(f"⚠️ Модель {model} повернула порожню відповідь.")
            except Exception as model_err:
                print(f"⚠️ Помилка моделі {model}: {model_err}. Переходимо до наступної.")
                continue
                
        print("⚠️ Жодна з моделей Gemini не відповіла успішно, активовано резервний підпис.")
        
    except Exception as general_err:
        print(f"⚠️ Загальний збій блоку ШІ-генерації: {general_err}")
        
    if is_furniture:
        return "Трохи меблевого гумору вам у стрічку! Як вам? 👇😂 #меблі #гумор"
    return "Трохи гумору вам у стрічку! Як вам? 👇😂 #гумор #розваги"
