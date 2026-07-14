"""
scratch/diagnose_portal.py
Diagnóstico del portal SEMARNAT — inspecciona el DOM real para encontrar
los selectores correctos de botones de descarga.
"""
import sys, time, json, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Una clave que sabemos existe en el portal
TEST_CLAVE = "23QR2025T0061"  # Aparece en extractions/
BASE_URL = "https://app.semarnat.gob.mx/consulta-tramite/#/portal-consulta/consulta-tramite/"

from scrapers.semarnat_downloader import make_chrome_driver

print(f"[DIAG] Lanzando Chrome headless para diagnosticar: {BASE_URL}{TEST_CLAVE}")

download_tmp = Path("/tmp/diag_downloads")
download_tmp.mkdir(exist_ok=True)

driver = make_chrome_driver(download_tmp, headless=True)

try:
    url = BASE_URL + TEST_CLAVE
    driver.get(url)
    print(f"[DIAG] URL cargada: {url}")

    # Esperar hasta 30s a que cargue algo útil
    for i in range(30):
        time.sleep(1)
        title = driver.title
        src_len = len(driver.page_source)
        print(f"[DIAG] t={i+1}s | title={title!r} | page_source_len={src_len}")
        if src_len > 5000:
            break

    # Guardar screenshot
    ss_path = "/tmp/diag_portal_screenshot.png"
    driver.save_screenshot(ss_path)
    print(f"[DIAG] Screenshot guardado: {ss_path}")

    # Dump del DOM completo
    page_src = driver.page_source
    dom_path = "/tmp/diag_portal_dom.html"
    with open(dom_path, "w", encoding="utf-8") as f:
        f.write(page_src)
    print(f"[DIAG] DOM guardado: {dom_path} ({len(page_src)} bytes)")

    # Inspeccionar elementos relevantes
    from selenium.webdriver.common.by import By

    # Buscar TODOS los botones
    all_buttons = driver.find_elements(By.TAG_NAME, "button")
    print(f"\n[DIAG] Total botones encontrados: {len(all_buttons)}")
    for i, btn in enumerate(all_buttons[:20]):
        try:
            txt = btn.text.strip()
            cls = btn.get_attribute("class") or ""
            typ = btn.get_attribute("type") or ""
            parent_cls = driver.execute_script("return arguments[0].parentElement ? arguments[0].parentElement.className : ''", btn)
            print(f"  btn[{i}]: text={txt!r} class={cls!r} type={typ!r} parent_class={parent_cls!r}")
        except Exception as e:
            print(f"  btn[{i}]: ERROR {e}")

    # Buscar elementos con clase 'descargas'
    descargas_els = driver.find_elements(By.CSS_SELECTOR, "[class*='descargas']")
    print(f"\n[DIAG] Elementos con clase 'descargas': {len(descargas_els)}")
    for el in descargas_els[:5]:
        try:
            print(f"  tag={el.tag_name} class={el.get_attribute('class')!r} html={el.get_attribute('outerHTML')[:200]!r}")
        except Exception as e:
            print(f"  ERROR: {e}")

    # Buscar anclas de descarga (a[href*='.pdf'])
    pdf_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='.pdf'], a[href*='download'], a[download]")
    print(f"\n[DIAG] Links de PDF/download: {len(pdf_links)}")
    for lnk in pdf_links[:10]:
        try:
            print(f"  href={lnk.get_attribute('href')!r} text={lnk.text[:80]!r}")
        except Exception as e:
            print(f"  ERROR: {e}")

    # Buscar SVG icons de descarga o elementos con download en atributos
    download_hints = driver.find_elements(By.XPATH, "//*[contains(@class,'download') or contains(@id,'download') or contains(@aria-label,'descargar') or contains(@title,'descargar') or contains(@title,'Descargar')]")
    print(f"\n[DIAG] Elementos con hints de 'download': {len(download_hints)}")
    for el in download_hints[:5]:
        try:
            print(f"  tag={el.tag_name} class={el.get_attribute('class')!r} html={el.get_attribute('outerHTML')[:300]!r}")
        except Exception as e:
            print(f"  ERROR: {e}")

    # Dump del network log (PDF URLs)
    import json as _json
    logs = driver.get_log("performance")
    pdf_urls_seen = []
    for entry in logs:
        msg = _json.loads(entry["message"])
        method = msg.get("message", {}).get("method", "")
        if method in ("Network.requestWillBeSent", "Network.responseReceived"):
            params = msg["message"].get("params", {})
            url_found = (
                params.get("request", {}).get("url", "")
                or params.get("response", {}).get("url", "")
            )
            if url_found and (".pdf" in url_found.lower() or "descarga" in url_found.lower() or "download" in url_found.lower()):
                pdf_urls_seen.append(url_found)

    print(f"\n[DIAG] URLs de PDF/descarga en network log: {len(set(pdf_urls_seen))}")
    for u in list(set(pdf_urls_seen))[:10]:
        print(f"  {u}")

    # Listar todas las requests de la red para entender la API
    api_requests = []
    for entry in logs:
        msg = _json.loads(entry["message"])
        if msg.get("message", {}).get("method") == "Network.requestWillBeSent":
            params = msg["message"].get("params", {})
            req_url = params.get("request", {}).get("url", "")
            if req_url and "semarnat" in req_url.lower():
                api_requests.append(req_url)
    
    print(f"\n[DIAG] Requests a semarnat.gob.mx: {len(set(api_requests))}")
    for u in sorted(set(api_requests))[:20]:
        print(f"  {u}")

    # Estado actual de la URL (puede haber redireccionado)
    print(f"\n[DIAG] URL final del driver: {driver.current_url}")
    print(f"[DIAG] Título final: {driver.title!r}")

    # Texto visible en la página
    body_text = driver.find_element(By.TAG_NAME, "body").text
    print(f"\n[DIAG] Primeros 2000 chars del body text:")
    print(body_text[:2000])

finally:
    driver.quit()
    print("\n[DIAG] Driver cerrado. Revisa /tmp/diag_portal_screenshot.png y /tmp/diag_portal_dom.html")
