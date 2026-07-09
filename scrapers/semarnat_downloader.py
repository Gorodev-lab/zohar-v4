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
SEMARNAT_URL = (
    "https://app.semarnat.gob.mx/consulta-tramite/"
    "#/portal-consulta/consulta-tramite/"
)

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
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        yield {"status": "log", "msg": f"Iniciando descarga: {bitacora_value}", "level": "info"}

        driver = self._get_driver()

        try:
            # Navegar al portal
            url = SEMARNAT_URL + bitacora_value
            driver.get(url)
            yield {"status": "log", "msg": f"Navegando a: {url}", "level": "info"}
            yield {"status": "progress", "msg": "Cargando portal...", "level": "info", "pct": 10}

            # Esperar que cargue la página (hasta 20s)
            time.sleep(3)
            WebDriverWait(driver, 20).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )

            # Detectar botones de descarga
            locator = (By.CSS_SELECTOR, BUTTON_CSS_SELECTOR)
            if not element_exists(driver, locator, timeout=8):
                yield {"status": "log", "msg": "Botones de descarga no encontrados de inmediato. Intentando buscar mediante el formulario...", "level": "info"}
                
                # Intentar localizar el input de búsqueda
                input_found = False
                search_input = None
                for selector in [
                    (By.CSS_SELECTOR, "input[placeholder*='bitácora']"),
                    (By.CSS_SELECTOR, "input[placeholder*='clave']"),
                    (By.XPATH, "//input[contains(@placeholder, 'proyecto') or contains(@placeholder, 'bitácora') or contains(@placeholder, 'clave')]"),
                    (By.CSS_SELECTOR, "input[type='text']"),
                    (By.XPATH, "//input")
                ]:
                    try:
                        if element_exists(driver, selector, timeout=2):
                            search_input = driver.find_element(*selector)
                            input_found = True
                            break
                    except Exception:
                        pass
                
                if input_found and search_input:
                    try:
                        search_input.clear()
                        time.sleep(0.3)
                        search_input.send_keys(bitacora_value)
                        yield {"status": "log", "msg": f"Clave ingresada en el buscador: {bitacora_value}", "level": "info"}
                        
                        # Localizar y hacer clic en el botón de búsqueda
                        btn_found = False
                        search_btn = None
                        for btn_selector in [
                            (By.XPATH, "//button[contains(., 'Buscar')]"),
                            (By.XPATH, "//button[contains(text(), 'Buscar')]"),
                            (By.CSS_SELECTOR, "button.btn-primary"),
                            (By.CSS_SELECTOR, "button"),
                            (By.XPATH, "//*[contains(text(), 'Buscar')]")
                        ]:
                            try:
                                if element_exists(driver, btn_selector, timeout=2):
                                    search_btn = driver.find_element(*btn_selector)
                                    btn_found = True
                                    break
                            except Exception:
                                pass
                        
                        if btn_found and search_btn:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", search_btn)
                            time.sleep(0.3)
                            search_btn.click()
                            yield {"status": "log", "msg": "Botón de búsqueda cliqueado. Esperando resultados...", "level": "info"}
                            
                            # Esperar a que los botones de descarga aparezcan
                            time.sleep(5)
                            if element_exists(driver, locator, timeout=12):
                                yield {"status": "log", "msg": "Botones de descarga localizados con éxito tras búsqueda.", "level": "info"}
                            else:
                                yield {
                                    "status": "not_found",
                                    "msg": f"Clave no encontrada en SINAT tras búsqueda: {bitacora_value}",
                                    "level": "warning",
                                }
                                return
                        else:
                            yield {"status": "log", "msg": "No se pudo encontrar el botón de búsqueda.", "level": "warning"}
                            yield {
                                "status": "not_found",
                                "msg": f"Clave no encontrada en SINAT: {bitacora_value}",
                                "level": "warning",
                            }
                            return
                    except Exception as e:
                        yield {"status": "log", "msg": f"Error interactuando con el formulario de búsqueda: {e}", "level": "warning"}
                        yield {
                            "status": "not_found",
                            "msg": f"Clave no encontrada en SINAT: {bitacora_value}",
                            "level": "warning",
                        }
                        return
                else:
                    yield {
                        "status": "not_found",
                        "msg": f"Clave no encontrada en SINAT y formulario de búsqueda inaccesible: {bitacora_value}",
                        "level": "warning",
                    }
                    return


            buttons = driver.find_elements(*locator)
            n_buttons = len(buttons)
            yield {
                "status": "log",
                "msg": f"Encontrados {n_buttons} botones de descarga",
                "level": "info",
                "n_buttons": n_buttons,
            }
            yield {"status": "progress", "msg": "Botones detectados", "level": "info", "pct": 20}

            # Extraer clave del bitácora para renombrar
            clave = self._extract_clave_from_page(driver) or bitacora_value

            since_ts = time.time()

            for i in range(1, n_buttons + 1):
                pct = 20 + int(60 * i / n_buttons)
                yield {
                    "status": "progress",
                    "msg": f"Descargando documento {i}/{n_buttons}...",
                    "level": "info",
                    "pct": pct,
                }

                # Click al botón n-ésimo via XPath
                xpath = BUTTON_XPATH_TEMPLATE.format(n=i)
                btn_locator = (By.XPATH, xpath)
                safe_click(driver, btn_locator)

                # Espera activa entre botones
                time.sleep(random.uniform(2.0, 4.0))

            # Esperar que terminen todas las descargas
            yield {"status": "progress", "msg": "Esperando finalización...", "level": "info", "pct": 85}
            new_files = wait_for_downloads(
                self.download_dir,
                since_ts=since_ts,
                expect_at_least=n_buttons,
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
