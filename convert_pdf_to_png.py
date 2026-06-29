import os
import sys
import subprocess

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
    """Конвертує DXF файл у PNG за допомогою ezdxf та matplotlib (динамічний імпорт)."""
    try:
        import ezdxf
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        import matplotlib.pyplot as plt

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        
        ctx = RenderContext(doc)
        fig = plt.figure(figsize=(12, 8))
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis('off')
        
        backend = MatplotlibBackend(ax)
        Frontend(ctx, backend).draw_layout(msp, finalize=True)
        
        fig.savefig(png_path, dpi=150, bbox_inches='tight', pad_inches=0)
        plt.close(fig)
        return True
    except Exception as e:
        print(f"  [Помилка рендерингу CAD]: {e}")
        return False

def main():
    if not os.environ.get('GDRIVE_REFRESH_TOKEN'):
        print("Помилка: Не знайдено змінні оточення для OAuth2 у GitHub Secrets!")
        sys.exit(1)

    # Перевіряємо режим роботи (сканування чи повна конвертація)
    is_scan_mode = "--scan" in sys.argv

    service = get_drive_service()
    os.makedirs(TEMP_DIR, exist_ok=True)

    if is_scan_mode:
        print("Режим розвідки: аналіз папки на Google Диску...")
    else:
        print("Режим конвертації: початок обробки файлів...")

    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and trashed = false",
        fields="files(id, name, mimeType, modifiedTime)",
        pageSize=1000
    ).execute()
    all_files = results.get('files', [])

    existing_names = {f['name'] for f in all_files}
    
    # Змінні для відстеження типів файлів, що дійсно потребують обробки
    need_pdf = False
    need_dxf = False
    need_dwg = False
    valid_files = []

    for f in all_files:
        base_name, ext = os.path.splitext(f['name'].lower())
        if ext in ['.pdf', '.dxf', '.dwg']:
            expected_img = f"{base_name}_1.png" if ext == '.pdf' else f"{base_name}.png"
            # Файл потребує обробки, тільки якщо його PNG-версії ще немає на диску
            if expected_img not in existing_names:
                valid_files.append(f)
                if ext == '.pdf': need_pdf = True
                if ext == '.dxf': need_dxf = True
                if ext == '.dwg': need_dwg = True

    # Якщо запущено в режимі сканування, записуємо результати для GitHub Actions і виходимо
    if is_scan_mode:
        print(f"Потребують обробки: PDF={need_pdf}, DXF={need_dxf}, DWG={need_dwg}")
        if 'GITHUB_OUTPUT' in os.environ:
            with open(os.environ['GITHUB_OUTPUT'], 'a') as github_output:
                github_output.write(f"has_pdf={str(need_pdf).lower()}\n")
                github_output.write(f"has_dxf={str(need_dxf).lower()}\n")
                github_output.write(f"has_dwg={str(need_dwg).lower()}\n")
        return

    if not valid_files:
        print("Немає нових файлів для обробки.")
        return

    print(f"Знайдено файлів для конвертації: {len(valid_files)}")

    for f in valid_files:
        file_name = f['name']
        file_id = f['id']
        mod_time_str = f['modifiedTime']
        
        base_name, ext = os.path.splitext(file_name)
        ext = ext.lower()

        print(f"Обробка файлу: {file_name}...")
        temp_input_path = os.path.join(TEMP_DIR, file_name)
        
        # Завантажуємо оригінал з Диску
        request = service.files().get_media(fileId=file_id)
        with open(temp_input_path, 'wb') as temp_file:
            downloader = MediaIoBaseDownload(temp_file, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        # --- ОБРОБКА PDF ---
        if ext == '.pdf':
            import fitz  # Локальний імпорт
            doc = fitz.open(temp_input_path)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=150)
                
                img_name = f"{base_name}_{page_num + 1}.png"
                img_path = os.path.join(TEMP_DIR, img_name)
                pix.save(img_path)
                
                file_metadata = {'name': img_name, 'parents': [FOLDER_ID], 'modifiedTime': mod_time_str}
                media = MediaFileUpload(img_path, mimetype='image/png')
                service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                print(f"  -> Завантажено сторінку PDF: {img_name}")
                os.remove(img_path)
            doc.close()

        # --- ОБРОБКА DXF або DWG ---
        elif ext in ['.dxf', '.dwg']:
            dxf_to_render = temp_input_path
            
            if ext == '.dwg':
                print("  -> Конвертація DWG у тимчасовий DXF...")
                temp_dxf_path = os.path.join(TEMP_DIR, f"{base_name}_converted.dxf")
                result = subprocess.run(['dwg2dxf', '-o', temp_dxf_path, temp_input_path], capture_output=True, text=True)
                
                if result.returncode != 0 or not os.path.exists(temp_dxf_path):
                    print(f"  [Помилка dwg2dxf]: {result.stderr}")
                    if os.path.exists(temp_input_path): os.remove(temp_input_path)
                    continue
                dxf_to_render = temp_dxf_path

            img_name = f"{base_name}.png"
            img_path = os.path.join(TEMP_DIR, img_name)
            
            if convert_dxf_to_png(dxf_to_render, img_path):
                file_metadata = {'name': img_name, 'parents': [FOLDER_ID], 'modifiedTime': mod_time_str}
                media = MediaFileUpload(img_path, mimetype='image/png')
                service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                print(f"  -> Завантажено готове креслення: {img_name}")
                os.remove(img_path)
            
            if ext == '.dwg' and os.path.exists(dxf_to_render):
                os.remove(dxf_to_render)

        if os.path.exists(temp_input_path):
            os.remove(temp_input_path)

    print("Процес завершено успішно!")

if __name__ == '__main__':
    main()
