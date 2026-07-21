"""
core/classifier.py
Clasificador heurístico determinístico (0% consumo LLM) para claves SINAT,
gacetas ASEA y archivos PDF/MD del ecosistema Zohar v4.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Any, Union, Optional

# Expresión regular anclada para claves SINAT (e.g. 21PU2025H0155, 10DU2026X0015, 03BS2026H0015)
SINAT_KEY_RE = re.compile(
    r"^(?P<sector>\d{2})"
    r"(?P<estado>[A-Z]{2})"
    r"(?P<year>\d{4})"
    r"(?P<tipo>[A-Z])"
    r"(?P<seq>\d{3,5})$",
    re.IGNORECASE,
)

# Expresión regular para gacetas ASEA (e.g. ASEA_GACETA_01-2026, gaceta_ASEA_2025)
ASEA_GACETA_RE = re.compile(
    r"ASEA.*GACETA.*?(?P<num>\d+)[-_](?P<year>\d{4})|"
    r"GACETA.*ASEA.*?(?P<num2>\d+)[-_](?P<year2>\d{4})",
    re.IGNORECASE,
)

ESTADO_NOMBRES: Dict[str, str] = {
    "AG": "Aguascalientes", "BC": "Baja California", "BS": "Baja California Sur",
    "CM": "Campeche", "CS": "Chiapas", "CH": "Chihuahua", "CO": "Coahuila",
    "CL": "Colima", "DF": "Ciudad de México", "DG": "Durango", "GT": "Guanajuato",
    "GR": "Guerrero", "HG": "Hidalgo", "JL": "Jalisco", "ME": "Estado de México",
    "MI": "Michoacán", "MO": "Morelos", "NA": "Nayarit", "NL": "Nuevo León",
    "OA": "Oaxaca", "PU": "Puebla", "QT": "Querétaro", "QR": "Quintana Roo",
    "SL": "San Luis Potosí", "SI": "Sinaloa", "SO": "Sonora", "TB": "Tabasco",
    "TM": "Tamaulipas", "TL": "Tlaxcala", "VE": "Veracruz", "YU": "Yucatán",
    "ZA": "Zacatecas", "MG": "Nacional/Marino", "MP": "Múltiple",
}

TIPO_MIA: Dict[str, str] = {
    "H": "MIA Particular",
    "G": "MIA Regional",
    "I": "Informe Preventivo",
    "T": "Cambio de Uso de Suelo",
    "D": "Estudio de Riesgo",
    "X": "Trámite Sector Hidrocarburos (X)",
}

SECTOR_MAP: Dict[str, str] = {
    "01": "Vías de Comunicación",
    "02": "Hidráulico",
    "03": "Oleoductos / Gasoductos / Minería",
    "04": "Eléctrico",
    "05": "Industria Química / Petroquímica",
    "06": "Siderúrgica / Metalúrgica",
    "07": "Cementera / Calera",
    "08": "Automotriz",
    "09": "Turístico",
    "10": "Inmobiliario / Desarrollo Urbano",
    "11": "Manejo de Residuos Peligrosos",
    "12": "Forestal / Cambio de Uso de Suelo",
    "14": "Obras Marítimas / Puertos",
    "21": "Sector Hidrocarburos",
    "28": "Sector Gas natural y Gas LP",
    "30": "Exploración y Extracción",
}

class DocumentClassifier:
    """Clasificador heurístico instantáneo sin dependencias externas."""

    @staticmethod
    def classify(input_item: Union[str, Path]) -> Dict[str, Any]:
        path_obj = Path(input_item) if isinstance(input_item, str) else input_item
        filename = path_obj.name
        stem = path_obj.stem.split(".")[0].upper()

        # Determinar Origen
        is_asea = "ASEA" in filename.upper() or "ASEA" in str(path_obj).upper()
        source = "ASEA" if is_asea else "SEMARNAT"

        # Determinar Categoría de Documento
        fn_lower = filename.lower()
        if "estudio" in fn_lower or "mia" in fn_lower:
            doc_category = "estudio"
        elif "resumen" in fn_lower:
            doc_category = "resumen"
        elif "resolutivo" in fn_lower or "resolucion" in fn_lower:
            doc_category = "resolutivo"
        elif "gaceta" in fn_lower:
            doc_category = "gaceta"
        else:
            doc_category = "desconocido"

        # Coincidencia SINAT
        m_sinat = SINAT_KEY_RE.match(stem)
        if m_sinat:
            groups = m_sinat.groupdict()
            sec_code = groups["sector"].zfill(2)
            est_code = groups["estado"].upper()
            tipo_code = groups["tipo"].upper()
            year = int(groups["year"])

            return {
                "input": str(input_item),
                "is_valid_sinat": True,
                "clave": stem,
                "source": source,
                "sector_code": sec_code,
                "sector_name": SECTOR_MAP.get(sec_code, f"Sector {sec_code}"),
                "estado_code": est_code,
                "estado_name": ESTADO_NOMBRES.get(est_code, est_code),
                "year": year,
                "tipo_code": tipo_code,
                "tipo_name": TIPO_MIA.get(tipo_code, f"Tipo {tipo_code}"),
                "sequence": groups["seq"],
                "doc_category": doc_category,
            }

        # Coincidencia Gaceta ASEA
        m_asea = ASEA_GACETA_RE.search(filename)
        if m_asea:
            num = m_asea.group("num") or m_asea.group("num2")
            year = m_asea.group("year") or m_asea.group("year2")
            return {
                "input": str(input_item),
                "is_valid_sinat": False,
                "clave": f"ASEA-GACETA-{num.zfill(2)}-{year}" if num and year else stem,
                "source": "ASEA",
                "sector_code": "21",
                "sector_name": "Sector Hidrocarburos",
                "estado_code": "MG",
                "estado_name": "Nacional/Marino",
                "year": int(year) if year else None,
                "tipo_code": "GACETA",
                "tipo_name": "Gaceta Informativa ASEA",
                "sequence": num if num else "00",
                "doc_category": "gaceta",
            }

        # Fallback genérico
        return {
            "input": str(input_item),
            "is_valid_sinat": False,
            "clave": stem,
            "source": source,
            "sector_code": None,
            "sector_name": "Desconocido",
            "estado_code": None,
            "estado_name": "Desconocido",
            "year": None,
            "tipo_code": None,
            "tipo_name": "Desconocido",
            "sequence": None,
            "doc_category": doc_category,
        }

def classify_item(input_item: Union[str, Path]) -> Dict[str, Any]:
    """Función helper para clasificar un elemento."""
    return DocumentClassifier.classify(input_item)
