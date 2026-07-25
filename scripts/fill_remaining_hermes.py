import sqlite3
import glob
import os
import re

DB_PATH = "data/metadata_proyecto.db"
EXTRACTIONS_DIR = "extractions"

# Patrones avanzados de entidades gubernamentales y corporativas
ENTIDAD_PATTERNS = [
    r"(?i)I\.1\s+NOMBRE[^\n:]*[:\s]+([^\n]+)",
    r"(?i)(?:PROMOVENTE|SOLICITANTE|TITULAR)[^\n:]*[:\s]+([^\n]+)",
    r"(?i)(SECRETAR[IÍ]A DE [A-ZÁÉÍÓÚÑ\s\,\.\-]+)",
    r"(?i)(COMISI[OÓ]N FEDERAL DE ELECTRICIDAD[A-ZÁÉÍÓÚÑ\s\,\.\-]*)",
    r"(?i)(PETR[OÓ]LEOS MEXICANOS[A-ZÁÉÍÓÚÑ\s\,\.\-]*)",
    r"(?i)(H?\s*AYUNTAMIENTO CONSTITUCIONAL DE [A-ZÁÉÍÓÚÑ\s\,\.\-]+)",
    r"(?i)(GOBIERNO DEL ESTADO DE [A-ZÁÉÍÓÚÑ\s\,\.\-]+)",
    r"(?i)(DIRECCI[OÓ]N GENERAL DE [A-ZÁÉÍÓÚÑ\s\,\.\-]+)",
    r"(?i)([A-Z0-9\.\,\s\-]+S\.?\s*A\.?\s*DE\s*C\.?\s*V\.?)",
    r"(?i)([A-Z0-9\.\,\s\-]+S\.?\s*DE\s*R\.?\s*L\.?\s*DE\s*C\.?\s*V\.?)"
]

def clean_promovente(text):
    if not text:
        return None
    # Limpiar caracteres Markdown y espacios sobrantes
    cleaned = re.sub(r"[\*\_>#]+", "", text).strip().upper()
    if len(cleaned) < 4 or cleaned in ["NONE", "NULL", "DESCONOCIDO"]:
        return None
    return cleaned[:120]

def extract_pnl_promovente(file_path):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read(15000) # Ventana amplia de contexto
        
    for pattern in ENTIDAD_PATTERNS:
        match = re.search(pattern, content)
        if match:
            candidate = clean_promovente(match.group(1))
            if candidate:
                return candidate
    return None

def run_batch():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT clave FROM metadata_proyecto WHERE promovente IS NULL OR promovente = ''")
    pending = cursor.fetchall()
    
    print(f"Iniciando procesamiento PNL para {len(pending)} proyectos pendientes...")
    
    success = 0
    for (clave,) in pending:
        pattern = os.path.join(EXTRACTIONS_DIR, f"{clave}*.md")
        files = glob.glob(pattern)
        
        if not files:
            continue
            
        promovente = extract_pnl_promovente(files[0])
        if promovente:
            cursor.execute("""
                UPDATE metadata_proyecto 
                SET promovente = ?, requiere_revision = 0
                WHERE clave = ?
            """, (promovente, clave))
            success += 1
            
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM metadata_proyecto WHERE promovente IS NULL")
    remaining = cursor.fetchone()[0]
    
    conn.close()
    print(f"✅ Extracción completada. {success} promoventes rescatados.")
    print(f"📊 Registros totales sin promovente restantes: {remaining}")

if __name__ == "__main__":
    run_batch()
