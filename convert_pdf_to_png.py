import os
import subprocess
import fitz  # PyMuPDF
import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
import matplotlib.pyplot as plt

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# ID папки на Google Диску
FOLDER_ID = '1NW5iKh6fkzXhvrHUmVVjJL5YJTmHGA7E'
TEMP_DIR = "temp_processing"

def get_drive_service():
    """Авторизація через особистий OAuth2 Refresh Token."""
    creds = Credentials(
        token=None,
        refresh_token=os.environ.get('GDRIVE_REFRESH_TOKEN'),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get('GDRIVE_CLIENT_ID'),
        client_secret=os.environ.get('GDRIVE_CLIENT_SECRET')
    )
    creds.refresh(Request())
    return build('drive', 'v3', credentials=creds)

def convert_dxf_to_png(dxf_path, png_path):
    """Конвертує DXF файл у PNG за допомогою ezdxf та matplotlib."""
    try:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        
        ctx = RenderContext(doc)
        fig = plt.figure(figsize=(12, 8))
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis('off')  # Вимикаємо осі координат matplotlib
        
        backend = MatplotlibBackend(ax)
        Frontend(ctx, backend).draw_layout(msp, finalize=True)
        
        # Зберігаємо без білих полів навколо
        fig.savefig(png_path, dpi=150, bbox_inches='tight', pad_inches=0)
        plt.close(fig)
        return True
    except Exception as e:
        print(f"  [Помилка рендерингу CAD]: {e}")
        return False

def main():
    if not os.environ.get('GDRIVE_REFRESH_TOKEN'):
        print("Помилка: Не знайдено змінні оточення для OAuth2 у GitHub Secrets!")
        return

    service = get_drive_service()
    os.makedirs(TEMP_DIR, exist_ok=True)

    print("Аналіз вмісту папки на Google Диску...")
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and trashed = false",
        fields="files(id, name, mimeType, modifiedTime)",
        pageSize=1000
    ).execute()
    all_files = results.get('files', [])

    existing_names = {f['name'] for f in all_files}
    
    # Фільтруємо файли за розширенням
    valid_files = []
    for f in all_files:
        ext = os.path.splitext(f['name'].lower())[1]
        if ext in ['.pdf', '.dxf', '.dwg']:
            valid_files.append(f)

    if not valid_files:
        print("У папці не знайдено файлів для обробки (PDF, DXF, DWG).")
        return

    print(f"Знайдено відповідних файлів на Диску: {len(valid_files)}")

    for f in valid_files:
        file_name = f['name']
        file_id = f['id']
        mod_time_str = f['modifiedTime']
        
        base_name, ext = os.path.splitext(file_name)
        ext = ext.lower()

        # Визначаємо ім'я результуючого файлу для перевірки дублікатів
        expected_img = f"{base_name}_1.png" if ext == '.pdf' else f"{base_name}.png"
        
        if expected_img in existing_names:
            print(f"Пропущено (вже конвертовано): {file_name}")
            continue

        print(f"Обробка файлу: {file_name}...")
        temp_input_path = os.path.join(TEMP_DIR, file_name)
        
        # Завантажуємо оригінальний файл з Диску
        request = service.files().get_media(fileId=file_id)
        with open(temp_input_path, 'wb') as temp_file:
            downloader = MediaIoBaseDownload(temp_file, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        # --- ОБРОБКА PDF ---
        if ext == '.pdf':
            doc = fitz.open(temp_input_path)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=150)
                
                img_name = f"{base_name}_{page_num + 1}.png"
                img_path = os.path.join(TEMP_DIR, img_name)
                pix.save(img_path)
                
                # Завантаження на Диск
                file_metadata = {'name': img_name, 'parents': [FOLDER_ID], 'modifiedTime': mod_time_str}
                media = MediaFileUpload(img_path, mimetype='image/png')
                service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                print(f"  -> Завантажено сторінку PDF: {img_name}")
                os.remove(img_path)
            doc.close()

        # --- ОБРОБКА DXF або DWG ---
        elif ext in ['.dxf', '.dwg']:
            dxf_to_render = temp_input_path
            
            # Якщо це DWG, спершу конвертуємо його в тимчасовий DXF через консоль
            if ext == '.dwg':
                print("  -> Конвертація DWG у тимчасовий DXF...")
                temp_dxf_path = os.path.join(TEMP_DIR, f"{base_name}_converted.dxf")
                # Виклик системної утиліти з урахуванням вихідного шляху
                result = subprocess.run(['dwg2dxf', '-o', temp_dxf_path, temp_input_path], capture_output=True, text=True)
                
                if result.returncode != 0 or not os.path.exists(temp_dxf_path):
                    print(f"  [Помилка dwg2dxf]: {result.stderr}")
                    if os.path.exists(temp_input_path): os.remove(temp_input_path)
                    continue
                dxf_to_render = temp_dxf_path

            # Рендеримо DXF у PNG
            img_name = f"{base_name}.png"
            img_path = os.path.join(TEMP_DIR, img_name)
            
            if convert_dxf_to_png(dxf_to_render, img_path):
                # Завантаження готового малюнка на Диск
                file_metadata = {'name': img_name, 'parents': [FOLDER_ID], 'modifiedTime': mod_time_str}
                media = MediaFileUpload(img_path, mimetype='image/png')
                service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                print(f"  -> Завантажено готове креслення: {img_name}")
                os.remove(img_path)
            
            # Очищення тимчасового DXF, якщо він створювався з DWG
            if ext == '.dwg' and os.path.exists(dxf_to_render):
                os.remove(dxf_to_render)

        # Видаляємо завантажений оригінал
        if os.path.exists(temp_input_path):
            os.remove(temp_input_path)

    print("Процес завершено успішно!")

if __name__ == '__main__':
    main()
