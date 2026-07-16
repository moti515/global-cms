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

# Класифікація розширень файлів
OFFICE_EXTS = ['.doc', '.docx', '.mhtml', '.ods', '.odt', '.txt', '.xls', '.xlsx', '.csv']
TIFF_EXTS = ['.tif', '.tiff']
CAD_EXTS = ['.dxf', '.dwg']
MULTI_PAGE_EXTS = ['.pdf'] + OFFICE_EXTS + TIFF_EXTS

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

def convert_office_to_pdf(input_path, out_dir):
    """Конвертує будь-який офісний документ у PDF за допомогою LibreOffice."""
    try:
        print(f"  -> Конвертація документа у PDF через LibreOffice...")
        cmd = ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", out_dir, input_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        expected_pdf = os.path.join(out_dir, f"{base_name}.pdf")
        
        if os.path.exists(expected_pdf):
            return expected_pdf
        else:
            print(f"  [Помилка LibreOffice]: PDF не створено. Лог: {result.stderr}")
            return None
    except Exception as e:
        print(f"  [Помилка виклику LibreOffice]: {e}")
        return None

def extract_dwg_thumbnail(dwg_path, png_path):
    """Витягує вбудоване зображення-прев'ю з DWG за допомогою dwgbmp."""
    try:
        from PIL import Image
        base_dir = os.path.dirname(dwg_path)
        base_name = os.path.splitext(os.path.basename(dwg_path))[0]
        temp_bmp = os.path.join(base_dir, f"{base_name}.bmp")
        
        if os.path.exists(temp_bmp): os.remove(temp_bmp)
        
        subprocess.run(["dwgbmp", dwg_path], capture_output=True)
        if not os.path.exists(temp_bmp):
            subprocess.run(["dwgbmp", "-o", temp_bmp, dwg_path], capture_output=True)
            
        if not os.path.exists(temp_bmp):
            for potential_name in [f"{base_name}.bmp", os.path.basename(dwg_path) + ".bmp"]:
                if os.path.exists(potential_name):
                    os.rename(potential_name, temp_bmp)
                    break

        if os.path.exists(temp_bmp) and os.path.getsize(temp_bmp) > 0:
            with Image.open(temp_bmp) as img:
                img.save(png_path, "PNG")
            os.remove(temp_bmp)
            print("  -> [Успіх 3D]: Успішно витягнуто вбудоване прев'ю з DWG.")
            return True
        return False
    except Exception as e:
        print(f"  [Помилка витягування прев'ю]: {e}")
        return False

def convert_dxf_to_png(dxf_path, png_path):
    """Конвертує DXF у PNG та аналізує наявність 3D об'єктів."""
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
        print(f"  [ДІАГНОСТИКА CAD]: Векторні об'єкти: {types_count}")

        renderable_2d_types = {'LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'CIRCLE', 'TEXT', 'MTEXT', 'HATCH', 'SPLINE', 'ELLIPSE'}
        has_renderable_2d = any(t in renderable_2d_types for t in types_count)
        has_3d_solids = '3DSOLID' in types_count
        
        if has_3d_solids and not has_renderable_2d:
            print("  -> Виявлено 3D об'єкти. Перенаправлення на екстрактор прев'ю.")
            return False, True

        if not entity_types or not has_renderable_2d:
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

def process_pdf_pages(pdf_path, base_name, folder_id, service, mod_time_str):
    """Нарізає PDF-файл на окремі PNG сторінки та завантажує їх."""
    import fitz
    doc = fitz.open(pdf_path)
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        pix = page.get_pixmap(dpi=150)
        img_name = f"{base_name}_{page_num + 1}.png"
        img_path = os.path.join(TEMP_DIR, img_name)
        pix.save(img_path)
        
        file_metadata = {'name': img_name, 'parents': [folder_id], 'modifiedTime': mod_time_str}
        media = MediaFileUpload(img_path, mimetype='image/png')
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        print(f"  -> Завантажено сторінку: {img_name}")
        if os.path.exists(img_path): os.remove(img_path)
    doc.close()

def main():
    if not os.environ.get('GDRIVE_REFRESH_TOKEN'):
        print("Помилка: Не знайдено змінні оточення для OAuth2!")
        sys.exit(1)

    is_scan_mode = "--scan" in sys.argv
    service = get_drive_service()
    os.makedirs(TEMP_DIR, exist_ok=True)

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
    need_office = False
    need_tif = False
    valid_files = []

    all_supported_exts = ['.pdf'] + CAD_EXTS + OFFICE_EXTS + TIFF_EXTS

    for f in all_files:
        base_name, ext = os.path.splitext(f['name'].lower())
        if ext in all_supported_exts:
            expected_img = f"{base_name}_1.png" if ext in MULTI_PAGE_EXTS else f"{base_name}.png"
            if expected_img not in existing_names:
                valid_files.append(f)
                if ext == '.pdf': need_pdf = True
                elif ext == '.dxf': need_dxf = True
                elif ext == '.dwg': need_dwg = True
                elif ext in OFFICE_EXTS: need_office = True
                elif ext in TIFF_EXTS: need_tif = True

    if is_scan_mode:
        print(f"Потребують обробки: PDF={need_pdf}, DXF={need_dxf}, DWG={need_dwg}, Office={need_office}, TIFF={need_tif}")
        if 'GITHUB_OUTPUT' in os.environ:
            with open(os.environ['GITHUB_OUTPUT'], 'a') as github_output:
                github_output.write(f"has_pdf={str(need_pdf).lower()}\n")
                github_output.write(f"has_dxf={str(need_dxf).lower()}\n")
                github_output.write(f"has_dwg={str(need_dwg).lower()}\n")
                github_output.write(f"has_office={str(need_office).lower()}\n")
                github_output.write(f"has_tif={str(need_tif).lower()}\n")
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

        # --- СЦЕНАРІЙ 1: ПРЯМИЙ PDF ---
        if ext == '.pdf':
            process_pdf_pages(temp_input_path, base_name, FOLDER_ID, service, mod_time_str)

        # --- СЦЕНАРІЙ 2: ОФІСНІ ДОКУМЕНТИ ТА ТЕКСТ (DOC, DOCX, XLSX, CSV, MHTML тощо) ---
        elif ext in OFFICE_EXTS:
            generated_pdf = convert_office_to_pdf(temp_input_path, TEMP_DIR)
            if generated_pdf:
                process_pdf_pages(generated_pdf, base_name, FOLDER_ID, service, mod_time_str)
                os.remove(generated_pdf)

        # --- СЦЕНАРІЙ 3: БАГАТОСТОРІНКОВІ ТА ЗВИЧАЙНІ TIFF ---
        elif ext in TIFF_EXTS:
            from PIL import Image, ImageSequence
            try:
                with Image.open(temp_input_path) as img:
                    for page_num, frame in enumerate(ImageSequence.Iterator(img)):
                        img_name = f"{base_name}_{page_num + 1}.png"
                        img_path = os.path.join(TEMP_DIR, img_name)
                        
                        if frame.mode not in ('RGB', 'RGBA'):
                            frame = frame.convert('RGB')
                            
                        frame.save(img_path, 'PNG')
                        
                        file_metadata = {'name': img_name, 'parents': [FOLDER_ID], 'modifiedTime': mod_time_str}
                        media = MediaFileUpload(img_path, mimetype='image/png')
                        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                        print(f"  -> Завантажено сторінку TIFF: {img_name}")
                        if os.path.exists(img_path): os.remove(img_path)
            except Exception as e:
                print(f"  [Помилка обробки TIFF]: {e}")

        # --- СЦЕНАРІЙ 4: CAD ФАЙЛИ (DXF / DWG) ---
        elif ext in CAD_EXTS:
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

            img_name = f"{base_name}.png"
            img_path = os.path.join(TEMP_DIR, img_name)
            
            success, is_pure_3d = convert_dxf_to_png(dxf_to_render, img_path)
            
            if is_pure_3d and original_dwg_path:
                success = extract_dwg_thumbnail(original_dwg_path, img_path)
            elif is_pure_3d and ext == '.dxf':
                possible_dwg_partner = os.path.join(TEMP_DIR, base_name.replace("3D1", "3D") + ".dwg")
                if os.path.exists(possible_dwg_partner):
                    success = extract_dwg_thumbnail(possible_dwg_partner, img_path)
            
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
