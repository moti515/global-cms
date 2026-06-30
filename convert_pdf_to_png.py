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

def extract_dwg_thumbnail(dwg_path, png_path):
    """Витягує вбудоване зображення-прев'ю (аксонометрію) з DWG за допомогою dwgbmp."""
    try:
        from PIL import Image
        base_dir = os.path.dirname(dwg_path)
        base_name = os.path.splitext(os.path.basename(dwg_path))[0]
        temp_bmp = os.path.join(base_dir, f"{base_name}.bmp")
        
        if os.path.exists(temp_bmp): os.remove(temp_bmp)
        
        # Пробуємо стандартне вилучення через LibreDWG утиліту
        subprocess.run(["dwgbmp", dwg_path], capture_output=True)
        
        # Альтернативний синтаксис, якщо утиліта вимагає прапорець -o
        if not os.path.exists(temp_bmp):
            subprocess.run(["dwgbmp", "-o", temp_bmp, dwg_path], capture_output=True)
            
        # Якщо утиліта скинула файл у корінь або поруч з іншим ім'ям
        if not os.path.exists(temp_bmp):
            for potential_name in [f"{base_name}.bmp", os.path.basename(dwg_path) + ".bmp"]:
                if os.path.exists(potential_name):
                    os.rename(potential_name, temp_bmp)
                    break

        if os.path.exists(temp_bmp) and os.path.getsize(temp_bmp) > 0:
            # Конвертуємо BMP в якісний PNG за допомогою Pillow
            with Image.open(temp_bmp) as img:
                img.save(png_path, "PNG")
            os.remove(temp_bmp)
            print("  -> [Успіх]: Знайдено 3D-модель. Успішно витягнуто вбудоване прев'ю з файлу.")
            return True
        else:
            print("  [Помилка dwgbmp]: Не вдалося знайти або витягти вбудоване прев'ю з DWG.")
            return False
    except Exception as e:
        print(f"  [Помилка витягування прев'ю]: {e}")
        return False

def convert_dxf_to_png(dxf_path, png_path):
    """Конвертує DXF у PNG та аналізує наявність 3D об'єктів (3DSOLID)."""
    try:
        import ezdxf
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        from ezdxf.addons.drawing.properties import LayoutProperties
        import matplotlib.pyplot as plt
        from collections import Counter

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        
        if not len(msp):
            for layout in doc.layouts:
                if layout.name != 'Model' and len(layout):
                    msp = layout
                    break

        entity_types = [e.dxftype() for e in msp]
        types_count = dict(Counter(entity_types))
        print(f"  [ДІАГНОСТИКА CAD]: Векторні об'єкти у файлі: {types_count}")

        # Визначаємо, чи містить файл суто 3D-тіла без класичних 2D-ліній
        renderable_2d_types = {'LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'CIRCLE', 'TEXT', 'MTEXT', 'HATCH', 'SPLINE', 'ELLIPSE'}
        has_renderable_2d = any(t in renderable_2d_types for t in types_count)
        has_3d_solids = '3DSOLID' in types_count
        
        # Якщо це чиста 3D модель — маркуємо її для обробки через dwgbmp
        if has_3d_solids and not has_renderable_2d:
            print("  -> Виявлено суто тривимірні об'єкти (3DSOLID). Направляємо на екстрактор прев'ю.")
            return False, True

        if not entity_types or not has_renderable_2d:
            print("  [Помилка]: У файлі взагалі немає векторних об'єктів для малювання.")
            return False, False

        fig = plt.figure(figsize=(20, 14), dpi=200)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis('off')
        
        ctx = RenderContext(doc)
        layout_props = LayoutProperties.from_layout(msp)
        layout_props.set_colors(bg='#ffffff')
        
        backend = MatplotlibBackend(ax)
        Frontend(ctx, backend).draw_layout(msp, finalize=True, layout_properties=layout_props)
        
        fig.savefig(png_path, dpi=200, bbox_inches='tight', pad_inches=0.05)
        plt.close(fig)
        return True, False
    except Exception as e:
        print(f"  [Помилка рендерингу CAD]: {e}")
        return False, False

def main():
    if not os.environ.get('GDRIVE_REFRESH_TOKEN'):
        print("Помилка: Не знайдено змінні оточення для OAuth2 у GitHub Secrets!")
        sys.exit(1)

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
    
    need_pdf = False
    need_dxf = False
    need_dwg = False
    valid_files = []

    for f in all_files:
        base_name, ext = os.path.splitext(f['name'].lower())
        if ext in ['.pdf', '.dxf', '.dwg']:
            expected_img = f"{base_name}_1.png" if ext == '.pdf' else f"{base_name}.png"
            if expected_img not in existing_names:
                valid_files.append(f)
                if ext == '.pdf': need_pdf = True
                if ext == '.dxf': need_dxf = True
                if ext == '.dwg': need_dwg = True

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
        
        request = service.files().get_media(fileId=file_id)
        with open(temp_input_path, 'wb') as temp_file:
            downloader = MediaIoBaseDownload(temp_file, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        # --- ОБРОБКА PDF ---
        if ext == '.pdf':
            import fitz  
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
            original_dwg_path = temp_input_path if ext == '.dwg' else None
            
            if ext == '.dwg':
                print("  -> Конвертація DWG у DXF через LibreDWG...")
                temp_dxf_path = os.path.join(TEMP_DIR, f"{base_name}.dxf")
                cmd = ["dwg2dxf", "-o", temp_dxf_path, temp_input_path]
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if not os.path.exists(temp_dxf_path):
                    print(f"  [Помилка dwg2dxf]: {result.stderr}")
                    if os.path.exists(temp_input_path): os.remove(temp_input_path)
                    continue
                
                dxf_to_render = temp_dxf_path
                print("  -> DWG успішно переведено в DXF.")

            img_name = f"{base_name}.png"
            img_path = os.path.join(TEMP_DIR, img_name)
            
            # Запускаємо стандартний рендеринг плоских ліній
            success, is_pure_3d = convert_dxf_to_png(dxf_to_render, img_path)
            
            # ФОЛБЕК ДЛЯ 3D: Якщо це чистий 3D DWG, витягуємо його растрову аксонометрію
            if is_pure_3d and original_dwg_path:
                success = extract_dwg_thumbnail(original_dwg_path, img_path)
            elif is_pure_3d and ext == '.dxf':
                # Для ізольованих DXF без DWG шукаємо однойменний супутній DWG у папці
                possible_dwg_partner = os.path.join(TEMP_DIR, base_name.replace("3D1", "3D") + ".dwg")
                if os.path.exists(possible_dwg_partner):
                    print(f"  -> Спроба витягти прев'ю для DXF з його супутнього DWG...")
                    success = extract_dwg_thumbnail(possible_dwg_partner, img_path)
                else:
                    print("  [Пропущено]: Чистий 3D DXF без супутнього DWG не має растрового шару.")
                    success = False
            
            if success and os.path.exists(img_path):
                file_metadata = {'name': img_name, 'parents': [FOLDER_ID], 'modifiedTime': mod_time_str}
                media = MediaFileUpload(img_path, mimetype='image/png')
                service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                print(f"  -> Завантажено малюнок на Google Drive: {img_name}")
                os.remove(img_path)
            
            if ext == '.dwg' and os.path.exists(dxf_to_render):
                os.remove(dxf_to_render)

        if os.path.exists(temp_input_path):
            os.remove(temp_input_path)

    print("Процес завершено успішно!")

if __name__ == '__main__':
    main()
