import sqlite3
import os
import sys
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('❌ Playwright no instalado. Ejecutando instalación...')
    os.system('pip install playwright && playwright install chromium')
    from playwright.sync_api import sync_playwright

DB_PATH = 'data/metadata_proyecto.db'
PACKAGES_DIR = 'data/packages'
SEMARNAT_URL = 'https://www.semarnat.gob.mx/gobmx/transparencia/constramite.html'

def get_missing_keys():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT clave FROM metadata_proyecto WHERE clave != 'SIN_CLAVE'")
    all_keys = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    missing = []
    for key in all_keys:
        pkg_folder = os.path.join(PACKAGES_DIR, key)
        if not os.path.exists(pkg_folder) or not any(f.endswith('.pdf') for f in os.listdir(pkg_folder)):
            missing.append(key)
    return missing

def run_visible_scraper():
    missing_keys = get_missing_keys()
    print('=== 🌐 INICIANDO SCRAPER VISIBLE (HEADLESS = FALSE) ===')
    print(f'📊 Claves pendientes por descargar PDF: {len(missing_keys)}\n')
    
    if not missing_keys:
        print('✅ ¡Todos los proyectos ya tienen sus PDFs descargados!')
        return

    os.makedirs(PACKAGES_DIR, exist_ok=True)

    with sync_playwright() as p:
        print('🖥️ Abriendo ventana de Chromium en tu pantalla...')
        browser = p.chromium.launch(headless=False, args=['--start-maximized'])
        context = browser.new_context(no_viewport=True, accept_downloads=True)
        page = context.new_page()

        successful = 0
        failed = 0

        for idx, clave in enumerate(missing_keys, 1):
            print(f'[{idx}/{len(missing_keys)}] 🔍 Consulta SEMARNAT: {clave}')
            target_dir = os.path.join(PACKAGES_DIR, clave)
            os.makedirs(target_dir, exist_ok=True)

            try:
                page.goto(SEMARNAT_URL, timeout=30000)
                page.wait_for_selector('#bitacora', timeout=10000)
                page.fill('#bitacora', clave)
                time.sleep(0.5)
                
                # Intentar clic en consultar
                page.click('input[type="submit"], button[type="submit"], #btnConsultar')
                time.sleep(2)

                pdf_links = page.query_selector_all("a[href$='.pdf'], a[href*='pdf'], a[href*='download']")
                if not pdf_links:
                    print(f'   ⚠️ No hay PDFs directos en la bitácora para {clave}')
                    failed += 1
                    continue

                for link in pdf_links:
                    try:
                        with page.expect_download(timeout=15000) as download_info:
                            link.click()
                        download = download_info.value
                        save_path = os.path.join(target_dir, download.suggested_filename)
                        download.save_as(save_path)
                        print(f'   📄 Guardado: {download.suggested_filename}')
                        successful += 1
                    except Exception:
                        pass
            except Exception as err:
                print(f'   ❌ Error en clave {clave}: {err}')
                failed += 1

            time.sleep(0.8)

        print(f'\n=== 🏁 RESUMEN ===')
        print(f'✅ Exitosos: {successful} | ⚠️ Fallidos/Sin PDF: {failed}')
        browser.close()

if __name__ == '__main__':
    run_visible_scraper()