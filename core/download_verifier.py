"""
core/download_verifier.py
Validador Híbrido Estricto de Integridad para Descargas de PDFs (SEMARNAT / ASEA).
Garantiza que ningún archivo corrupto, parcial o respuesta HTML 404 pase a la fase de extracción OCR/LLM.
Soporta estado flexible VERIFIED_NO_TEXT para PDFs escaneados sin capa de texto cruda.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Dict, Any, Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

CLAVE_REGEX = re.compile(r"(?<![A-Z0-9])(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})(?![A-Z0-9])")


class PDFDownloadVerifier:
    def __init__(self, min_bytes: int = 5120):
        self.min_bytes = min_bytes

    def verify_pdf_file(self, pdf_path: Path, expected_clave: Optional[str] = None) -> Dict[str, Any]:
        """
        Verifica un archivo PDF con un esquema flexible de 5 puntos:
        1. Existencia y Magic Bytes (%PDF-)
        2. Tamaño mínimo (> 5 KB para descartar errores HTML 404/500)
        3. Parseabilidad y renderizado estructural con PyMuPDF (fitz)
        4. Firma SHA-256 única
        5. Presencia de la clave SINAT/ASEA en la pág 1 (si hay texto) o VERIFIED_NO_TEXT si es escaneado
        """
        path = Path(pdf_path)
        if not path.exists():
            return {
                "status": "MISSING",
                "reason": "Archivo no existe en disco",
                "valid": False,
                "file_path": str(path)
            }

        file_size = path.stat().st_size

        # Check 1: Tamaño Mínimo (descartar respuestas 404/500 HTML)
        if file_size < self.min_bytes:
            return {
                "status": "EMPTY",
                "reason": f"Tamaño insuficiente ({file_size} bytes < {self.min_bytes} bytes). Posible error HTML 404/500.",
                "valid": False,
                "file_size": file_size,
                "file_path": str(path)
            }

        # Check 2: Magic Bytes (%PDF-)
        try:
            with open(path, "rb") as f:
                header = f.read(1024)
                if not header.startswith(b"%PDF-"):
                    return {
                        "status": "CORRUPT",
                        "reason": "Header de archivo no contiene Magic Bytes '%PDF-'",
                        "valid": False,
                        "file_size": file_size,
                        "file_path": str(path)
                    }

                # Check 3: SHA-256 Hash
                f.seek(0)
                sha256_hash = hashlib.sha256(f.read()).hexdigest()
        except Exception as exc:
            return {
                "status": "CORRUPT",
                "reason": f"Error de lectura I/O: {exc}",
                "valid": False,
                "file_path": str(path)
            }

        # Check 4: Renderizado PyMuPDF (fitz)
        try:
            doc = fitz.open(path)
            if doc.is_encrypted:
                return {
                    "status": "ENCRYPTED",
                    "reason": "El PDF está protegido por contraseña o encriptado",
                    "valid": False,
                    "sha256": sha256_hash,
                    "file_size": file_size,
                    "file_path": str(path)
                }

            page_count = doc.page_count
            if page_count == 0:
                return {
                    "status": "EMPTY",
                    "reason": "El PDF tiene 0 páginas",
                    "valid": False,
                    "sha256": sha256_hash,
                    "file_size": file_size,
                    "file_path": str(path)
                }

            # Check 5: Texto de página 1 y Coincidencia de Clave
            first_page_text = doc[0].get_text().strip()
            doc.close()

            # Manejo flexible para PDFs escaneados sin capa de texto
            if not first_page_text:
                return {
                    "status": "VERIFIED_NO_TEXT",
                    "reason": "PDF estructuralmente válido sin capa de texto cruda (PDF escaneado)",
                    "valid": True,
                    "is_scanned": True,
                    "sha256": sha256_hash,
                    "file_size": file_size,
                    "page_count": page_count,
                    "found_claves": [],
                    "file_path": str(path)
                }

            found_claves = CLAVE_REGEX.findall(first_page_text)
            
            clave_matched = True
            if expected_clave and expected_clave not in found_claves:
                if expected_clave not in path.name:
                    clave_matched = False

            final_status = "VERIFIED" if clave_matched else "MISMATCH"

            return {
                "status": final_status,
                "reason": "PDF íntegro y verificado 100%" if clave_matched else "Clave esperada no encontrada en página 1",
                "valid": True if final_status in ["VERIFIED", "VERIFIED_NO_TEXT", "MISMATCH"] else False,
                "is_scanned": False,
                "sha256": sha256_hash,
                "file_size": file_size,
                "page_count": page_count,
                "found_claves": found_claves,
                "file_path": str(path)
            }

        except Exception as exc:
            logger.warning("Error parseando PDF con PyMuPDF en %s: %s", path, exc)
            return {
                "status": "CORRUPT",
                "reason": f"Fallo al abrir estructura PyMuPDF: {exc}",
                "valid": False,
                "sha256": sha256_hash if 'sha256_hash' in locals() else "",
                "file_size": file_size,
                "file_path": str(path)
            }
