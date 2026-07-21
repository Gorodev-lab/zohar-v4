"""
core/text_utils.py
==================
Utilidades de procesamiento de texto para el pipeline de Zohar v4.
"""

import re

_ESTADOS_MX = [
    "Aguascalientes", "Baja California Sur", "Baja California", "Campeche",
    "Chiapas", "Chihuahua", "Coahuila", "Colima", "Ciudad de México",
    "Durango", "Guanajuato", "Guerrero", "Hidalgo", "Jalisco",
    "México", "Michoacán", "Morelos", "Nayarit", "Nuevo León",
    "Oaxaca", "Puebla", "Querétaro", "Quintana Roo", "San Luis Potosí",
    "Sinaloa", "Sonora", "Tabasco", "Tamaulipas", "Tlaxcala",
    "Veracruz", "Yucatán", "Zacatecas",
]

_KEYWORD_PATTERNS = [
    r"promovente[:\s]", r"solicitante[:\s]", r"raz[oó]n social[:\s]",
    r"nombre\s+de\s+la\s+empresa", r"responsable\s+del\s+proyecto",
    r"ubicad[oa]\s+en", r"municipio\s+de", r"localidad\s+de",
    r"sector\s+(productivo|econ[oó]mico)?",
]

def build_targeted_snippet(text: str, prefix_chars: int = 2000,
                            window_chars: int = 220, max_total_chars: int = 5000) -> str:
    """
    Construye un snippet determinista concatenando el encabezado (prefijo) del texto
    y ventanas de contexto alrededor de palabras clave (promoventes, estados, etc.).
    Evita cortes ciegos y maximiza la probabilidad de que el LLM encuentre metadatos.
    """
    if not text:
        return ""
    
    # Agregar el prefijo inicial como primera ventana
    pieces = [text[:prefix_chars]]
    seen_spans = [(0, prefix_chars)]
    
    pattern = re.compile(
        "(" + "|".join(_KEYWORD_PATTERNS) + "|" + "|".join(re.escape(e) for e in _ESTADOS_MX) + ")",
        re.IGNORECASE,
    )
    
    for match in pattern.finditer(text):
        start = max(0, match.start() - 60)
        end = min(len(text), match.end() + window_chars)
        
        # Verificar si hay solapamiento con ventanas de texto ya incluidas
        if any(start < s_end and end > s_start for s_start, s_end in seen_spans):
            continue
            
        pieces.append(text[start:end])
        seen_spans.append((start, end))
        
        if sum(len(p) for p in pieces) >= max_total_chars:
            break
            
    return "\n[...]\n".join(pieces)[:max_total_chars]
