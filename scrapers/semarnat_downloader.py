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
    driver.set_page_load_timeout(30.0)

    # Configurar CDP setDownloadBehavior a nivel de Browser (no de Page/target).
    # Page.setDownloadBehavior solo aplica al tab/target actual: si el portal
    # abre el PDF en una pestaña nueva (target="_blank" o window.open, muy
    # común en portales gubernamentales para "ver/descargar documento"), esa
    # pestaña nueva NO hereda el comportamiento configurado y Chrome termina
    # sin escribir nada a disco, sin lanzar ningún error visible.
    # Browser.setDownloadBehavior aplica globalmente a todos los targets,
    # incluyendo pestañas que se abran después de configurarlo.
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
        # Fallback al comportamiento anterior por si el navegador remoto no
        # soporta el dominio Browser (versiones muy viejas de Chrome/CDP).
        try:
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(download_dir.resolve())},
            )
        except Exception as exc2:
            logger.warning("Fallback Page.setDownloadBehavior también falló: %s", exc2)

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

        # Fase 1: esperando inicio
        if time.time() < phase1_deadline:
            if temps or len(new_pdfs) >= expect_at_least:
                phase1_deadline = 0  # pasar a fase 2
            time.sleep(poll)
            continue

        # Fase 2: esperando finalización
        if not temps and len(new_pdfs) >= expect_at_least:
            return new_pdfs

        # Optimización: si ya no hay descargas activas (.part/.crdownload) por 5 segundos
        # y tenemos al menos 1 PDF descargado, retornamos de inmediato en lugar de esperar el timeout.
        if not temps and len(new_pdfs) > 0 and stable_count >= 5:
            logger.info("No active downloads (.part/.crdownload) for 5s. Returning %d downloaded files.", len(new_pdfs))
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


def extract_initial_pages_text(pdf_path: Path, max_pages: int = 2) -> str:
    """Extrae el texto de las primeras 1 o 2 páginas del PDF de forma perezosa."""
    from core.pdf_processor import iter_pages_as_markdown
    text_chunks = []
    try:
        for page_num, total_pages, md_text, is_scanned in iter_pages_as_markdown(pdf_path):
            text_chunks.append(md_text)
            if len(text_chunks) >= max_pages:
                break
            # Si la primera página es bastante larga y representativa, paramos
            if page_num == 1 and len(md_text.strip()) > 300:
                break
    except Exception as exc:
        logger.warning(f"Error extrayendo páginas iniciales de {pdf_path.name}: {exc}")
    return "\n\n".join(text_chunks)


def clasificar_pdf_con_llm(pdf_path: Path, intento: int = 1, razonamiento_previo: str = "") -> Optional[str]:
    """
    Usa el modelo local Gemma para clasificar el PDF con Self-Recursive Improvement.
    Si el modelo tiene baja certeza o falla, se pide a sí mismo reevaluar su lógica.
    """
    try:
        from core.llm_client import detect_active_backend, generate_completion
        
        provider, _ = detect_active_backend()
        if provider in ("heuristic", "fallback_heuristic"):
            logger.warning("No hay LLM local activo para clasificar PDF. Usando fallback.")
            return None

        # Si estamos en un intento recursivo, podríamos extraer más texto, pero
        # por ahora usaremos el mismo texto base con una inyección de contexto.
        text = extract_initial_pages_text(pdf_path, max_pages=3 if intento > 1 else 2)
        
        if len(text.strip()) < 50:
            logger.warning(f"Texto insuficiente en {pdf_path.name} para clasificar con LLM.")
            return None

        sys_prompt = """
        Eres un asistente experto en trámites ambientales de SEMARNAT en México.
        Tu tarea es clasificar el siguiente documento PDF a partir del texto extraído.
        Debes responder ÚNICAMENTE en JSON con esta estructura exacta:
        {
          "tipo": "estudio" | "resumen" | "resolutivo" | "desconocido",
          "certeza": "alta" | "media" | "baja",
          "razon": "Breve explicación de por qué pertenece a esta clase"
        }
        Guía de clasificación:
        - "estudio": Contiene el texto técnico de la Manifestación de Impacto Ambiental (MIA), Estudio de Riesgo, etc. Suele comenzar con índices largos, capítulos, descripciones técnicas del proyecto, justificación, etc.
        - "resumen": Dice explícitamente "RESUMEN EJECUTIVO" o "RESUMEN DEL PROYECTO". Es más corto y simplificado.
        - "resolutivo": Es un oficio oficial firmado por delegados de SEMARNAT. Contiene la palabra "RESOLUCIÓN", "RESOLUTIVO", "OFICIO NÚMERO", "SE RESUELVE", etc.
        """

        prompt = f"Texto del documento:\n{text[:4000]}"
        
        # Inyección de auto-mejora si es una llamada recursiva
        if intento > 1 and razonamiento_previo:
            prompt = f"ATENCIÓN: En tu intento anterior clasificaste esto como 'desconocido' o tuviste certeza 'baja' por esta razón: '{razonamiento_previo}'. Reevalúa el documento buscando pistas sutiles en este texto ampliado.\n\n" + prompt

        res = generate_completion(
            prompt=prompt,
            system_prompt=sys_prompt,
            response_json=True
        )

        if not res.get("is_fallback") and "tipo" in res:
            tipo = res["tipo"].strip().lower()
            certeza = res.get("certeza", "alta").strip().lower()
            razon = res.get("razon", "")
            
            logger.info(f"Intento {intento} LLM: {tipo} (Certeza: {certeza}) - {pdf_path.name}")

            # Lógica de Self-Recursive Improvement
            if (tipo == "desconocido" or certeza == "baja") and intento < 2:
                logger.info(f"Activando Self-Recursive Improvement para {pdf_path.name}...")
                return clasificar_pdf_con_llm(pdf_path, intento=intento+1, razonamiento_previo=razon)
            
            if tipo in ("estudio", "resumen", "resolutivo"):
                return tipo

    except Exception as exc:
        logger.warning(f"Error clasificando {pdf_path.name} con LLM: {exc}")
    
    return None 


def extract_metadata_from_dom(driver) -> dict:
    """
    Intenta extraer metadatos estructurados directamente del DOM de la página de resultados.
    """
    from selenium.webdriver.common.by import By
    import re
    
    metadata = {}
    try:
        # Extraer todo el texto del body
        body_text = driver.find_element(By.TAG_NAME, "body").text
        
        # 1. Búsqueda por expresiones regulares en el texto de la página
        promovente_match = re.search(r"(?:Promovente|Interesado|Empresa|Solicitante)\s*:\s*([^\n]+)", body_text, re.IGNORECASE)
        if promovente_match:
            metadata["promovente"] = promovente_match.group(1).strip()
            
        project_match = re.search(r"(?:Nombre del Proyecto|Proyecto|Trámite)\s*:\s*([^\n]+)", body_text, re.IGNORECASE)
        if project_match:
            metadata["project_name"] = project_match.group(1).strip()
            
        sector_match = re.search(r"(?:Sector|Actividad)\s*:\s*([^\n]+)", body_text, re.IGNORECASE)
        if sector_match:
            metadata["sector"] = sector_match.group(1).strip()
            
        state_match = re.search(r"(?:Estado|Entidad Federativa)\s*:\s*([^\n]+)", body_text, re.IGNORECASE)
        if state_match:
            metadata["state"] = state_match.group(1).strip()
            
        muni_match = re.search(r"(?:Municipio|Ubicación|Localidad)\s*:\s*([^\n]+)", body_text, re.IGNORECASE)
        if muni_match:
            metadata["municipio"] = muni_match.group(1).strip()
            
        fecha_match = re.search(r"(?:Fecha de Ingreso|Ingresado el|Fecha)\s*:\s*([^\n]+)", body_text, re.IGNORECASE)
        if fecha_match:
            metadata["fecha_ingreso"] = fecha_match.group(1).strip()
            
        estatus_match = re.search(r"(?:Estatus|Etapa|Estado actual)\s*:\s*([^\n]+)", body_text, re.IGNORECASE)
        if estatus_match:
            metadata["status"] = estatus_match.group(1).strip()

        # 2. Análisis estructural de tablas del DOM para precisión exacta
        tables = driver.find_elements(By.TAG_NAME, "table")
        for table in tables:
            rows = table.find_elements(By.TAG_NAME, "tr")
            for row in rows:
                cols = [c.text.strip() for c in row.find_elements(By.TAG_NAME, "td")]
                if len(cols) == 2:
                    k, v = cols[0].lower(), cols[1]
                    if "promovente" in k or "interesado" in k:
                        metadata["promovente"] = v
                    elif "proyecto" in k or "nombre del trámite" in k:
                        metadata["project_name"] = v
                    elif "sector" in k:
                        metadata["sector"] = v
                    elif "estado" in k or "entidad" in k:
                        metadata["state"] = v
                    elif "municipio" in k or "delegación" in k:
                        metadata["municipio"] = v
                    elif "fecha" in k:
                        metadata["fecha_ingreso"] = v
                    elif "estatus" in k or "situación" in k:
                        metadata["status"] = v
                        
    except Exception as e:
        logger.warning("Error al extraer metadatos del DOM: %s", e)
        
    return metadata


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

    Regla de fallback posicional cuando el nombre no contiene keywords y el LLM falla:
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
        
        # Prioridad 2: Gemma LLM clasificador
        if tipo is None:
            tipo = clasificar_pdf_con_llm(pdf)
            if tipo:
                logger.info("PDF %s clasificado por Gemma como: %s", pdf.name, tipo)
            else:
                logger.info("Gemma no pudo clasificar %s. Usando fallback posicional.", pdf.name)

        # Prioridad 3: posición
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

    def _descargar_clave_gen_with_retry(
        self, clave: str, max_retries: int = 2
    ) -> Generator[dict, None, None]:
        """
        Wrapper de reintentos sobre _descargar_clave_gen.

        - Reintenta hasta max_retries veces adicionales (3 intentos total).
        - No reintenta si el último evento fue 'complete' o 'not_found' (definitivos).
        - Entre reintentos: emite evento SSE 'retry', destruye el driver y espera 5s.
        """
        for attempt in range(1 + max_retries):
            last_event: dict = {}
            for event in self._descargar_clave_gen(clave):
                last_event = event
                yield event

            terminal_status = last_event.get("status", "")

            # Exito o 404 definitivo: no reintentar
            if terminal_status in ("complete", "not_found"):
                return

            # Si quedan intentos, emitir aviso y reintentar
            if attempt < max_retries:
                retry_n = attempt + 1
                logger.warning(
                    "Descarga fallida para %s (intento %d/%d). Reintentando en 5s...",
                    clave, retry_n, max_retries
                )
                yield {
                    "status": "retry",
                    "attempt": retry_n,
                    "max_retries": max_retries,
                    "msg": f"Reintentando ({retry_n}/{max_retries})...",
                    "level": "warning",
                }
                self._quit_driver()
                time.sleep(5)

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
            # EXTRACCIÓN DE METADATOS DEL DOM
            # ----------------------------------------------------------------
            metadata = extract_metadata_from_dom(driver)
            if metadata:
                yield {
                    "status": "log",
                    "msg": f"Metadatos extraídos del DOM: Promovente='{metadata.get('promovente', 'Desconocido')}', Proyecto='{metadata.get('project_name', 'Desconocido')}'",
                    "level": "info",
                    "metadata": metadata
                }
            else:
                metadata = {}

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
                        "metadata": metadata,
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
	
	total_descargados = len(new_files)
        descargas_perdidas = max(0, n_buttons - total_descargados)

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
            "archivos_esperados": n_buttons,
            "descargas_perdidas": descargas_perdidas,
            "alerta_integridad": "Faltan documentos" if descargas_perdidas > 0 else "Completo",
            "method": "selenium",
            "metadata": metadata
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
