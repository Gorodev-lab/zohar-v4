import re
import sys
from pathlib import Path

try:
    from core.pdf_processor import iter_pages_as_markdown
except ImportError:
    sys.exit(1)

def sanar_clave_dgira(clave_sucia):
    clave = clave_sucia.strip().upper()
    if len(clave) != 13: return None
    
    ocr_num_fixes = {'O': '0', 'I': '1', 'L': '1', 'S': '5', 'Z': '2'}
    mascara = "NNLLNNNNLNNNN"
    clave_limpia = ""
    
    for i, char in enumerate(clave):
        if mascara[i] == 'N': clave_limpia += ocr_num_fixes.get(char, char)
        elif mascara[i] == 'L':
            if char == '0': clave_limpia += 'O'
            elif char == '1': clave_limpia += 'I'
            elif char == '5': clave_limpia += 'S'
            else: clave_limpia += char
            
    if re.match(r"^[0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4}$", clave_limpia):
        return clave_limpia
    return None

# Regex sin "\b" para que la puntuación del OCR no nos oculte claves
patron_captura = re.compile(r'[a-zA-Z0-9]{13}')
gaceta_dir = Path("downloads/gacetas")
pdf_files = list(gaceta_dir.glob("*.pdf"))

claves_totales = set()

print(f"--- Procesando TODAS las Gacetas con Escáner Profundo ---")
for pdf_path in pdf_files:
    print(f"📄 Analizando: {pdf_path.name}...")
    try:
        texto_completo = ""
        for pagina in iter_pages_as_markdown(str(pdf_path)):
            texto_completo += (" ".join(map(str, pagina)) if isinstance(pagina, tuple) else str(pagina)) + " "
        
        posibles_claves = patron_captura.findall(texto_completo)
        
        for candidata in posibles_claves:
            limpia = sanar_clave_dgira(candidata)
            if limpia: claves_totales.add(limpia)
            
    except Exception as e:
        print(f"   ❌ Error procesando {pdf_path.name}: {e}")

# Guardar en archivo para el orquestador
with open("claves_pendientes.txt", "w", encoding="utf-8") as f:
    for c in list(claves_totales):
        f.write(c + "\n")

print(f"\n✅ Extracción completada. {len(claves_totales)} claves únicas guardadas en 'claves_pendientes.txt'")
