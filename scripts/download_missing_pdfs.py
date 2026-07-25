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
                page.goto(PORTAL_URL, timeout=30000)
                time.sleep(2)
                
                # Selector del input
                input_selector = 'input[placeholder*="bitácora"], input[placeholder*="clave"], input[type="text"]'
                page.wait_for_selector(input_selector, timeout=10000)
                
                # Escribir la clave
                search_input = page.locator(input_selector).first
                search_input.click()
                search_input.fill('')
                search_input.type(clave, delay=50)
                time.sleep(0.5)

                # Clic en Buscar O presionar Enter
                search_input.press('Enter')
                
                # Intentar clic en el botón 'Buscar' granate por si acaso
                try:
                    page.click('button:has-text("Buscar")', timeout=2000)
                except Exception:
                    pass
                
                print('   ⏳ Esperando resultados de la consulta...')
                time.sleep(4) # Tiempo para respuesta SPA

                # Buscar elementos de descarga en los resultados
                download_selectors = [
                    "a[href*='.pdf']", 
                    "a[href*='download']", 
                    "button:has-text('PDF')", 
                    "a:has-text('Descargar')",
                    "i.fa-file-pdf",
                    "i.fa-download"
                ]

                pdf_elements = []
                for sel in download_selectors:
                    found = page.query_selector_all(sel)
                    if found:
                        pdf_elements.extend(found)

                if not pdf_elements:
                    print(f'   ⚠️ No se detectaron archivos adjuntos/PDFs visibles para {clave}')
                    failed += 1
                    continue

                print(f'   📄 {len(pdf_elements)} enlace(s) o documento(s) encontrado(s). Descargando...')
                for elem in pdf_elements:
                    try:
                        with page.expect_download(timeout=10000) as download_info:
                            elem.click()
                        download = download_info.value
                        save_path = os.path.join(target_dir, download.suggested_filename)
                        download.save_as(save_path)
                        print(f'   ✅ Archivo guardado: {download.suggested_filename}')
                        successful += 1
                    except Exception:
                        pass
            except Exception as err:
                print(f'   ❌ Error procesando {clave}: {err}')
                failed += 1

            time.sleep(2)

        print(f'\n=== 🏁 RESUMEN ===')
        print(f'✅ Descargados: {successful} | ⚠️ Sin PDF / Fallidos: {failed}')
        browser.close()

if __name__ == '__main__':
    run_visible_scraper()