"""
scrapers/semarnat_downloader.py
Motor principal de descarga SINAT/SEMARNAT.
Chrome + Selenium con espera activa inteligente y clasificador posicional.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import time
import logging
from pathlib import Path
from typing import Generator, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes fijas del portal
# ---------------------------------------------------------------------------
SEMARNAT_URL = "https://app.semarnat.gob.mx/consulta-tramite/"

# URL base del portal — el router Angular siempre aterriza aquí
SEMARNAT_PORTAL_BASE = "https://app.semarnat.gob.mx/consulta-tramite/#/portal-consulta"

TEMP_SUFFIXES = (".part", ".tmp", ".crdownload", ".download", ".~download")

BUTTON_CSS_SELECTOR = ".descargas button, .descargas .btn"

BUTTON_XPATH_TEMPLATE = (
    "(//div[contains(@class,'descargas')]//button | "
    "//div[contains(@class,'descargas')]//*[contains(@class,'btn')])[{n}]"
)

# Tabla de clasificación posicional (sin keywords en nombre de archivo)
# n_files → {0: tipo, 1: tipo, 2: tipo}
POSITIONAL_CLASSIFICATION = {
    3: {0: "resumen", 1: "estudio", 2: "resolutivo"},
    2: {0: "resumen", 1: "resolutivo"},
    1: {0: "estudio"},
}

# Keywords explícitas en nombres de archivo
KEYWORD_MAP = {
    "resumen":     "resumen",
    "ejecutivo":   "resumen",
    "estudio":     "estudio",
    "eia":         "estudio",
    "resolutivo":  "resolutivo",
    "resolucion":  "resolutivo",
    "oficio":      "resolutivo",
}


# ---------------------------------------------------------------------------
# Helpers de Chrome / Selenium
# ---------------------------------------------------------------------------

def make_chrome_driver(download_dir: Path, headless: bool = True):
    """Chrome configurado: descarga sin diálogo, sin visor PDF, CDP logging."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    download_dir.mkdir(parents=True, exist_ok=True)

    chrome_binary = os.environ.get("CHROME_BINARY", "")

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    if chrome_binary and Path(chrome_binary).exists():
        opts.binary_location = chrome_binary

    prefs = {
        "download.default_directory":        str(download_dir.resolve()),
        "download.prompt_for_download":       False,
        "download.directory_upgrade":         True,
        "plugins.always_open_pdf_externally": True,
        "safebrowsing.enabled":              True,
    }
    opts.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(options=opts)

    # Intentar configurar CDP setDownloadBehavior (no fatal si falla)
    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(download_dir.resolve())},
        )
    except Exception as exc:
        logger.warning("No se pudo configurar setDownloadBehavior via CDP: %s", exc)

    return driver


def safe_click(driver, locator, timeout: int = 15):
    """Click robusto con scroll, retry y fallback JS."""
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        ElementClickInterceptedException,
        StaleElementReferenceException,
    )

    by, value = locator
    wait = WebDriverWait(driver, timeout)

    for attempt in range(3):
        try:
            el = wait.until(EC.element_to_be_clickable((by, value)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.3)
            el.click()
            return True
        except (ElementClickInterceptedException, StaleElementReferenceException):
            if attempt == 2:
                try:
                    el = driver.find_element(by, value)
                    driver.execute_script("arguments[0].click();", el)
                    return True
                except Exception:
                    pass
            time.sleep(0.5)
    return False


def element_exists(driver, locator, timeout: int = 0) -> bool:
    """Verifica existencia de elemento sin lanzar excepción."""
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    by, value = locator
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )
        return True
    except TimeoutException:
        return False


def extract_pdf_urls_from_network_log(driver) -> list[str]:
    """Extrae URLs de PDF desde CDP performance logs."""
    import json

    urls = []
    try:
        logs = driver.get_log("performance")
        for entry in logs:
            msg = json.loads(entry["message"])
            method = msg.get("message", {}).get("method", "")
            if method in ("Network.requestWillBeSent", "Network.responseReceived"):
                params = msg["message"].get("params", {})
                url = (
                    params.get("request", {}).get("url", "")
                    or params.get("response", {}).get("url", "")
                )
                if url.lower().endswith(".pdf") or "pdf" in url.lower():
                    urls.append(url)
    except Exception as exc:
        logger.debug("Error extrayendo logs CDP: %s", exc)
    return list(set(urls))


def download_pdf_via_requests(
    url: str,
    dest_path: Path,
    cookies: dict | None = None,
    headers: dict | None = None,
    timeout: int = 120,
) -> bool:
    """Descarga PDF usando requests con cookies de sesión Selenium."""
    import requests as req

    h = {"User-Agent": "Mozilla/5.0", **(headers or {})}
    try:
        resp = req.get(url, cookies=cookies, headers=h, timeout=timeout, stream=True)
        resp.raise_for_status()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        return True
    except Exception as exc:
        logger.error("Error descargando %s: %s", url, exc)
        return False


def wait_for_downloads(
    download_dir: Path,
    since_ts: float,
    expect_at_least: int = 1,
    timeout: int = 600,
    poll: float = 1.0,
) -> list[Path]:
    """
    Espera activa inteligente de dos fases.
    Fase 1: detectar inicio (hasta 30s).
    Fase 2: esperar finalización (hasta timeout).
    Retorna lista de PDFs nuevos descargados.
    """
    deadline = time.time() + timeout
    phase1_deadline = time.time() + 30
    saw_temp = False

    while time.time() < deadline:
        all_files = list(download_dir.iterdir())
        temps = [f for f in all_files if f.suffix.lower() in TEMP_SUFFIXES]
        new_pdfs = [
            f for f in all_files
            if f.suffix.lower() == ".pdf"
            and f.stat().st_mtime >= since_ts
        ]

        if temps:
            saw_temp = True

        # Fase 1: esperando inicio
        if time.time() < phase1_deadline:
            if temps or len(new_pdfs) >= expect_at_least:
                phase1_deadline = 0  # pasar a fase 2
            time.sleep(poll)
            continue

        # Fase 2: esperando finalización
        if not temps and len(new_pdfs) >= expect_at_least:
            return new_pdfs

        # Cancelación: vimos temps, desaparecieron, pero sin nuevos PDFs
        if saw_temp and not temps and len(new_pdfs) == 0:
            logger.warning("Descarga cancelada: temps desaparecieron sin PDFs nuevos")
            return []

        time.sleep(poll)

    logger.warning("Timeout esperando descargas (%ds)", timeout)
    return [
        f for f in download_dir.iterdir()
        if f.suffix.lower() == ".pdf" and f.stat().st_mtime >= since_ts
    ]


def safe_move(src_path: Path, dst_dir: Path) -> Path:
    """Mueve evitando colisiones: agrega _v2, _v3... si ya existe."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst_path = dst_dir / src_path.name

    if not dst_path.exists():
        shutil.move(str(src_path), str(dst_path))
        return dst_path

    stem = src_path.stem
    suffix = src_path.suffix
    for v in range(2, 100):
        candidate = dst_dir / f"{stem}_v{v}{suffix}"
        if not candidate.exists():
            shutil.move(str(src_path), str(candidate))
            return candidate

    raise RuntimeError(f"No se pudo mover {src_path} — demasiadas colisiones")


def mover_estudios_y_resolutivos(
    download_dir: Path,
    carpeta_estudios: Path,
    carpeta_resolutivos: Path,
    extension: str = ".pdf",
) -> dict:
    """Clasifica por prefijo 'estudio.' / 'resolutivo.' y mueve."""
    moved = {"estudios": [], "resolutivos": []}
    for f in download_dir.iterdir():
        if f.suffix.lower() != extension.lower():
            continue
        name = f.name.lower()
        if name.startswith("estudio."):
            moved["estudios"].append(safe_move(f, carpeta_estudios))
        elif name.startswith("resolutivo."):
            moved["resolutivos"].append(safe_move(f, carpeta_resolutivos))
    return moved


# ---------------------------------------------------------------------------
# Clasificador posicional (contrato del harness)
# ---------------------------------------------------------------------------

def _classify_by_keyword(filename: str) -> Optional[str]:
    """Detecta tipo por keywords en el nombre del archivo."""
    name_lower = filename.lower()
    for kw, tipo in KEYWORD_MAP.items():
        if kw in name_lower:
            return tipo
    return None


def renombrar_archivos_por_clave(
    download_dir: Path,
    clave: str,
    since_ts: float,
    carpeta_estudios: Optional[Path] = None,
    carpeta_resolutivos: Optional[Path] = None,
    carpeta_resumenes: Optional[Path] = None,
) -> dict[str, list[Path]]:
    """
    Clasifica y renombra PDFs nuevos por clave SEMARNAT.

    Regla de fallback posicional cuando el nombre no contiene keywords:
        n=3 → [resumen, estudio, resolutivo]
        n=2 → [resumen, resolutivo]          ← NO asigna estudio al índice 1
        n=1 → [estudio]

    Returns:
        {"resumenes": [...], "estudios": [...], "resolutivos": [...]}
    """
    result: dict[str, list[Path]] = {
        "resumenes": [], "estudios": [], "resolutivos": []
    }

    new_pdfs = sorted(
        [
            f for f in download_dir.iterdir()
            if f.suffix.lower() == ".pdf"
            and f.stat().st_mtime >= since_ts
            and not f.name.startswith(clave)
        ],
        key=lambda f: f.stat().st_mtime,
    )

    if not new_pdfs:
        return result

    n = len(new_pdfs)
    pos_map = POSITIONAL_CLASSIFICATION.get(n, {i: "resumen" for i in range(n)})

    for idx, pdf in enumerate(new_pdfs):
        # Prioridad 1: keyword en el nombre
        tipo = _classify_by_keyword(pdf.name)
        # Prioridad 2: posición
        if tipo is None:
            tipo = pos_map.get(idx, "resumen")

        nuevo_nombre = f"{clave}.{tipo}.{idx:02d}{pdf.suffix}"
        dst_pdf = download_dir / nuevo_nombre
        pdf.rename(dst_pdf)

        # Mover a carpeta correspondiente
        if tipo == "resumen" and carpeta_resumenes:
            dst_pdf = safe_move(dst_pdf, carpeta_resumenes)
        elif tipo == "estudio" and carpeta_estudios:
            dst_pdf = safe_move(dst_pdf, carpeta_estudios)
        elif tipo == "resolutivo" and carpeta_resolutivos:
            dst_pdf = safe_move(dst_pdf, carpeta_resolutivos)

        key = {"resumen": "resumenes", "estudio": "estudios", "resolutivo": "resolutivos"}.get(tipo, "resumenes")
        result[key].append(dst_pdf)

    return result


# ---------------------------------------------------------------------------
# SemarnatDownloader — Clase principal
# ---------------------------------------------------------------------------

class SemarnatDownloader:
    """
    Descargador automático de documentos SINAT/SEMARNAT.
    Usa Chrome + Selenium con espera activa inteligente.
    """

    def __init__(
        self,
        download_dir: str | Path,
        headless: bool = True,
        download_timeout: int = 600,
        carpeta_estudios: Optional[str | Path] = None,
        carpeta_resolutivos: Optional[str | Path] = None,
        carpeta_resumenes: Optional[str | Path] = None,
    ):
        self.download_dir = Path(download_dir)
        self.headless = headless
        self.download_timeout = download_timeout
        self.carpeta_estudios = Path(carpeta_estudios) if carpeta_estudios else None
        self.carpeta_resolutivos = Path(carpeta_resolutivos) if carpeta_resolutivos else None
        self.carpeta_resumenes = Path(carpeta_resumenes) if carpeta_resumenes else None
        self._driver = None

    def _get_driver(self):
        if self._driver is None:
            self._driver = make_chrome_driver(self.download_dir, self.headless)
        return self._driver

    def _quit_driver(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def descargar_clave(self, bitacora_value: str) -> dict:
        """Wrapper síncrono. Consume _descargar_clave_gen, retorna último evento."""
        last_event = {"status": "error", "msg": "No se emitió ningún evento"}
        for event in self._descargar_clave_gen(bitacora_value):
            last_event = event
        return last_event

    def _descargar_clave_gen(
        self, bitacora_value: str
    ) -> Generator[dict, None, None]:
        """
        Generador SSE. Emite dicts con:
            {"status": str, "msg": str, "level": str, ...}

        status values:
            "log"       — mensaje informativo
            "progress"  — progreso numérico (campo "pct": int)
            "complete"  — descarga finalizada exitosamente
            "not_found" — clave no existe en SINAT
            "error"     — error inesperado

        NOTA: El portal SEMARNAT es una Angular v18 SPA. El router Angular
        IGNORA el hash fragment al inicio y SIEMPRE aterriza en el home
        con formulario de búsqueda (#/portal-consulta). El flujo correcto:
          1. Ir al home base
          2. Escribir la clave en el <input>
          3. Clic en btn-primary "Buscar"
          4. Esperar a que Angular enrute a los resultados
          5. Clic en botones de descarga
          6. Fallback: interceptar URLs de PDF del network log CDP
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        yield {"status": "log", "msg": f"Iniciando descarga: {bitacora_value}", "level": "info"}

        driver = self._get_driver()

        try:
            # ----------------------------------------------------------------
            # PASO 1: Navegar al portal BASE — no al URL con la clave.
            # El router Angular v18 ignora el hash fragment al arrancar;
            # siempre aterriza en #/portal-consulta (home con formulario).
            # ----------------------------------------------------------------
            driver.get(SEMARNAT_URL)
            yield {"status": "log", "msg": f"Navegando al portal base: {SEMARNAT_URL}", "level": "info"}
            yield {"status": "progress", "msg": "Cargando portal Angular...", "level": "info", "pct": 5}

            # Esperar a que Angular monte el formulario de búsqueda (hasta 30s)
            try:
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "input[type='text'], input[type='search']")
                    )
                )
                yield {"status": "log", "msg": "Formulario de búsqueda detectado.", "level": "info"}
            except Exception:
                yield {"status": "log", "msg": "Timeout esperando formulario. Continuando...", "level": "warning"}
                time.sleep(5)

            yield {"status": "progress", "msg": "Portal cargado.", "level": "info", "pct": 15}

            # ----------------------------------------------------------------
            # PASO 2: Localizar el input de búsqueda y escribir la clave.
            # DOM real: <input type="text" placeholder="Ingresa el número de
            #            bitácora o clave de proyecto">
            # ----------------------------------------------------------------
            search_input = None
            for _sel in [
                (By.CSS_SELECTOR, "input[type='text']"),
                (By.CSS_SELECTOR, "input[type='search']"),
                (By.XPATH, "//input[contains(@placeholder,'bitácora') or contains(@placeholder,'clave') or contains(@placeholder,'proyecto')]"),
                (By.XPATH, "//input[not(@type='hidden')]"),
            ]:
                try:
                    els = driver.find_elements(*_sel)
                    if els:
                        search_input = els[0]
                        break
                except Exception:
                    pass

            if not search_input:
                yield {
                    "status": "not_found",
                    "msg": "No se encontró el campo de búsqueda en el portal SEMARNAT.",
                    "level": "warning",
                }
                return

            try:
                driver.execute_script("arguments[0].click();", search_input)
                search_input.clear()
                time.sleep(0.3)
                search_input.send_keys(bitacora_value)
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('input', { bubbles: true }));"
                    "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
                    search_input,
                )
                yield {"status": "log", "msg": f"Clave '{bitacora_value}' ingresada en el formulario.", "level": "info"}
            except Exception as e:
                yield {"status": "log", "msg": f"Error ingresando clave: {e}", "level": "warning"}

            yield {"status": "progress", "msg": "Clave ingresada — buscando botón Buscar...", "pct": 20}

            # ----------------------------------------------------------------
            # PASO 3: Localizar y clic en el botón "Buscar"
            # DOM real: <button class="btn btn-primary mt-4 float-left shadow" type="submit">
            # ----------------------------------------------------------------
            search_btn = None
            for _sel in [
                (By.CSS_SELECTOR, "button.btn-primary"),
                (By.XPATH, "//button[contains(text(), 'Buscar')]"),
                (By.CSS_SELECTOR, "button[type='submit']"),
                (By.XPATH, "//button[@type='submit']"),
            ]:
                try:
                    els = driver.find_elements(*_sel)
                    if els:
                        search_btn = els[0]
                        break
                except Exception:
                    pass

            if not search_btn:
                yield {
                    "status": "not_found",
                    "msg": "No se encontró el botón 'Buscar' en el portal.",
                    "level": "warning",
                }
                return

            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", search_btn)
                time.sleep(0.3)
                try:
                    search_btn.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", search_btn)
                yield {"status": "log", "msg": "Botón 'Buscar' clickeado — esperando resultados Angular...", "level": "info"}
            except Exception as e:
                yield {"status": "log", "msg": f"Error en clic de Buscar: {e}", "level": "warning"}

            yield {"status": "progress", "msg": "Buscando en SEMARNAT...", "pct": 30}

            # ----------------------------------------------------------------
            # PASO 4: Esperar a que Angular enrute y aparezcan botones de
            # descarga (polling hasta 60s)
            # ----------------------------------------------------------------
            locator = (By.CSS_SELECTOR, BUTTON_CSS_SELECTOR)
            # También buscar por selectores más amplios en caso de que el portal cambie
            broad_locator = (By.CSS_SELECTOR, ".descargas button, .descargas .btn, [class*='descargas'] button, [class*='descarga'] button")
            buttons_found = False

            for attempt in range(1, 61):
                if element_exists(driver, locator, timeout=1) or element_exists(driver, broad_locator, timeout=0):
                    buttons_found = True
                    break
                time.sleep(1)
                if attempt % 10 == 0:
                    current_url = driver.current_url
                    yield {"status": "log", "msg": f"Esperando resultados ({attempt}s)... URL: {current_url}", "level": "info"}
                    # Si Angular enrutó a página de trámite: buscar cualquier botón de acción
                    if attempt >= 20:
                        all_btns = driver.find_elements(By.TAG_NAME, "button")
                        action_btns = [
                            b for b in all_btns
                            if "navbar" not in (b.get_attribute("class") or "")
                            and b.text.strip() not in ("Buscar", "")
                        ]
                        if action_btns:
                            yield {"status": "log", "msg": f"{len(action_btns)} botones de acción en página de resultados.", "level": "info"}
                            buttons_found = True
                            break

            # ----------------------------------------------------------------
            # FALLBACK: Interceptar URLs de PDF del network log CDP
            # Si Selenium no pudo hacer click, descargar directamente vía requests
            # ----------------------------------------------------------------
            pdf_urls_from_log = extract_pdf_urls_from_network_log(driver)
            if pdf_urls_from_log and not buttons_found:
                yield {"status": "log", "msg": f"Fallback requests: {len(pdf_urls_from_log)} URL(s) de PDF en network log.", "level": "info"}
                selenium_cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
                since_ts_direct = time.time()
                downloaded_direct = []
                for idx, pdf_url in enumerate(pdf_urls_from_log):
                    raw_name = pdf_url.split("/")[-1].split("?")[0]
                    dest_filename = raw_name if raw_name.endswith(".pdf") else f"{bitacora_value}_{idx:02d}.pdf"
                    dest_path = self.download_dir / dest_filename
                    yield {"status": "log", "msg": f"Descargando via requests: {dest_filename}", "level": "info"}
                    if download_pdf_via_requests(pdf_url, dest_path, cookies=selenium_cookies):
                        downloaded_direct.append(dest_path)

                if downloaded_direct:
                    classified = renombrar_archivos_por_clave(
                        download_dir=self.download_dir,
                        clave=bitacora_value,
                        since_ts=since_ts_direct,
                        carpeta_estudios=self.carpeta_estudios,
                        carpeta_resolutivos=self.carpeta_resolutivos,
                        carpeta_resumenes=self.carpeta_resumenes,
                    )
                    yield {
                        "status": "complete",
                        "msg": f"Descarga directa (fallback) completada: {len(downloaded_direct)} archivo(s)",
                        "level": "success",
                        "pct": 100,
                        "clave": bitacora_value,
                        "bitacora": bitacora_value,
                        "files": classified,
                        "n_resumenes": len(classified["resumenes"]),
                        "n_estudios": len(classified["estudios"]),
                        "n_resolutivos": len(classified["resolutivos"]),
                        "method": "requests_fallback",
                    }
                    return

            if not buttons_found:
                try:
                    page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
                except Exception:
                    page_text = ""
                if any(kw in page_text for kw in ("no se encontr", "no exist", "not found", "sin result")):
                    yield {
                        "status": "not_found",
                        "msg": f"Clave '{bitacora_value}' no encontrada en el portal SEMARNAT.",
                        "level": "warning",
                    }
                else:
                    yield {
                        "status": "not_found",
                        "msg": f"Timeout (60s): sin botones de descarga para '{bitacora_value}'. Portal lento o clave inexistente.",
                        "level": "warning",
                    }
                return

            # ----------------------------------------------------------------
            # PASO 5: Clic en cada botón de descarga encontrado
            # ----------------------------------------------------------------
            buttons = driver.find_elements(*broad_locator) or driver.find_elements(*locator)
            if not buttons:
                all_btns = driver.find_elements(By.TAG_NAME, "button")
                buttons = [b for b in all_btns if "navbar" not in (b.get_attribute("class") or "") and b.text.strip() not in ("Buscar", "")]

            n_buttons = len(buttons)
            yield {
                "status": "log",
                "msg": f"Encontrados {n_buttons} botones de descarga",
                "level": "info",
                "n_buttons": n_buttons,
            }
            yield {"status": "progress", "msg": "Botones detectados — iniciando descargas", "level": "info", "pct": 60}

            # Extraer clave real de la página para nombrar archivos
            clave = self._extract_clave_from_page(driver) or bitacora_value
            since_ts = time.time()

            for i in range(1, n_buttons + 1):
                pct = 60 + int(25 * i / n_buttons)
                yield {
                    "status": "progress",
                    "msg": f"Descargando documento {i}/{n_buttons}...",
                    "level": "info",
                    "pct": pct,
                }

                # Re-obtener botones para evitar StaleElementReferenceException
                try:
                    fresh_btns = driver.find_elements(*broad_locator) or driver.find_elements(*locator)
                    if not fresh_btns:
                        all_btns = driver.find_elements(By.TAG_NAME, "button")
                        fresh_btns = [b for b in all_btns if "navbar" not in (b.get_attribute("class") or "") and b.text.strip() not in ("Buscar", "")]
                    if i <= len(fresh_btns):
                        btn = fresh_btns[i - 1]
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        time.sleep(0.3)
                        try:
                            btn.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", btn)
                except Exception as btn_exc:
                    yield {"status": "log", "msg": f"Error en botón {i}: {btn_exc}", "level": "warning"}

                # Pausa entre descargas
                time.sleep(random.uniform(2.0, 4.0))

            # ----------------------------------------------------------------
            # PASO 6: Esperar finalización + fallback de network log post-click
            # ----------------------------------------------------------------
            yield {"status": "progress", "msg": "Esperando finalización de descargas...", "level": "info", "pct": 85}

            # Capturar también URLs detectadas tras los clicks (por si Chrome las redirigió)
            post_pdf_urls = extract_pdf_urls_from_network_log(driver)
            if post_pdf_urls:
                selenium_cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
                for pdf_url in post_pdf_urls:
                    raw_name = pdf_url.split("/")[-1].split("?")[0]
                    dest_filename = raw_name if raw_name.endswith(".pdf") else None
                    if dest_filename:
                        dest_path = self.download_dir / dest_filename
                        if not dest_path.exists():
                            download_pdf_via_requests(pdf_url, dest_path, cookies=selenium_cookies)

            new_files = wait_for_downloads(
                self.download_dir,
                since_ts=since_ts,
                expect_at_least=max(1, n_buttons),
                timeout=self.download_timeout,
            )

            # Clasificar y mover
            classified = renombrar_archivos_por_clave(
                download_dir=self.download_dir,
                clave=clave,
                since_ts=since_ts,
                carpeta_estudios=self.carpeta_estudios,
                carpeta_resolutivos=self.carpeta_resolutivos,
                carpeta_resumenes=self.carpeta_resumenes,
            )

            yield {
                "status": "complete",
                "msg": f"Descarga completada: {len(new_files)} archivo(s)",
                "level": "success",
                "pct": 100,
                "clave": clave,
                "bitacora": bitacora_value,
                "files": classified,
                "n_resumenes": len(classified["resumenes"]),
                "n_estudios": len(classified["estudios"]),
                "n_resolutivos": len(classified["resolutivos"]),
                "method": "selenium",
            }

        except Exception as exc:
            logger.exception("Error inesperado descargando %s", bitacora_value)
            yield {
                "status": "error",
                "msg": f"Error: {exc}",
                "level": "error",
            }

    def _extract_clave_from_page(self, driver) -> Optional[str]:
        """Intenta extraer la clave SINAT del DOM de la página."""
        try:
            text = driver.page_source
            match = re.search(r"\b(\d{2}[A-Z]{2}\d{4}[A-Z]\d{4})\b", text)
            if match:
                return match.group(1)
        except Exception:
            pass
        return None

    def batch_desde_lista(
        self,
        claves: list[str],
        pausa_entre: tuple[float, float] = (2.0, 5.0),
        log_csv: Optional[Path] = None,
    ) -> list[dict]:
        """Descarga una lista de bitácoras en batch."""
        results = []
        for i, clave in enumerate(claves):
            logger.info("[%d/%d] Descargando: %s", i + 1, len(claves), clave)
            result = self.descargar_clave(clave)
            result["bitacora_input"] = clave
            results.append(result)
            if i < len(claves) - 1:
                time.sleep(random.uniform(*pausa_entre))

        if log_csv:
            import pandas as pd
            df = pd.DataFrame(results)
            log_csv.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(log_csv, index=False)
            logger.info("Log CSV guardado: %s", log_csv)

        return results

    def batch_desde_csv(
        self,
        csv_path: str | Path,
        columna: str = "clave",
        **kwargs,
    ) -> list[dict]:
        """Descarga bitácoras desde un CSV."""
        import pandas as pd
        df = pd.read_csv(csv_path)
        claves = df[columna].dropna().astype(str).tolist()
        return self.batch_desde_lista(claves, **kwargs)

    def __del__(self):
        self._quit_driver()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._quit_driver()
