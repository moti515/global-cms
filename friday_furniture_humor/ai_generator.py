import os
import io
import base64
from datetime import datetime
from PIL import Image
from google import genai

from config import GEMINI_API_KEY, GEMINI_MODELS


def get_active_rules_ordered():
    """Визначає актуальні календарні свята та день тижня для підбору відповідної категорії."""
    now = datetime.now()
    day_of_week = now.strftime('%A')
    day_month = now.strftime('%d.%m')
    
    days_map = {
        'Monday': 'Понеділок', 'Tuesday': 'Вівторок', 'Wednesday': 'Середа',
        'Thursday': 'Четвер', 'Friday': "П'ятниця", 'Saturday': 'Субота', 'Sunday': 'Неділя'
    }
    
    active_rules = []
    if "22.12" <= day_month <= "31.12" or "01.01" == day_month: active_rules.append("Новий рік")
    if "01.04" <= day_month <= "02.04": active_rules.append("1 квітня")
    if "22.02" <= day_month <= "23.02": active_rules.append("23 лютого")
    if day_month == "08.03": active_rules.append("8 Березня")
    if day_month == "03.09": active_rules.append("3 вересня")
    if "31.05" <= day_month <= "15.06": active_rules.append("31 травня")
    if now.month == 11 and 23 <= now.day <= 30: active_rules.append("Чорна п'ятниця")
    
    if day_of_week == 'Friday':
        if now.day == 13: active_rules.append("П'ятниця 13-те")
        elif now.day == 12: active_rules.append("П'ятниця 12-те")
            
    if day_of_week in ['Saturday', 'Sunday']:
        active_rules.append("Weekend")
    
    active_rules.append(days_map[day_of_week])
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

    topic_context = "розважальної сторінки з гострим гумором"
    if is_furniture:
        topic_context = "популярного пабліку про меблі, дизайн інтер'єрів та запеклі будні меблевиків (майстрів, дизайнерів, збірників)"

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
            f"Ти топ-маркетолог {topic_context}.\n"
            f"Подивись на цю картинку/мем і напиши короткий, влучний і дійсно смішний коментар "
            f"(або життєву фразу/біль клієнта чи майстра) окремо ТРЬОМА мовами.\n\n"
            f"🎯 ТОН ТА СТИЛІСТИКА:\n"
            f"- КРИТИЧНО: Це не має бути дослівний нудний переклад! Жарт має бути якісно адаптований під кожну мову.\n"
            f"- Використовуй живий сленг, професійні жарти або зрозумілий контекст для носіїв кожної з мов.\n"
            f"- Врахуй контекст публікації — категорія '{category}' з розділу '{tab_name}'.\n"
            f"- Додай відповідні емодзі.\n\n"
            f"⚠️ СУВОРІ ОБМЕЖЕННЯ:\n"
            f"1. Не використовуй жодних офіційних вступів, лапок чи підписів на кшталт 'Ось ваш жарт'.\n"
            f"2. Без хештегів та додаткових пояснень копірайтера.\n"
            f"3. Формат відповіді має бути СТРОГО такий (3 абзаци з прапорами):\n\n"
            f"🇺🇦 [Жарт українською]\n\n"
            f"🇬🇧 [Жарт англійською]\n\n"
            f"🇩🇪 [Жарт німецькою]"
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
