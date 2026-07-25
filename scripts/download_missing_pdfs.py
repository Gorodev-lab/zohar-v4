import sqlite3
import os
import sys
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('❌ Playwright no instalado.')
    sys.exit(1)

DB_PATH = 'data/metadata_proyecto.db'
PACKAGES_DIR = 'data/packages'
PORTAL_URL = 'https://app.semarnat.gob.mx/consulta-tramite/#/portal-consulta'

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
    print('=== 🌐 SCRAPER VISIBLE: PORTAL TRÁMITES SEMARNAT 2.0 ===')
    print(f'📊 Claves pendientes por procesar: {len(missing_keys)}\n')
    
    if not missing_keys:
        print('✅ ¡Todos los proyectos ya tienen sus PDFs descargados!')
        return

    os.makedirs(PACKAGES_DIR, exist_ok=True)

    with sync_playwright() as p:
        print('🖥️ Desplegando Chromium en tu pantalla...')
        browser = p.chromium.launch(headless=False, args=['--start-maximized'])
        context = browser.new_context(no_viewport=True, accept_downloads=True)
        page = context.new_page()

        successful = 0
        failed = 0

        for idx, clave in enumerate(missing_keys, 1):
            print(f'[{idx}/{len(missing_keys)}] 🔍 Consultando clave: {clave}')
            target_dir = os.path.join(PACKAGES_DIR, clave)
            os.makedirs(target_dir, exist_ok=True)

            try:
                page.goto(PORTAL_URL, timeout=30000, wait_until='networkidle')
                time.sleep(1.5)
                
                # Buscar input del formulario de la SPA
                input_selector = 'input[type="text"], input[placeholder*="clave"], input[placeholder*="Clave"], input'
                page.wait_for_selector(input_selector, timeout=10000)
                
                # Limpiar y rellenar la clave del proyecto
                search_input = page.locator(input_selector).first
                search_input.fill(clave)
                time.sleep(0.5)

                # Clic en botón Buscar / Consultar
                btn = page.locator('button:has-text("Buscar"), button:has-text("Consultar"), input[type="submit"]').first
                btn.click()
                
                time.sleep(2.5) # Esperar renderizado de respuesta SPA

                # Descargar adjuntos o resolutivos
                pdf_links = page.query_selector_all("a[href*='.pdf'], a[href*='download'], button:has-text('PDF')")
                if not pdf_links:
                    print(f'   ⚠️ No se detectaron enlaces PDF directos para {clave}')
                    failed += 1
                    continue

                for link in pdf_links:
                    try:
                        with page.expect_download(timeout=15000) as download_info:
                            link.click()
                        download = download_info.value
                        save_path = os.path.join(target_dir, download.suggested_filename)
                        download.save_as(save_path)
                        print(f'   📄 Guardado con éxito: {download.suggested_filename}')
                        successful += 1
                    except Exception:
                        pass
            except Exception as err:
                print(f'   ❌ Error interactuando con la SPA para {clave}: {err}')
                failed += 1

            time.sleep(1)

        print(f'\n=== 🏁 RESUMEN DE DESCARGAS ===')
        print(f'✅ Completados: {successful} | ⚠️ Sin PDF / Fallidos: {failed}')
        browser.close()

if __name__ == '__main__':
    run_visible_scraper()