import sqlite3
import os
import re
import sys
import time
import base64

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
except ImportError:
    print('❌ Playwright no instalado.')
    sys.exit(1)

DB_PATH = 'data/metadata_proyecto.db'
PACKAGES_DIR = 'data/packages'
PORTAL_URL = 'https://app.semarnat.gob.mx/consulta-tramite/#/portal-consulta'

ARCHIVOPDF_EP = 'https://apps1.semarnat.gob.mx/ws-bitacora-tramite/proyectos/archivopdf'


def get_missing_keys():
    # Claves pendientes = solo DGIRA válidas (máscara 13 chars
    # NNLLNNNNLNNNN) de metadata_proyecto cuya carpeta en
    # data/packages/<clave>/ aún no contiene al menos un PDF válido.
    # Se excluyen gacetas/ASEA (no son servidas por el portal de trámites).
    # Idempotente: si ya se descargó, se omite.
    import glob
    CLAVE_RE = re.compile(r'^[0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4}$')
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT clave FROM metadata_proyecto')
    all_keys = [r[0] for r in cur.fetchall()]
    conn.close()

    pending = []
    for clave in all_keys:
        if not CLAVE_RE.match(clave):
            continue  # gacetas/ASEA u otras: fuera de alcance del portal
        dir_path = os.path.join(PACKAGES_DIR, clave)
        pdfs = glob.glob(os.path.join(dir_path, '*.pdf')) if os.path.isdir(dir_path) else []
        # considerar válido si pesa > 1 KB (evita PDFs corruptos/vacíos)
        if any(os.path.getsize(p) > 1024 for p in pdfs):
            continue
        pending.append(clave)
    return pending


def fetch_archivopdf(request_ctx, auth, payload, endpoint=ARCHIVOPDF_EP, timeout=120000):
    """Reenvía el POST archivopdf con los headers exactos que usa Angular y
    devuelve los bytes del PDF. request_ctx es un APIRequestContext (page.request).
    El endpoint espera: Content-Type text/plain, body = path crudo del archivo,
    Authorization Bearer. Devuelve bytes vacíos si no es 200 o no es PDF."""
    headers = {
        'Content-Type': 'text/plain',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://app.semarnat.gob.mx/',
        'Authorization': auth,
    }
    resp = request_ctx.post(endpoint, data=payload, headers=headers, timeout=timeout)
    if resp.status != 200:
        return b''
    body = resp.body()
    return body if body and body[:4] == b'%PDF' else b''

def run_visible_scraper():
    missing_keys = get_missing_keys()
    print('=== 🌐 SCRAPER VISIBLE: PORTAL TRÁMITES SEMARNAT 2.0 ===')
    print(f'📊 Claves pendientes por procesar: {len(missing_keys)}\n')
    
    if not missing_keys:
        print('✅ ¡Todos los proyectos ya tienen sus PDFs descargados!')
        return

    os.makedirs(PACKAGES_DIR, exist_ok=True)

    with sync_playwright() as p:
        print('🖥️ Desplegando Chromium (headless)...')
        browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(accept_downloads=True)
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
                
                # 🔥 SCROLL CRÍTICO: Desplazar la pantalla hacia abajo para ver 'Documentos relacionados al trámite'
                try:
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    print('   📜 Scroll hacia abajo ejecutado.')
                except Exception:
                    pass
                
                # Espera explícita para que Angular renderice los botones en la parte inferior
                time.sleep(1.5)
                
                # ESPERA EXPLÍCITA: Contenedor de 'Documentos relacionados al tramite' para asegurar que Angular terminó de renderizar
                try:
                    page.wait_for_selector('app-tramite-documentos, .documentos-relacionados, [class*="document"]', timeout=2000)
                except Exception:
                    pass
                
                # ESPERA EXPLÍCITA: Contenedor de 'Documentos relacionados al tramite' para asegurar que Angular terminó de renderizar
                try:
                    page.wait_for_selector('app-tramite-documentos, .documentos-relacionados, [class*="document"]', timeout=2000)
                except Exception:
                    pass

                # Selector permisivo: busca elementos que contengan el texto exacto o parcial (ignorando mayúsculas/minúsculas)
                targets = ['Resolutivos', 'Estudios', 'Resumen', 'MIA']
                found_buttons = []
                for t in targets:
                    buttons = page.locator(f"text={t}")
                    if buttons.count() > 0:
                        found_buttons.append(t)

                if not found_buttons:
                    print(f'   ⚠️ No se encontraron botones para {clave}. Saltando...')
                    failed += 1
                    continue

                print(f'   🎯 Botones hallados: {found_buttons}')

                # Mapeo de botón -> nombre de archivo de salida dentro de data/packages/<clave>/
                filename_map = {
                    'Resolutivos': 'resolutivos.pdf',
                    'Estudios': 'estudios.pdf',
                    'Resumen': 'resumen.pdf',
                    'MIA': 'mia.pdf',
                }

                # Endpoint real de SEMARNAT que entrega el PDF (no es popup ni download event:
                # el botón dispara un POST con Bearer token y el path del archivo como body crudo).
                ARCHIVOPDF_EP = 'https://apps1.semarnat.gob.mx/ws-bitacora-tramite/proyectos/archivopdf'

                for b_name in found_buttons:
                    print(f'   📄 Solicitando: "{b_name}"...')
                    btn = page.locator(f"text={b_name}").first

                    captured = {}
                    def _on_req(req, _bname=b_name):
                        # Solo capturamos el POST archivopdf disparado por ESTE botón
                        if 'archivopdf' in req.url and req.method == 'POST':
                            try:
                                captured['auth'] = req.headers.get('authorization')
                                captured['payload'] = req.post_data
                            except Exception:
                                pass
                    page.on('request', _on_req)

                    try:
                        btn.scroll_into_view_if_needed()

                        # 1) Clic para que Angular dispare el POST archivopdf (con headers correctos)
                        try:
                            with page.context.expect_page(timeout=4000):
                                btn.click()
                            # Si abrió popup, cerrarla (el PDF también viene por el POST)
                            for pg in context.pages[1:]:
                                try:
                                    pg.close()
                                except Exception:
                                    pass
                        except PWTimeoutError:
                            pass  # popup no abrió; el POST igual se disparó
                        except Exception:
                            pass

                        # Esperar a que se capture el POST real
                        waited = 0
                        while not captured and waited < 15:
                            time.sleep(0.5)
                            waited += 0.5

                        page.remove_listener('request', _on_req)

                        if not captured.get('payload') or not captured.get('auth'):
                            print(f'   ⚠️ No se capturó el POST archivopdf para "{b_name}". Saltando...')
                            failed += 1
                            continue

                        # 2) Replay con page.request (lleva cookies del contexto, bufferiza bien)
                        try:
                            pdf_bytes = fetch_archivopdf(
                                page.request, captured['auth'], captured['payload']
                            )
                            if pdf_bytes:
                                out_name = filename_map.get(b_name, f'{b_name.lower()}.pdf')
                                out_path = os.path.join(target_dir, out_name)
                                with open(out_path, 'wb') as f:
                                    f.write(pdf_bytes)
                                print(f'   ✅ Guardado: {out_path} ({len(pdf_bytes)} bytes)')
                                successful += 1
                            else:
                                print(f'   ⚠️ Respuesta no es PDF válido para "{b_name}".')
                                failed += 1
                        except Exception as post_err:
                            print(f'   ⚠️ Error en replay POST para "{b_name}": {str(post_err)[:80]}')
                            failed += 1

                    except Exception as click_err:
                        try:
                            page.remove_listener('request', _on_req)
                        except Exception:
                            pass
                        print(f'   ⚠️ Error procesando botón "{b_name}": {str(click_err)[:80]}')
                        failed += 1
                        continue

            except Exception as err:
                print(f'   ❌ Error procesando {clave}: {err}')
                failed += 1

            time.sleep(2)

        print(f'\n=== 🏁 RESUMEN ===\n✅ Descargados: {successful} | ⚠️ Sin PDF / Fallidos: {failed}')
        browser.close()

if __name__ == '__main__':
    run_visible_scraper()