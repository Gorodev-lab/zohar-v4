"""
scrapers/base.py
================
Módulo base para scrapers web (SEMARNAT, ASEA, Gacetas).
Proporciona una factoría de Selenium Chrome WebDriver, utilidades robustas para interacción DOM,
descargas HTTP con reintentos y gestión de descargas asíncronas.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("scrapers_base")

TEMP_SUFFIXES = (".part", ".tmp", ".crdownload", ".download", ".~download")


def make_chrome_driver(download_dir: Path, headless: bool = True, timeout: float = 30.0):
    """
    Factoría estandarizada para Chrome Selenium WebDriver.
    Configura descarga silenciosa sin diálogo, desactiva visor de PDF interno y activa CDP logging.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

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
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-component-update")
    opts.add_argument("--disable-features=Translate,OptimizationHints")
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    opts.page_load_strategy = "none"
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

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
    driver.set_page_load_timeout(timeout)

    try:
        driver.execute_cdp_cmd(
            "Browser.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(download_dir.resolve()),
                "eventsEnabled": True,
            },
        )
    except Exception as exc:
        logger.warning("No se pudo configurar Browser.setDownloadBehavior via CDP: %s", exc)
        try:
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(download_dir.resolve())},
            )
        except Exception as exc2:
            logger.warning("Fallback Page.setDownloadBehavior también falló: %s", exc2)

    return driver


def safe_click(driver, locator, timeout: int = 15) -> bool:
    """Clic robusto con scroll automático, retry y fallback a JavaScript."""
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

# Alias para compatibilidad
robust_click = safe_click


def element_exists(driver, locator, timeout: int = 0) -> bool:
    """Verifica la existencia de un elemento en el DOM sin lanzar excepción."""
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
    """Extrae URLs de PDFs desde los registros de performance CDP de Chrome."""
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


def download_file_with_retry(
    url: str,
    dest_path: Path,
    cookies: dict | None = None,
    headers: dict | None = None,
    timeout: int = 120,
    max_retries: int = 3
) -> bool:
    """Descarga un archivo vía HTTP utilizando httpx/requests con reintentos."""
    h = {"User-Agent": "Mozilla/5.0", **(headers or {})}
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, cookies=cookies) as client:
                res = client.get(url, headers=h)
                if res.status_code == 200 and len(res.content) > 100:
                    dest_path.write_bytes(res.content)
                    return True
        except Exception as exc:
            logger.warning("Intento %d/%d falló para descargar %s: %s", attempt + 1, max_retries, url, exc)
            time.sleep(2)
    return False


def wait_for_downloads(
    download_dir: Path,
    since_ts: float,
    expect_at_least: int = 1,
    timeout: int = 600,
    poll: float = 1.0,
) -> list[Path]:
    """
    Espera activa inteligente de dos fases para detectar y aguardar la finalización de descargas PDF.
    """
    deadline = time.time() + timeout
    phase1_deadline = time.time() + 30
    saw_temp = False
    stable_count = 0

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
            stable_count = 0
        else:
            if new_pdfs:
                stable_count += 1
            else:
                stable_count = 0

        if time.time() < phase1_deadline:
            if temps or len(new_pdfs) >= expect_at_least:
                phase1_deadline = 0
            time.sleep(poll)
            continue

        if not temps and len(new_pdfs) >= expect_at_least:
            return new_pdfs

        if not temps and len(new_pdfs) > 0 and stable_count >= 5:
            logger.info("Sin descargas activas por 5s. Retornando %d archivos descargados.", len(new_pdfs))
            return new_pdfs

        if saw_temp and not temps and len(new_pdfs) == 0:
            logger.warning("Descarga cancelada: los archivos temporales desaparecieron sin nuevos PDFs")
            return []

        time.sleep(poll)

    logger.warning("Timeout esperando descargas (%ds)", timeout)
    return [
        f for f in download_dir.iterdir()
        if f.suffix.lower() == ".pdf" and f.stat().st_mtime >= since_ts
    ]


def safe_move(src_path: Path, dst_dir: Path) -> Path:
    """Mueve archivos evitando colisiones añadiendo sufijos _v2, _v3 si ya existen."""
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


class BaseWebScraper:
    """Clase base con ciclo de vida Selenium WebDriver."""
    def __init__(self, download_dir: Path | str, headless: bool = True):
        self.download_dir = Path(download_dir)
        self.headless = headless
        self._driver = None

    def get_driver(self):
        if self._driver is None:
            self._driver = make_chrome_driver(self.download_dir, self.headless)
        return self._driver

    def quit_driver(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def __enter__(self):
        return self.get_driver()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.quit_driver()
