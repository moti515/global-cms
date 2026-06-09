import os
import sys
import json
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from config_tiktok import CLIENT_KEY, CLIENT_SECRET, TOKENS_FILE

def get_gdrive_service():
    try:
        key_dict = json.loads(os.environ['GDRIVE_SERVICE_ACCOUNT_KEY'])
        creds = service_account.Credentials.from_service_account_info(key_dict, scopes=['https://www.googleapis.com/auth/drive'])
        return build('drive', 'v3', credentials=creds)
    except KeyError:
        sys.exit("❌ ПОМИЛКА: Секрет GDRIVE_SERVICE_ACCOUNT_KEY не знайдено в змінних оточення!")
    except json.JSONDecodeError:
        sys.exit("❌ ПОМИЛКА: Вміст GDRIVE_SERVICE_ACCOUNT_KEY не є коректним JSON!")
    except Exception as e:
        sys.exit(f"❌ ПОМИЛКА ініціалізації Google Drive: {e}")

def get_valid_tiktok_token():
    try:
        with open(TOKENS_FILE, 'r') as f:
            tokens = json.load(f)
    except FileNotFoundError:
        sys.exit("❌ АВАРІЙНЕ ЗАВЕРШЕННЯ: Файл токенів tiktok_tokens.json не знайдено!")

    url = "https://open.tiktokapis.com/v2/oauth/token/"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": tokens['refresh_token']
    }
    
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        new_tokens = response.json()
        tokens.update(new_tokens) 
        with open(TOKENS_FILE, 'w') as f:
            json.dump(tokens, f, indent=4)
        return tokens['access_token']
    else:
        print(f"❌ Помилка оновлення токена TikTok: {response.text}")
        return tokens.get('access_token')
