"""
scrapers/asea_scraper.py
Descargador de Gacetas ASEA (sin Selenium — solo requests + BS4).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Generator, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ASEA_INDEX_URL = "http://transparencia.asea.gob.mx/Gaceta_ASEA"
ASEA_USER_AGENT = "Mozilla/5.0 (compatible; ZoharBot/4.0)"


class ASEAScraper:
    """
    Scraper de Gacetas ASEA (Agencia de Seguridad, Energía y Ambiente).
    No requiere Selenium — usa requests + BeautifulSoup4.
    """

    def __init__(
        self,
        output_dir: str | Path = "downloads/asea",
        year_filter: Optional[int] = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.year_filter = year_filter

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({"User-Agent": ASEA_USER_AGENT})
        return s

    def listar_gacetas(self) -> list[dict]:
        """
        Retorna lista de gacetas disponibles:
        [{"url": str, "year": int|None, "filename": str, "label": str}, ...]
        """
        session = self._session()
        try:
            resp = session.get(ASEA_INDEX_URL, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("Error obteniendo índice ASEA: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        gacetas = []

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if ".pdf" not in href.lower():
                continue

            # Nombre de archivo local con prefijo ASEA_
            filename_raw = Path(href).name
            filename = f"ASEA_{filename_raw}"

            # Intentar extraer año del label o URL
            label = a.get_text(strip=True)
            year = self._extract_year(label) or self._extract_year(href)

            # Construir URL absoluta
            if href.startswith("http"):
                url = href
            else:
                url = f"{ASEA_INDEX_URL.rstrip('/')}/{href.lstrip('/')}"

            # Filtrar por año si se especificó
            if self.year_filter is not None and year != self.year_filter:
                continue

            gacetas.append({
                "url": url,
                "year": year,
                "filename": filename,
                "label": label,
            })

        return gacetas

    @staticmethod
    def _extract_year(text: str) -> Optional[int]:
        """Extrae el año (20xx) de una cadena de texto."""
        import re
        match = re.search(r"20\d{2}", text)
        if match:
            return int(match.group(0))
        return None

    def descargar_gacetas_gen(self) -> Generator[dict, None, None]:
        """
        Generador SSE de descarga de gacetas ASEA.
        Emite {"status": "progress"|"complete"|"error", "msg": str, ...}
        """
        yield {"status": "progress", "msg": "Listando gacetas ASEA...", "pct": 5}

        gacetas = self.listar_gacetas()
        yield {
            "status": "progress",
            "msg": f"Encontradas {len(gacetas)} gacetas",
            "pct": 15,
            "n_gacetas": len(gacetas),
        }

        session = self._session()
        downloaded = []

        for i, g in enumerate(gacetas):
            pct = 15 + int(80 * (i + 1) / max(len(gacetas), 1))
            dest = self.output_dir / g["filename"]

            if dest.exists():
                downloaded.append(dest)
                yield {"status": "progress", "msg": f"Ya existe: {g['filename']}", "pct": pct}
                continue

            try:
                resp = session.get(g["url"], timeout=120, stream=True)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(65536):
                        f.write(chunk)
                downloaded.append(dest)
                yield {
                    "status": "progress",
                    "msg": f"Descargado: {g['filename']}",
                    "pct": pct,
                    "year": g["year"],
                }
            except Exception as exc:
                yield {
                    "status": "progress",
                    "msg": f"Error: {g['filename']}: {exc}",
                    "pct": pct,
                    "level": "warning",
                }

        yield {
            "status": "complete",
            "msg": f"ASEA: {len(downloaded)} gacetas descargadas",
            "pct": 100,
            "files": [str(p) for p in downloaded],
        }

    def descargar_gacetas(self) -> list[Path]:
        """Descarga todas las gacetas. Wrapper síncrono."""
        downloaded = []
        for event in self.descargar_gacetas_gen():
            if event.get("status") == "complete":
                downloaded = [Path(p) for p in event.get("files", [])]
        return downloaded
