"""
scrapers/gazette_scraper.py
Descargador de Gacetas SINAT/SEMARNAT.
Usa Selenium + BeautifulSoup4 para navegación con iframe autenticado.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Generator, Optional

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Regex de clave SEMARNAT válida: ej. 23QR2024TD085, 05CO2026I0001
_CLAVE_RE = re.compile(r"(?<![A-Z0-9])(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})(?![A-Z0-9])")


class GazetteScraper:
    """
    Scraper de Gacetas Ecológicas publicadas en el portal SINAT.
    Navega el iframe con Selenium y descarga PDFs via requests.
    Detecta automáticamente si el contenido está en un iframe separado.
    """

    IFRAME_URL = "https://sinat.semarnat.gob.mx:8443/Gaceta/gacetapublicacion/?ai={year}"

    def __init__(
        self,
        output_dir: str | Path = "downloads/gacetas",
        headless: bool = True,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.supabase_url = supabase_url or os.environ.get("SUPABASE_URL", "")
        self.supabase_key = supabase_key or os.environ.get("SUPABASE_KEY", "")

    def get_iframe_url(self, year: int) -> str:
        """Genera URL del iframe para el año especificado."""
        return self.IFRAME_URL.format(year=year)

    def _get_driver(self):
        from scrapers.base import make_chrome_driver
        return make_chrome_driver(self.output_dir, self.headless)

    def _parse_pdf_links(self, html: str, base_url: str) -> list[dict]:
        """Extrae enlaces PDF válidos del HTML del iframe."""
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin

        soup = BeautifulSoup(html, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if ".pdf" in href.lower() and (
                "gaceta" in href.lower() or "archivos" in href.lower()
            ):
                full_url = urljoin(base_url, href)
                filename = Path(href).name
                links.append({"url": full_url, "filename": filename, "label": a.get_text(strip=True)})

        # También buscar atributos data-href y data-url
        for el in soup.find_all(attrs={"data-href": True}):
            href = el["data-href"]
            if ".pdf" in href.lower():
                full_url = urljoin(base_url, href)
                links.append({"url": full_url, "filename": Path(href).name, "label": el.get_text(strip=True)})

        return links

    def _get_page_html_with_iframe_detection(self, driver, base_url: str, timeout: int = 30) -> tuple[str, str]:
        """
        Detecta si el contenido PDF está en el body principal o en un iframe.
        Retorna (html_con_links, base_url_efectiva).

        Estrategia:
        1. Espera activa con polling hasta encontrar links PDF en el body.
        2. Si no aparecen, busca iframes y navega a cada uno.
        3. Retorna el primer HTML que contenga links PDF.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        def _has_pdf_link(d):
            """Condición de polling: aparece algún link .pdf en el DOM actual."""
            src = d.page_source
            return ".pdf" in src.lower()

        # Fase 1: esperar que aparezcan links en el body
        try:
            WebDriverWait(driver, timeout).until(_has_pdf_link)
        except Exception:
            pass  # Continuar aunque el timeout expire; intentaremos iframes

        # Comprobar body principal primero
        html = driver.page_source
        if ".pdf" in html.lower():
            return html, base_url

        # Fase 2: buscar y navegar iframes
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        logger.debug("Buscando PDF links en %d iframe(s)", len(iframes))

        for idx, iframe in enumerate(iframes):
            try:
                driver.switch_to.frame(iframe)
                # Dar tiempo al iframe para cargar su contenido
                try:
                    WebDriverWait(driver, 10).until(_has_pdf_link)
                except Exception:
                    pass

                iframe_html = driver.page_source
                iframe_src = iframe.get_attribute("src") or base_url
                driver.switch_to.default_content()

                if ".pdf" in iframe_html.lower():
                    logger.debug("Links PDF encontrados en iframe %d (src=%s)", idx, iframe_src)
                    return iframe_html, iframe_src
            except Exception as exc:
                logger.debug("Error navegando iframe %d: %s", idx, exc)
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass

        # Fallback: devolver el body principal aunque no tenga links
        return driver.page_source, base_url

    def _extract_clave_from_filename(self, filename: str) -> Optional[str]:
        """Intenta extraer clave SEMARNAT válida desde el nombre del archivo."""
        match = _CLAVE_RE.search(filename.upper())
        return match.group(1) if match else None

    def descargar_gacetas_ano(self, year: int) -> list[Path]:
        """Descarga todas las gacetas de un año. Retorna lista de PDFs."""
        downloaded = []
        for event in self._descargar_gacetas_ano_gen(year):
            if event.get("status") == "complete":
                downloaded.extend(event.get("files", []))
        return downloaded

    def _descargar_gacetas_ano_gen(self, year: int) -> Generator[dict, None, None]:
        """
        Generador SSE para descarga de gacetas.
        Emite {"status": "progress"|"complete"|"error"|"warning", "msg": str, ...}
        Usa WebDriverWait + detección automática de iframe.
        """
        iframe_url = self.get_iframe_url(year)
        yield {"status": "progress", "msg": f"Cargando gacetas {year}...", "pct": 5}

        driver = None
        downloaded_files = []
        try:
            driver = self._get_driver()
            driver.get(iframe_url)

            # Detección automática iframe/body con polling activo
            yield {"status": "progress", "msg": "Esperando contenido del portal...", "pct": 10}
            html, effective_base = self._get_page_html_with_iframe_detection(
                driver, iframe_url, timeout=30
            )

            # Obtener cookies de sesión para requests
            cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
            links = self._parse_pdf_links(html, effective_base)

            if not links:
                yield {
                    "status": "warning",
                    "msg": f"No se encontraron links PDF para {year}. El portal puede estar caído o requiere autenticación.",
                    "pct": 20,
                    "n_links": 0,
                }
            else:
                yield {
                    "status": "progress",
                    "msg": f"Encontrados {len(links)} PDFs para {year}",
                    "pct": 20,
                    "n_links": len(links),
                }

            for i, link in enumerate(links):
                pct = 20 + int(75 * (i + 1) / max(len(links), 1))
                dest = self.output_dir / link["filename"]

                if dest.exists():
                    yield {
                        "status": "progress",
                        "msg": f"Ya existe: {link['filename']}",
                        "pct": pct,
                    }
                    downloaded_files.append(dest)
                    continue

                try:
                    resp = requests.get(
                        link["url"],
                        cookies=cookies,
                        timeout=120,
                        headers={"User-Agent": "Mozilla/5.0"},
                        stream=True,
                    )
                    resp.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_content(65536):
                            f.write(chunk)
                    downloaded_files.append(dest)

                    # Registrar en Supabase si está configurado
                    self.registrar_gaceta_en_supabase(year, link["filename"], link["url"])

                    yield {
                        "status": "progress",
                        "msg": f"Descargado: {link['filename']}",
                        "pct": pct,
                    }
                except Exception as exc:
                    yield {
                        "status": "progress",
                        "msg": f"Error descargando {link['filename']}: {exc}",
                        "pct": pct,
                        "level": "warning",
                    }

            yield {
                "status": "complete",
                "msg": f"Gacetas {year}: {len(downloaded_files)} descargadas",
                "pct": 100,
                "files": downloaded_files,
                "year": year,
            }

        except Exception as exc:
            logger.exception("Error en GazetteScraper para año %s", year)
            yield {"status": "error", "msg": str(exc)}
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def registrar_gaceta_en_supabase(self, year: int, filename: str, url: str):
        """Registra la gaceta descargada en Supabase (si está configurado)."""
        if not self.supabase_url or not self.supabase_key:
            return
        try:
            headers = {
                "apikey": self.supabase_key,
                "Authorization": f"Bearer {self.supabase_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            }
            payload = {
                "year": year,
                "filename": filename,
                "url": url,
                "source": "sinat",
            }
            requests.post(
                f"{self.supabase_url}/rest/v1/gacetas_eco",
                json=payload,
                headers=headers,
                timeout=10,
            )
        except Exception as exc:
            logger.debug("Supabase registro falló (no crítico): %s", exc)
